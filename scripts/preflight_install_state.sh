#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
PROJECT_DIR=/opt/just1kbot
ENV_FILE=$PROJECT_DIR/.env
UNIT_FILE=/etc/systemd/system/just1kbot.service
BACKUP_SCRIPT=/usr/local/bin/just1kbot-backup.sh
BACKUP_SERVICE=/etc/systemd/system/just1kbot-backup.service
BACKUP_CONF=/etc/just1kbot-backup.conf
BACKUP_IDENTITY=/root/.config/just1kbot/backup.agekey
BACKUP_DIR=/root/backups/just1kbot
RUNTIME_DIR=/run/just1kbot
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SOURCE_BACKUP=$SCRIPT_DIR/ops/backup_postgres.sh

fail() {
    printf 'ОШИБКА preflight: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '[preflight] %s\n' "$*"
}

require_regular_file() {
    local path=$1
    [[ -f "$path" && ! -L "$path" ]] || fail "небезопасный или отсутствующий файл: $path"
}

read_env_value() {
    local key=$1
    ENV_FILE_PATH=$ENV_FILE ENV_KEY=$key python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE_PATH"])
key = os.environ["ENV_KEY"]
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    current, value = line.split("=", 1)
    if current.strip() != key:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    print(value)
    break
PY
}

parse_database_url() {
    local database_url=$1
    DATABASE_URL_VALUE=$database_url python3 - <<'PY'
import os
from urllib.parse import unquote, urlsplit

raw = os.environ["DATABASE_URL_VALUE"]
raw = raw.replace("postgresql+asyncpg://", "postgresql://", 1)
parsed = urlsplit(raw)
if parsed.scheme not in {"postgresql", "postgres"}:
    raise SystemExit("unsupported database scheme")
if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("database host must be local")
if unquote(parsed.username or "") != "just1kbot":
    raise SystemExit("unexpected database user")
if parsed.path.lstrip("/") != "just1kbot_bot":
    raise SystemExit("unexpected database name")
print(parsed.port or 5432)
PY
}

select_postgresql_cluster() {
    local port=$1 version cluster cluster_port status
    PG_VERSION=
    PG_CLUSTER=

    while IFS=' ' read -r version cluster cluster_port status _; do
        if [[ "$cluster_port" == "$port" && "$status" == online ]]; then
            [[ -z "$PG_VERSION" ]] || fail "несколько online PostgreSQL-кластеров используют port=$port"
            PG_VERSION=$version
            PG_CLUSTER=$cluster
        fi
    done < <(pg_lsclusters --no-header)

    [[ -n "$PG_VERSION" && -n "$PG_CLUSTER" ]] || fail "не найден online PostgreSQL-кластер на port=$port"
    PG_UNIT="postgresql@${PG_VERSION}-${PG_CLUSTER}.service"
}

validate_database_revision() {
    local port=$1 exists revision count

    exists=$(runuser -u postgres -- psql -XAtq -v ON_ERROR_STOP=1 \
        -h /var/run/postgresql -p "$port" -d postgres \
        -c "SELECT count(*) FROM pg_database WHERE datname='just1kbot_bot'") ||
        fail 'не удалось проверить production database'
    [[ "$exists" == 1 ]] || fail 'production .env существует, но database just1kbot_bot отсутствует'

    revision=$(runuser -u postgres -- psql -XAtq -v ON_ERROR_STOP=1 \
        -h /var/run/postgresql -p "$port" -d just1kbot_bot \
        -c 'SELECT version_num FROM alembic_version' 2>/dev/null) ||
        fail 'database не содержит корректную alembic_version; автоматическое продолжение запрещено'
    count=$(printf '%s\n' "$revision" | awk 'NF {n++} END {print n+0}')
    [[ "$count" == 1 ]] || fail 'alembic_version должна содержать ровно одну revision'
    [[ "$revision" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]] || fail 'некорректная Alembic revision'
    log "database revision=$revision"
}

ensure_service_account() {
    local configured_home

    [[ "$BOT_HOME" == /home/just1kbot && "$BOT_HOME" != *'..'* ]] || fail 'unsafe BOT_HOME'
    [[ ! -L "$BOT_HOME" ]] || fail 'BOT_HOME является symlink'

    if ! id "$BOT_USER" >/dev/null 2>&1; then
        if [[ -e "$BOT_HOME" ]]; then
            [[ -d "$BOT_HOME" ]] || fail 'BOT_HOME существует и не является directory'
            useradd -r -M -d "$BOT_HOME" -s /bin/bash "$BOT_USER"
        else
            useradd -r -m -d "$BOT_HOME" -s /bin/bash "$BOT_USER"
        fi
    fi

    configured_home=$(getent passwd "$BOT_USER" | cut -d: -f6) || fail 'не удалось прочитать service account'
    [[ "$configured_home" == "$BOT_HOME" ]] || fail "неожиданный service-account home: $configured_home"
    [[ -d "$BOT_HOME" && ! -L "$BOT_HOME" ]] || fail 'service-account home небезопасен'

    chown -R "$BOT_USER:$BOT_USER" "$BOT_HOME"
    chmod 0750 "$BOT_HOME"
    if find "$BOT_HOME" -xdev \( -type f -o -type d \) -perm /022 -print -quit | grep -q .; then
        fail 'service-account home содержит group/other-writable пути'
    fi
}

repair_incomplete_permissions() {
    [[ "$PROJECT_DIR" == /opt/just1kbot && ! -L "$PROJECT_DIR" && -d "$PROJECT_DIR" ]] ||
        fail 'live project path отсутствует или небезопасен'
    require_regular_file "$ENV_FILE"

    chown root:"$BOT_USER" "$PROJECT_DIR" "$ENV_FILE"
    chmod 0750 "$PROJECT_DIR"
    chmod 0640 "$ENV_FILE"

    install -d -o "$BOT_USER" -g "$BOT_USER" -m 0750 "$RUNTIME_DIR"
    install -d -o "$BOT_USER" -g "$BOT_USER" -m 0700 "$RUNTIME_DIR/.postgresql"

    runuser -u "$BOT_USER" -- test -x "$PROJECT_DIR" || fail 'service user не может пройти в live project'
    runuser -u "$BOT_USER" -- test -r "$ENV_FILE" || fail 'service user не может прочитать production .env'
}

validate_protected_home_runtime() {
    command -v systemd-run >/dev/null 2>&1 || fail 'systemd-run не найден'

    systemd-run --quiet --wait --pipe --collect \
        --uid="$BOT_USER" \
        --gid="$BOT_USER" \
        --property=ProtectHome=true \
        /usr/bin/env HOME="$RUNTIME_DIR" \
        /usr/bin/python3 -c '
from pathlib import Path

expected = Path("/run/just1kbot")
if Path.home() != expected:
    raise SystemExit(f"unexpected HOME: {Path.home()}")
(expected / ".postgresql" / "postgresql.key").exists()
' >/dev/null || fail 'runtime HOME не работает внутри ProtectHome=true sandbox'
}

prepare_backup_config() {
    local recipient temp

    install -d -o root -g root -m 0700 "$(dirname "$BACKUP_IDENTITY")" "$BACKUP_DIR"

    if [[ -e "$BACKUP_CONF" || -L "$BACKUP_CONF" ]]; then
        require_regular_file "$BACKUP_CONF"
        [[ "$(stat -c '%U:%G %a' "$BACKUP_CONF")" == 'root:root 600' ]] ||
            fail 'существующий backup config должен быть root:root 0600'
        recipient=$(awk -F= '/^BACKUP_AGE_RECIPIENT=age1/ {value=$2} END {print value}' "$BACKUP_CONF")
        [[ "$recipient" == age1* ]] || fail 'существующий backup config не содержит валидный age recipient'
        return 0
    fi

    if [[ -e "$BACKUP_IDENTITY" || -L "$BACKUP_IDENTITY" ]]; then
        require_regular_file "$BACKUP_IDENTITY"
        [[ "$(stat -c '%U:%G %a' "$BACKUP_IDENTITY")" == 'root:root 600' ]] ||
            fail 'существующий backup age identity должен быть root:root 0600'
    else
        age-keygen -o "$BACKUP_IDENTITY" >/dev/null
        chown root:root "$BACKUP_IDENTITY"
        chmod 0600 "$BACKUP_IDENTITY"
        log "создан новый backup age identity: $BACKUP_IDENTITY"
    fi

    recipient=$(age-keygen -y "$BACKUP_IDENTITY")
    [[ "$recipient" == age1* ]] || fail 'не удалось получить age recipient'

    temp=$(mktemp)
    printf 'BACKUP_RETENTION_COUNT=14\n' > "$temp"
    printf 'BACKUP_REQUIRE_OFFSITE=false\n' >> "$temp"
    printf 'BACKUP_AGE_RECIPIENT=%s\n' "$recipient" >> "$temp"
    install -o root -g root -m 0600 "$temp" "$BACKUP_CONF"
    rm -f -- "$temp"
}

install_recovery_backup_tooling() {
    require_regular_file "$SOURCE_BACKUP"
    prepare_backup_config

    install -o root -g root -m 0750 "$SOURCE_BACKUP" "$BACKUP_SCRIPT"

    cat > "$BACKUP_SERVICE" <<EOF_UNIT
[Unit]
Description=Just1kBot encrypted PostgreSQL backup
After=${PG_UNIT}
Requires=${PG_UNIT}

[Service]
Type=oneshot
EnvironmentFile=${BACKUP_CONF}
Environment=PROJECT_DIR=${PROJECT_DIR}
Environment=ENV_FILE=${ENV_FILE}
ExecStart=${BACKUP_SCRIPT}
PrivateTmp=true
NoNewPrivileges=true
UMask=0077
EOF_UNIT

    chown root:root "$BACKUP_SERVICE"
    chmod 0644 "$BACKUP_SERVICE"
    systemctl daemon-reload
    systemctl cat just1kbot-backup.service >/dev/null 2>&1 || fail 'systemd не видит восстановленный backup service'
    log 'backup tooling восстановлен; обязательный backup выполнит основной transactional deploy'
}

validate_complete_install() {
    local required

    for required in "$UNIT_FILE" "$BACKUP_SERVICE" "$BACKUP_SCRIPT" "$BACKUP_CONF"; do
        require_regular_file "$required"
    done
    [[ -x "$BACKUP_SCRIPT" ]] || fail 'установленный backup script не является исполняемым'

    id "$BOT_USER" >/dev/null 2>&1 || fail 'service account отсутствует у установленной системы'
    runuser -u "$BOT_USER" -- test -r "$ENV_FILE" || fail 'service user не может прочитать production .env'
    systemctl daemon-reload
    systemctl cat just1kbot-backup.service >/dev/null 2>&1 || fail 'systemd не видит installed backup service'
    log 'существующая установка прошла preflight'
}

main() {
    local argument

    [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'запустите от root'

    for argument in "$@"; do
        case "$argument" in
            --check|--dry-run)
                log 'read-only команда: восстановительный preflight не изменяет сервер'
                return 0
                ;;
        esac
    done

    if [[ ! -e "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
        [[ ! -e "$UNIT_FILE" && ! -L "$UNIT_FILE" ]] ||
            fail 'systemd unit существует без production .env'
        log 'признаков предыдущей установки нет; будет обычная первичная установка'
        return 0
    fi

    for argument in python3 pg_lsclusters psql runuser age-keygen systemctl; do
        command -v "$argument" >/dev/null 2>&1 || fail "не найдена команда: $argument"
    done

    require_regular_file "$ENV_FILE"

    if [[ -f "$UNIT_FILE" && ! -L "$UNIT_FILE" ]]; then
        validate_complete_install
        return 0
    fi
    [[ ! -e "$UNIT_FILE" && ! -L "$UNIT_FILE" ]] || fail 'main systemd unit имеет небезопасный тип'

    log 'обнаружена незавершённая установка: .env и database сохранены, main unit отсутствует'

    ensure_service_account
    repair_incomplete_permissions
    validate_protected_home_runtime

    local database_url port
    database_url=$(read_env_value DATABASE_URL)
    [[ -n "$database_url" ]] || fail 'DATABASE_URL отсутствует в production .env'
    port=$(parse_database_url "$database_url") || fail 'DATABASE_URL не прошёл безопасную проверку'
    [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || fail 'некорректный PostgreSQL port'

    select_postgresql_cluster "$port"
    validate_database_revision "$port"
    install_recovery_backup_tooling
}

main "$@"
