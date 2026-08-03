#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BOT_USER=just1kbot
PROJECT_DIR=/opt/just1kbot
BACKUP_DIR=/root/backups/just1kbot
BACKUP_CONF=/etc/just1kbot-backup.conf
AGE_IDENTITY_DEFAULT=/root/.config/just1kbot/backup.agekey
SNAPSHOT_DIR=/var/lib/just1kbot/rollback-releases
RESTORE_STATE_DIR=/var/lib/just1kbot/restore-transactions
RESTORE_ACTIVE_STATE=$RESTORE_STATE_DIR/active.env
RESTORE_JOURNAL_STATE=$RESTORE_STATE_DIR/cutover-journal.env
LOCK_FILE=/run/lock/just1kbot-deploy.lock
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PG_LIB=$SCRIPT_DIR/lib/postgresql.sh
PREFLIGHT_RESOURCES=$SCRIPT_DIR/preflight_uninstall_resources.sh
MODE=
WEBHOOK_DOMAIN=
WEBHOOK_PORT=8080
NGINX_STASH=
declare -a PURGE_REDIS_CONNECTION=()

fail() {
    local summary=${1:-'uninstall не выполнен'}
    local details=${2:-$summary}
    local action=${3:-'Исправьте указанную причину и повторите uninstall.'}
    printf '\nОШИБКА UNINSTALL\n' >&2
    printf 'Этап: %s\n' "${UNINSTALL_STEP:-initialization}" >&2
    printf 'Проблема: %s\n' "$summary" >&2
    printf 'Причина: %s\n' "$details" >&2
    printf 'Что сделать: %s\n' "$action" >&2
    exit 1
}

log() {
    printf '[uninstall] %s\n' "$*"
}

set_step() {
    UNINSTALL_STEP=$1
    log "$1"
}

usage() {
    cat <<'TXT'
Использование:
  sudo bash scripts/uninstall.sh
  sudo bash scripts/uninstall.sh --keep-data
  sudo bash scripts/uninstall.sh --purge-data

Без аргументов открывается безопасное интерактивное меню.

--keep-data  удаляет приложение и service account, но сохраняет PostgreSQL data,
             encrypted backups, backup config и age identity. Перед удалением
             создаётся и проверяется свежий backup.
--purge-data удаляет приложение, все Just1kBot production/restore databases,
             PostgreSQL role, Redis keys с подтверждённым prefix, backups и
             age identity. Требует точную фразу DELETE JUST1KBOT.

Удаление не изменяет firewall, системный Redis/PostgreSQL/Nginx, чужие сайты,
сертификаты, Docker, VPN-сервисы или standalone node utilities.
При незавершённой restore-транзакции uninstall запрещён: сначала выполните
restore recover, затем rollback или finalize.
TXT
}

choose_mode() {
    [[ -t 0 ]] || fail \
        'не выбран режим удаления' \
        'интерактивный терминал недоступен' \
        'Повторите команду с --keep-data или --purge-data.'

    printf '\nВыберите режим удаления:\n'
    printf '  1. Удалить приложение, сохранить PostgreSQL и backups\n'
    printf '  2. Полностью удалить данные Just1kBot\n'
    printf '  0. Отмена\n\n'
    local choice
    read -rp 'Выбор: ' choice
    case "$choice" in
        1) MODE=keep ;;
        2) MODE=purge ;;
        0) exit 0 ;;
        *) fail 'неизвестный пункт меню' "получено значение: $choice" ;;
    esac
}

parse() {
    case $# in
        0) choose_mode ;;
        1)
            case "$1" in
                --keep-data) MODE=keep ;;
                --purge-data) MODE=purge ;;
                -h|--help) usage; exit 0 ;;
                *) usage >&2; exit 2 ;;
            esac
            ;;
        *) usage >&2; exit 2 ;;
    esac
}

safe_project_path() {
    [[ "$PROJECT_DIR" == /opt/just1kbot && "$PROJECT_DIR" != *'..'* ]] ||
        fail 'небезопасный production path' "PROJECT_DIR=$PROJECT_DIR"
    [[ ! -L "$PROJECT_DIR" ]] ||
        fail 'production path является symlink' "$PROJECT_DIR" \
            'Проверьте symlink вручную. Installer не будет следовать по нему.'
    [[ "$(realpath -m -- "$PROJECT_DIR")" == "$(realpath -e -- /opt)/just1kbot" ]] ||
        fail 'canonical production path не совпал' "$PROJECT_DIR"
}

preflight_restore_state() {
    [[ ! -L "$RESTORE_STATE_DIR" ]] ||
        fail 'restore state directory является symlink' "$RESTORE_STATE_DIR"
    if [[ -e "$RESTORE_JOURNAL_STATE" || -L "$RESTORE_JOURNAL_STATE" ]]; then
        fail \
            'обнаружен прерванный production restore' \
            "$RESTORE_JOURNAL_STATE" \
            'Сначала выполните deploy.sh restore-recover.'
    fi
    if [[ -e "$RESTORE_ACTIVE_STATE" || -L "$RESTORE_ACTIVE_STATE" ]]; then
        fail \
            'обнаружен незавершённый production restore' \
            "$RESTORE_ACTIVE_STATE" \
            'Сначала выполните restore-rollback или restore-finalize.'
    fi
}

acquire_uninstall_lock() {
    install -d -o root -g root -m 0755 "$(dirname "$LOCK_FILE")"
    exec 200>"$LOCK_FILE"
    flock -n 200 || fail \
        'uninstall заблокирован' \
        'deploy, backup, restore или другой uninstall уже выполняется' \
        'Дождитесь завершения активной операции и повторите команду.'
}

run_resource_preflight() {
    [[ -f "$PREFLIGHT_RESOURCES" && ! -L "$PREFLIGHT_RESOURCES" ]] ||
        fail 'resource preflight отсутствует или небезопасен' "$PREFLIGHT_RESOURCES"
    bash "$PREFLIGHT_RESOURCES" || fail \
        'ownership удаляемых ресурсов не подтверждён' \
        'read-only resource preflight завершился ошибкой' \
        'Прочитайте точный конфликт выше. Чужой ресурс автоматически изменён не будет.'
}

pause_operational_work() {
    local timer
    for timer in just1kbot-backup.timer just1kbot-healthcheck.timer; do
        systemctl stop "$timer" 2>/dev/null || true
        if systemctl is-active --quiet "$timer" 2>/dev/null; then
            fail 'не удалось остановить timer' "$timer остаётся active"
        fi
    done

    local end=$(( $(date +%s) + 180 ))
    while systemctl is-active --quiet just1kbot-backup.service 2>/dev/null; do
        (( $(date +%s) <= end )) || fail \
            'активный backup не завершился' \
            'ожидание превысило 180 секунд' \
            'Проверьте systemctl status just1kbot-backup.service и journalctl.'
        sleep 2
    done
}

latest_backup() {
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' \
        -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-
}

backup_before_keep() {
    local identity=${AGE_IDENTITY_FILE:-$AGE_IDENTITY_DEFAULT}
    local start artifact

    [[ -x /usr/local/bin/just1kbot-backup.sh ]] ||
        fail 'backup tool отсутствует' /usr/local/bin/just1kbot-backup.sh
    [[ -x /usr/local/bin/verify_backup.sh ]] ||
        fail 'backup verifier отсутствует' /usr/local/bin/verify_backup.sh
    [[ -f "$BACKUP_CONF" && ! -L "$BACKUP_CONF" ]] ||
        fail 'backup config отсутствует или небезопасен' "$BACKUP_CONF"
    [[ -f "$identity" && ! -L "$identity" ]] ||
        fail \
            'age identity отсутствует или небезопасен' \
            "$identity" \
            'Передайте AGE_IDENTITY_FILE с ключом, соответствующим backup recipient.'

    start=$(date +%s)
    systemctl --wait start just1kbot-backup.service ||
        fail 'backup service завершился ошибкой' 'systemctl start just1kbot-backup.service'
    artifact=$(latest_backup)
    [[ -n "$artifact" && -s "$artifact" && -s "$artifact.sha256" ]] ||
        fail 'новый backup не опубликован' "$BACKUP_DIR"
    (( $(stat -c %Y "$artifact") >= start )) ||
        fail 'найденный backup создан не текущей операцией' "$artifact"

    AGE_IDENTITY_FILE="$identity" /usr/local/bin/verify_backup.sh "$artifact" ||
        fail 'новый backup не прошёл verification' "$artifact"
    log "verified backup сохранён: $artifact"
}

confirm_purge() {
    [[ -t 0 ]] || fail \
        'полное удаление требует интерактивного подтверждения' \
        'stdin не является TTY'
    local answer
    read -rp 'Введите точную фразу DELETE JUST1KBOT: ' answer
    [[ "$answer" == 'DELETE JUST1KBOT' ]] ||
        fail 'подтверждение полного удаления не совпало' 'ожидалась точная фраза DELETE JUST1KBOT'
}

stop_units() {
    local unit
    for unit in \
        just1kbot-healthcheck.timer \
        just1kbot-backup.timer \
        just1kbot-healthcheck.service \
        just1kbot-backup.service \
        just1kbot.service \
        just1kbot-traffic.service \
        just1kbot-notifications.service \
        just1kbot-cleanup.service \
        just1kbot-stale-payments.service \
        just1kbot-heartbeat.service; do
        systemctl stop "$unit" 2>/dev/null || true
        systemctl disable "$unit" 2>/dev/null || true
    done

    if id "$BOT_USER" >/dev/null 2>&1; then
        pkill -TERM -u "$BOT_USER" 2>/dev/null || true
        local end=$(( $(date +%s) + 30 ))
        while pgrep -u "$BOT_USER" >/dev/null 2>&1; do
            (( $(date +%s) <= end )) || {
                pkill -KILL -u "$BOT_USER" 2>/dev/null || true
                sleep 1
                break
            }
            sleep 1
        done
        pgrep -u "$BOT_USER" >/dev/null 2>&1 &&
            fail 'процессы service user не остановлены' "user=$BOT_USER"
    fi
}

read_webhook_config() {
    [[ -f "$PROJECT_DIR/.env" && ! -L "$PROJECT_DIR/.env" ]] || return 0
    local output
    output=$(ENV_FILE_PATH="$PROJECT_DIR/.env" python3 - <<'PY'
import os
import re
from pathlib import Path

values = {}
counts = {}
for raw in Path(os.environ["ENV_FILE_PATH"]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    counts[key] = counts.get(key, 0) + 1
    values[key] = value.strip()

if counts.get("DOMAIN", 0) > 1 or counts.get("YOOKASSA_WEBHOOK_PORT", 0) > 1:
    raise SystemExit("duplicate DOMAIN or YOOKASSA_WEBHOOK_PORT")

domain = values.get("DOMAIN", "").lower().rstrip(".")
port_text = values.get("YOOKASSA_WEBHOOK_PORT", "8080")
if domain:
    label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if len(domain) > 253 or len(domain.split(".")) < 2 or any(
        not label.fullmatch(part) for part in domain.split(".")
    ):
        raise SystemExit("unsafe DOMAIN")
if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
    raise SystemExit("unsafe YOOKASSA_WEBHOOK_PORT")
print(domain)
print(int(port_text))
PY
) || fail \
        'production webhook configuration не прошла безопасную проверку' \
        "$PROJECT_DIR/.env"

    local -a values=()
    mapfile -t values <<<"$output"
    WEBHOOK_DOMAIN=${values[0]:-}
    WEBHOOK_PORT=${values[1]:-8080}
}

redis_connection() {
    [[ -f "$PROJECT_DIR/.env" && ! -L "$PROJECT_DIR/.env" ]] ||
        fail 'невозможно очистить Redis без безопасного production .env' "$PROJECT_DIR/.env"

    ENV_FILE_PATH="$PROJECT_DIR/.env" python3 - <<'PY'
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

values = {}
counts = {}
for raw in Path(os.environ["ENV_FILE_PATH"]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    counts[key] = counts.get(key, 0) + 1
    values[key] = value

if counts.get("REDIS_URL") != 1:
    raise SystemExit("expected exactly one REDIS_URL")
parsed = urlsplit(values["REDIS_URL"])
if parsed.scheme != "redis" or parsed.hostname not in {"127.0.0.1", "localhost"}:
    raise SystemExit("REDIS_URL must target local redis://")
if parsed.username not in {None, ""}:
    raise SystemExit("unexpected Redis username")
try:
    port = parsed.port or 6379
except ValueError as exc:
    raise SystemExit("invalid Redis port") from exc
database_text = parsed.path.lstrip("/") or "0"
if not database_text.isdigit():
    raise SystemExit("invalid Redis database")
database = int(database_text)
if not (1 <= port <= 65535 and 0 <= database <= 15):
    raise SystemExit("Redis endpoint out of range")
prefix = values.get("REDIS_KEY_PREFIX", "just1kbot_bot:")
if not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", prefix):
    raise SystemExit("invalid REDIS_KEY_PREFIX")

print(parsed.hostname)
print(port)
print(database)
print(unquote(parsed.password or ""))
print(prefix)
PY
}

load_redis_connection() {
    local output
    output=$(redis_connection) || fail 'REDIS_URL не прошёл безопасную проверку' "$PROJECT_DIR/.env"
    mapfile -t PURGE_REDIS_CONNECTION <<<"$output"
    (( ${#PURGE_REDIS_CONNECTION[@]} == 5 )) ||
        fail 'не удалось разобрать Redis connection' 'ожидалось пять проверенных полей'
}

ping_redis_connection() {
    (( ${#PURGE_REDIS_CONNECTION[@]} == 5 )) || load_redis_connection
    local host=${PURGE_REDIS_CONNECTION[0]}
    local port=${PURGE_REDIS_CONNECTION[1]}
    local database=${PURGE_REDIS_CONNECTION[2]}
    local password=${PURGE_REDIS_CONNECTION[3]}

    if [[ -n "$password" ]]; then
        REDISCLI_AUTH="$password" redis-cli -h "$host" -p "$port" -n "$database" PING |
            grep -qx PONG || fail 'Redis PING завершился ошибкой' "$host:$port/$database"
    else
        redis-cli -h "$host" -p "$port" -n "$database" PING |
            grep -qx PONG || fail 'Redis PING завершился ошибкой' "$host:$port/$database"
    fi
}

preflight_purge() {
    command -v redis-cli >/dev/null 2>&1 || fail 'redis-cli не найден' 'он требуется для purge Redis keys'
    load_redis_connection
    ping_redis_connection

    [[ -f "$PG_LIB" && ! -L "$PG_LIB" ]] ||
        fail 'PostgreSQL library отсутствует или небезопасна' "$PG_LIB"
    # shellcheck source=lib/postgresql.sh
    source "$PG_LIB"
    pg_select_cluster || fail 'не удалось выбрать PostgreSQL cluster'
    pg_start_cluster || fail 'не удалось запустить выбранный PostgreSQL cluster'

    local database_output
    local -a databases=()
    local database
    database_output=$(pg_admin_psql_on_port "$PG_PORT" -v main_database="$PG_DATABASE" <<'SQL'
SELECT datname
FROM pg_database
WHERE datname = :'main_database'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$'
ORDER BY datname;
SQL
) || fail 'не удалось перечислить Just1kBot databases'
    [[ -z "$database_output" ]] || mapfile -t databases <<<"$database_output"

    for database in "${databases[@]}"; do
        [[ "$database" == "$PG_DATABASE" || "$database" =~ ^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$ ]] ||
            fail 'обнаружена database с неожиданным именем' "$database"
    done
}

purge_redis() {
    (( ${#PURGE_REDIS_CONNECTION[@]} == 5 )) || load_redis_connection
    local host=${PURGE_REDIS_CONNECTION[0]}
    local port=${PURGE_REDIS_CONNECTION[1]}
    local database=${PURGE_REDIS_CONNECTION[2]}
    local password=${PURGE_REDIS_CONNECTION[3]}
    local prefix=${PURGE_REDIS_CONNECTION[4]}
    local output key deleted=0

    if [[ -n "$password" ]]; then
        output=$(REDISCLI_AUTH="$password" redis-cli -h "$host" -p "$port" -n "$database" \
            --scan --pattern "${prefix}*") || fail 'Redis SCAN завершился ошибкой'
    else
        output=$(redis-cli -h "$host" -p "$port" -n "$database" \
            --scan --pattern "${prefix}*") || fail 'Redis SCAN завершился ошибкой'
    fi

    while IFS= read -r key; do
        [[ -n "$key" ]] || continue
        [[ "$key" == "$prefix"* ]] || fail 'Redis SCAN вернул key вне разрешённого prefix' "$key"
        if [[ -n "$password" ]]; then
            REDISCLI_AUTH="$password" redis-cli -h "$host" -p "$port" -n "$database" \
                DEL "$key" >/dev/null || fail 'не удалось удалить Redis key' "$key"
        else
            redis-cli -h "$host" -p "$port" -n "$database" \
                DEL "$key" >/dev/null || fail 'не удалось удалить Redis key' "$key"
        fi
        deleted=$((deleted + 1))
    done <<<"$output"

    log "удалены Redis keys с prefix ${prefix}: $deleted"
}

purge_db() {
    local database_output
    local -a databases=()
    local database
    database_output=$(pg_admin_psql_on_port "$PG_PORT" -v main_database="$PG_DATABASE" <<'SQL'
SELECT datname
FROM pg_database
WHERE datname = :'main_database'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$'
ORDER BY datname;
SQL
) || fail 'не удалось повторно перечислить Just1kBot databases'
    [[ -z "$database_output" ]] || mapfile -t databases <<<"$database_output"

    for database in "${databases[@]}"; do
        [[ "$database" == "$PG_DATABASE" || "$database" =~ ^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$ ]] ||
            fail 'отказ удалять database с неожиданным именем' "$database"
        pg_admin_psql_on_port "$PG_PORT" -v database_name="$database" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'database_name'
  AND pid <> pg_backend_pid();
SQL
        runuser -u postgres -- dropdb \
            -h "$PG_SOCKET_DIR" -p "$PG_PORT" \
            --if-exists --maintenance-db=postgres "$database" ||
            fail 'не удалось удалить PostgreSQL database' "$database"
    done

    local remaining
    remaining=$(pg_admin_psql_on_port "$PG_PORT" -v main_database="$PG_DATABASE" <<'SQL'
SELECT count(*)
FROM pg_database
WHERE datname = :'main_database'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$';
SQL
) || fail 'не удалось проверить удаление databases'
    [[ "$remaining" == 0 ]] || fail 'часть Just1kBot databases осталась' "count=$remaining"

    pg_admin_psql_on_port "$PG_PORT" -v role_name="$PG_ROLE" >/dev/null <<'SQL'
DROP ROLE IF EXISTS :"role_name";
SQL
    local role_remaining
    role_remaining=$(pg_admin_psql_on_port "$PG_PORT" -v role_name="$PG_ROLE" <<'SQL'
SELECT count(*) FROM pg_roles WHERE rolname = :'role_name';
SQL
) || fail 'не удалось проверить удаление PostgreSQL role'
    [[ "$role_remaining" == 0 ]] || fail 'PostgreSQL role осталась' "$PG_ROLE"
}

remove_systemd_resources() {
    rm -f -- \
        /etc/systemd/system/just1kbot.service \
        /etc/systemd/system/just1kbot-backup.service \
        /etc/systemd/system/just1kbot-healthcheck.service \
        /etc/systemd/system/just1kbot-backup.timer \
        /etc/systemd/system/just1kbot-healthcheck.timer \
        /etc/systemd/system/just1kbot-traffic.service \
        /etc/systemd/system/just1kbot-notifications.service \
        /etc/systemd/system/just1kbot-cleanup.service \
        /etc/systemd/system/just1kbot-stale-payments.service \
        /etc/systemd/system/just1kbot-heartbeat.service
    systemctl daemon-reload
    systemctl reset-failed >/dev/null 2>&1 || true
}

remove_installed_tools() {
    rm -f -- \
        /usr/local/bin/just1kbot-backup.sh \
        /usr/local/bin/just1kbot-restore.sh \
        /usr/local/bin/just1kbot-healthcheck.sh \
        /usr/local/bin/verify_backup.sh \
        /usr/local/bin/restore_rehearsal.sh \
        /usr/local/sbin/just1kbot \
        /usr/local/bin/just1kbot
}

nginx_site_has_expected_markers() {
    local path=$1 domain=$2 port=$3
    [[ -f "$path" && ! -L "$path" ]] || return 1
    grep -Fq "server_name ${domain};" "$path" || return 1
    grep -Fq 'location = /webhook/yookassa' "$path" || return 1
    grep -Fq "proxy_pass http://127.0.0.1:${port}/webhook/yookassa;" "$path" || return 1
}

remove_nginx_site() {
    [[ -n "$WEBHOOK_DOMAIN" ]] || return 0
    local available="/etc/nginx/sites-available/$WEBHOOK_DOMAIN"
    local enabled="/etc/nginx/sites-enabled/$WEBHOOK_DOMAIN"

    if [[ -e "$available" || -L "$available" ]]; then
        nginx_site_has_expected_markers "$available" "$WEBHOOK_DOMAIN" "$WEBHOOK_PORT" ||
            fail \
                'Nginx site не содержит ожидаемых Just1kBot markers' \
                "$available" \
                'Проверьте конфигурацию вручную; uninstall не будет удалять возможный чужой site.'
    fi

    if [[ -e "$enabled" || -L "$enabled" ]]; then
        [[ -L "$enabled" ]] ||
            fail 'enabled Nginx site не является symlink' "$enabled"
        [[ "$(readlink -f -- "$enabled")" == "$(realpath -m -- "$available")" ]] ||
            fail 'enabled Nginx symlink ведёт не на ожидаемый site' "$enabled"
    fi

    [[ -e "$available" || -L "$available" || -e "$enabled" || -L "$enabled" ]] || return 0

    NGINX_STASH=$(mktemp -d /run/just1kbot-uninstall-nginx.XXXXXX)
    if [[ -L "$enabled" ]]; then
        printf '%s\n' "$(readlink -- "$enabled")" > "$NGINX_STASH/enabled-target"
        rm -f -- "$enabled"
    fi
    if [[ -f "$available" && ! -L "$available" ]]; then
        mv -- "$available" "$NGINX_STASH/site"
    fi

    if command -v nginx >/dev/null 2>&1; then
        if ! nginx -t; then
            [[ -f "$NGINX_STASH/site" ]] && mv -- "$NGINX_STASH/site" "$available"
            if [[ -f "$NGINX_STASH/enabled-target" ]]; then
                ln -s -- "$(cat "$NGINX_STASH/enabled-target")" "$enabled"
            fi
            rm -rf -- "$NGINX_STASH"
            NGINX_STASH=
            fail \
                'Nginx configuration стала невалидной после удаления site' \
                'nginx -t завершился ошибкой; site автоматически восстановлен'
        fi
        systemctl reload nginx || fail 'не удалось reload Nginx после безопасного удаления site'
    fi

    rm -rf -- "$NGINX_STASH"
    NGINX_STASH=
}

remove_application_files() {
    safe_project_path
    rm -rf -- "$PROJECT_DIR" /var/lib/just1kbot /var/log/just1kbot
    rm -f -- \
        /etc/logrotate.d/just1kbot \
        /var/log/just1kbot-deploy.log \
        /var/log/just1kbot-rollback.log
}

remove_service_user() {
    id "$BOT_USER" >/dev/null 2>&1 || return 0
    pgrep -u "$BOT_USER" >/dev/null 2>&1 &&
        fail 'service user всё ещё имеет процессы' "$BOT_USER"
    userdel "$BOT_USER" || fail 'не удалось удалить service user' "$BOT_USER"
}

purge_saved_data() {
    rm -rf -- "$BACKUP_DIR" "$SNAPSHOT_DIR" "$RESTORE_STATE_DIR"
    rm -f -- "$BACKUP_CONF" "$AGE_IDENTITY_DEFAULT"
    rmdir /root/.config/just1kbot 2>/dev/null || true
}

main() {
    parse "$@"
    [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'uninstall требует root' "uid=$(id -u)"

    set_step 'Проверка production paths и ownership ресурсов'
    safe_project_path
    acquire_uninstall_lock
    preflight_restore_state
    read_webhook_config
    run_resource_preflight

    if [[ "$MODE" == purge ]]; then
        confirm_purge
        set_step 'Проверка purge targets'
        preflight_purge
    fi

    set_step 'Остановка timers и активного backup'
    pause_operational_work

    if [[ "$MODE" == keep ]]; then
        set_step 'Создание обязательного backup перед удалением приложения'
        backup_before_keep
    fi

    set_step 'Остановка Just1kBot services'
    stop_units

    if [[ "$MODE" == purge ]]; then
        set_step 'Удаление только Redis keys с подтверждённым prefix'
        purge_redis
        set_step 'Удаление подтверждённых PostgreSQL databases и role'
        purge_db
    fi

    set_step 'Удаление подтверждённого Nginx site'
    remove_nginx_site
    set_step 'Удаление Just1kBot systemd resources'
    remove_systemd_resources
    set_step 'Удаление root-owned operational tools'
    remove_installed_tools
    set_step 'Удаление application files и runtime state'
    remove_application_files

    if [[ "$MODE" == purge ]]; then
        set_step 'Удаление encrypted backups и identity'
        purge_saved_data
    fi

    set_step 'Удаление service account'
    remove_service_user

    printf 'Основной этап uninstall завершён. Официальная точка входа должна выполнить post-uninstall verification.\n'
}

main "$@"
