#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

PROJECT_DIR=/opt/just1kbot
ENV_FILE="$PROJECT_DIR/.env"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE=just1kbot.service
HEALTHCHECK_SERVICE=just1kbot-healthcheck.service
HEALTHCHECK_TIMER=just1kbot-healthcheck.timer
BACKUP_SERVICE=just1kbot-backup.service
BACKUP_TIMER=just1kbot-backup.timer
HEARTBEAT_FILE=/run/just1kbot/heartbeat
RELEASE_METADATA="$PROJECT_DIR/.release-version"
BACKUP_DIR=/root/backups/just1kbot
DEPLOY_LOCK=/run/lock/just1kbot-deploy.lock
MAX_HEARTBEAT_AGE=180
MAX_BACKUP_AGE=172800
MODE=summary
FAILURES=0
WARNINGS=0

usage() {
    cat <<'EOF_USAGE'
Just1kBot doctor — read-only production diagnostics

Usage:
  sudo bash scripts/ops/doctor.sh
  sudo bash scripts/ops/doctor.sh --smoke

Modes:
  default  full owner-friendly report
  --smoke  concise post-deploy gate; exits non-zero on critical failures
EOF_USAGE
}

ok() {
    printf '[OK] %s\n' "$*"
}

warn() {
    WARNINGS=$((WARNINGS + 1))
    printf '[WARN] %s\n' "$*"
}

fail() {
    FAILURES=$((FAILURES + 1))
    printf '[FAIL] %s\n' "$*" >&2
}

require_root() {
    if (( EUID != 0 )); then
        printf 'Ошибка: doctor нужно запускать от root через sudo.\n' >&2
        exit 2
    fi
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --smoke)
                MODE=smoke
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                printf 'Неизвестный аргумент doctor: %s\n' "$1" >&2
                usage >&2
                exit 2
                ;;
        esac
        shift
    done
}

acquire_shared_deploy_lock() {
    install -d -o root -g root -m 0755 "$(dirname "$DEPLOY_LOCK")"
    exec 201>"$DEPLOY_LOCK"
    if ! flock -s -w 5 201; then
        printf 'Диагностика не запущена: deploy/backup/restore/uninstall сейчас держит lock.\n' >&2
        exit 75
    fi
}

safe_regular_file() {
    local path=$1
    [[ -f "$path" && ! -L "$path" ]]
}

check_service() {
    local active enabled
    active=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
    enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)

    if [[ "$active" == active ]]; then
        ok "Bot service: active"
    else
        fail "Bot service: ${active:-not-found}"
    fi

    if [[ "$enabled" == enabled ]]; then
        ok "Bot autostart: enabled"
    else
        fail "Bot autostart: ${enabled:-not-found}"
    fi

    if ! systemctl cat "$SERVICE" >/dev/null 2>&1; then
        fail "Systemd unit $SERVICE отсутствует"
        return
    fi

    local unit
    unit=$(systemctl cat "$SERVICE" 2>/dev/null || true)
    if grep -Fq 'ProtectHome=true' <<<"$unit"; then
        ok "Systemd sandbox: ProtectHome=true"
    else
        fail "Systemd sandbox: ProtectHome=true отсутствует"
    fi
    if grep -Fq 'JUST1KBOT_HEARTBEAT_FILE=/run/just1kbot/heartbeat' <<<"$unit"; then
        ok "Heartbeat path: runtime directory"
    else
        fail "Heartbeat path в systemd unit не соответствует production contract"
    fi
}

check_service_account_and_permissions() {
    local account home project_state env_state

    account=$(getent passwd just1kbot 2>/dev/null || true)
    if [[ -z "$account" ]]; then
        fail "Service account just1kbot отсутствует"
        return
    fi
    home=$(cut -d: -f6 <<<"$account")
    if [[ "$home" == /home/just1kbot ]]; then
        ok "Service account home: $home"
    else
        fail "Service account имеет неожиданный home: ${home:-empty}"
    fi

    if [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]]; then
        project_state=$(stat -c '%U:%G %a' "$PROJECT_DIR" 2>/dev/null || true)
        if [[ "$project_state" == 'root:just1kbot 750' ]]; then
            ok "Project permissions: $project_state"
        else
            fail "Project permissions: ${project_state:-unreadable}, expected root:just1kbot 750"
        fi
    else
        fail "Project directory отсутствует или является symlink"
    fi

    if safe_regular_file "$ENV_FILE"; then
        env_state=$(stat -c '%U:%G %a' "$ENV_FILE" 2>/dev/null || true)
        if [[ "$env_state" == 'root:just1kbot 640' ]]; then
            ok "Production .env permissions: $env_state"
        else
            fail "Production .env permissions: ${env_state:-unreadable}, expected root:just1kbot 640"
        fi
    else
        fail "Production .env отсутствует или небезопасен"
    fi

    if runuser -u just1kbot -- test -x "$PROJECT_DIR" 2>/dev/null; then
        ok "Service user может пройти в project directory"
    else
        fail "Service user не может пройти в project directory"
    fi
    if runuser -u just1kbot -- test -r "$ENV_FILE" 2>/dev/null; then
        ok "Service user может прочитать production .env"
    else
        fail "Service user не может прочитать production .env"
    fi
}

check_heartbeat() {
    if ! safe_regular_file "$HEARTBEAT_FILE"; then
        fail "Heartbeat отсутствует или небезопасен: $HEARTBEAT_FILE"
        return
    fi

    local now modified age
    now=$(date +%s)
    modified=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || printf 0)
    age=$((now - modified))
    if (( age >= 0 && age <= MAX_HEARTBEAT_AGE )); then
        ok "Heartbeat fresh: age=${age}s"
    else
        fail "Heartbeat stale: age=${age}s, limit=${MAX_HEARTBEAT_AGE}s"
    fi
}

check_release_metadata() {
    if ! safe_regular_file "$RELEASE_METADATA"; then
        warn "Release metadata отсутствует; вероятно использован deploy из checkout"
        return
    fi

    local owner group mode repository ref commit count
    owner=$(stat -c '%U' "$RELEASE_METADATA" 2>/dev/null || true)
    group=$(stat -c '%G' "$RELEASE_METADATA" 2>/dev/null || true)
    mode=$(stat -c '%a' "$RELEASE_METADATA" 2>/dev/null || true)
    if [[ "$owner" != root || ! "$mode" =~ ^[0-7]{3,4}$ ]] ||
        (( (8#$mode & 8#022) != 0 )); then
        fail "Release metadata имеет небезопасные permissions: ${owner:-?}:${group:-?} ${mode:-?}"
        return
    fi

    repository=$(sed -n 's/^source_repository=//p' "$RELEASE_METADATA")
    ref=$(sed -n 's/^source_ref=//p' "$RELEASE_METADATA")
    commit=$(sed -n 's/^source_commit=//p' "$RELEASE_METADATA")
    count=$(grep -c '^source_commit=' "$RELEASE_METADATA" 2>/dev/null || true)

    if [[ "$repository" != 'https://github.com/justik13/projectx.git' || "$ref" != 'refs/heads/main' ]]; then
        fail "Release metadata указывает на неожиданный source"
        return
    fi
    if [[ "$count" == 1 && "$commit" =~ ^[0-9a-f]{40}$ ]]; then
        ok "Installed commit: ${commit:0:12}"
    else
        fail "Release metadata содержит некорректный source_commit"
    fi
}

unit_exists() {
    systemctl cat "$1" >/dev/null 2>&1
}

check_operational_units() {
    local unit active enabled

    for unit in "$HEALTHCHECK_SERVICE" "$HEALTHCHECK_TIMER" "$BACKUP_SERVICE" "$BACKUP_TIMER"; do
        if unit_exists "$unit"; then
            ok "Unit installed: $unit"
        else
            fail "Unit missing: $unit"
        fi
    done

    for unit in "$HEALTHCHECK_TIMER" "$BACKUP_TIMER"; do
        active=$(systemctl is-active "$unit" 2>/dev/null || true)
        enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
        if [[ "$active" == active && "$enabled" == enabled ]]; then
            ok "Timer ready: $unit"
        else
            fail "Timer not ready: $unit active=${active:-unknown} enabled=${enabled:-unknown}"
        fi
    done
}

check_latest_backup() {
    local latest sidecar filename expected actual age
    latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
        -name 'just1kbot-pg-v1-????????T??????Z.tar.age' \
        -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n1 | cut -d' ' -f2- || true)

    if [[ -z "$latest" ]]; then
        warn "Encrypted PostgreSQL backup пока не найден"
        return
    fi
    sidecar="$latest.sha256"
    if ! safe_regular_file "$sidecar"; then
        fail "Checksum sidecar отсутствует для $(basename "$latest")"
        return
    fi

    filename=$(basename "$latest")
    expected=$(awk -v name="$filename" '$2 == name {print $1; exit}' "$sidecar" 2>/dev/null || true)
    actual=$(sha256sum "$latest" 2>/dev/null | awk '{print $1}' || true)
    if [[ "$expected" =~ ^[0-9a-f]{64}$ && "$actual" == "$expected" ]]; then
        age=$(( $(date +%s) - $(stat -c %Y "$latest") ))
        ok "Latest backup: $filename age=${age}s checksum=ok"
        if (( age > MAX_BACKUP_AGE )); then
            warn "Последний backup старше ${MAX_BACKUP_AGE}s"
        fi
    else
        fail "Checksum не подтверждён для $filename"
    fi
}

run_application_checks() {
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        fail "Runtime Python отсутствует или не исполняемый"
        return
    fi
    if ! runuser -u just1kbot -- test -x "$VENV_DIR/bin/python" 2>/dev/null; then
        fail "Service user не может запустить runtime Python"
        return
    fi

    local output rc
    set +e
    output=$(timeout --signal=TERM --kill-after=5s 35s \
        runuser -u just1kbot -- \
        env HOME=/run/just1kbot PYTHONPATH="$PROJECT_DIR" PYTHONDONTWRITEBYTECODE=1 \
        bash -c "cd '$PROJECT_DIR' && '$VENV_DIR/bin/python' -" <<'PY'
import asyncio

import redis.asyncio as redis
from aiogram import Bot
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from config.settings import get_settings


async def check() -> None:
    settings = get_settings()
    config = Config("alembic.ini")
    code_heads = set(ScriptDirectory.from_config(config).get_heads())
    if not code_heads:
        raise RuntimeError("alembic code head is empty")

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
        retry_on_timeout=False,
    )
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(text("SELECT version_num FROM alembic_version"))
            database_heads = {str(row[0]) for row in rows if row[0]}
        if database_heads != code_heads:
            raise RuntimeError(
                "alembic mismatch: database="
                + ",".join(sorted(database_heads))
                + " code="
                + ",".join(sorted(code_heads))
            )
        if not await redis_client.ping():
            raise RuntimeError("redis ping returned false")
        me = await bot.get_me()
        username = me.username or "<none>"
        print("database=ok")
        print("alembic=" + ",".join(sorted(database_heads)))
        print("redis=ok")
        print(f"telegram=@{username} id={me.id}")
    finally:
        await bot.session.close()
        await redis_client.aclose()
        await engine.dispose()


asyncio.run(asyncio.wait_for(check(), timeout=30))
PY
    2>&1)
    rc=$?
    set -e

    if (( rc == 0 )); then
        while IFS= read -r line; do
            [[ -n "$line" ]] && ok "$line"
        done <<<"$output"
    else
        local safe_output
        safe_output=$(printf '%s\n' "${output:-exit_code=$rc}" | sed -E \
            -e 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/***TELEGRAM_TOKEN_REDACTED***/g' \
            -e 's#(postgresql(\+asyncpg)?://)[^/@[:space:]]+(:[^/@[:space:]]*)?@#\1***@#g' \
            -e 's#(redis://)[^/@[:space:]]+@#\1***@#g')
        fail "Application dependencies check failed: $safe_output"
    fi
}

print_summary() {
    printf '\nResult: failures=%s warnings=%s\n' "$FAILURES" "$WARNINGS"
    if (( FAILURES == 0 )); then
        printf 'Just1kBot core is ready.\n'
    else
        printf 'Just1kBot core is not ready. See failed checks above.\n' >&2
        printf 'Logs: journalctl -u just1kbot.service -n 100 --no-pager\n' >&2
    fi
}

main() {
    parse_args "$@"
    require_root
    for command in systemctl flock install stat getent runuser date find sort cut sha256sum timeout sed grep awk; do
        command -v "$command" >/dev/null 2>&1 || {
            printf 'Ошибка: не найдена обязательная команда %s\n' "$command" >&2
            exit 2
        }
    done

    acquire_shared_deploy_lock
    [[ "$MODE" == smoke ]] || printf 'Just1kBot production doctor\n\n'

    check_service
    check_service_account_and_permissions
    check_heartbeat
    check_release_metadata
    check_operational_units
    check_latest_backup
    run_application_checks
    print_summary

    (( FAILURES == 0 ))
}

main "$@"
