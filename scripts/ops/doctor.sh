#!/bin/bash
# Read-only production diagnostics for Just1kBot.
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

PROJECT_DIR=/opt/just1kbot
ENV_FILE="$PROJECT_DIR/.env"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE=just1kbot.service
REDIS_SERVICE=just1kbot-redis.service
REDIS_CONFIG=/etc/just1kbot/redis.conf
MANIFEST=/var/lib/just1kbot/install-state/manifest.json
TRANSACTION=/var/lib/just1kbot/install-state/transaction.json
HEALTHCHECK_TIMER=just1kbot-healthcheck.timer
BACKUP_TIMER=just1kbot-backup.timer
HEARTBEAT_FILE=/run/just1kbot/heartbeat
RELEASE_METADATA="$PROJECT_DIR/.release-version"
BACKUP_DIR=/var/lib/just1kbot/backups
DEPLOY_LOCK=/run/lock/just1kbot-deploy.lock
MAX_HEARTBEAT_AGE=180
MAX_BACKUP_AGE=172800
MODE=summary
FAILURES=0
WARNINGS=0
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
FOUNDATION="$ROOT_DIR/scripts/lib/installer_foundation.sh"
FOUNDATION_COMPAT="$ROOT_DIR/scripts/lib/installer_foundation_compat.sh"

usage() {
    cat <<'EOF_USAGE'
Just1kBot doctor — read-only production diagnostics
Usage:
  sudo bash scripts/ops/doctor.sh
  sudo bash scripts/ops/doctor.sh --smoke
EOF_USAGE
}

ok() { printf '[OK] %s\n' "$*"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf '[WARN] %s\n' "$*"; }
fail() { FAILURES=$((FAILURES + 1)); printf '[FAIL] %s\n' "$*" >&2; }

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --smoke) MODE=smoke ;;
            -h|--help) usage; exit 0 ;;
            *) printf 'Неизвестный argument doctor: %s\n' "$1" >&2; exit 2 ;;
        esac
        shift
    done
}

require_root() {
    (( EUID == 0 )) || {
        printf 'Ошибка: doctor нужно запускать от root.\n' >&2
        exit 2
    }
}

acquire_shared_deploy_lock() {
    [[ -d "$(dirname "$DEPLOY_LOCK")" ]] || {
        fail 'deploy lock directory отсутствует'
        return
    }
    exec 201>"$DEPLOY_LOCK"
    flock -s -w 5 201 || {
        printf 'Doctor: operation lock занят.\n' >&2
        exit 75
    }
}

safe_regular_file() { [[ -f "$1" && ! -L "$1" ]]; }

check_os() {
    if [[ ! -f /etc/os-release || -L /etc/os-release ]]; then
        fail '/etc/os-release отсутствует или небезопасен'
        return
    fi
    local id version
    id=$(awk -F= '$1=="ID" {gsub(/"/,"",$2); print $2}' /etc/os-release)
    version=$(awk -F= '$1=="VERSION_ID" {gsub(/"/,"",$2); print $2}' /etc/os-release)
    if [[ "$id" == ubuntu && "$version" == 24.04 ]]; then
        ok 'OS: Ubuntu 24.04 LTS'
    else
        fail "OS unsupported: $id $version"
    fi
}

check_manifest() {
    local library
    for library in "$FOUNDATION" "$FOUNDATION_COMPAT"; do
        if [[ ! -f "$library" || -L "$library" ]]; then
            fail "installer library отсутствует: $library"
            return
        fi
    done
    INSTALLER_FOUNDATION_SOURCE_ONLY=1
    # shellcheck source=../lib/installer_foundation.sh
    source "$FOUNDATION"
    unset INSTALLER_FOUNDATION_SOURCE_ONLY
    INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
    # shellcheck source=../lib/installer_foundation_compat.sh
    source "$FOUNDATION_COMPAT"
    unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

    if foundation_manifest_validate >/dev/null 2>&1; then
        ok "Ownership manifest: valid id=$(foundation_manifest_id)"
    else
        fail "Ownership manifest invalid: $MANIFEST"
    fi
    if [[ -e "$TRANSACTION" || -L "$TRANSACTION" ]]; then
        if foundation_journal_validate >/dev/null 2>&1; then
            warn 'Найдена незавершённая installer transaction; используйте install-recover.'
        else
            fail 'Installer transaction journal повреждён.'
        fi
    else
        ok 'Installer transaction journal: absent'
    fi
}

check_service() {
    local active enabled unit
    active=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
    enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)
    [[ "$active" == active ]] && ok 'Bot service: active' || fail "Bot service: ${active:-not-found}"
    [[ "$enabled" == enabled ]] && ok 'Bot autostart: enabled' || fail "Bot autostart: ${enabled:-not-found}"
    unit=$(systemctl cat "$SERVICE" 2>/dev/null || true)
    [[ -n "$unit" ]] || { fail "Systemd unit $SERVICE отсутствует"; return; }
    grep -Fq 'ProtectHome=true' <<<"$unit" && ok 'Systemd sandbox: ProtectHome=true' || fail 'ProtectHome=true отсутствует'
    grep -Fq 'Environment=HOME=/run/just1kbot' <<<"$unit" && ok 'HOME=/run/just1kbot' || fail 'Runtime HOME mismatch'
    grep -Fq 'JUST1KBOT_HEARTBEAT_FILE=/run/just1kbot/heartbeat' <<<"$unit" && ok 'Heartbeat runtime path' || fail 'Heartbeat path mismatch'
    (grep -Fq 'Requires=just1kbot-redis.service' <<<"$unit" || systemctl show "$SERVICE" -p Requires 2>/dev/null | grep -q 'just1kbot-redis.service') && ok 'Dedicated Redis dependency' || fail 'Dedicated Redis dependency absent'
}

check_dedicated_redis() {
    local active enabled unit config_state
    active=$(systemctl is-active "$REDIS_SERVICE" 2>/dev/null || true)
    enabled=$(systemctl is-enabled "$REDIS_SERVICE" 2>/dev/null || true)
    [[ "$active" == active ]] && ok 'Dedicated Redis service: active' || fail "Dedicated Redis service: ${active:-not-found}"
    [[ "$enabled" == enabled ]] && ok 'Dedicated Redis autostart: enabled' || fail "Dedicated Redis autostart: ${enabled:-not-found}"
    unit=$(systemctl cat "$REDIS_SERVICE" 2>/dev/null || true)
    grep -Fq 'ExecStart=/usr/bin/redis-server /etc/just1kbot/redis.conf' <<<"$unit" && ok 'Dedicated Redis config path' || fail 'Dedicated Redis unit mismatch'
    if safe_regular_file "$REDIS_CONFIG"; then
        config_state=$(stat -c '%U %G %a' "$REDIS_CONFIG" 2>/dev/null || true)
        [[ "$config_state" == 'root redis 640' ]] && ok 'Redis config permissions: root:redis 640' || fail "Redis config permissions: $config_state"
        grep -Fxq 'port 6380' "$REDIS_CONFIG" && ok 'Redis port: 6380' || fail 'Redis port is not 6380'
        grep -Fxq 'maxmemory-policy noeviction' "$REDIS_CONFIG" && ok 'Redis eviction: noeviction' || fail 'Redis eviction policy mismatch'
    else
        fail 'Dedicated Redis config missing/unsafe'
    fi
}

check_permissions() {
    local state account home shell
    account=$(getent passwd just1kbot 2>/dev/null || true)
    if [[ -z "$account" ]]; then
        fail 'Service account just1kbot отсутствует'
        return
    fi
    home=$(cut -d: -f6 <<<"$account")
    shell=$(cut -d: -f7 <<<"$account")
    [[ "$home" == /home/just1kbot ]] && ok "Service account home: $home" || fail "Service account home: $home"
    [[ "$shell" == /usr/sbin/nologin || "$shell" == /sbin/nologin ]] && ok "Service shell: $shell" || fail "Service shell: $shell"
    if [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]]; then
        state=$(stat -c '%U %G %a' "$PROJECT_DIR" 2>/dev/null || true)
        [[ "$state" == 'root just1kbot 750' ]] && ok 'Project permissions: root:just1kbot 750' || fail "Project permissions: $state"
    else
        fail 'Project directory missing/unsafe'
    fi
    if safe_regular_file "$ENV_FILE"; then
        state=$(stat -c '%U %G %a' "$ENV_FILE" 2>/dev/null || true)
        [[ "$state" == 'root just1kbot 640' ]] && ok 'Environment permissions: root:just1kbot 640' || fail "Environment permissions: $state"
    else
        fail 'Production .env missing/unsafe'
    fi
    runuser -u just1kbot -- test -r "$ENV_FILE" 2>/dev/null && ok 'Service user can read .env' || fail 'Service user cannot read .env'
    if find "$PROJECT_DIR" -xdev -user just1kbot -print -quit 2>/dev/null | grep -q .; then
        fail 'Live release contains service-owned paths'
    else
        ok 'Live release is root-owned'
    fi
}

check_heartbeat() {
    if ! safe_regular_file "$HEARTBEAT_FILE"; then
        fail "Heartbeat missing/unsafe: $HEARTBEAT_FILE"
        return
    fi
    local age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))
    (( age >= 0 && age <= MAX_HEARTBEAT_AGE )) && ok "Heartbeat fresh: age=${age}s" || fail "Heartbeat stale: age=${age}s"
}

check_release_metadata() {
    if ! safe_regular_file "$RELEASE_METADATA"; then
        warn '.release-version отсутствует; вероятно deploy из checkout'
        return
    fi
    local commit
    commit=$(sed -n 's/^source_commit=//p' "$RELEASE_METADATA")
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] && ok "Release commit: $commit" || fail 'Release metadata commit invalid'
}

check_timers() {
    local unit state
    for unit in "$HEALTHCHECK_TIMER" "$BACKUP_TIMER"; do
        state=$(systemctl is-active "$unit" 2>/dev/null || true)
        [[ "$state" == active ]] && ok "$unit: active" || fail "$unit: ${state:-not-found}"
    done
}

check_backup() {
    local latest age sidecar expected actual
    latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-????????T??????Z.tar.age' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
    if [[ -z "$latest" ]]; then
        warn 'Encrypted backup отсутствует'
        return
    fi
    age=$(( $(date +%s) - $(stat -c %Y "$latest") ))
    (( age <= MAX_BACKUP_AGE )) && ok "Latest backup age=${age}s" || warn "Latest backup old: age=${age}s"
    sidecar="$latest.sha256"
    if safe_regular_file "$sidecar"; then
        expected=$(awk 'NF {print $1; exit}' "$sidecar")
        actual=$(sha256sum "$latest" | awk '{print $1}')
        [[ "$expected" == "$actual" ]] && ok 'Backup sha256 sidecar valid' || fail 'Backup sha256 mismatch'
    else
        fail 'Backup sha256 sidecar missing/unsafe'
    fi
}

check_telegram_api() {
    # Pre-flight reachability check for api.telegram.org via curl.
    # Gives a clear "Telegram API blocked/unreachable" message instead of
    # hiding the root cause behind "secrets redacted" in the Python check.
    if ! command -v curl >/dev/null 2>&1; then
        warn 'curl не установлен; пропуск pre-flight проверки Telegram API'
        return
    fi
    local http_code
    http_code=$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --max-time 10 --connect-timeout 5 \
        'https://api.telegram.org/' 2>/dev/null || printf '000')
    if [[ "$http_code" == '000' ]]; then
        fail 'Telegram API unreachable: api.telegram.org (timeout/DNS/network). Проверьте провайдера, DNS, прокси или файрвол.'
        return
    fi
    if [[ "$http_code" == '200' || "$http_code" == '404' || "$http_code" == '301' || "$http_code" == '302' ]]; then
        ok "Telegram API reachable (HTTP $http_code)"
    else
        warn "Telegram API returned HTTP $http_code — может быть временная проблема на стороне Telegram"
    fi
}

check_runtime_dependencies() {
    # P3-2: Защита от потери backup.agekey
    if grep -q "^DB_ENCRYPTION_KEY=" "$ENV_FILE" 2>/dev/null; then
        if [ ! -f "/etc/just1kbot/backup.agekey" ]; then
            fail 'CRITICAL WARNING: DB_ENCRYPTION_KEY is present but /etc/just1kbot/backup.agekey is missing!'
        else
            ok 'Backup key is present'
        fi
    fi

    [[ -x "$VENV_DIR/bin/python" ]] || {
        fail 'Virtualenv Python отсутствует'
        return
    }
    [[ -d "$PROJECT_DIR" ]] || {
        fail 'Project directory отсутствует: $PROJECT_DIR'
        return
    }

    # CRITICAL: cd into project directory before runuser so that pydantic-settings
    # finds .env in the correct location. Without this, Python searches the
    # caller's CWD (e.g. /root), causing PermissionError for just1kbot user.
    cd "$PROJECT_DIR" || { fail "Project dir missing: $PROJECT_DIR"; return; }

    if ! timeout --signal=TERM --kill-after=5s 45s \
        runuser -u just1kbot -- env \
        HOME=/run/just1kbot \
        PYTHONPATH="$PROJECT_DIR" \
        PYTHONDONTWRITEBYTECODE=1 \
        "$VENV_DIR/bin/python" - <<'PY_RUNTIME'
import asyncio
import sys

from aiogram import Bot
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import redis.asyncio as redis

from config.settings import get_settings


async def check():
    settings = get_settings()
    config = Config("/opt/just1kbot/alembic.ini")
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("migration graph must have one head")
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    redis_client = redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    bot = Bot(settings.BOT_TOKEN)
    try:
        async with engine.connect() as connection:
            revision = (
                await connection.execute(
                    text("SELECT version_num FROM alembic_version")
                )
            ).scalar_one()
            if revision != heads[0]:
                raise RuntimeError("database revision mismatch")
        if not await redis_client.ping():
            raise RuntimeError("redis ping false")

        # Retry Telegram API with 3 attempts and backoff
        last_error = None
        for attempt in range(3):
            try:
                me = await asyncio.wait_for(bot.get_me(), timeout=10)
                if not me.id:
                    raise RuntimeError("Telegram get_me invalid")
                break
            except (asyncio.TimeoutError, Exception) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise last_error
    finally:
        await redis_client.aclose()
        await bot.session.close()
        await engine.dispose()


asyncio.run(asyncio.wait_for(check(), timeout=35))
PY_RUNTIME
    then
        fail 'Runtime dependency check failed (secrets redacted)'
        return
    fi
    ok 'PostgreSQL/Alembic, Redis and Telegram checks passed'
}

check_nginx() {
    command -v nginx >/dev/null 2>&1 || {
        fail 'nginx binary отсутствует'
        return
    }
    nginx -t >/dev/null 2>&1 && ok 'Nginx configuration valid' || fail 'nginx -t failed'
}

main() {
    parse_args "$@"
    require_root
    acquire_shared_deploy_lock
    check_os
    check_manifest
    check_service
    check_dedicated_redis
    check_permissions
    check_heartbeat
    check_release_metadata
    check_timers
    check_backup
    check_telegram_api
    check_runtime_dependencies
    check_nginx
    printf '\nDoctor result: failures=%s warnings=%s mode=%s\n' "$FAILURES" "$WARNINGS" "$MODE"
    (( FAILURES == 0 ))
}

main "$@"