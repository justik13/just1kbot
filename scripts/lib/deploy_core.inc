#!/bin/bash
# =============================================================================
# JUST1KBOT — безопасная установка, обновление и эксплуатационные команды
# =============================================================================
# Первичная установка: sudo bash deploy.sh
# Обновление:          sudo bash deploy.sh
# Управление:          sudo bash deploy.sh --status|--logs|--restart|--backup
# Проверка восстановления:
#   sudo AGE_IDENTITY_FILE=/etc/just1kbot/backup.agekey \
#       bash deploy.sh --restore /var/lib/just1kbot/backups/<backup>.tar.age
# =============================================================================

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

BOT_USER="just1kbot"
PROJECT_DIR="/opt/just1kbot"
VENV_DIR="$PROJECT_DIR/venv"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="just1kbot"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_FILE="/var/log/just1kbot-deploy.log"
ROLLBACK_LOG="/var/log/just1kbot-rollback.log"
BACKUP_DIR="/var/lib/just1kbot/backups"
BACKUP_CONF="/etc/just1kbot-backup.conf"
BACKUP_IDENTITY="/etc/just1kbot/backup.agekey"
SNAPSHOT_DIR="/var/lib/just1kbot/rollback-releases"
HEARTBEAT_FILE="/run/just1kbot/heartbeat"
HEALTHCHECK_COMMAND="/usr/local/bin/just1kbot-healthcheck.sh"
DEPLOY_LOCK_FILE="/run/lock/just1kbot-deploy.lock"
PYTHON_MIN_VERSION="3.11"
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ACTION="deploy"
ACTION_ARG=""
NON_INTERACTIVE=false
DRY_RUN=false
INITIAL_INSTALL=false
DOMAIN=""
SSL_EMAIL=""

TEMP_FILES=()
TEMP_DIRS=()

cleanup_temp_files() {
    local path
    for path in "${TEMP_FILES[@]:-}"; do
        [[ -z "$path" ]] || rm -f -- "$path" 2>/dev/null || true
    done
    for path in "${TEMP_DIRS[@]:-}"; do
        [[ -z "$path" ]] || rm -rf -- "$path" 2>/dev/null || true
    done
}
trap cleanup_temp_files EXIT INT TERM

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    printf '%b[%s]%b %s\n' "$GREEN" "$timestamp" "$NC" "$1"
    printf '[%s] %s\n' "$timestamp" "$1" >> "$LOG_FILE"
}

warn() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    printf '%b[%s] ВНИМАНИЕ:%b %s\n' "$YELLOW" "$timestamp" "$NC" "$1"
    printf '[%s] WARNING: %s\n' "$timestamp" "$1" >> "$LOG_FILE"
}

error() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    printf '%b[%s] ОШИБКА:%b %s\n' "$RED" "$timestamp" "$NC" "$1" >&2
    if [[ -e "$LOG_FILE" ]]; then
        printf '[%s] ERROR: %s\n' "$timestamp" "$1" >> "$LOG_FILE"
    fi
}

info() {
    printf '%b%s%b\n' "$BLUE" "$1" "$NC"
}

usage() {
    cat <<'USAGE'
Использование:
  sudo bash deploy.sh [--yes] [--dry-run]
  sudo bash deploy.sh --status
  sudo bash deploy.sh --logs
  sudo bash deploy.sh --restart
  sudo bash deploy.sh --backup
  sudo AGE_IDENTITY_FILE=/path/to/key bash deploy.sh --restore <backup.tar.age>
  bash deploy.sh --help

Команды:
  без команды       Первичная установка или безопасное обновление.
  --status          Статус приложения, PostgreSQL, Redis, Nginx и таймеров.
  --logs            Следить за журналом systemd приложения.
  --restart         Перезапустить приложение и выполнить healthcheck.
  --backup          Создать зашифрованный PostgreSQL backup.
  --restore FILE    Выполнить изолированную проверку восстановления FILE.
                    Рабочая production-БД не изменяется.
  --yes, -y         Неинтерактивная первичная установка из env-переменных.
  --dry-run         Показать план установки/обновления без изменений.
  --help, -h        Показать эту справку.

Неизвестные аргументы завершают скрипт с кодом 2 и никогда не запускают деплой.
USAGE
}

set_action() {
    local next_action=$1
    if [[ "$ACTION" != deploy && "$ACTION" != "$next_action" ]]; then
        printf 'Нельзя одновременно использовать несколько команд управления.\n' >&2
        exit 2
    fi
    ACTION=$next_action
}

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --yes|-y|--force)
                NON_INTERACTIVE=true
                ;;
            --dry-run)
                DRY_RUN=true
                ;;
            --status)
                set_action status
                ;;
            --logs)
                set_action logs
                ;;
            --restart)
                set_action restart
                ;;
            --backup)
                set_action backup
                ;;
            --restore)
                set_action restore
                shift
                if (( $# == 0 )) || [[ "$1" == --* ]]; then
                    printf 'Для --restore требуется путь к backup-файлу.\n' >&2
                    exit 2
                fi
                ACTION_ARG=$1
                ;;
            --help|-h)
                set_action help
                ;;
            --)
                shift
                if (( $# > 0 )); then
                    printf 'Неожиданный позиционный аргумент: %s\n' "$1" >&2
                    exit 2
                fi
                break
                ;;
            --*)
                printf 'Неизвестный аргумент: %s\n' "$1" >&2
                usage >&2
                exit 2
                ;;
            *)
                printf 'Неожиданный аргумент: %s\n' "$1" >&2
                usage >&2
                exit 2
                ;;
        esac
        shift
    done

    if [[ "$ACTION" != deploy && ( "$NON_INTERACTIVE" == true || "$DRY_RUN" == true ) ]]; then
        printf '--yes и --dry-run применимы только к установке/обновлению.\n' >&2
        exit 2
    fi
}

require_root() {
    if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
        printf 'Ошибка: команда должна быть запущена через sudo/root.\n' >&2
        exit 1
    fi
}

init_logging() {
    install -d -o root -g root -m 0750 "$(dirname "$LOG_FILE")"
    touch "$LOG_FILE" "$ROLLBACK_LOG"
    chmod 0640 "$LOG_FILE" "$ROLLBACK_LOG"
}

acquire_deploy_lock() {
    install -d -o root -g root -m 0755 "$(dirname "$DEPLOY_LOCK_FILE")"
    exec 200>"$DEPLOY_LOCK_FILE"
    if ! flock -n 200; then
        error "Другой deploy/restart уже выполняется"
        return 1
    fi
}

command_required() {
    command -v "$1" >/dev/null 2>&1 || {
        error "Не найдена обязательная команда: $1"
        return 1
    }
}

read_env_value() {
    local key=$1
    ENV_FILE_PATH="$ENV_FILE" ENV_KEY="$key" python3 - <<'PY'
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
        # Unescape backslash sequences to match python-dotenv behavior
        value = value.encode().decode('unicode_escape')
    print(value)
    break
PY
}

validate_env_file_safety() {
    if [[ -L "$ENV_FILE" || ! -f "$ENV_FILE" ]]; then
        error "Production .env отсутствует, не является regular file или является symlink"
        return 1
    fi

    local mode owner
    mode=$(stat -c '%a' "$ENV_FILE")
    owner=$(stat -c '%U' "$ENV_FILE")

    if (( (8#$mode & 8#077) != 0 )); then
        error "Production .env имеет небезопасные permissions: $mode"
        return 1
    fi
    if [[ "$owner" != root && "$owner" != "$BOT_USER" ]]; then
        error "Production .env имеет неожиданного владельца: $owner"
        return 1
    fi
}

validate_env_file() {
    validate_env_file_safety || return 1

    local required value
    for required in \
        BOT_TOKEN ADMIN_IDS SUPPORT_USERNAME DATABASE_URL \
        REDIS_URL REDIS_PASSWORD DB_ENCRYPTION_KEY \
        YOOKASSA_SHOP_ID YOOKASSA_SECRET_KEY \
        YOOKASSA_RETURN_URL YOOKASSA_WEBHOOK_PORT DOMAIN SSL_EMAIL; do
        value=$(read_env_value "$required")
        if [[ -z "$value" ]]; then
            error "В production .env отсутствует обязательный параметр: $required"
            return 1
        fi
    done

    local removed
    for removed in AMNEZIA_API_URL AMNEZIA_API_KEY WEBHOOK_URL; do
        value=$(read_env_value "$removed")
        if [[ -n "$value" ]]; then
            error "В production .env найден удалённый параметр: $removed"
            return 1
        fi
    done

    local bot_token support_username admin_ids encryption_key
    local shop_id secret_key domain ssl_email webhook_port

    bot_token=$(read_env_value BOT_TOKEN)
    if [[ ! "$bot_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
        error "BOT_TOKEN в production .env имеет неверный формат"
        return 1
    fi

    support_username=$(read_env_value SUPPORT_USERNAME)
    support_username=${support_username#@}
    if [[ ! "$support_username" =~ ^[A-Za-z][A-Za-z0-9_]{0,31}$ ]] ||
       [[ "${support_username,,}" == support ]] ||
       [[ "${support_username,,}" == change_me_support_username ]]; then
        error "SUPPORT_USERNAME в production .env должен быть реальным Telegram username"
        return 1
    fi

    admin_ids=$(read_env_value ADMIN_IDS)
    ADMIN_IDS_JSON="$admin_ids" python3 - <<'PY_ADMIN_IDS'
import json
import os

value = json.loads(os.environ["ADMIN_IDS_JSON"])
if not isinstance(value, list) or not value:
    raise SystemExit("ADMIN_IDS must be a non-empty JSON array")
if any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in value):
    raise SystemExit("ADMIN_IDS must contain positive integers")
PY_ADMIN_IDS

    encryption_key=$(read_env_value DB_ENCRYPTION_KEY)
    DB_KEY="$encryption_key" python3 - <<'PY_DB_KEY'
import base64
import os

key = os.environ["DB_KEY"].encode("ascii")
try:
    decoded = base64.urlsafe_b64decode(key)
except Exception as exc:
    raise SystemExit(f"invalid DB_ENCRYPTION_KEY: {type(exc).__name__}")
if len(decoded) != 32:
    raise SystemExit("invalid DB_ENCRYPTION_KEY length")
PY_DB_KEY

    shop_id=$(read_env_value YOOKASSA_SHOP_ID)
    secret_key=$(read_env_value YOOKASSA_SECRET_KEY)
    if [[ "${shop_id,,}" == *change_me* || "${secret_key,,}" == *change_me* ]]; then
        error "YooKassa credentials в production .env содержат placeholder"
        return 1
    fi

    domain=$(read_env_value DOMAIN)
    if ! normalize_domain "$domain" >/dev/null; then
        error "DOMAIN в production .env имеет неверный формат"
        return 1
    fi

    ssl_email=$(read_env_value SSL_EMAIL)
    if ! validate_email "$ssl_email"; then
        error "SSL_EMAIL в production .env должен быть реальным email"
        return 1
    fi

    webhook_port=$(read_env_value YOOKASSA_WEBHOOK_PORT)
    if [[ ! "$webhook_port" =~ ^[1-9][0-9]{0,4}$ ]] || (( webhook_port > 65535 )); then
        error "YOOKASSA_WEBHOOK_PORT в production .env имеет неверный формат"
        return 1
    fi
}

determine_install_kind() {
    if [[ -e "$ENV_FILE" || -L "$ENV_FILE" ]]; then
        INITIAL_INSTALL=false
        if [[ "${DEPLOY_TEST_MODE:-0}" == 1 ]]; then
            validate_env_file_safety
        else
            validate_env_file
        fi
        log "Режим: безопасное обновление существующей установки"
    else
        INITIAL_INSTALL=true
        log "Режим: первичная установка"
    fi
}

validate_supported_os() {
    local os_id=${1:-}
    local version_id=${2:-}
    local version_major=${version_id%%.*}

    if [[ ! "$version_major" =~ ^[0-9]+$ ]]; then
        error "Не удалось определить поддерживаемую версию ОС: ${os_id:-unknown} ${version_id:-unknown}"
        return 1
    fi

    case "$os_id" in
        ubuntu)
            # Require Ubuntu 24.04 LTS or newer, but exclude non-LTS releases (25.04, 25.10, etc.)
            # Match only 24.04, 24.10, 26.04, 26.10, etc. (even years)
            if (( version_major < 24 )); then
                error "Поддерживается Ubuntu 24.04 или новее; обнаружено: $version_id"
                return 1
            fi
            # Warn for non-LTS versions (odd minor versions like 24.10, 25.10)
            local version_minor=${version_id#*.}
            if [[ "$version_minor" =~ ^[0-9]+$ ]] && (( version_minor % 10 != 0 )); then
                warn "Ubuntu $version_id не является LTS релизом; production рекомендуется 24.04 LTS"
            fi
            ;;
        debian)
            if (( version_major < 12 )); then
                error "Поддерживается Debian 12 или новее; обнаружено: $version_id"
                return 1
            fi
            ;;
        *)
            error "Поддерживаются только Ubuntu 24.04+ и Debian 12+; обнаружено: ${os_id:-unknown}"
            return 1
            ;;
    esac
}

check_os() {
    if [[ ! -f /etc/os-release ]]; then
        error "Не удалось определить операционную систему"
        return 1
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    validate_supported_os "${ID:-}" "${VERSION_ID:-}"
}

validate_source_tree() {
    local required
    for required in \
        requirements.txt \
        alembic.ini \
        bot/main.py \
        ops/deploy_application.sh \
        ops/backup_postgres.sh \
        ops/verify_backup.sh \
        ops/restore_rehearsal.sh \
        ops/just1kbot-restore.sh; do
        if [[ ! -f "$SOURCE_DIR/$required" ]]; then
            error "В исходном каталоге отсутствует $required"
            return 1
        fi
    done

    if [[ "$INITIAL_INSTALL" == false ]]; then
        local source_real project_real
        source_real=$(cd "$SOURCE_DIR" && pwd -P)
        project_real=$(cd "$PROJECT_DIR" && pwd -P)
        if [[ "$source_real" == "$project_real" ]]; then
            error "Обновление нельзя запускать прямо из live-каталога $PROJECT_DIR"
            error "Используйте отдельный checkout/release-каталог, чтобы rollback сохранял старую версию"
            return 1
        fi
    fi
}

assign_var() {
    printf -v "$1" '%s' "$2"
}

read_optional() {
    local prompt=$1 var_name=$2 default=$3 value=""
    read -rp "$prompt [$default]: " value
    assign_var "$var_name" "${value:-$default}"
}

read_optional_secret() {
    local prompt=$1 var_name=$2 default=$3 value=""
    read -rsp "$prompt: " value
    printf '\n'
    assign_var "$var_name" "${value:-$default}"
}

read_db_password() {
    local prompt=$1 var_name=$2 value=""
    while true; do
        read -rsp "$prompt" value
        printf '\n'
        if [[ "$value" =~ ^[A-Za-z0-9_@%*+=-]{8,}$ ]]; then
            assign_var "$var_name" "$value"
            return
        fi
        printf 'Минимум 8 символов. Разрешены A-Z, a-z, 0-9, _ @ %% * + = -\n'
    done
}

read_bot_token() {
    local value=""
    while true; do
        read -rsp 'BOT_TOKEN: ' value
        printf '
'
        if [[ -z "$value" ]]; then
            printf 'Значение не может быть пустым.
'
            continue
        fi
        if [[ "$value" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            BOT_TOKEN=$value
            return
        fi
        printf 'Неверный формат BOT_TOKEN.\n'
    done
}

read_admin_ids() {
    local value=""
    while true; do
        read -rp 'ADMIN_IDS, числа через запятую: ' value
        if [[ -z "$value" ]]; then
            printf 'Значение не может быть пустым.
'
            continue
        fi
        if [[ "$value" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
            ADMIN_IDS=$value
            return
        fi
        printf 'Неверный формат ADMIN_IDS.\n'
    done
}


read_support_username() {
    local value=""
    while true; do
        read -rp 'SUPPORT_USERNAME, без @: ' value
        value=${value#@}
        if [[ -z "$value" ]]; then
            printf 'Значение не может быть пустым.
'
            continue
        fi
        if [[ "$value" =~ ^[A-Za-z][A-Za-z0-9_]{0,31}$ ]] &&
           [[ "${value,,}" != support ]] &&
           [[ "${value,,}" != change_me_support_username ]]; then
            SUPPORT_USERNAME=$value
            return
        fi
        printf '%s\n' 'Укажите реальный Telegram username поддержки.'
    done
}

collect_initial_input() {
    if [[ "$NON_INTERACTIVE" == true ]]; then
        BOT_TOKEN=${BOT_TOKEN:?BOT_TOKEN не задан}
        DB_PASSWORD=${DB_PASSWORD:?DB_PASSWORD не задан}
        REDIS_PASSWORD=${REDIS_PASSWORD:?REDIS_PASSWORD не задан}
        ADMIN_IDS=${ADMIN_IDS:?ADMIN_IDS не задан}
        SUPPORT_USERNAME=${SUPPORT_USERNAME:?SUPPORT_USERNAME не задан}
        YOOKASSA_SHOP_ID=${YOOKASSA_SHOP_ID:?YOOKASSA_SHOP_ID не задан}
        YOOKASSA_SECRET_KEY=${YOOKASSA_SECRET_KEY:?YOOKASSA_SECRET_KEY не задан}
        YOOKASSA_RETURN_URL=${YOOKASSA_RETURN_URL:-https://t.me/{bot_username}}
        YOOKASSA_WEBHOOK_PORT=${YOOKASSA_WEBHOOK_PORT:-8080}
        DOMAIN=${DOMAIN:?DOMAIN не задан}
        SSL_EMAIL=${SSL_EMAIL:?SSL_EMAIL не задан}
        DB_ENCRYPTION_KEY=${DB_ENCRYPTION_KEY:-}
        return
    fi

    info "=== Первичная конфигурация ==="
    read_bot_token
    read_db_password 'Пароль PostgreSQL: ' DB_PASSWORD
    read_db_password 'Пароль Redis: ' REDIS_PASSWORD
    read_admin_ids
    read_support_username

    while [[ -z "${YOOKASSA_SHOP_ID:-}" ]]; do
        read_optional 'YooKassa Shop ID' YOOKASSA_SHOP_ID ''
        [[ -n "$YOOKASSA_SHOP_ID" ]] || printf 'YooKassa Shop ID обязателен.\n'
    done
    while [[ -z "${YOOKASSA_SECRET_KEY:-}" ]]; do
        read_optional_secret 'YooKassa Secret Key' YOOKASSA_SECRET_KEY ''
        [[ -n "$YOOKASSA_SECRET_KEY" ]] || printf 'YooKassa Secret Key обязателен.\n'
    done
    YOOKASSA_RETURN_URL='https://t.me/{bot_username}'
    YOOKASSA_WEBHOOK_PORT=8080

    while true; do
        read_optional 'Публичный домен для YooKassa webhook/health' DOMAIN ''
        if normalized_domain=$(normalize_domain "$DOMAIN"); then
            DOMAIN=$normalized_domain
            break
        fi
        printf 'Укажите корректный публичный домен.\n'
    done
    while true; do
        read_optional 'Email для сертификата Let's Encrypt' SSL_EMAIL ''
        if validate_email "$SSL_EMAIL"; then
            break
        fi
        printf 'Укажите реальный email для сертификата.\n'
    done
}

normalize_domain() {
    DOMAIN_VALUE="$1" python3 - <<'PY_DOMAIN'
import os
import re

raw = os.environ["DOMAIN_VALUE"].strip().lower().rstrip(".")
if not raw or len(raw) > 253:
    raise SystemExit(1)

labels = raw.split(".")
if len(labels) < 2:
    raise SystemExit(1)

label_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if any(not label_pattern.fullmatch(label) for label in labels):
    raise SystemExit(1)

print(raw)
PY_DOMAIN
}

validate_email() {
    EMAIL_VALUE="$1" python3 - <<'PY_EMAIL'
import os
import re

value = os.environ["EMAIL_VALUE"].strip()
if not value or len(value) > 254 or any(character.isspace() for character in value):
    raise SystemExit(1)
if value.lower() in {"admin@example.com", "change_me@example.com"}:
    raise SystemExit(1)

# Improved regex: require proper domain structure with valid TLD
# - Local part: alphanumeric, dots, underscores, hyphens, plus signs
# - Domain: labels separated by dots, each 1-63 chars, alphanumeric and hyphens
# - TLD: at least 2 characters, letters only (rejects numeric TLDs like .123)
pattern = re.compile(
    r"^[a-zA-Z0-9._%+-]+@"
    r"[a-zA-Z0-9.-]+\."
    r"[a-zA-Z]{2,}$"
)
if pattern.fullmatch(value) is None:
    raise SystemExit(1)
PY_EMAIL
}

validate_initial_input() {
    if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
        error "BOT_TOKEN имеет неверный формат"
        return 1
    fi
    if [[ ! "$DB_PASSWORD" =~ ^[A-Za-z0-9_@%*+=-]{8,}$ ]]; then
        error "DB_PASSWORD имеет неверный формат"
        return 1
    fi
    if [[ ! "$REDIS_PASSWORD" =~ ^[A-Za-z0-9_@%*+=-]{8,}$ ]]; then
        error "REDIS_PASSWORD имеет неверный формат"
        return 1
    fi
    if [[ ! "$ADMIN_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        error "ADMIN_IDS имеет неверный формат"
        return 1
    fi

    SUPPORT_USERNAME=${SUPPORT_USERNAME#@}
    if [[ ! "$SUPPORT_USERNAME" =~ ^[A-Za-z][A-Za-z0-9_]{0,31}$ ]] ||
       [[ "${SUPPORT_USERNAME,,}" == support ]] ||
       [[ "${SUPPORT_USERNAME,,}" == change_me_support_username ]]; then
        error "SUPPORT_USERNAME должен быть реальным Telegram username"
        return 1
    fi

    if [[ -z "$YOOKASSA_SHOP_ID" || -z "$YOOKASSA_SECRET_KEY" ]]; then
        error "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY обязательны"
        return 1
    fi
    if [[ "${YOOKASSA_SHOP_ID,,}" == *change_me* || "${YOOKASSA_SECRET_KEY,,}" == *change_me* ]]; then
        error "YooKassa credentials не должны содержать placeholder"
        return 1
    fi

    local normalized_domain
    if ! normalized_domain=$(normalize_domain "$DOMAIN"); then
        error "DOMAIN обязателен и должен быть корректным публичным доменом"
        return 1
    fi
    DOMAIN=$normalized_domain

    if ! validate_email "$SSL_EMAIL"; then
        error "SSL_EMAIL обязателен и должен быть реальным email для Let's Encrypt"
        return 1
    fi

    if [[ ! "$YOOKASSA_WEBHOOK_PORT" =~ ^[1-9][0-9]{0,4}$ ]] ||
       (( YOOKASSA_WEBHOOK_PORT > 65535 )); then
        error "YOOKASSA_WEBHOOK_PORT имеет неверный формат"
        return 1
    fi
}

install_dependencies() {
    log "Установка системных зависимостей"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev \
        postgresql postgresql-contrib redis-server \
        nginx certbot python3-certbot-nginx \
        age ufw curl git rsync build-essential libpq-dev \
        logrotate util-linux >/dev/null
}

validate_runtime_commands() {
    local command
    for command in python3 rsync systemctl stat git flock runuser age age-keygen pg_dump pg_restore psql sha256sum; do
        command_required "$command"
    done

    if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
        error "Требуется Python >= $PYTHON_MIN_VERSION"
        return 1
    fi
}

setup_user_and_dirs() {
    if ! id "$BOT_USER" >/dev/null 2>&1; then
        useradd -r -m -s /bin/bash "$BOT_USER"
    fi

    install -d -o "$BOT_USER" -g "$BOT_USER" -m 0750 "$PROJECT_DIR"
    install -d -o root -g root -m 0700 "$BACKUP_DIR" "$SNAPSHOT_DIR"
    install -d -o "$BOT_USER" -g "$BOT_USER" -m 0750 /var/log/just1kbot
}

setup_postgresql_initial() {
    log "Настройка PostgreSQL"
    systemctl enable --now postgresql >/dev/null

    # Use runuser instead of su - to avoid login shell issues with nologin users
    # and PAM session creation in headless environments
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 <<EOSQL
DO \$\$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'just1kbot') THEN
        ALTER ROLE just1kbot WITH LOGIN PASSWORD '${DB_PASSWORD}';
    ELSE
        CREATE ROLE just1kbot WITH LOGIN PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;
EOSQL

    if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='just1kbot_bot'" | grep -q 1; then
        runuser -u postgres -- createdb -O just1kbot just1kbot_bot
    fi
}

setup_redis() {
    local configure_credentials="${1:-true}"
    local conf="/etc/redis/redis.conf"
    local backup=""

    if [[ "${DEPLOY_TEST_MODE:-0}" == 1 ]]; then
        conf="${TEST_REDIS_CONF:?TEST_REDIS_CONF не задан}"
    fi

    [[ -f "$conf" && ! -L "$conf" ]] || {
        error "Redis config отсутствует, не является regular file или является symlink: $conf"
        return 1
    }

    if [[ "${DEPLOY_TEST_MODE:-0}" != 1 ]]; then
        backup="${conf}.bak.$(date +%s).$$"
        cp -a -- "$conf" "$backup"
    fi

    if grep -q '^bind ' "$conf"; then
        sed -i -E 's/^bind .*/bind 127.0.0.1 ::1/' "$conf"
    else
        printf 'bind 127.0.0.1 ::1\n' >> "$conf"
    fi

    # Under systemd Redis must log to stdout/stderr. A filesystem logfile can
    # prevent the whole service from starting when distro permissions differ.
    if grep -Eq '^[[:space:]]*logfile([[:space:]]+|$)' "$conf"; then
        sed -i -E 's|^[[:space:]]*logfile([[:space:]]+.*)?$|logfile ""|' "$conf"
    else
        printf 'logfile ""\n' >> "$conf"
    fi

    if [[ "$configure_credentials" == true ]]; then
        if grep -q '^requirepass ' "$conf"; then
            sed -i -E "s/^requirepass .*/requirepass ${REDIS_PASSWORD}/" "$conf"
        else
            printf 'requirepass %s\n' "$REDIS_PASSWORD" >> "$conf"
        fi
    else
        log "Update deployment: существующий Redis credential сохранён без ротации"
    fi

    if grep -q '^maxmemory ' "$conf"; then
        sed -i -E 's/^maxmemory .*/maxmemory 256mb/' "$conf"
    else
        printf 'maxmemory 256mb\n' >> "$conf"
    fi

    if grep -q '^maxmemory-policy ' "$conf"; then
        sed -i -E 's/^maxmemory-policy .*/maxmemory-policy allkeys-lru/' "$conf"
    else
        printf 'maxmemory-policy allkeys-lru\n' >> "$conf"
    fi

    if grep -q '^appendonly ' "$conf"; then
        sed -i -E 's/^appendonly .*/appendonly yes/' "$conf"
    else
        printf 'appendonly yes\n' >> "$conf"
    fi

    if grep -q '^appendfsync ' "$conf"; then
        sed -i -E 's/^appendfsync .*/appendfsync everysec/' "$conf"
    else
        printf 'appendfsync everysec\n' >> "$conf"
    fi

    # Redis 7 does not provide a config-only validation command.
    # --test-memory checks RAM rather than redis.conf, so startup below is the
    # authoritative validation. On failure the previous config is restored.

    systemctl enable redis-server >/dev/null 2>&1 || true
    systemctl reset-failed redis-server >/dev/null 2>&1 || true

    if systemctl restart redis-server && systemctl is-active --quiet redis-server; then
        return 0
    fi

    error "Redis не запустился с новой конфигурацией"
    journalctl -u redis-server -n 40 --no-pager >&2 2>/dev/null || true

    if [[ -n "$backup" && -f "$backup" ]]; then
        warn "Восстановление предыдущей Redis-конфигурации"
        cp -a -- "$backup" "$conf"
        systemctl reset-failed redis-server >/dev/null 2>&1 || true
        if systemctl restart redis-server && systemctl is-active --quiet redis-server; then
            error "Новая конфигурация отклонена; предыдущая восстановлена, deploy остановлен"
        else
            error "Не удалось запустить Redis даже после восстановления предыдущей конфигурации"
            journalctl -u redis-server -n 40 --no-pager >&2 2>/dev/null || true
        fi
    fi
    return 1
}
setup_redis_initial() {
    log "Настройка Redis"
    setup_redis true
}

normalize_admin_ids_json() {
    local raw=$1
    python3 - "$raw" <<'PY'
import json
import re
import sys

raw = sys.argv[1].strip()
if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", raw):
    raise SystemExit("invalid ADMIN_IDS")
print(json.dumps([int(value) for value in raw.split(",")], separators=(",", ":")))
PY
}

write_env_var() {
    local key=$1 value=$2
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//$'\n'/\\n}
    printf '%s="%s"\n' "$key" "$value" >> "$ENV_FILE"
}

create_env_if_missing() {
    if [[ -e "$ENV_FILE" ]]; then
        validate_env_file
        log "Существующий production .env сохранён без изменений"
        return
    fi

    log "Создание production .env"
    install -o "$BOT_USER" -g "$BOT_USER" -m 0600 /dev/null "$ENV_FILE"

    if [[ -z "${DB_ENCRYPTION_KEY:-}" ]]; then
        DB_ENCRYPTION_KEY=$(python3 - <<'PY'
import base64
import secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
)
    fi

    local db_encoded redis_encoded redis_port
    db_encoded=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$DB_PASSWORD")
    redis_encoded=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$REDIS_PASSWORD")

    # Use configured Redis port instead of hardcoded 6379
    # Default to 6379 for legacy compatibility if REDIS_PORT is not set
    redis_port="${REDIS_PORT:-6379}"

    write_env_var BOT_TOKEN "$BOT_TOKEN"
    write_env_var ADMIN_IDS "$(normalize_admin_ids_json "$ADMIN_IDS")"
    write_env_var SUPPORT_USERNAME "$SUPPORT_USERNAME"
    write_env_var DATABASE_URL "postgresql+asyncpg://just1kbot:${db_encoded}@127.0.0.1:5432/just1kbot_bot"
    write_env_var DB_ENCRYPTION_KEY "$DB_ENCRYPTION_KEY"
    write_env_var REDIS_URL "redis://:${redis_encoded}@127.0.0.1:${redis_port}/0"
    write_env_var REDIS_PASSWORD "$REDIS_PASSWORD"
    write_env_var YOOKASSA_SHOP_ID "$YOOKASSA_SHOP_ID"
    write_env_var YOOKASSA_SECRET_KEY "$YOOKASSA_SECRET_KEY"
    write_env_var YOOKASSA_RETURN_URL 'https://t.me/{bot_username}'
    write_env_var YOOKASSA_WEBHOOK_PORT '8080'
    write_env_var DOMAIN "$DOMAIN"
    write_env_var SSL_EMAIL "$SSL_EMAIL"

    chown "$BOT_USER:$BOT_USER" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    validate_env_file
}

setup_venv() {
    log "Подготовка Python virtualenv"
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        python3 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/python" -m pip install --upgrade pip --quiet
    "$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt" --quiet
    chown -R "$BOT_USER:$BOT_USER" "$VENV_DIR"
}

init_database() {
    log "Применение Alembic migrations"
    runuser -u "$BOT_USER" -- \
        env PYTHONPATH="$PROJECT_DIR" \
        bash -c "cd '$PROJECT_DIR' && '$VENV_DIR/bin/alembic' upgrade head" \
        2>> "$LOG_FILE"
}

setup_systemd() {
    log "Установка systemd unit"
    cat > "$UNIT_FILE" <<EOF_UNIT
[Unit]
Description=Just1kBot Telegram Bot
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target postgresql.service redis-server.service

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONPATH=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=JUST1KBOT_HEARTBEAT_FILE=/run/just1kbot/heartbeat
ExecStart=${VENV_DIR}/bin/python -m bot.main
Restart=always
RestartSec=5
TimeoutStopSec=45
KillSignal=SIGTERM
StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=${PROJECT_DIR} /var/log/just1kbot
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF_UNIT

    chmod 0644 "$UNIT_FILE"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null
}

install_backup_tooling() {
    log "Установка зашифрованного backup/restore tooling"
    local source target temp

    for source in backup_postgres.sh verify_backup.sh restore_rehearsal.sh just1kbot-restore.sh; do
        target="/usr/local/bin/$source"
        [[ "$source" != backup_postgres.sh ]] || target=/usr/local/bin/just1kbot-backup.sh
        temp="${target}.new.$$"
        install -o root -g root -m 0750 "$SOURCE_DIR/ops/$source" "$temp"
        mv -f -- "$temp" "$target"
    done

    install -d -o root -g root -m 0700 "$(dirname "$BACKUP_IDENTITY")" "$BACKUP_DIR"

    local recipient="" local_recipient=""
    if [[ -f "$BACKUP_CONF" ]]; then
        recipient=$(awk -F= '/^BACKUP_AGE_RECIPIENT=age1/ {value=$2} END {print value}' "$BACKUP_CONF")
    fi

    if [[ "$recipient" != age1* ]]; then
        if [[ ! -s "$BACKUP_IDENTITY" ]]; then
            age-keygen -o "$BACKUP_IDENTITY" >/dev/null
            chmod 0600 "$BACKUP_IDENTITY"
            warn "Создан backup age identity: $BACKUP_IDENTITY"
            warn "Скопируйте этот ключ в защищённое место вне сервера"
        fi

        recipient=$(age-keygen -y "$BACKUP_IDENTITY")
        [[ "$recipient" == age1* ]] || {
            error "Не удалось получить age recipient"
            return 1
        }

        local temp_conf
        temp_conf=$(mktemp)
        TEMP_FILES+=("$temp_conf")
        if [[ -f "$BACKUP_CONF" ]]; then
            grep -v '^BACKUP_AGE_RECIPIENT=' "$BACKUP_CONF" > "$temp_conf" || :
        else
            printf 'BACKUP_RETENTION_COUNT=14\nBACKUP_REQUIRE_OFFSITE=false\n' > "$temp_conf"
        fi
        printf 'BACKUP_AGE_RECIPIENT=%s\n' "$recipient" >> "$temp_conf"
        install -o root -g root -m 0600 "$temp_conf" "$BACKUP_CONF"
    elif [[ -s "$BACKUP_IDENTITY" ]]; then
        local_recipient=$(age-keygen -y "$BACKUP_IDENTITY" 2>/dev/null || true)
        if [[ -n "$local_recipient" && "$local_recipient" != "$recipient" ]]; then
            warn "Настроенный BACKUP_AGE_RECIPIENT не соответствует локальному $BACKUP_IDENTITY"
            warn "Для restore используйте закрытый ключ, соответствующий настроенному recipient"
        fi
    else
        warn "Backup использует внешний age recipient; локальный закрытый ключ не найден"
    fi

    cat > /etc/systemd/system/just1kbot-backup.service <<'EOF_BACKUP_SERVICE'
[Unit]
Description=Just1kBot encrypted PostgreSQL backup
After=postgresql.service
Requires=postgresql.service

[Service]
Type=oneshot
EnvironmentFile=/etc/just1kbot-backup.conf
Environment=PROJECT_DIR=/opt/just1kbot
Environment=ENV_FILE=/opt/just1kbot/.env
ExecStart=/usr/local/bin/just1kbot-backup.sh
PrivateTmp=true
NoNewPrivileges=true
EOF_BACKUP_SERVICE

    cat > /etc/systemd/system/just1kbot-backup.timer <<'EOF_BACKUP_TIMER'
[Unit]
Description=Daily Just1kBot encrypted PostgreSQL backup

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
RandomizedDelaySec=20m
Unit=just1kbot-backup.service

[Install]
WantedBy=timers.target
EOF_BACKUP_TIMER

    systemctl daemon-reload
}

create_encrypted_backup() {
    local context=${1:-manual}
    log "Создание encrypted PostgreSQL backup: context=$context"

    local deadline started latest
    deadline=$(( $(date +%s) + 180 ))
    while systemctl is-active --quiet just1kbot-backup.service; do
        if (( $(date +%s) > deadline )); then
            error "Предыдущий backup не завершился за 180 секунд"
            return 1
        fi
        sleep 2
    done

    started=$(date +%s)
    systemctl start just1kbot-backup.service

    latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
        -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@ %p\n' \
        | sort -rn | head -1 | cut -d' ' -f2-)
    if [[ -z "$latest" || ! -s "$latest" || ! -s "$latest.sha256" ]]; then
        error "Backup service завершился без готового артефакта"
        return 1
    fi
    if (( $(stat -c %Y "$latest") < started )); then
        error "Не создан новый backup для текущей операции"
        return 1
    fi
    log "Encrypted backup: $(basename "$latest")"
}

create_pre_migration_backup() {
    create_encrypted_backup pre-migration
}

install_healthcheck() {
    log "Установка healthcheck"
    cat > "$HEALTHCHECK_COMMAND" <<'EOF_HEALTH'
#!/bin/bash
set -Eeuo pipefail

SERVICE=just1kbot
PROJECT_DIR=/opt/just1kbot
VENV_DIR="$PROJECT_DIR/venv"
# Use the same heartbeat path as show_status() for consistency
HEARTBEAT_FILE="/run/just1kbot/heartbeat"
LOCK_FILE=/run/lock/just1kbot-healthcheck.lock
MAX_HEARTBEAT_AGE=180

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

if ! systemctl is-active --quiet "$SERVICE"; then
    echo "healthcheck: service is not active" >&2
    exit 1
fi

if [[ ! -f "$HEARTBEAT_FILE" ]]; then
    echo "healthcheck: heartbeat is missing" >&2
    exit 2
fi

age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))
if (( age < 0 || age > MAX_HEARTBEAT_AGE )); then
    echo "healthcheck: heartbeat is stale, age=${age}s" >&2
    exit 2
fi

cd "$PROJECT_DIR"
runuser -u just1kbot -- env PYTHONPATH="$PROJECT_DIR" "$VENV_DIR/bin/python" - <<'PY'
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import redis.asyncio as redis

from config.settings import get_settings


async def check() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    client = redis.from_url(settings.REDIS_URL)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        if not await client.ping():
            raise RuntimeError("redis ping returned false")
    finally:
        await client.aclose()
        await engine.dispose()


asyncio.run(check())
PY
EOF_HEALTH
    chmod 0750 "$HEALTHCHECK_COMMAND"

    cat > /etc/systemd/system/just1kbot-healthcheck.service <<'EOF_HEALTH_SERVICE'
[Unit]
Description=Just1kBot application healthcheck
After=just1kbot.service

[Service]
Type=oneshot
WorkingDirectory=/opt/just1kbot
ExecStart=/usr/local/bin/just1kbot-healthcheck.sh
EOF_HEALTH_SERVICE

    cat > /etc/systemd/system/just1kbot-healthcheck.timer <<'EOF_HEALTH_TIMER'
[Unit]
Description=Run Just1kBot healthcheck every two minutes

[Timer]
OnBootSec=3m
OnUnitActiveSec=2m
AccuracySec=15s
Persistent=true
Unit=just1kbot-healthcheck.service

[Install]
WantedBy=timers.target
EOF_HEALTH_TIMER

    systemctl daemon-reload

    # Remove the obsolete healthcheck cron; preserve every unrelated root cron line.
    if command -v crontab >/dev/null 2>&1; then
        local current filtered
        current=$(mktemp)
        filtered=$(mktemp)
        TEMP_FILES+=("$current" "$filtered")
        crontab -l > "$current" 2>/dev/null || :
        grep -v 'just1kbot-healthcheck' "$current" > "$filtered" || :
        if ! cmp -s "$current" "$filtered"; then
            crontab "$filtered"
        fi
    fi
}

pause_operational_timers() {
    systemctl stop just1kbot-healthcheck.timer 2>/dev/null || true
    systemctl stop just1kbot-backup.timer 2>/dev/null || true

    local deadline=$(( $(date +%s) + 180 ))
    while systemctl is-active --quiet just1kbot-backup.service; do
        if (( $(date +%s) > deadline )); then
            error "Активный backup не завершился за 180 секунд"
            return 1
        fi
        sleep 2
    done
}

resume_operational_timers() {
    systemctl enable --now just1kbot-backup.timer >/dev/null
    systemctl enable --now just1kbot-healthcheck.timer >/dev/null
}

wait_for_application_health() {
    local deadline=$(( $(date +%s) + 150 ))
    while (( $(date +%s) <= deadline )); do
        if [[ -x "$HEALTHCHECK_COMMAND" ]] && "$HEALTHCHECK_COMMAND" >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    return 1
}

setup_logrotate() {
    cat > /etc/logrotate.d/just1kbot <<'EOF_LOGROTATE'
/var/log/just1kbot-deploy.log
/var/log/just1kbot-rollback.log
/var/log/just1kbot/*.log
{
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
}
EOF_LOGROTATE
}

write_nginx_proxy_config() {
    local domain=$1
    local cert_dir="/etc/letsencrypt/live/$domain"

    if [[ -f "$cert_dir/fullchain.pem" && -f "$cert_dir/privkey.pem" ]]; then
        cat > "/etc/nginx/sites-available/$domain" <<EOF_NGINX_TLS
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${domain};

    ssl_certificate ${cert_dir}/fullchain.pem;
    ssl_certificate_key ${cert_dir}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 64k;

    location = /health {
        proxy_pass http://127.0.0.1:8080/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location = /webhook/yookassa {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:8080/webhook/yookassa;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location / {
        return 404;
    }
}
EOF_NGINX_TLS
    else
        cat > "/etc/nginx/sites-available/$domain" <<EOF_NGINX_HTTP
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    client_max_body_size 64k;

    location = /health {
        proxy_pass http://127.0.0.1:8080/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location = /webhook/yookassa {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:8080/webhook/yookassa;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        return 404;
    }
}
EOF_NGINX_HTTP
    fi

    ln -sfn "/etc/nginx/sites-available/$domain" "/etc/nginx/sites-enabled/$domain"
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl enable --now nginx >/dev/null
    systemctl reload nginx
}

setup_nginx_initial() {
    [[ -n "$DOMAIN" ]] || return 0
    command_required nginx
    command_required certbot
    log "Настройка Nginx/HTTPS для $DOMAIN"

    write_nginx_proxy_config "$DOMAIN"

    if [[ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]]; then
        certbot certonly --nginx \
            -d "$DOMAIN" \
            --non-interactive \
            --agree-tos \
            -m "$SSL_EMAIL"
    fi

    write_nginx_proxy_config "$DOMAIN"
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
}

refresh_existing_nginx() {
    DOMAIN=$(read_env_value DOMAIN)
    [[ -n "$DOMAIN" ]] || return 0
    command_required nginx
    if [[ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ||           ! -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]]; then
        error "DOMAIN=$DOMAIN задан, но production SSL-сертификат отсутствует"
        return 1
    fi
    write_nginx_proxy_config "$DOMAIN"
}

setup_firewall_initial() {
    log "Настройка UFW без сброса существующих правил"
    local ssh_port=22
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        ssh_port=$(awk '{print $4}' <<< "$SSH_CONNECTION")
    fi

    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow "${ssh_port}/tcp" >/dev/null
    if [[ -n "$DOMAIN" ]]; then
        ufw allow 80/tcp >/dev/null
        ufw allow 443/tcp >/dev/null
    fi
    ufw deny 8080/tcp >/dev/null
    ufw deny 6379/tcp >/dev/null
    ufw deny 5432/tcp >/dev/null
    ufw --force enable >/dev/null
}

prepare_release_runtime() {
    setup_user_and_dirs
    create_env_if_missing
    setup_venv
}

activate_release() {
    setup_systemd
}

run_manual_backup() {
    [[ -x /usr/local/bin/just1kbot-backup.sh ]] || {
        error "Backup tooling не установлен. Сначала выполните deploy/update"
        return 1
    }
    create_encrypted_backup manual
}

run_restore_rehearsal() {
    local artifact=$1
    [[ -f "$artifact" && ! -L "$artifact" ]] || {
        error "Backup-файл не найден или небезопасен: $artifact"
        return 1
    }
    [[ -n "${AGE_IDENTITY_FILE:-}" && -f "$AGE_IDENTITY_FILE" && ! -L "$AGE_IDENTITY_FILE" ]] || {
        error "Задайте AGE_IDENTITY_FILE с закрытым age-ключом"
        return 1
    }
    [[ -x /usr/local/bin/just1kbot-restore.sh ]] || {
        error "Restore tooling не установлен. Сначала выполните deploy/update"
        return 1
    }
    systemctl is-active --quiet postgresql || {
        error "PostgreSQL не запущен"
        return 1
    }

    local work artifact_copy identity_copy sidecar_copy rc
    work=$(mktemp -d /var/lib/postgresql/just1kbot-restore.XXXXXX)
    TEMP_DIRS+=("$work")
    chown postgres:postgres "$work"
    chmod 0700 "$work"
    artifact_copy="$work/$(basename "$artifact")"
    identity_copy="$work/identity.agekey"
    install -o postgres -g postgres -m 0400 "$artifact" "$artifact_copy"
    install -o postgres -g postgres -m 0400 "$AGE_IDENTITY_FILE" "$identity_copy"
    if [[ -f "$artifact.sha256" && ! -L "$artifact.sha256" ]]; then
        sidecar_copy="$artifact_copy.sha256"
        install -o postgres -g postgres -m 0400 "$artifact.sha256" "$sidecar_copy"
    fi

    set +e
    runuser -u postgres -- env \
        AGE_IDENTITY_FILE="$identity_copy" \
        REHEARSAL_MAINTENANCE_DATABASE=postgres \
        /usr/local/bin/just1kbot-restore.sh "$artifact_copy"
    rc=$?
    set -e
    rm -rf -- "$work"
    return "$rc"
}

show_status() {
    printf '\n%-32s %s\n' 'Компонент' 'Состояние'
    printf '%-32s %s\n' '---------' '---------'

    local unit state
    for unit in postgresql redis-server nginx "$SERVICE_NAME" \
        just1kbot-backup.timer just1kbot-healthcheck.timer; do
        state=$(systemctl is-active "$unit" 2>/dev/null || true)
        if [[ "$state" == active ]]; then
            printf '%-32s %b%s%b\n' "$unit" "$GREEN" "$state" "$NC"
        else
            printf '%-32s %b%s%b\n' "$unit" "$RED" "${state:-unknown}" "$NC"
        fi
    done

    printf '\nMainPID: %s\n' "$(systemctl show "$SERVICE_NAME" -p MainPID --value 2>/dev/null || printf 0)"
    printf 'NRestarts: %s\n' "$(systemctl show "$SERVICE_NAME" -p NRestarts --value 2>/dev/null || printf 0)"

    if [[ -f "$HEARTBEAT_FILE" ]]; then
        printf 'Heartbeat age: %ss\n' "$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))"
    else
        printf 'Heartbeat: отсутствует\n'
    fi

    if [[ -x "$HEALTHCHECK_COMMAND" ]]; then
        if "$HEALTHCHECK_COMMAND" >/dev/null 2>&1; then
            printf 'Application health: %bhealthy%b\n' "$GREEN" "$NC"
        else
            printf 'Application health: %bunhealthy%b\n' "$RED" "$NC"
        fi
    fi

    systemctl list-timers --all \
        just1kbot-backup.timer just1kbot-healthcheck.timer \
        --no-pager 2>/dev/null || true
}

run_management_action() {
    require_root
    init_logging

    case "$ACTION" in
        status)
            show_status
            ;;
        logs)
            exec journalctl -u "$SERVICE_NAME" -f -n 100
            ;;
        restart)
            acquire_deploy_lock
            systemctl restart "$SERVICE_NAME"
            if ! wait_for_application_health; then
                journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2 || true
                printf 'Сервис перезапущен, но readiness/healthcheck не пройден.\n' >&2
                exit 1
            fi
            show_status
            ;;
        backup)
            run_manual_backup
            ;;
        restore)
            run_restore_rehearsal "$ACTION_ARG"
            ;;
    esac
}

print_dry_run() {
    info "=== DRY RUN ==="
    if [[ "$INITIAL_INSTALL" == true ]]; then
        cat <<'EOF_DRY_INITIAL'
Будет выполнена первичная установка:
1. Установка системных зависимостей.
2. Создание системного пользователя, PostgreSQL DB/role и Redis-конфигурации.
3. Создание production .env с новым DB_ENCRYPTION_KEY.
4. Установка зашифрованных backup/restore инструментов и systemd timers.
5. Настройка Nginx, UFW, logrotate и healthcheck.
6. Транзакционная активация приложения и Alembic migrations.
EOF_DRY_INITIAL
    else
        cat <<'EOF_DRY_UPDATE'
Будет выполнено безопасное обновление:
1. Production .env и DB_ENCRYPTION_KEY останутся без изменений.
2. Старый процесс будет остановлен и проверен на завершение.
3. Перед migrations будет создан обязательный зашифрованный PostgreSQL backup.
4. Текущий release и virtualenv будут сохранены в rollback snapshot.
5. Новый код и зависимости будут установлены, затем применятся Alembic migrations.
6. Новая версия пройдёт readiness gate: process, heartbeat, PostgreSQL и Redis.
7. При ошибке приложение откатится; автоматический downgrade БД не выполняется.
EOF_DRY_UPDATE
    fi
}

print_result() {
    printf '\n%bДЕПЛОЙ ЗАВЕРШЁН%b\n' "$GREEN" "$NC"
    printf 'Проект: %s\n' "$PROJECT_DIR"
    printf 'Статус: sudo bash deploy.sh --status\n'
    printf 'Логи: sudo bash deploy.sh --logs\n'
    printf 'Backup: sudo bash deploy.sh --backup\n'
    printf 'Backup config: %s\n' "$BACKUP_CONF"
    if [[ -s "$BACKUP_IDENTITY" ]]; then
        printf 'Локальный age identity: %s — сохраните копию вне сервера\n' "$BACKUP_IDENTITY"
    fi
    printf 'Rollback snapshots: %s\n' "$SNAPSHOT_DIR"
    printf 'Автоматический downgrade PostgreSQL при rollback не выполняется.\n'
}

run_deploy() {
    require_root
    init_logging
    acquire_deploy_lock
    check_os
    determine_install_kind
    validate_source_tree

    if [[ "$DRY_RUN" == true ]]; then
        print_dry_run
        return
    fi

    if [[ "$INITIAL_INSTALL" == true ]]; then
        collect_initial_input
        validate_initial_input
        install_dependencies
    fi

    validate_runtime_commands
    setup_user_and_dirs

    if [[ "$INITIAL_INSTALL" == true ]]; then
        setup_postgresql_initial
        setup_redis_initial
    else
        systemctl is-active --quiet postgresql || {
            error "PostgreSQL не запущен"
            return 1
        }
        systemctl is-active --quiet redis-server || {
            error "Redis не запущен"
            return 1
        }
    fi

    install_backup_tooling
    install_healthcheck
    setup_logrotate

    if [[ "$INITIAL_INSTALL" == true ]]; then
        setup_firewall_initial
        setup_nginx_initial
    else
        refresh_existing_nginx
    fi

    pause_operational_timers

    # shellcheck source=ops/deploy_application.sh
    source "$SOURCE_DIR/ops/deploy_application.sh"
    PREPARE_COMMAND=(prepare_release_runtime)
    MIGRATION_COMMAND=(init_database)
    ACTIVATION_COMMAND=(activate_release)
    BACKUP_COMMAND=()
    if [[ "$INITIAL_INSTALL" == false ]]; then
        BACKUP_COMMAND=(create_pre_migration_backup)
    fi

    if run_application_transaction; then
        resume_operational_timers
        show_status
        print_result
    else
        local code=$?
        resume_operational_timers || true
        error "Application deploy transaction failed (code=$code)"
        error "Database downgrade не выполнялся; проверьте журнал и rollback snapshot"
        return "$code"
    fi
}

main() {
    parse_args "$@"

    case "$ACTION" in
        help)
            usage
            ;;
        deploy)
            run_deploy
            ;;
        status|logs|restart|backup|restore)
            run_management_action
            ;;
        *)
            printf 'Внутренняя ошибка: неизвестное действие %s\n' "$ACTION" >&2
            exit 2
            ;;
    esac
}

if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" != 1 ]]; then
    main "$@"
fi