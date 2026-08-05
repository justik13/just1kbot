#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

MODE=auto
BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
PROJECT_DIR=/opt/just1kbot
STATE_ROOT=/var/lib/just1kbot
LOG_DIR=/var/log/just1kbot
BACKUP_DIR=/var/lib/just1kbot/backups
BACKUP_CONF=/etc/just1kbot-backup.conf
BACKUP_IDENTITY=/etc/just1kbot/backup.agekey
BACKUP_IDENTITY_DIR=/etc/just1kbot
SNAPSHOT_DIR=/var/lib/just1kbot/rollback-releases
RESTORE_STATE_DIR=/var/lib/just1kbot/restore-transactions
CLI_SBIN=/usr/local/bin/just1kbot
CLI_BIN=/usr/local/bin/just1kbot
PG_ROLE=just1kbot
PG_DATABASE=just1kbot_bot
VERIFY_NGINX_DOMAIN=
LEFTOVERS=()

add_leftover() {
    LEFTOVERS+=("$1")
}

path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

check_absent_path() {
    local path=$1
    path_exists "$path" && add_leftover "filesystem:$path" || true
}

verify_uninstall_prepare() {
    local env_file="$PROJECT_DIR/.env"
    VERIFY_NGINX_DOMAIN=
    [[ -f "$env_file" && ! -L "$env_file" ]] || return 0

    VERIFY_NGINX_DOMAIN=$(ENV_FILE_PATH="$env_file" python3 - <<'PY'
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

if counts.get("DOMAIN", 0) > 1:
    raise SystemExit("duplicate DOMAIN")
domain = values.get("DOMAIN", "").lower().rstrip(".")
if domain:
    label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if len(domain) > 253 or len(domain.split(".")) < 2 or any(
        not label.fullmatch(part) for part in domain.split(".")
    ):
        raise SystemExit("unsafe DOMAIN")
print(domain)
PY
) || return 1
}

check_common_filesystem() {
    local path
    for path in \
        "$PROJECT_DIR" \
        "$STATE_ROOT" \
        "$LOG_DIR" \
        "$BOT_HOME" \
        "$CLI_SBIN" \
        "$CLI_BIN" \
        /etc/logrotate.d/just1kbot \
        /var/log/just1kbot-deploy.log \
        /var/log/just1kbot-rollback.log \
        /etc/nginx/sites-available/just1kbot-webhook \
        /etc/nginx/sites-enabled/just1kbot-webhook \
        /usr/local/bin/just1kbot-backup.sh \
        /usr/local/bin/just1kbot-restore.sh \
        /usr/local/bin/just1kbot-healthcheck.sh \
        /usr/local/bin/verify_backup.sh \
        /usr/local/bin/restore_rehearsal.sh; do
        check_absent_path "$path"
    done

    if [[ -n "$VERIFY_NGINX_DOMAIN" ]]; then
        check_absent_path "/etc/nginx/sites-available/$VERIFY_NGINX_DOMAIN"
        check_absent_path "/etc/nginx/sites-enabled/$VERIFY_NGINX_DOMAIN"
    fi

    id "$BOT_USER" >/dev/null 2>&1 && add_leftover "service_user:$BOT_USER" || true

    for path in \
        /etc/systemd/system/just1kbot.service \
        /etc/systemd/system/just1kbot-backup.service \
        /etc/systemd/system/just1kbot-healthcheck.service \
        /etc/systemd/system/just1kbot-backup.timer \
        /etc/systemd/system/just1kbot-healthcheck.timer \
        /etc/systemd/system/just1kbot-traffic.service \
        /etc/systemd/system/just1kbot-notifications.service \
        /etc/systemd/system/just1kbot-cleanup.service \
        /etc/systemd/system/just1kbot-stale-payments.service \
        /etc/systemd/system/just1kbot-heartbeat.service; do
        check_absent_path "$path"
    done

    while IFS= read -r path; do
        [[ -n "$path" ]] && add_leftover "nginx:$path"
    done < <(find /etc/nginx/sites-available /etc/nginx/sites-enabled \
        -maxdepth 1 \( -type f -o -type l \) -name 'just1kbot-*' -print 2>/dev/null || true)
}

check_no_running_processes() {
    command -v ps >/dev/null 2>&1 || {
        add_leftover 'verification:ps command missing'
        return 0
    }

    local process_rows
    process_rows=$(ps -eo pid=,args= 2>/dev/null | awk '
        $0 ~ /\/opt\/just1kbot\/(venv|\.venv)\/bin\/python/ ||
        $0 ~ /python[^ ]*[[:space:]]+-m[[:space:]]+bot\.main([[:space:]]|$)/ ||
        $0 ~ /\/opt\/just1kbot\/bot\/main\.py([[:space:]]|$)/ {
            print
        }
    ' || true)
    [[ -z "$process_rows" ]] || add_leftover "processes:just1kbot $(tr '\n' ';' <<<"$process_rows" | sed 's/;$//')"
}

check_purge_filesystem() {
    check_absent_path "$BACKUP_DIR"
    check_absent_path "$BACKUP_CONF"
    check_absent_path "$BACKUP_IDENTITY"
    check_absent_path "$BACKUP_IDENTITY_DIR"
    check_absent_path "$SNAPSHOT_DIR"
    check_absent_path "$RESTORE_STATE_DIR"
}

check_postgresql_purge() {
    command -v pg_lsclusters >/dev/null 2>&1 || {
        add_leftover 'verification:pg_lsclusters command missing'
        return 0
    }
    command -v psql >/dev/null 2>&1 || {
        add_leftover 'verification:psql command missing'
        return 0
    }
    command -v runuser >/dev/null 2>&1 || {
        add_leftover 'verification:runuser command missing'
        return 0
    }

    local version cluster port status role_count database_count online_seen=0
    while IFS=' ' read -r version cluster port status _; do
        [[ "$status" == online ]] || continue
        online_seen=1
        role_count=$(runuser -u postgres -- psql -XAtq -v ON_ERROR_STOP=1 \
            -h /var/run/postgresql -p "$port" -d postgres \
            -v role_name="$PG_ROLE" <<'SQL' 2>/dev/null || printf 'check-failed'
SELECT count(*) FROM pg_roles WHERE rolname = :'role_name';
SQL
)
        database_count=$(runuser -u postgres -- psql -XAtq -v ON_ERROR_STOP=1 \
            -h /var/run/postgresql -p "$port" -d postgres \
            -v database_name="$PG_DATABASE" <<'SQL' 2>/dev/null || printf 'check-failed'
SELECT count(*)
FROM pg_database
WHERE datname = :'database_name'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$';
SQL
)
        [[ "$role_count" == 0 ]] || add_leftover "postgresql:${version}/${cluster}:role=$PG_ROLE count=$role_count"
        [[ "$database_count" == 0 ]] || add_leftover "postgresql:${version}/${cluster}:databases count=$database_count"
    done < <(pg_lsclusters --no-header 2>/dev/null || true)

    (( online_seen == 1 )) || add_leftover 'verification:no online PostgreSQL cluster available'
}

print_result() {
    if (( ${#LEFTOVERS[@]} == 0 )); then
        printf 'Проверка удаления пройдена: остаточные объекты Just1kBot не найдены.\n'
        return 0
    fi

    printf 'ОШИБКА проверки удаления: найдены остаточные объекты Just1kBot.\n' >&2
    printf 'Удаление не считается завершённым.\n' >&2
    printf 'Остатки или непроверенные области:\n' >&2
    printf '  - %s\n' "${LEFTOVERS[@]}" >&2
    printf 'Следующее действие: проверьте каждый объект; не удаляйте его вслепую, если ownership не подтверждён.\n' >&2
    return 1
}

verify_uninstall_main() {
    LEFTOVERS=()
    case ${1:-} in
        --keep-data) MODE=keep ;;
        --purge-data) MODE=purge ;;
        --auto|'') MODE=auto ;;
        -h|--help)
            printf 'Использование: verify_uninstall_state.sh [--auto|--keep-data|--purge-data]\n'
            return 0
            ;;
        *)
            printf 'Неизвестный режим проверки удаления: %s\n' "$1" >&2
            return 2
            ;;
    esac

    check_common_filesystem
    check_no_running_processes

    if [[ "$MODE" == purge ]]; then
        check_purge_filesystem
        check_postgresql_purge
    fi

    print_result
}

if [[ "${VERIFY_UNINSTALL_SOURCE_ONLY:-0}" == 1 ]]; then
    return 0 2>/dev/null || exit 0
fi

verify_uninstall_prepare || {
    printf 'Не удалось безопасно подготовить post-uninstall verification context.\n' >&2
    exit 1
}
verify_uninstall_main "$@"
