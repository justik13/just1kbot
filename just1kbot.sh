#!/usr/bin/env bash

set -Eeuo pipefail

# ============================================================
# just1kbot — Скрипт автоматического деплоя и управления
# ============================================================

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi

# --- ИСПРАВЛЕНО: Корректное регулярное выражение для парсинга GitHub URL ---
ORIGIN_URL="$(git -C "$SCRIPT_DIR" config --get remote.origin.url 2>/dev/null || true)"
if [[ "$ORIGIN_URL" =~ github\.com[:/\\/]([^/.]+)/([^/.]+)(\.git)?$ ]]; then
    DETECTED_REPO_OWNER="${BASH_REMATCH[1]}"
    DETECTED_REPO_NAME="${BASH_REMATCH[2]}"
else
    DETECTED_REPO_OWNER=""
    DETECTED_REPO_NAME=""
fi

REPO_OWNER="${REPO_OWNER:-${DETECTED_REPO_OWNER:-justik13}}"
REPO_NAME="${REPO_NAME:-${DETECTED_REPO_NAME:-just1kbot}}"
DEFAULT_REPO_BRANCH="bot"

INSTALL_DIR="/opt/just1kbot"
STATE_DIR="${INSTALL_DIR}/.state"
REPO_BRANCH_FILE="${STATE_DIR}/repo_branch"

REPO_BRANCH="${REPO_BRANCH:-$(cat "$REPO_BRANCH_FILE" 2>/dev/null | tr -d '\r\n' || true)}"
REPO_BRANCH="${REPO_BRANCH:-$DEFAULT_REPO_BRANCH}"

REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"
RAW_BASE_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"
COMMIT_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/commits/${REPO_BRANCH}"

ENV_FILE="${INSTALL_DIR}/.env"
VENV_DIR="${INSTALL_DIR}/.venv"
SERVICE_NAME="just1kbot.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
BOT_USER="just1kbot"
INSTALL_LOG="/var/log/just1kbot-install.log"
APP_LOG_DIR="/var/log/just1kbot"
APP_LOG_FILE="${APP_LOG_DIR}/bot.log"
PYTHON_BIN="$(command -v python3 || echo "/usr/bin/python3")"
SELF_SYMLINK="/usr/local/bin/just1kbot"
BACKUP_ROOT="${INSTALL_DIR}/backups"

setup_tty() {
    # Без exec </dev/tty, чтобы не ломать поток ввода при пайпе (curl | bash)
    return 0
}
setup_tty

log_to_file() {
    local msg="$1"
    mkdir -p "$(dirname "$INSTALL_LOG")" 2>/dev/null || true
    local sanitized
    sanitized="$(printf '%s' "$msg" | sed -E -e 's/(BOT_TOKEN|YOOKASSA_SECRET_KEY|REDIS_PASSWORD|DB_ENCRYPTION_KEY)=[^[:space:]]+/\1=***REDACTED***/g' -e 's/[0-9]{6,}:[A-Za-z0-9_-]{20,}/***TELEGRAM_TOKEN_REDACTED***/g')"
    printf '[%s] %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$sanitized" >> "$INSTALL_LOG" 2>/dev/null || true
}

print_line() { printf '%s\n' "------------------------------------------------------------" >&2; }
supports_color() { [[ -t 1 ]] && [[ "${TERM:-}" != "dumb" ]]; }

color_red() { supports_color && printf '\033[1;31m%s\033[0m' "$1" || printf '%s' "$1"; }
color_green() { supports_color && printf '\033[1;32m%s\033[0m' "$1" || printf '%s' "$1"; }
color_yellow() { supports_color && printf '\033[1;33m%s\033[0m' "$1" || printf '%s' "$1"; }
color_cyan() { supports_color && printf '\033[1;36m%s\033[0m' "$1" || printf '%s' "$1"; }

info() { printf '[*] %s\n' "$*" >&2; log_to_file "[INFO] $*"; }
ok() { printf '[+] %s\n' "$*" >&2; log_to_file "[OK] $*"; }
warn() { printf '[!] %s\n' "$*" >&2; log_to_file "[WARN] $*"; }
error() { printf '[ERROR] %s\n' "$*" >&2; log_to_file "[ERROR] $*"; }
die() { error "$*"; exit 1; }

on_error_trap() {
    local line_no="${1:-unknown}" exit_code="${2:-1}"
    printf "\n[!] Ошибка на строке %s (rc=%s). Подробности в логе: %s\n" "$line_no" "$exit_code" "$INSTALL_LOG" >&2
}
trap 'on_error_trap "$LINENO" "$?"' ERR

cleanup_transient_install_state() {
    # Remove tmp directory if left over
    if [[ -d "$tmp_dir" ]]; then rm -rf "$tmp_dir"; fi
}

on_interrupt() {
    printf '\n[!] Прервано пользователем (Ctrl+C).\n' >&2
    cleanup_transient_install_state 2>/dev/null || true
    # We exit if running non-interactively or if not in main menu.
    # If in main menu, let prompt_raw handle it.
}
trap on_interrupt INT TERM


require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "Запустите скрипт от имени root (sudo):"
        echo "  sudo bash just1kbot.sh"
        echo "Или одной командой:"
        echo "  curl -fsSL ${RAW_BASE_URL}/just1kbot.sh | sudo REPO_BRANCH=${REPO_BRANCH} bash -s --"
        exit 1
    fi
}

pause_if_tty() {
    printf '\nНажмите Enter, чтобы продолжить...'
    if [[ -c /dev/tty ]]; then
        read -r _dummy </dev/tty 2>/dev/null || true
    else
        read -r _dummy 2>/dev/null || true
    fi
    printf '\n'
}

clear_if_tty() { clear 2>/dev/null || true; }


prompt_raw() {
    local prompt="$1" __resultvar="$2" __input=""
    printf '%s' "$prompt"
    local read_status=0

    # Temporarily disable set -e for read to handle EOF / Ctrl+D / errors without exiting
    set +e
    if [[ -c /dev/tty ]]; then
        read -r __input </dev/tty || read_status=$?
    else
        read -r __input || read_status=$?
    fi
    set -e

    if [[ $read_status -ne 0 ]]; then
        warn "Ввод прерван или пуст."
        return 1
    fi
    __input="${__input#"${__input%%[![:space:]]*}"}"
    __input="${__input%"${__input##*[![:space:]]}"}"
    printf -v "$__resultvar" '%s' "$__input"
}


prompt_with_default() {
    local prompt="$1" default="${2:-}" __resultvar="$3" input_value=""
    while true; do
        if [[ -n "$default" ]]; then
            prompt_raw "$prompt [$default]: " input_value
            input_value="${input_value:-$default}"
        else
            prompt_raw "$prompt: " input_value
        fi
        if [[ -n "$input_value" ]]; then
            printf -v "$__resultvar" '%s' "$input_value"
            return 0
        fi
        warn "Значение не может быть пустым."
    done
}

confirm_explicit() {
    local prompt="$1" value=""
    while true; do
        prompt_raw "$prompt [y/n]: " value
        case "${value,,}" in
            y|yes|д|да) return 0 ;;
            n|no|н|нет) return 1 ;;
            *) warn "Нужно подтверждение: введите y или n." ;;
        esac
    done
}

service_exists() { [[ -f "$SERVICE_FILE" ]]; }

# --- ИСПРАВЛЕНО: tr удалял букву 'r' из-за отсутствия бэкслэша ---
get_env_value() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || return 0
    grep -m1 -E "^${key}=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '\r"' || true
}

# --- ИСПРАВЛЕНО: sed-регулярка для корректного экранирования спецсимволов ---
set_env_value() {
    local key="$1" value="$2"
    mkdir -p "$INSTALL_DIR"
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE" || true
    local escaped
    escaped="$(printf '%s' "$value" | sed -e 's/[\/&]/\\&/g')"
    if grep -q -E "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
    return 0
}

persist_repo_branch() {
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$REPO_BRANCH" > "$REPO_BRANCH_FILE"
}

get_local_sha() {
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true
    elif [[ -f "${STATE_DIR}/release_sha" ]]; then
        cat "${STATE_DIR}/release_sha" 2>/dev/null | tr -d '\r\n' || true
    fi
}

fetch_remote_commit_info() {
    local payload="" parsed=""
    payload="$(curl -fsSL --connect-timeout 10 --max-time 30 --retry 2 "$COMMIT_API_URL" 2>/dev/null || true)"
    [[ -n "$payload" ]] || return 0
    parsed="$("$PYTHON_BIN" - "$payload" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.loads(sys.argv[1])
    sha = data.get("sha", "").strip()
    msg = data.get("commit", {}).get("message", "").splitlines()[0].strip()
    if sha: print(f"{sha}\t{msg}")
except Exception: pass
PY
)"
    printf '%s' "$parsed"
}

ensure_service_user() {
    if ! id "$BOT_USER" >/dev/null 2>&1; then
        info "Создаю системного пользователя ${BOT_USER}..."
        useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" -m "$BOT_USER" || true
        ok "Пользователь ${BOT_USER} создан."
    fi
}

# --- ИСПРАВЛЕНО: Добавлена инициализация PostgreSQL для dnf/yum ---
install_system_packages() {
    info "Проверяю и устанавливаю системные пакеты (PostgreSQL, Redis, Python3, Git)..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq python3 python3-pip python3-venv git curl tar sudo postgresql postgresql-contrib redis-server >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q python3 python3-pip git curl tar sudo postgresql-server redis >/dev/null
        if [[ ! -d "/var/lib/pgsql/data/base" ]]; then
            postgresql-setup --initdb >/dev/null 2>&1 || true
        fi
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q python3 python3-pip git curl tar sudo postgresql-server redis >/dev/null
        if [[ ! -d "/var/lib/pgsql/data/base" ]]; then
            postgresql-setup --initdb >/dev/null 2>&1 || true
        fi
    else
        warn "Не удалось автоматически определить пакетный менеджер. Убедитесь, что Python3, PostgreSQL и Redis установлены."
    fi
    systemctl enable --now postgresql >/dev/null 2>&1 || true
    systemctl enable --now redis-server >/dev/null 2>&1 || systemctl enable --now redis >/dev/null 2>&1 || true
    ok "Системные зависимости готовы."
}

setup_venv() {
    info "Настраиваю виртуальное окружение Python в ${VENV_DIR}..."
    if [[ ! -d "$VENV_DIR" ]]; then
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
        info "Устанавливаю зависимости из requirements.txt..."
        "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
        ok "Зависимости библиотеки Python успешно установлены."
    fi
}

generate_fernet_key() {
    "$VENV_DIR/bin/python" -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
}

# --- ИСПРАВЛЕНО: Убраны лишние кавычки при записи в .env, исправлен wildcard для *CHANGE_ME* ---
configure_env_interactively() {
    info "Настройка конфигурации .env..."
    mkdir -p "$INSTALL_DIR"
    if [[ ! -f "$ENV_FILE" && -f "$INSTALL_DIR/.env.example" ]]; then
        cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
    fi
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    local bot_token admin_ids support_user db_url db_enc_key redis_url redis_pass shop_id secret_key domain ssl_email

    bot_token="$(get_env_value BOT_TOKEN)"
    if [[ -z "$bot_token" || "$bot_token" == *CHANGE_ME* ]]; then
        prompt_with_default "Введите Telegram BOT_TOKEN от @BotFather (формат 123456:ABC...)" "" bot_token
        set_env_value BOT_TOKEN "$bot_token"
    fi

    admin_ids="$(get_env_value ADMIN_IDS)"
    if [[ -z "$admin_ids" || "$admin_ids" == *CHANGE_ME* ]]; then
        prompt_with_default "Введите Telegram ID админа (например 123456789 или [123456789])" "[123456789]" admin_ids
        [[ "$admin_ids" != "["* ]] && admin_ids="[ $admin_ids ]"
        set_env_value ADMIN_IDS "$admin_ids"
    fi

    support_user="$(get_env_value SUPPORT_USERNAME)"
    if [[ -z "$support_user" || "$support_user" == *CHANGE_ME* || "$support_user" == "support" ]]; then
        prompt_with_default "Введите Username поддержки Telegram (без @)" "my_support_bot" support_user
        set_env_value SUPPORT_USERNAME "$support_user"
    fi

    db_url="$(get_env_value DATABASE_URL)"
    if [[ -z "$db_url" || "$db_url" == *CHANGE_ME* ]]; then
        prompt_with_default "DATABASE_URL PostgreSQL" "postgresql+asyncpg://just1kbot:just1kpass@localhost:5432/just1kbot_bot" db_url
        set_env_value DATABASE_URL "$db_url"
    fi

    db_enc_key="$(get_env_value DB_ENCRYPTION_KEY)"
    if [[ -z "$db_enc_key" || "$db_enc_key" == *CHANGE_ME* ]]; then
        info "Генерирую DB_ENCRYPTION_KEY (Fernet base64)..."
        db_enc_key="$(generate_fernet_key)"
        set_env_value DB_ENCRYPTION_KEY "$db_enc_key"
        ok "DB_ENCRYPTION_KEY создан."
    fi

    redis_url="$(get_env_value REDIS_URL)"
    if [[ -z "$redis_url" || "$redis_url" == *CHANGE_ME* ]]; then
        prompt_with_default "REDIS_URL" "redis://localhost:6379/0" redis_url
        set_env_value REDIS_URL "$redis_url"
    fi

    redis_pass="$(get_env_value REDIS_PASSWORD)"
    if [[ -z "$redis_pass" || "$redis_pass" == *CHANGE_ME* ]]; then
        prompt_with_default "REDIS_PASSWORD (если без пароля, введите testpass)" "testpass" redis_pass
        set_env_value REDIS_PASSWORD "$redis_pass"
    fi

    shop_id="$(get_env_value YOOKASSA_SHOP_ID)"
    if [[ -z "$shop_id" || "$shop_id" == *CHANGE_ME* ]]; then
        prompt_with_default "YOOKASSA_SHOP_ID" "123456" shop_id
        set_env_value YOOKASSA_SHOP_ID "$shop_id"
    fi

    secret_key="$(get_env_value YOOKASSA_SECRET_KEY)"
    if [[ -z "$secret_key" || "$secret_key" == *CHANGE_ME* ]]; then
        prompt_with_default "YOOKASSA_SECRET_KEY" "live_secret_key_123" secret_key
        set_env_value YOOKASSA_SECRET_KEY "$secret_key"
    fi

    set_env_value YOOKASSA_RETURN_URL "https://t.me/{bot_username}"
    set_env_value YOOKASSA_WEBHOOK_PORT "8080"

    domain="$(get_env_value DOMAIN)"
    if [[ -z "$domain" || "$domain" == "example.com"* || "$domain" == *CHANGE_ME* ]]; then
        prompt_with_default "Ваш публичный HTTPS домен (DOMAIN)" "vpn.mydomain.com" domain
        set_env_value DOMAIN "$domain"
    fi

    ssl_email="$(get_env_value SSL_EMAIL)"
    if [[ -z "$ssl_email" || "$ssl_email" == "example.com"* || "$ssl_email" == *CHANGE_ME* ]]; then
        prompt_with_default "SSL_EMAIL (ваш email для Let's Encrypt)" "admin@mydomain.com" ssl_email
        set_env_value SSL_EMAIL "$ssl_email"
    fi

    chown "$BOT_USER:$BOT_USER" "$ENV_FILE" || true
    ok "Конфигурация .env успешно сформирована."
}

setup_postgres_db_if_local() {
    local db_url
    db_url="$(get_env_value DATABASE_URL)"
    if [[ "$db_url" == *"@localhost"* || "$db_url" == *"@127.0.0.1"* ]]; then
        info "Проверяю локальную базу данных PostgreSQL..."
        sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='just1kbot'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE USER just1kbot WITH PASSWORD 'just1kpass';" || true
        sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='just1kbot_bot'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE DATABASE just1kbot_bot OWNER just1kbot;" || true
        sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE just1kbot_bot TO just1kbot;" || true
        ok "Локальная БД PostgreSQL подготовлена."
    fi
}

run_alembic_migrations() {
    info "Применяю миграции базы данных Alembic..."
    if [[ -f "$INSTALL_DIR/alembic.ini" ]]; then
        cd "$INSTALL_DIR"
        "$VENV_DIR/bin/alembic" upgrade head
        ok "Миграции Alembic успешно применены."
    else
        warn "alembic.ini не найден в $INSTALL_DIR, пропускаем миграции."
    fi
}

# --- ИСПРАВЛЕНО: Добавлен пробел в ReadWritePaths ---
setup_systemd_service() {
    info "Создаю службу systemd (${SERVICE_NAME})..."
    cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=just1kbot Telegram VPN Service
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m bot.main
Restart=always
RestartSec=5s
LimitNOFILE=65535
ProtectSystem=full
ProtectHome=true
RuntimeDirectory=just1kbot
ReadWritePaths=${INSTALL_DIR} ${APP_LOG_DIR}

[Install]
WantedBy=multi-user.target
SERVICE
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    ok "Служба systemd создана и включена."
}

setup_logrotate() {
    cat > /etc/logrotate.d/just1kbot <<ROTATE
${APP_LOG_FILE} {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su ${BOT_USER} ${BOT_USER}
}
${INSTALL_LOG} {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su root root
}
ROTATE
    chmod 644 /etc/logrotate.d/just1kbot
}

create_symlink() {
    mkdir -p "$(dirname "$SELF_SYMLINK")"
    if [[ -f "$INSTALL_DIR/just1kbot.sh" ]]; then
        chmod +x "$INSTALL_DIR/just1kbot.sh"
        ln -sfn "$INSTALL_DIR/just1kbot.sh" "$SELF_SYMLINK"
        ok "Создан симлинк команды: ${SELF_SYMLINK}"
    fi
}

download_code() {
    local ref="${1:-$REPO_BRANCH}" tmp_dir download_url src_dir
    tmp_dir="$(mktemp -d)"
    download_url="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/${ref}"
    info "Скачиваю код из ${REPO_URL} (${ref})..."
    if ! curl -fsSL --connect-timeout 20 --max-time 120 --retry 3 "$download_url" -o "$tmp_dir/repo.tar.gz"; then
        die "Не удалось скачать исходный код из GitHub!"
    fi
    tar -xzf "$tmp_dir/repo.tar.gz" -C "$tmp_dir"
    src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
    if [[ -z "$src_dir" || ! -d "$src_dir/bot" ]]; then
        die "Некорректная структура репозитория в архиве."
    fi
    printf '%s' "$src_dir"
}

deploy_code_from_dir() {
    local src_dir="$1"
    info "Копирую файлы в ${INSTALL_DIR}..."
    mkdir -p "$INSTALL_DIR" "$STATE_DIR"
    if [[ "$src_dir" != "$INSTALL_DIR" ]]; then
        cp -a "$src_dir/bot" "$INSTALL_DIR/"
        [[ -d "$src_dir/config" ]] && cp -a "$src_dir/config" "$INSTALL_DIR/"
        [[ -d "$src_dir/database" ]] && cp -a "$src_dir/database" "$INSTALL_DIR/"
        [[ -d "$src_dir/services" ]] && cp -a "$src_dir/services" "$INSTALL_DIR/"
        [[ -d "$src_dir/utils" ]] && cp -a "$src_dir/utils" "$INSTALL_DIR/"
        [[ -d "$src_dir/alembic" ]] && cp -a "$src_dir/alembic" "$INSTALL_DIR/"
        [[ -f "$src_dir/alembic.ini" ]] && cp "$src_dir/alembic.ini" "$INSTALL_DIR/"
        [[ -f "$src_dir/requirements.txt" ]] && cp "$src_dir/requirements.txt" "$INSTALL_DIR/"
        [[ -f "$src_dir/.env.example" ]] && cp "$src_dir/.env.example" "$INSTALL_DIR/"
        [[ -f "$src_dir/just1kbot.sh" ]] && cp "$src_dir/just1kbot.sh" "$INSTALL_DIR/"
    fi
    chown -R "$BOT_USER:$BOT_USER" "$INSTALL_DIR"
    chmod 755 "$INSTALL_DIR"
    ok "Файлы успешно обновлены."
}


check_pre_install() {
    info "Проверка системы перед установкой..."
    detect_install_state

    local errors=0

    if [[ "$STATE_PYTHON_FOUND" -eq 0 ]] && ! command -v apt-get >/dev/null 2>&1 && ! command -v dnf >/dev/null 2>&1 && ! command -v yum >/dev/null 2>&1; then
        error "Менеджер пакетов не найден, а Python 3 отсутствует."
        errors=$((errors+1))
    fi

    local free_space
    free_space=$(df -m / | awk 'NR==2 {print $4}' 2>/dev/null || echo 0)
    if [[ "$free_space" -lt 500 ]]; then
        warn "Свободного места на диске меньше 500МБ (${free_space}МБ). Возможны проблемы."
        confirm_explicit "Продолжить установку несмотря на предупреждение?" || errors=$((errors+1))
    fi

    if ! curl -Is https://github.com | head -1 | grep -q '200\|301\|302'; then
        error "Нет связи с GitHub. Установка невозможна."
        errors=$((errors+1))
    fi

    if [[ $errors -gt 0 ]]; then
        die "Проверка системы выявила критические ошибки. Установка прервана."
    fi
    ok "Проверка системы пройдена успешно."
}

action_install() {
    print_line
    info "Запуск полной установки just1kbot..."
    print_line
    require_root
    check_pre_install
    ensure_service_user
    install_system_packages

    if [[ -d "$SCRIPT_DIR/bot" && -f "$SCRIPT_DIR/requirements.txt" ]]; then
        deploy_code_from_dir "$SCRIPT_DIR"
    else
        local src_dir
        src_dir="$(download_code "$REPO_BRANCH")"
        deploy_code_from_dir "$src_dir"
    fi

    setup_venv
    configure_env_interactively
    setup_postgres_db_if_local
    run_alembic_migrations
    setup_systemd_service
    setup_logrotate
    create_symlink
    persist_repo_branch

    info "Запускаю службу ${SERVICE_NAME}..."
    systemctl restart "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_line
        ok "Установка успешно завершена! Бот запущен и работает."
        print_line
    else
        error "Служба не смогла запуститься. Проверьте логи командой: journalctl -u ${SERVICE_NAME} -e"
    fi
}


# --- ИСПРАВЛЕНО: Добавлен бэкап БД перед миграциями и поддержка отката ---
action_update() {
    print_line
    info "Обновление / Переустановка just1kbot..."
    print_line
    require_root
    ensure_service_user

    local backup_snapshot="${BACKUP_ROOT}/snapshot_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_snapshot"
    if [[ -f "$ENV_FILE" ]]; then cp "$ENV_FILE" "$backup_snapshot/"; fi

    # Backup code
    if [[ -d "$INSTALL_DIR/bot" ]]; then
        info "Создаю бэкап текущего кода перед обновлением..."
        cp -a "$INSTALL_DIR/bot" "$backup_snapshot/"
    fi

    local src_dir
    if [[ -d "$SCRIPT_DIR/bot" && "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
        info "Использую исходные файлы из ${SCRIPT_DIR}..."
        src_dir="$SCRIPT_DIR"
    else
        src_dir="$(download_code "$REPO_BRANCH")"
        if [[ -z "$src_dir" ]]; then
            error "Ошибка скачивания исходного кода. Отмена обновления."
            return 1
        fi
    fi

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    # Резервное копирование БД перед обновлением и миграциями
    if [[ -f "$INSTALL_DIR/alembic.ini" ]] && command -v pg_dump >/dev/null 2>&1; then
        action_backup_db || warn "Не удалось создать бэкап БД, но продолжаем..."
    fi


    if ! deploy_code_from_dir "$src_dir"; then
        error "Ошибка копирования файлов! Запуск отката..."
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        return 1
    fi

    if ! setup_venv; then
        error "Ошибка настройки виртуального окружения. Откат..."
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        return 1
    fi

    if ! run_alembic_migrations; then
        error "Ошибка миграции БД. Откат..."
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        # DB is harder to rollback perfectly without full restore, but code rollback is better than nothing
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        return 1
    fi

    setup_systemd_service
    create_symlink
    persist_repo_branch

    systemctl restart "$SERVICE_NAME"
    sleep 2

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_line
        ok "Обновление успешно выполнено! Бот перезапущен."
        print_line
    else
        error "Ошибка запуска после обновления. Выполняем откат кода!"
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true
        warn "Сделан откат кода до предыдущей версии. Проверьте логи: journalctl -u $SERVICE_NAME -e"
    fi
}


action_restart() {
    require_root
    info "Перезапуск службы ${SERVICE_NAME}..."
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "Служба успешно перезапущена."
    else
        error "Служба не активна после перезапуска."
    fi
}

action_stop() {
    require_root
    info "Остановка службы ${SERVICE_NAME}..."
    systemctl stop "$SERVICE_NAME"
    ok "Служба остановлена."
}

action_status() {
    print_line
    printf '%s\n' "$(color_cyan 'СТАТУС СИСТЕМЫ JUST1KBOT')"
    print_line
    echo "Директория установки: ${INSTALL_DIR}"
    echo "Служба systemd: ${SERVICE_NAME}"
    if service_exists; then
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            printf 'Статус бота: %s\n' "$(color_green 'РАБОТАЕТ (Active)')"
        else
            printf 'Статус бота: %s\n' "$(color_red 'ОСТАНОВЛЕН (Inactive)')"
        fi
    else
        printf 'Статус бота: %s\n' "$(color_yellow 'НЕ УСТАНОВЛЕН')"
    fi
    echo "Текущая ветка: ${REPO_BRANCH}"

    local local_sha remote_info remote_sha
    local_sha="$(get_local_sha)"
    if [[ -n "$local_sha" ]]; then
        echo "Локальный commit: ${local_sha:0:12}"
    else
        echo "Локальный commit: неизвестно"
    fi

    remote_info="$(fetch_remote_commit_info)"
    remote_sha="${remote_info%%$'\t'*}"
    if [[ -n "$remote_sha" ]]; then
        echo "Remote commit: ${remote_sha:0:12}"
        if [[ -n "$local_sha" && "$local_sha" != "$remote_sha" ]]; then
            printf 'Доступно обновление: %s\n' "$(color_red 'ДА (Запустите обновление)')"
        else
            printf 'Доступно обновление: %s\n' "$(color_green 'НЕТ (Актуальная версия)')"
        fi
    fi
    print_line
}

action_logs() {
    if service_exists; then
        info "Просмотр последних 100 строк логов бота (Ctrl+C для выхода)..."
        journalctl -u "$SERVICE_NAME" -n 100 -f
    elif [[ -f "$APP_LOG_FILE" ]]; then
        tail -n 100 -f "$APP_LOG_FILE"
    else
        warn "Логи не найдены."
    fi
}

action_edit_env() {
    require_root
    if [[ -f "$ENV_FILE" ]]; then
        local editor="${EDITOR:-nano}"
        if ! command -v "$editor" >/dev/null 2>&1; then editor="vi"; fi
        "$editor" "$ENV_FILE"
        confirm_explicit "Перезапустить бота для применения изменений в .env?" && action_restart || true
    else
        error "Файл .env не найден в ${ENV_FILE}"
    fi
}

action_backup_db() {
    require_root
    mkdir -p "$BACKUP_ROOT"
    local backup_file="${BACKUP_ROOT}/just1kbot_db_$(date +%Y%m%d_%H%M%S).sql"
    info "Создаю бэкап базы данных PostgreSQL..."
    if sudo -u postgres pg_dump just1kbot_bot > "$backup_file" 2>/dev/null; then
        gzip "$backup_file"
        ok "Бэкап создан: ${backup_file}.gz"
    else
        error "Не удалось создать бэкап БД!"
    fi
}


action_diagnostics() {
    print_line
    info "Запуск полной диагностики системы..."
    print_line

    detect_install_state

    echo "[ ПАКЕТЫ ]"
    echo "  Python 3:   $(if [[ "$STATE_PYTHON_FOUND" -eq 1 ]]; then echo "$(color_green 'Установлен')"; else echo "$(color_red 'ОТСУТСТВУЕТ')"; fi)"
    echo "  PostgreSQL: $(if [[ "$STATE_PG_FOUND" -eq 1 ]]; then echo "$(color_green 'Работает')"; else echo "$(color_yellow 'Не активен / Отсутствует')"; fi)"
    echo "  Redis:      $(if [[ "$STATE_REDIS_FOUND" -eq 1 ]]; then echo "$(color_green 'Работает')"; else echo "$(color_yellow 'Не активен / Отсутствует')"; fi)"
    echo "  Git:        $(if command -v git >/dev/null; then echo "$(color_green 'Установлен')"; else echo "$(color_red 'ОТСУТСТВУЕТ')"; fi)"
    echo "  Curl:       $(if command -v curl >/dev/null; then echo "$(color_green 'Установлен')"; else echo "$(color_red 'ОТСУТСТВУЕТ')"; fi)"
    echo ""
    echo "[ СИСТЕМА И СЕТЬ ]"
    echo "  Свободное место: $(df -h / | awk 'NR==2 {print $4}' 2>/dev/null || echo 'Неизвестно')"
    echo "  Связь с GitHub:  $(if curl -Is https://github.com | head -1 | grep -q '200\|301\|302'; then echo "$(color_green 'Доступен')"; else echo "$(color_red 'Ошибка связи')"; fi)"
    echo "  Связь с API TG:  $(if curl -Is https://api.telegram.org | head -1 | grep -q '200\|301\|302'; then echo "$(color_green 'Доступен')"; else echo "$(color_red 'Ошибка связи (Возможно нужен прокси/VPN)')"; fi)"
    echo ""
    echo "[ БОТ JUST1KBOT ]"
    echo "  Папка установки: $(if [[ -d "$INSTALL_DIR" ]]; then echo "$(color_green "Существует ($INSTALL_DIR)")"; else echo "$(color_yellow 'Отсутствует')"; fi)"
    echo "  Файл .env:       $(if [[ -f "$ENV_FILE" ]]; then echo "$(color_green 'Найден')"; else echo "$(color_yellow 'Отсутствует')"; fi)"
    echo "  Служба systemd:  $(if [[ -f "$SERVICE_FILE" ]]; then echo "$(color_green 'Создана')"; else echo "$(color_yellow 'Отсутствует')"; fi)"

    print_line
}


action_uninstall() {
    require_root
    print_line
    printf '%s\n' "$(color_red 'ВНИМАНИЕ: ПОЛНОЕ УДАЛЕНИЕ JUST1KBOT')"
    print_line
    if ! confirm_explicit "Вы уверены, что хотите полностью удалить бота и службу?"; then
        info "Удаление отменено."
        return 0
    fi

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    rm -f "$SELF_SYMLINK"
    rm -f /etc/logrotate.d/just1kbot

    if confirm_explicit "Удалить директорию установки ${INSTALL_DIR} (включая .env и кэши)?"; then
        rm -rf "$INSTALL_DIR"
        ok "Директория ${INSTALL_DIR} удалена."
    fi
    ok "just1kbot успешно удален из системы."
}


# --- СТАТУСЫ СИСТЕМЫ ---
STATE_BOT_INSTALLED=0
STATE_BOT_RUNNING=0
STATE_BOT_RESIDUAL=0
STATE_PYTHON_FOUND=0
STATE_PG_FOUND=0
STATE_REDIS_FOUND=0

detect_install_state() {
    STATE_BOT_INSTALLED=0
    STATE_BOT_RUNNING=0
    STATE_BOT_RESIDUAL=0
    STATE_PYTHON_FOUND=0
    STATE_PG_FOUND=0
    STATE_REDIS_FOUND=0

    if command -v python3 >/dev/null 2>&1; then STATE_PYTHON_FOUND=1; fi
    if systemctl is-active --quiet postgresql 2>/dev/null || systemctl is-active --quiet postgresql-server 2>/dev/null; then STATE_PG_FOUND=1; fi
    if systemctl is-active --quiet redis-server 2>/dev/null || systemctl is-active --quiet redis 2>/dev/null; then STATE_REDIS_FOUND=1; fi

    if [[ -d "$INSTALL_DIR" ]]; then
        STATE_BOT_RESIDUAL=1
    fi
    if [[ -f "$SERVICE_FILE" ]] && [[ -f "$ENV_FILE" ]]; then
        STATE_BOT_INSTALLED=1
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            STATE_BOT_RUNNING=1
        fi
    fi
}


show_menu() {
    while true; do
        detect_install_state
        clear_if_tty
        printf '%s\n' "$(color_cyan '==================================================================')"
        printf '%s\n' "$(color_cyan ' 🚀   J U S T 1 K B O T   —   У П Р А В Л Е Н И Е   Б О Т О М   🚀')"
        printf '%s\n' "$(color_cyan '==================================================================')"

        if [[ "$STATE_BOT_INSTALLED" -eq 1 ]]; then
            printf ' 👤 Пользователь: %-10s 📂 Папка: %s\n' "root" "$INSTALL_DIR"
            printf ' 🌿 Ветка: %-17s 🤖 Служба: %s\n' "$REPO_BRANCH" "$SERVICE_NAME"
            printf '%s\n' "$(color_cyan '==================================================================')"

            if [[ "$STATE_BOT_RUNNING" -eq 1 ]]; then
                printf ' [ СТАТУС ] %s\n' "$(color_green '🟢 РАБОТАЕТ (Active)')"
            else
                printf ' [ СТАТУС ] %s\n' "$(color_red '🔴 ОСТАНОВЛЕН (Inactive)')"
            fi
            printf '%s\n' "$(color_cyan '==================================================================')"
            echo ""
            echo " [1] 🛠  Статус бота и проверка обновлений (Status)"
            echo " [2] 🔄  Обновить / Переустановить (Update)"
            echo " [3] ⚙️  Настройки .env (Edit Config)"
            if [[ "$STATE_BOT_RUNNING" -eq 1 ]]; then
                echo " [4] ♻️  Перезапустить бота (Restart)"
                echo " [5] ⏹  Остановить бота (Stop)"
            else
                echo " [4] ▶️  Запустить бота (Start)"
            fi
            echo " [6] 📜  Логи бота (Logs)"
            echo " [7] 💾  Сделать бэкап базы данных (Backup)"
            echo " [8] 🩺  Диагностика системы (Diagnostics)"
            echo " [9] 🗑  Удалить бота (Uninstall)"
            echo ""
            echo " [0] ❌  Выход"
        else
            printf ' [ СТАТУС ] %s\n' "$(color_yellow '⚪️ НЕ УСТАНОВЛЕН')"
            printf '%s\n' "$(color_cyan '==================================================================')"
            echo ""
            echo " [1] 🛠  Установить бота (Полная установка с проверкой)"
            echo " [2] 🩺  Диагностика системы (Проверка готовности)"
            if [[ "$STATE_BOT_RESIDUAL" -eq 1 ]]; then
                echo " [9] 🗑  Удалить остаточные файлы (Clean up)"
            fi
            echo ""
            echo " [0] ❌  Выход"
        fi


        echo ""
        local choice=""
        prompt_raw "Выберите действие [0-9]: " choice || continue

        if [[ "$STATE_BOT_INSTALLED" -eq 1 ]]; then
            case "$choice" in
                1) action_status; pause_if_tty ;;
                2) action_update; pause_if_tty ;;
                3) action_edit_env; pause_if_tty ;;
                4) if [[ "$STATE_BOT_RUNNING" -eq 1 ]]; then action_restart; else systemctl start "$SERVICE_NAME"; ok "Служба запущена"; fi; pause_if_tty ;;
                5) if [[ "$STATE_BOT_RUNNING" -eq 1 ]]; then action_stop; else warn "Неверный пункт меню."; sleep 1; fi; pause_if_tty ;;
                6) action_logs; pause_if_tty ;;
                7) action_backup_db; pause_if_tty ;;
                8) action_diagnostics; pause_if_tty ;;
                9) action_uninstall; pause_if_tty ;;
                0|q|Q) echo "Выход."; return 0 ;;
                *) warn "Неверный пункт меню."; sleep 1 ;;
            esac
        else
            case "$choice" in
                1) action_install; pause_if_tty ;;
                2) action_diagnostics; pause_if_tty ;;
                9) if [[ "$STATE_BOT_RESIDUAL" -eq 1 ]]; then action_uninstall; pause_if_tty; else warn "Неверный пункт меню."; sleep 1; fi ;;
                0|q|Q) echo "Выход."; return 0 ;;
                *) warn "Неверный пункт меню."; sleep 1 ;;
            esac
        fi
    done
}


main() {
    setup_tty
    local cmd="${1:-}"
    case "$cmd" in
        install) action_install ;;
        update|reinstall) action_update ;;
        restart) action_restart ;;
        stop) action_stop ;;
        status) action_status ;;
        logs) action_logs ;;
        edit-env) action_edit_env ;;
        backup) action_backup_db ;;
        diag|diagnostics) action_diagnostics ;;
        uninstall) action_uninstall ;;
        "") show_menu ;;
        *)
            echo "Использование: $0 {install|update|restart|stop|status|logs|edit-env|backup|diag|uninstall}"
            exit 1
            ;;
    esac
}

main "$@"
