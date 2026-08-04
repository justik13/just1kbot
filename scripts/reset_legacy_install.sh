#!/bin/bash
# Safe reset for legacy/incomplete Just1kBot installations without a valid manifest.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}
STATE_ROOT=${STATE_ROOT:-/var/lib/just1kbot}
STATE_DIR=${STATE_DIR:-$STATE_ROOT/install-state}
MANIFEST=${MANIFEST:-$STATE_DIR/manifest.json}
BOT_USER=${BOT_USER:-just1kbot}
BOT_HOME=${BOT_HOME:-/home/just1kbot}
CLI_PATH=${CLI_PATH:-/usr/local/sbin/just1kbot}
REDIS_CONFIG=${REDIS_CONFIG:-/etc/just1kbot/redis.conf}
REDIS_DATA_DIR=${REDIS_DATA_DIR:-$STATE_ROOT/redis}

RESET_PHRASE='RESET JUST1KBOT'

log() { printf '[legacy-reset] %s\n' "$*"; }
warn() { printf '[legacy-reset] WARNING: %s\n' "$*" >&2; }
die() { printf '[legacy-reset] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
    (( EUID == 0 )) || die 'запустите скрипт от root.'
}

require_no_manifest() {
    [[ ! -e "$MANIFEST" && ! -L "$MANIFEST" ]] ||
        die "ownership manifest существует: $MANIFEST. Для управляемой установки используйте обычный uninstall."
}

confirm_reset() {
    [[ -t 0 ]] || die 'reset требует интерактивный TTY.'
    local answer
    read -rp "Это удалит legacy/incomplete Just1kBot installation. Введите '$RESET_PHRASE': " answer
    [[ "$answer" == "$RESET_PHRASE" ]] || die 'подтверждение не совпало.'
}

root_owned_regular_file() {
    local path=$1
    [[ -f "$path" && ! -L "$path" ]] || return 1
    [[ "$(stat -c '%U:%G' "$path" 2>/dev/null || true)" == 'root:root' ]] || return 1
    local mode
    mode=$(stat -c '%a' "$path" 2>/dev/null || true)
    [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
    (( (8#$mode & 8#022) == 0 ))
}

legacy_cli_looks_managed() {
    root_owned_regular_file "$CLI_PATH" || return 1
    grep -Fq 'Just1kBot' "$CLI_PATH" 2>/dev/null || return 1
    grep -Fq '/opt/just1kbot' "$CLI_PATH" 2>/dev/null || return 1
}

managed_unit_file() {
    local path=$1
    root_owned_regular_file "$path" || return 1
    grep -Fq 'Just1kBot' "$path" 2>/dev/null
}

stop_and_remove_unit() {
    local unit=$1
    local path="/etc/systemd/system/$unit"
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
    if [[ -e "$path" || -L "$path" ]]; then
        managed_unit_file "$path" || {
            warn "Чужой или неподтверждённый unit оставлен: $path"
            return 0
        }
        rm -f -- "$path"
        log "Удалён unit: $path"
    fi
}

remove_legacy_cli() {
    if [[ -e "$CLI_PATH" || -L "$CLI_PATH" ]]; then
        legacy_cli_looks_managed || die "CLI path существует, но ownership не доказан: $CLI_PATH"
        rm -f -- "$CLI_PATH"
        log "Удалён legacy global CLI: $CLI_PATH"
    fi
}

remove_known_helper() {
    local path=$1
    [[ -e "$path" || -L "$path" ]] || return 0
    [[ -L "$path" ]] && { warn "Symlink оставлен: $path"; return 0; }
    root_owned_regular_file "$path" || { warn "Небезопасный/чужой файл оставлен: $path"; return 0; }
    grep -Fq 'Just1kBot' "$path" 2>/dev/null || { warn "Не подтверждённый helper оставлен: $path"; return 0; }
    rm -f -- "$path"
    log "Удалён helper: $path"
}

remove_legacy_project() {
    [[ ! -e "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] && return 0
    [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || die "PROJECT_DIR имеет небезопасный тип: $PROJECT_DIR"
    [[ -f "$PROJECT_DIR/deploy.sh" ]] || die "PROJECT_DIR существует без deploy.sh; автоматическое удаление заблокировано: $PROJECT_DIR"
    grep -Fq 'Just1kBot' "$PROJECT_DIR/deploy.sh" 2>/dev/null ||
        die "PROJECT_DIR не подтверждён как Just1kBot: $PROJECT_DIR"
    rm -rf --one-file-system -- "$PROJECT_DIR"
    log "Удалён legacy project tree: $PROJECT_DIR"
}

remove_legacy_runtime() {
    if [[ -e "$REDIS_DATA_DIR" || -L "$REDIS_DATA_DIR" ]]; then
        [[ -d "$REDIS_DATA_DIR" && ! -L "$REDIS_DATA_DIR" ]] || die "Dedicated Redis data path имеет небезопасный тип: $REDIS_DATA_DIR"
        rm -rf --one-file-system -- "$REDIS_DATA_DIR"
    fi
    if [[ -e "$REDIS_CONFIG" || -L "$REDIS_CONFIG" ]]; then
        [[ -f "$REDIS_CONFIG" && ! -L "$REDIS_CONFIG" ]] || die "Dedicated Redis config имеет небезопасный тип: $REDIS_CONFIG"
        if grep -Fq 'Just1kBot' "$REDIS_CONFIG" 2>/dev/null; then
            rm -f -- "$REDIS_CONFIG"
            log "Удалён dedicated Redis config: $REDIS_CONFIG"
        else
            warn "Redis config не содержит marker; оставлен: $REDIS_CONFIG"
        fi
    fi
    rmdir /etc/just1kbot 2>/dev/null || true
}

postgres_marker_pair() {
    local port=$1
    local database=just1kbot_bot
    local role=just1kbot
    local db_comment role_comment

    db_comment=$(runuser -u postgres -- psql -p "$port" -At -v db="$database" <<'SQL' 2>/dev/null || true
SELECT COALESCE(shobj_description(oid, 'pg_database'), '')
FROM pg_database
WHERE datname = :'db';
SQL
)
    role_comment=$(runuser -u postgres -- psql -p "$port" -At -v role="$role" <<'SQL' 2>/dev/null || true
SELECT COALESCE(shobj_description(oid, 'pg_authid'), '')
FROM pg_authid
WHERE rolname = :'role';
SQL
)

    [[ "$db_comment" == managed-by=just1kbot\;installation-id=* ]] || return 1
    [[ "$role_comment" == managed-by=just1kbot\;installation-id=* ]] || return 1
    [[ "$db_comment" == "$role_comment" ]] || return 1

    printf '%s\n' "$db_comment"
}

reset_postgres_if_owned() {
    command -v pg_lsclusters >/dev/null 2>&1 || return 0
    command -v runuser >/dev/null 2>&1 || return 0
    command -v dropdb >/dev/null 2>&1 || return 0

    local found=false version cluster port marker
    while read -r version cluster port _; do
        [[ -n "$version" && -n "$cluster" && -n "$port" ]] || continue
        found=true
        marker=$(postgres_marker_pair "$port") || {
            if runuser -u postgres -- psql -p "$port" -At -v db=just1kbot_bot \
                -c "SELECT 1 FROM pg_database WHERE datname='just1kbot_bot';" 2>/dev/null | grep -q 1; then
                warn "PostgreSQL just1kbot_bot найден на $version/$cluster:$port, но ownership marker не подтверждён; БД/роль сохранены."
            fi
            continue
        }

        log "Подтверждён PostgreSQL ownership marker: $marker"
        runuser -u postgres -- psql -p "$port" -At \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='just1kbot_bot' AND pid <> pg_backend_pid();" \
            >/dev/null 2>&1 || true
        runuser -u postgres -- dropdb -p "$port" --if-exists just1kbot_bot || die "не удалось удалить database just1kbot_bot на $version/$cluster:$port"
        runuser -u postgres -- psql -p "$port" -v role=just1kbot \
            -c 'DROP ROLE IF EXISTS :"role";' >/dev/null || die "не удалось удалить role just1kbot на $version/$cluster:$port"
        log "Удалены PostgreSQL database/role на $version/$cluster:$port"
    done < <(pg_lsclusters --no-header 2>/dev/null)

    [[ "$found" == true ]] || true
}

remove_service_user() {
    id "$BOT_USER" >/dev/null 2>&1 || return 0
    pgrep -u "$BOT_USER" >/dev/null 2>&1 && die "процессы пользователя $BOT_USER ещё работают"
    userdel "$BOT_USER" 2>/dev/null || die "не удалось удалить service user $BOT_USER"
    [[ ! -e "$BOT_HOME" && ! -L "$BOT_HOME" ]] || rm -rf --one-file-system -- "$BOT_HOME"
    log "Удалён service user: $BOT_USER"
}

remove_state_and_logs() {
    rm -rf --one-file-system -- "$STATE_ROOT"
    rm -rf --one-file-system -- /var/log/just1kbot
    rm -f -- /var/log/just1kbot-deploy.log /var/log/just1kbot-rollback.log
}

main() {
    require_root
    require_no_manifest
    confirm_reset

    log 'Остановка и удаление только подтверждённых Just1kBot units.'
    for unit in \
        just1kbot.service \
        just1kbot-redis.service \
        just1kbot-healthcheck.service \
        just1kbot-healthcheck.timer \
        just1kbot-backup.service \
        just1kbot-backup.timer; do
        stop_and_remove_unit "$unit"
    done
    systemctl daemon-reload

    remove_legacy_cli
    for helper in \
        /usr/local/bin/just1kbot-backup.sh \
        /usr/local/bin/just1kbot-restore.sh \
        /usr/local/bin/just1kbot-healthcheck.sh \
        /usr/local/bin/verify_backup.sh \
        /usr/local/bin/restore_rehearsal.sh; do
        remove_known_helper "$helper"
    done

    remove_legacy_runtime
    reset_postgres_if_owned
    remove_legacy_project
    remove_service_user
    remove_state_and_logs

    log 'Legacy reset завершён.'
    log 'Global Redis (/etc/redis/redis.conf) и firewall намеренно не изменялись.'
    log 'Если PostgreSQL database/role были сохранены из-за отсутствия ownership marker, удалите их вручную только после проверки, что они принадлежат неиспользуемой старой установке.'
    log 'Теперь повторите: sudo bash deploy.sh state && sudo bash deploy.sh deploy --dry-run && sudo bash deploy.sh deploy'
}

main "$@"
