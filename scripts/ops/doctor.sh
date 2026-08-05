#!/bin/bash
set -Eeuo pipefail

SERVICE=just1kbot
REDIS_SERVICE=just1kbot-redis.service
PROJECT_DIR=/opt/just1kbot
VENV_DIR="$PROJECT_DIR/venv"
ENV_FILE="$PROJECT_DIR/.env"
STATE_DIR=/var/lib/just1kbot/install-state
STATE_ROOT=/var/lib/just1kbot
MANIFEST="$STATE_DIR/manifest.json"
TRANSACTION="$STATE_DIR/transaction.json"
SYSTEMD_UNIT=/etc/systemd/system/just1kbot.service
HEALTHCHECK_TIMER=just1kbot-healthcheck.timer
BACKUP_TIMER=just1kbot-backup.timer
HEARTBEAT_FILE=/run/just1kbot/heartbeat
RELEASE_METADATA="$PROJECT_DIR/.release-version"
BACKUP_DIR=/var/lib/just1kbot/backups
DEPLOY_LOCK=/run/lock/just1kbot-deploy.lock
MAX_HEARTBEAT_AGE=180
MAX_BACKUP_AGE=172800

FAILURES=0

log() { printf '[DOCTOR] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; FAILURES=$((FAILURES + 1)); }

acquire_doctor_lock() {
    exec 9>"$DEPLOY_LOCK"
    flock -s -w 5 9 || {
        fail "Не удалось захватить разделяемую блокировку $DEPLOY_LOCK"
        exit 1
    }
}

check_release_freshness() {
    if [ ! -d "$PROJECT_DIR/.git" ]; then
        if [ -f "$RELEASE_METADATA" ]; then
            ok "Standalone deployment metadata present: $RELEASE_METADATA"
        else
            fail "Release version metadata missing: $RELEASE_METADATA"
        fi
        return
    fi
    local status dirty
    status=$(git -C "$PROJECT_DIR" status --porcelain 2>/dev/null || true)
    dirty=$(grep -v '^?? ' <<<"$status" || true)
    if [ -n "$dirty" ]; then
        fail 'Git working tree in /opt/just1kbot has uncommitted changes'
    else
        ok 'Git working tree is clean'
    fi
}

check_python_environment() {
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        fail 'Virtualenv python missing'
        return
    fi

    local ver
    ver=$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
    if [ "$ver" = "3.12" ]; then
        ok 'Python runtime: 3.12'
    else
        fail "Python runtime version mismatch: ${ver:-unknown}"
    fi

    if [ -f "$PROJECT_DIR/requirements.lock" ]; then
        ok 'Requirements lock present'
    else
        fail 'Requirements lock missing in release tree'
    fi
}

check_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        fail "Environment file $ENV_FILE missing"
        return
    fi
    local mode owner
    mode=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || true)
    owner=$(stat -c '%U:%G' "$ENV_FILE" 2>/dev/null || true)
    if [ "$mode" = "600" ] || [ "$mode" = "640" ]; then
        ok "Environment file permissions: $mode"
    else
        fail "Environment file mode unsafe: ${mode:-unknown}"
    fi
    if [ "$owner" = "root:just1kbot" ] || [ "$owner" = "root:root" ]; then
        ok "Environment file owner: $owner"
    else
        fail "Environment file ownership mismatch: ${owner:-unknown}"
    fi
}

check_system_isolation() {
    if [ -f "$MANIFEST" ]; then
        ok "Installer manifest present: $MANIFEST"
    else
        fail "Installer manifest missing: $MANIFEST"
    fi

    if [ -f "$TRANSACTION" ]; then
        fail "Active/incomplete transaction journal present: $TRANSACTION"
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
    grep -q 'just1kbot-redis.service' <<<"$unit" && ok 'Dedicated Redis dependency' || fail 'Dedicated Redis dependency absent'
}

check_dedicated_redis() {
    local active enabled unit config_state
    active=$(systemctl is-active "$REDIS_SERVICE" 2>/dev/null || true)
    enabled=$(systemctl is-enabled "$REDIS_SERVICE" 2>/dev/null || true)
    [[ "$active" == active ]] && ok 'Dedicated Redis service: active' || fail "Dedicated Redis service: ${active:-not-found}"
    [[ "$enabled" == enabled ]] && ok 'Dedicated Redis autostart: enabled' || fail "Dedicated Redis autostart: ${enabled:-not-found}"
    unit=$(systemctl cat "$REDIS_SERVICE" 2>/dev/null || true)
    grep -Fq 'ExecStart=/usr/bin/redis-server /etc/just1kbot/redis.conf' <<<"$unit" && ok 'Dedicated Redis config path' || fail 'Dedicated Redis unit mismatch'
    if [ -f /etc/just1kbot/redis.conf ]; then
        config_state=$(stat -c '%U:%G %a' /etc/just1kbot/redis.conf 2>/dev/null || true)
        [[ "$config_state" == 'root:just1kbot 640' || "$config_state" == 'root:root 640' ]] && ok 'Dedicated Redis config permissions' || fail "Dedicated Redis config mode mismatch: ${config_state:-unknown}"
    else
        fail 'Dedicated Redis config missing: /etc/just1kbot/redis.conf'
    fi
}

check_timers() {
    local timer active
    for timer in "$HEALTHCHECK_TIMER" "$BACKUP_TIMER"; do
        active=$(systemctl is-active "$timer" 2>/dev/null || true)
        if [ "$active" = "active" ]; then
            ok "Timer $timer: active"
        else
            fail "Timer $timer: ${active:-not-found}"
        fi
    done
}

check_heartbeat() {
    if [ ! -f "$HEARTBEAT_FILE" ]; then
        fail "Heartbeat file missing: $HEARTBEAT_FILE"
        return
    fi
    local now mtime age
    now=$(date +%s)
    mtime=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
    age=$((now - mtime))
    if [ "$age" -le "$MAX_HEARTBEAT_AGE" ]; then
        ok "Heartbeat fresh (${age}s old)"
    else
        fail "Heartbeat stale (${age}s old > ${MAX_HEARTBEAT_AGE}s)"
    fi
}

check_backups() {
    if [ ! -d "$BACKUP_DIR" ]; then
        fail "Backup directory missing: $BACKUP_DIR"
        return
    fi

    local latest mtime age now
    latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-????????T??????Z.tar.age' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n1 | cut -d' ' -f2-)
    if [ -z "$latest" ]; then
        fail 'No encrypted database backups found'
        return
    fi

    if [ -f "$latest.sha256" ]; then
        ok "Backup checksum sidecar present for $(basename "$latest")"
        if (cd "$BACKUP_DIR" && sha256sum -c "$latest.sha256" >/dev/null 2>&1); then
            ok 'Backup checksum verification passed'
        else
            fail 'Backup checksum verification failed'
        fi
    else
        fail "Backup checksum sidecar missing for $(basename "$latest")"
    fi

    now=$(date +%s)
    mtime=$(stat -c %Y "$latest" 2>/dev/null || echo 0)
    age=$((now - mtime))
    if [ "$age" -le "$MAX_BACKUP_AGE" ]; then
        ok "Recent backup fresh (${age}s old)"
    else
        fail "Latest backup stale (${age}s old > ${MAX_BACKUP_AGE}s)"
    fi
}

check_database_migrations() {
    if [ ! -f "$ENV_FILE" ]; then
        return
    fi
    local db_url
    db_url=$(grep -E '^DATABASE_URL=' "$ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    if [ -z "$db_url" ]; then
        fail 'DATABASE_URL not found in .env'
        return
    fi

    local output
    output=$("$VENV_DIR/bin/python" -c "
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from alembic.config import Config
from alembic.script import ScriptDirectory

async def check():
    engine = create_async_engine('$db_url')
    async with engine.connect() as conn:
        res = await conn.execute(text('SELECT version_num FROM alembic_version'))
        current = res.scalar()
    await engine.dispose()
    
    cfg = Config('$PROJECT_DIR/alembic.ini')
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    head = heads[0] if heads else None
    
    if current == head:
        print(f'OK:{current}')
    else:
        print(f'FAIL:current={current},head={head}')

try:
    asyncio.run(check())
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null || echo 'ERROR:python-execution-failed')

    if [[ "$output" == OK:* ]]; then
        ok "Database schema up to date (${output#OK:})"
    elif [[ "$output" == FAIL:* ]]; then
        fail "Database schema migration mismatch: ${output#FAIL:}"
    else
        fail "Failed to verify database migration status: $output"
    fi
}

check_redis_connection() {
    if [ ! -f "$ENV_FILE" ]; then
        return
    fi
    local redis_url
    redis_url=$(grep -E '^REDIS_URL=' "$ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    if [ -z "$redis_url" ]; then
        fail 'REDIS_URL not found in .env'
        return
    fi

    local output
    output=$("$VENV_DIR/bin/python" -c "
import asyncio
from redis.asyncio import Redis

async def check():
    r = Redis.from_url('$redis_url')
    res = await r.ping()
    await r.aclose()
    if res:
        print('OK')
    else:
        print('FAIL')

try:
    asyncio.run(check())
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null || echo 'ERROR:python-execution-failed')

    if [ "$output" = "OK" ]; then
        ok 'Redis connection verified'
    else
        fail "Redis connection check failed: $output"
    fi
}

check_telegram_api() {
    if [ ! -f "$ENV_FILE" ]; then
        return
    fi
    local bot_token
    bot_token=$(grep -E '^BOT_TOKEN=' "$ENV_FILE" | head -n1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    if [ -z "$bot_token" ]; then
        fail 'BOT_TOKEN not found in .env'
        return
    fi

    local output
    output=$("$VENV_DIR/bin/python" -c "
import asyncio
from aiogram import Bot

async def check():
    bot = Bot(token='$bot_token')
    me = await bot.get_me()
    await bot.session.close()
    print(f'OK:@{me.username}')

try:
    asyncio.run(check())
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null || echo 'ERROR:python-execution-failed')

    if [[ "$output" == OK:* ]]; then
        ok "Telegram API reachability verified (${output#OK:})"
    else
        fail "Telegram API check failed: $output"
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
}

main() {
    log "Starting diagnostic checks for $SERVICE..."
    acquire_doctor_lock
    check_release_freshness
    check_python_environment
    check_env_file
    check_system_isolation
    check_service
    check_dedicated_redis
    check_timers
    check_heartbeat
    check_backups
    check_database_migrations
    check_redis_connection
    check_telegram_api
    check_runtime_dependencies

    if [ "$FAILURES" -eq 0 ]; then
        log 'All diagnostic checks PASSED successfully.'
        exit 0
    else
        log "Diagnostic checks completed with $FAILURES failure(s)."
        exit 1
    fi
}

main "$@"
