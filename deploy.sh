#!/bin/bash
# =============================================================================
# JUST1KBOT - Автоматический деплой
# =============================================================================
# Использование: sudo ./deploy.sh [--yes] [--dry-run]
# Лог: /var/log/just1kbot-deploy.log
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

# --- Константы ---
BOT_USER="just1kbot"
PROJECT_DIR="/opt/just1kbot"
VENV_DIR="$PROJECT_DIR/venv"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="just1kbot"
LOG_FILE="/var/log/just1kbot-deploy.log"
ROLLBACK_LOG="/var/log/just1kbot-rollback.log"
BACKUP_DIR="/root/backups/just1kbot"
RESTORE_OPERATION_DIR="/root/restore-operations"
DEPLOY_LOCK_FILE="/run/lock/just1kbot-deploy.lock"
PYTHON_MIN_VERSION="3.11"
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

# --- Цвета ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Временные файлы ---
PG_PASS_FILE=""
TEMP_FILES=()

cleanup_temp_files() {
    for f in "${TEMP_FILES[@]:-}"; do
        rm -f "$f" 2>/dev/null || true
    done
    if [[ -n "${PG_PASS_FILE:-}" ]]; then
        rm -f "$PG_PASS_FILE" 2>/dev/null || true
    fi
}
trap cleanup_temp_files EXIT INT TERM

# --- Проверка root ---
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Ошибка: скрипт должен быть запущен с правами root (sudo).${NC}"
    exit 1
fi

# Deployment and production restore are mutually exclusive.  Both tools use
# the same nonblocking lock contract and fail rather than waiting ambiguously.
mkdir -p "$(dirname "$DEPLOY_LOCK_FILE")"
exec 19>"$DEPLOY_LOCK_FILE"
flock -n 19 || { echo "Ошибка: production restore или другой deploy уже выполняется." >&2; exit 3; }

# --- Проверка ОС ---
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}Внимание: скрипт оптимизирован для Ubuntu/Debian. Текущая ОС: $ID${NC}"
        read -p "Продолжить? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Отменено."
            exit 0
        fi
    fi
fi

# --- Флаги ---
NON_INTERACTIVE=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --yes|-y|--force) NON_INTERACTIVE=true ;;
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            echo "Использование: sudo ./deploy.sh [--yes] [--dry-run]"
            echo "  --yes, -y, --force  Неинтерактивный режим (значения из переменных окружения)"
            echo "  --dry-run           Показать что будет сделано без выполнения"
            exit 0
            ;;
    esac
done

# --- Логирование ---
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${GREEN}[$timestamp]${NC} $1"
    echo "[$timestamp] $1" >> "$LOG_FILE"
}

warn() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${YELLOW}[$timestamp] ВНИМАНИЕ:${NC} $1"
    echo "[$timestamp] WARNING: $1" >> "$LOG_FILE"
}

error() {
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${RED}[$timestamp] ОШИБКА:${NC} $1" >&2
    echo "[$timestamp] ERROR: $1" >> "$LOG_FILE"
}

info() {
    echo -e "${BLUE}$1${NC}"
}

# --- Утилиты ---

# Безопасная запись переменной в .env
write_env_var() {
    local key="$1"
    local value="$2"
    # Экранирование одинарных кавычек для single-quoted контекста
    value=${value//\'/\\\'}
    printf "%s='%s'\n" "$key" "$value" >> "$ENV_FILE"
    chown "$BOT_USER:$BOT_USER" "$ENV_FILE" 2>/dev/null || true
    chmod 600 "$ENV_FILE"
}

# Безопасное присваивание переменной (без eval)
assign_var() {
    local var_name="$1"
    local value="$2"
    printf -v "$var_name" '%s' "$value"
}

read_required() {
    local prompt="$1"
    local var_name="$2"
    local value=""

    while true; do
        read -rp "$(echo -e "${BLUE}$prompt${NC}")" value
        if [[ -n "$value" ]]; then
            assign_var "$var_name" "$value"
            break
        fi
        warn "Значение не может быть пустым. Попробуйте снова."
    done
}

read_required_secret() {
    local prompt="$1"
    local var_name="$2"
    local value=""

    while true; do
        read -rsp "$(echo -e "${BLUE}$prompt${NC}")" value
        echo
        if [[ -n "$value" ]]; then
            assign_var "$var_name" "$value"
            break
        fi
        warn "Значение не может быть пустым. Попробуйте снова."
    done
}

read_optional() {
    local prompt="$1"
    local var_name="$2"
    local default="$3"
    local value=""

    read -rp "$(echo -e "${BLUE}$prompt [${default}]${NC}")" value
    if [[ -z "$value" ]]; then
        assign_var "$var_name" "$default"
    else
        assign_var "$var_name" "$value"
    fi
}

read_db_password() {
    local prompt="$1"
    local var_name="$2"
    local value=""

    while true; do
        read -rsp "$(echo -e "${BLUE}$prompt${NC}")" value
        echo
        if [[ ${#value} -lt 8 ]]; then
            warn "Пароль должен быть не менее 8 символов."
            continue
        fi
        if [[ ! "$value" =~ ^[a-zA-Z0-9_@#%*+=-]+$ ]]; then
            warn "Пароль содержит недопустимые символы. Используйте: a-z, A-Z, 0-9, _ @ # % * + = -"
            continue
        fi
        assign_var "$var_name" "$value"
        break
    done
}

read_bot_token() {
    local prompt="$1"
    local var_name="$2"
    local value=""

    while true; do
        read -rp "$(echo -e "${BLUE}$prompt${NC}")" value
        if [[ "$value" =~ ^[0-9]+:[a-zA-Z0-9_-]+$ ]]; then
            assign_var "$var_name" "$value"
            break
        fi
        warn "Неверный формат токена. Ожидается: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
    done
}

read_admin_ids() {
    local prompt="$1"
    local var_name="$2"
    local value=""

    while true; do
        read -rp "$(echo -e "${BLUE}$prompt${NC}")" value
        if [[ "$value" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
            assign_var "$var_name" "$value"
            break
        fi
        warn "Неверный формат. Введите числа через запятую: 123456789,987654321"
    done
}

# --- Сбор данных ---
collect_input() {
    echo ""
    info "=== Ввод данных для деплоя ==="
    echo ""
    info "Для отмены введите /cancel в любом поле."
    echo ""

    if [[ "$NON_INTERACTIVE" == true ]]; then
        BOT_TOKEN="${BOT_TOKEN:?Переменная BOT_TOKEN не задана}"
        DB_PASSWORD="${DB_PASSWORD:?Переменная DB_PASSWORD не задана}"
        REDIS_PASSWORD="${REDIS_PASSWORD:?Переменная REDIS_PASSWORD не задана}"
        ADMIN_IDS="${ADMIN_IDS:?Переменная ADMIN_IDS не задана}"
        DOMAIN="${DOMAIN:-}"
        SSL_EMAIL="${SSL_EMAIL:-admin@example.com}"
        AMNEZIA_API_URL="${AMNEZIA_API_URL:-http://127.0.0.1:4001}"
        AMNEZIA_API_KEY="${AMNEZIA_API_KEY:-}"
        YOOKASSA_SHOP_ID="${YOOKASSA_SHOP_ID:-}"
        YOOKASSA_SECRET_KEY="${YOOKASSA_SECRET_KEY:-}"
        DB_ENCRYPTION_KEY="${DB_ENCRYPTION_KEY:-}"
        log "Неинтерактивный режим: данные взяты из переменных окружения"
        return
    fi

    read_bot_token "Токен бота (от @BotFather): " BOT_TOKEN
    [[ "$BOT_TOKEN" == "/cancel" ]] && { log "Отменено пользователем"; exit 0; }

    read_db_password "Пароль PostgreSQL (мин. 8 символов): " DB_PASSWORD
    [[ "$DB_PASSWORD" == "/cancel" ]] && { log "Отменено пользователем"; exit 0; }

    read_db_password "Пароль Redis (мин. 8 символов): " REDIS_PASSWORD
    [[ "$REDIS_PASSWORD" == "/cancel" ]] && { log "Отменено пользователем"; exit 0; }

    read_admin_ids "Admin IDs (через запятую): " ADMIN_IDS
    [[ "$ADMIN_IDS" == "/cancel" ]] && { log "Отменено пользователем"; exit 0; }

    read_optional "Домен (Enter = пропустить SSL): " DOMAIN ""
    [[ "$DOMAIN" == "/cancel" ]] && { log "Отменено пользователем"; exit 0; }

    if [[ -n "$DOMAIN" ]]; then
        read_optional "Email для Let's Encrypt: " SSL_EMAIL "admin@example.com"
    else
        SSL_EMAIL=""
    fi

    read_optional "Amnezia API URL: " AMNEZIA_API_URL "http://127.0.0.1:4001"
    read_optional "Amnezia API Key: " AMNEZIA_API_KEY ""
    read_optional "YooKassa Shop ID: " YOOKASSA_SHOP_ID ""
    read_optional "YooKassa Secret Key: " YOOKASSA_SECRET_KEY ""
}

# --- Предварительные проверки ---
preflight_checks() {
    log "Предварительные проверки..."

    if ! command -v python3 &> /dev/null; then
        error "python3 не найден. Установите Python >= $PYTHON_MIN_VERSION"
        exit 1
    fi

    local py_version
    py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
        error "Требуется Python >= $PYTHON_MIN_VERSION, найдено: $py_version"
        exit 1
    fi

    log "Python $py_version — OK"

    if [[ ! "$DB_PASSWORD" =~ ^[a-zA-Z0-9_@%*+=-]{8,}$ ]]; then
        error "Пароль PostgreSQL должен содержать минимум 8 безопасных символов"
        exit 1
    fi
    if [[ ! "$REDIS_PASSWORD" =~ ^[a-zA-Z0-9_@%*+=-]{8,}$ ]]; then
        error "Пароль Redis должен содержать минимум 8 безопасных символов"
        exit 1
    fi
    if [[ -n "$YOOKASSA_SHOP_ID" && -z "$YOOKASSA_SECRET_KEY" ]] || \
       [[ -z "$YOOKASSA_SHOP_ID" && -n "$YOOKASSA_SECRET_KEY" ]]; then
        error "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY должны задаваться вместе"
        exit 1
    fi
}

# --- Проверка доступности портов ---
check_port_available() {
    local port="$1"
    local service="$2"
    if ss -tlnp 2>/dev/null | grep -q ":${port} " || \
       netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
        warn "Порт $port уже занят ($service)."
        return 1
    fi
    return 0
}

# --- Установка зависимостей ---
install_dependencies() {
    log "Обновление пакетов..."
    apt-get update -qq
    apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev \
        postgresql postgresql-contrib age util-linux \
        redis-server \
        nginx certbot python3-certbot-nginx \
        ufw curl git rsync \
        build-essential libpq-dev \
        logrotate \
        > /dev/null 2>&1

    log "Зависимости установлены"
}

# --- PostgreSQL ---
setup_postgresql() {
    log "Настройка PostgreSQL..."

    systemctl enable postgresql > /dev/null 2>&1
    systemctl start postgresql

    if su - postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='just1kbot_bot'\"" 2>/dev/null | grep -q 1; then
        log "База данных just1kbot_bot уже существует — пропускаю создание"
    else
        su - postgres -c "psql -v ON_ERROR_STOP=1" <<EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'just1kbot') THEN
        CREATE ROLE just1kbot WITH LOGIN PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;
CREATE DATABASE just1kbot_bot OWNER just1kbot;
GRANT ALL PRIVILEGES ON DATABASE just1kbot_bot TO just1kbot;
EOSQL
        log "База данных создана"
    fi
}

# --- Redis ---
setup_redis() {
    log "Настройка Redis..."

    local redis_conf="/etc/redis/redis.conf"

    # Сохраняем оригинал для rollback
    if [[ -f "$redis_conf" ]]; then
        cp "$redis_conf" "${redis_conf}.bak.$(date +%s)"
    fi

    # Модифицируем существующий конфиг через sed (не перезаписываем)
    if [[ -f "$redis_conf" ]]; then
        sed -i 's/^bind .*/bind 127.0.0.1 ::1/' "$redis_conf"

        if grep -q "^requirepass" "$redis_conf"; then
            sed -i "s/^requirepass .*/requirepass ${REDIS_PASSWORD}/" "$redis_conf"
        else
            echo "requirepass ${REDIS_PASSWORD}" >> "$redis_conf"
        fi

        if grep -q "^maxmemory " "$redis_conf"; then
            sed -i 's/^maxmemory .*/maxmemory 256mb/' "$redis_conf"
        else
            echo "maxmemory 256mb" >> "$redis_conf"
        fi

        if grep -q "^maxmemory-policy" "$redis_conf"; then
            sed -i 's/^maxmemory-policy .*/maxmemory-policy allkeys-lru/' "$redis_conf"
        else
            echo "maxmemory-policy allkeys-lru" >> "$redis_conf"
        fi
    else
        # Минимальный конфиг если файла нет
        cat > "$redis_conf" <<EOF
bind 127.0.0.1 ::1
port 6379
daemonize no
supervised systemd
dir /var/lib/redis
logfile /var/log/redis/redis-server.log
requirepass ${REDIS_PASSWORD}
maxmemory 256mb
maxmemory-policy allkeys-lru
EOF
    fi

    systemctl enable redis-server > /dev/null 2>&1
    systemctl restart redis-server

    log "Redis настроен"
}

# --- Пользователь и директории ---
setup_user_and_dirs() {
    log "Создание пользователя и директорий..."

    if ! id "$BOT_USER" &>/dev/null; then
        useradd -r -m -s /bin/bash "$BOT_USER"
    fi

    mkdir -p "$PROJECT_DIR"
    mkdir -p "$BACKUP_DIR"
    mkdir -p "/var/log/just1kbot"

    chown -R "$BOT_USER:$BOT_USER" "$PROJECT_DIR"
    chown -R "$BOT_USER:$BOT_USER" "/var/log/just1kbot"

    log "Пользователь и директории готовы"
}

sync_project_files() {
    log "Копирование проекта из $SOURCE_DIR..."
    if [[ "$SOURCE_DIR" != "$PROJECT_DIR" ]]; then
        rsync -a --delete \
            --exclude '.git/' \
            --exclude '.env' \
            --exclude 'venv/' \
            --exclude '__pycache__/' \
            "$SOURCE_DIR/" "$PROJECT_DIR/"
    fi
    chown -R "$BOT_USER:$BOT_USER" "$PROJECT_DIR"
    log "Файлы проекта синхронизированы"
}

# --- Виртуальное окружение ---
setup_venv() {
    log "Создание виртуального окружения..."

    if [[ ! -d "$VENV_DIR" ]]; then
        python3 -m venv "$VENV_DIR"
    fi

    "$VENV_DIR/bin/pip" install --upgrade pip --quiet
    "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" --quiet

    chown -R "$BOT_USER:$BOT_USER" "$VENV_DIR"

    log "Виртуальное окружение готово"
}

# --- Инициализация БД ---
init_database() {
    log "Инициализация базы данных (alembic)..."

    cd "$PROJECT_DIR"
    su "$BOT_USER" -c "cd $PROJECT_DIR && $VENV_DIR/bin/alembic upgrade head" 2>> "$LOG_FILE"

    log "Миграции применены"
}

# --- Systemd сервис ---
setup_systemd() {
    log "Создание systemd сервиса..."

    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Just1kBot Telegram Bot
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONPATH=${PROJECT_DIR}
ExecStart=${VENV_DIR}/bin/python -m bot.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# Hardening
ProtectSystem=strict
ReadWritePaths=${PROJECT_DIR} /var/log/just1kbot
PrivateTmp=true
NoNewPrivileges=true
ProtectHome=read-only
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" > /dev/null 2>&1

    log "Systemd сервис создан"
}

# --- Nginx + SSL ---
setup_nginx_ssl() {
    if [[ -z "$DOMAIN" ]]; then
        log "Домен не указан — пропускаю настройку Nginx/SSL"
        return
    fi

    log "Настройка Nginx для домена: $DOMAIN"

    # Валидация домена
    while true; do
        if [[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]]; then
            break
        fi
        warn "Некорректный домен: $DOMAIN"
        if [[ "$NON_INTERACTIVE" == true ]]; then
            error "Некорректный домен в неинтерактивном режиме"
            exit 1
        fi
        read -rp "Введите корректный домен (или Enter для пропуска SSL): " DOMAIN
        if [[ -z "$DOMAIN" ]]; then
            log "SSL пропущен"
            return
        fi
    done

    # Проверка доступности портов
    check_port_available 80 "nginx/веб-сервер" || true
    check_port_available 443 "nginx/веб-сервер" || true

    # Временный конфиг для certbot
    cat > "/etc/nginx/sites-available/${DOMAIN}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

    ln -sf "/etc/nginx/sites-available/${DOMAIN}" "/etc/nginx/sites-enabled/${DOMAIN}"
    rm -f /etc/nginx/sites-enabled/default
    nginx -t 2>> "$LOG_FILE"
    systemctl reload nginx

    # Certbot
    log "Получение SSL сертификата..."
    if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$SSL_EMAIL" --redirect 2>> "$LOG_FILE"; then
        log "SSL сертификат получен"
    else
        warn "Не удалось получить сертификат. Проверьте DNS и firewall."
    fi

    # Автообновление
    systemctl enable certbot.timer > /dev/null 2>&1
    systemctl start certbot.timer 2>/dev/null || true

    log "Nginx настроен"
}

# --- Logrotate ---
setup_logrotate() {
    log "Настройка logrotate..."

    cat > /etc/logrotate.d/just1kbot <<EOF
/var/log/just1kbot-deploy.log
/var/log/just1kbot-rollback.log
/var/log/just1kbot-uninstall.log
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
EOF

    log "Logrotate настроен"
}

# --- Version-controlled backup and rehearsal tooling ---
create_backup_script() {
    log "Установка инструментов зашифрованного бэкапа..."
    for command in age pg_dump pg_restore flock sha256sum; do
        command -v "$command" >/dev/null || { error "Не найдена обязательная команда: $command"; return 1; }
    done
    install -d -o root -g root -m 0700 "$BACKUP_DIR" "$RESTORE_OPERATION_DIR"
    local source target temporary
    for source in backup_postgres.sh verify_backup.sh restore_rehearsal.sh restore_production.sh just1kbot-restore.sh; do
        target="/usr/local/bin/${source}"
        [[ "$source" != backup_postgres.sh ]] || target=/usr/local/bin/just1kbot-backup.sh
        temporary="${target}.new.$$"
        install -o root -g root -m 0750 "$SOURCE_DIR/ops/$source" "$temporary"
        mv -f "$temporary" "$target"
    done
    for source in validate_restore_candidate.py hold_restore_advisory_lock.py; do
        temporary="/usr/local/bin/${source}.new.$$"
        install -o root -g root -m 0750 "$SOURCE_DIR/ops/$source" "$temporary"
        mv -f "$temporary" "/usr/local/bin/$source"
    done
    cat > /etc/systemd/system/just1kbot-backup.service <<'EOF'
[Unit]
Description=Just1kBot encrypted PostgreSQL backup
[Service]
Type=oneshot
EnvironmentFile=/etc/just1kbot-backup.conf
ExecStart=/usr/local/bin/just1kbot-backup.sh
EOF
    cat > /etc/systemd/system/just1kbot-backup.timer <<'EOF'
[Unit]
Description=Daily Just1kBot encrypted PostgreSQL backup
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
RandomizedDelaySec=20m
[Install]
WantedBy=timers.target
EOF
    if [[ ! -e /etc/just1kbot-backup.conf ]]; then
        install -o root -g root -m 0600 /dev/null /etc/just1kbot-backup.conf
        printf '%s\n' '# Set BACKUP_AGE_RECIPIENT=age1... before enabling the timer.' > /etc/just1kbot-backup.conf
    fi
    systemctl daemon-reload
    # Migrate only the legacy backup command; preserve healthcheck and every
    # unrelated root cron line byte-for-byte.
    local cron_current cron_filtered
    cron_current=$(mktemp)
    cron_filtered=$(mktemp)
    TEMP_FILES+=("$cron_current" "$cron_filtered")
    crontab -l >"$cron_current" 2>/dev/null || :
    awk '!(NF == 6 && $6 == "/usr/local/bin/just1kbot-backup.sh")' "$cron_current" >"$cron_filtered"
    if ! cmp -s "$cron_current" "$cron_filtered"; then
        crontab "$cron_filtered"
        log "Legacy backup cron удалён; остальные cron-задачи сохранены"
    fi
    if compgen -G "$BACKUP_DIR/backup_*.tar.gz" >/dev/null; then
        warn "Обнаружены legacy plaintext backup_*.tar.gz; перенесите или удалите их вручную только после проверки нового encrypted backup"
    fi
    # Fail closed until the operator provisions the public recipient.
    if grep -q '^BACKUP_AGE_RECIPIENT=age1' /etc/just1kbot-backup.conf; then
        systemctl enable --now just1kbot-backup.timer
    else
        systemctl disable --now just1kbot-backup.timer 2>/dev/null || true
        warn "Backup timer не включён: задайте BACKUP_AGE_RECIPIENT в /etc/just1kbot-backup.conf"
    fi
    log "Инструменты бэкапа установлены (systemd timer, Persistent=true)"
}

# Kept as a separate deployment step for compatibility; it is now fail-safe.
create_restore_script() {
    [[ -x /usr/local/bin/just1kbot-restore.sh ]] || return 1
}

# --- Healthcheck ---
create_healthcheck() {
    log "Создание healthcheck..."

    cat > /usr/local/bin/just1kbot-healthcheck.sh <<'HC_EOF'
#!/bin/bash
SERVICE="just1kbot"
MAX_CRASHES=5
CRASH_FILE="/tmp/just1kbot_crash_count"
HEARTBEAT_FILE="/opt/just1kbot/.heartbeat"
LOG="/var/log/just1kbot/healthcheck.log"

# Проверяем heartbeat (бот должен писать этот файл каждые 60 сек)
if [[ -f "$HEARTBEAT_FILE" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))
    if [[ $age -lt 120 ]]; then
        rm -f "$CRASH_FILE"
        exit 0
    fi
fi

# Бот не отвечает
if systemctl is-active --quiet "$SERVICE"; then
    echo "[$(date)] Heartbeat stale, restarting" >> "$LOG"
    systemctl restart "$SERVICE"
else
    count=$(cat "$CRASH_FILE" 2>/dev/null || echo 0)
    count=$((count + 1))
    echo "$count" > "$CRASH_FILE"

    if [[ $count -ge $MAX_CRASHES ]]; then
        echo "[$(date)] MAX CRASHES reached ($count). Not restarting." >> "$LOG"
        exit 1
    fi

    echo "[$(date)] Service down, restart attempt $count/$MAX_CRASHES" >> "$LOG"
    systemctl start "$SERVICE"
fi
HC_EOF

    chmod +x /usr/local/bin/just1kbot-healthcheck.sh

    # Cron: каждые 2 минуты
    (crontab -l 2>/dev/null | grep -v "just1kbot-healthcheck"; echo "*/2 * * * * /usr/local/bin/just1kbot-healthcheck.sh") | crontab -

    log "Healthcheck создан (cron: каждые 2 мин)"
}

# --- Firewall ---
setup_firewall() {
    log "Настройка UFW..."

    # Не сбрасываем существующие правила: сервер может обслуживать другие
    # приложения или использовать нестандартный SSH-порт.
    local ssh_port="22"
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        ssh_port=$(awk '{print $4}' <<< "$SSH_CONNECTION")
    fi
    ufw default deny incoming > /dev/null 2>&1
    ufw default allow outgoing > /dev/null 2>&1
    ufw allow "${ssh_port}/tcp" > /dev/null 2>&1
    ufw allow 80/tcp > /dev/null 2>&1
    ufw allow 443/tcp > /dev/null 2>&1
    ufw deny 8080/tcp > /dev/null 2>&1
    ufw deny 6379/tcp > /dev/null 2>&1
    ufw deny 5432/tcp > /dev/null 2>&1
    ufw --force enable > /dev/null 2>&1

    log "Firewall настроен"
}

# --- Запуск бота ---
start_bot() {
    log "Запуск бота..."

    systemctl start "$SERVICE_NAME"
    sleep 3

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        log "Бот запущен успешно"
    else
        error "Бот не запустился. Проверьте: journalctl -u $SERVICE_NAME -n 50"
        return 1
    fi
}

# --- Статус ---
show_status() {
    echo ""
    info "=== Статус сервисов ==="
    echo ""
    printf "%-20s %s\n" "Сервис" "Статус"
    printf "%-20s %s\n" "-------" "------"
    for svc in postgresql redis-server nginx "$SERVICE_NAME"; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            printf "%-20s ${GREEN}%s${NC}\n" "$svc" "active"
        else
            printf "%-20s ${RED}%s${NC}\n" "$svc" "inactive"
        fi
    done
    echo ""
}

# --- Итоговый вывод ---
print_result() {
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  ДЕПЛОЙ ЗАВЕРШЁН${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo "  Проект:     $PROJECT_DIR"
    echo "  Сервис:     systemctl status $SERVICE_NAME"
    echo "  Логи:       journalctl -u $SERVICE_NAME -f"
    echo "  Бэкап:      /usr/local/bin/just1kbot-backup.sh"
    echo "  Rehearsal:  AGE_IDENTITY_FILE=... /usr/local/bin/restore_rehearsal.sh <file>"
    if [[ -n "$DOMAIN" ]]; then
        echo "  Домен:      https://$DOMAIN"
    fi
    echo ""
    echo "  Полезные команды:"
    echo "    systemctl restart $SERVICE_NAME"
    echo "    journalctl -u $SERVICE_NAME -n 100 --no-pager"
    echo "    sudo -u $BOT_USER $VENV_DIR/bin/alembic upgrade head"
    echo ""
}

# --- Rollback ---
rollback() {
    local reason="$1"
    echo "[$(date)] ROLLBACK: $reason" >> "$ROLLBACK_LOG"
    warn "Откат: $reason"

    # Восстановление Redis конфига
    local redis_bak
    redis_bak=$(ls -t /etc/redis/redis.conf.bak.* 2>/dev/null | head -1)
    if [[ -n "$redis_bak" ]]; then
        cp "$redis_bak" /etc/redis/redis.conf
        systemctl restart redis-server 2>/dev/null || true
    fi

    error "Деплой отменён. Подробности: $ROLLBACK_LOG"
}

# --- Main ---
main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     JUST1KBOT — Автоматический деплой   ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
    echo ""

    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Следующие действия будут выполнены:"
        echo "  1. Установка пакетов (python3, postgresql, redis, nginx, ufw, logrotate)"
        echo "  2. Создание пользователя $BOT_USER"
        echo "  3. Настройка PostgreSQL (БД: just1kbot_bot)"
        echo "  4. Настройка Redis (пароль, maxmemory 256mb)"
        echo "  5. Создание venv в $VENV_DIR"
        echo "  6. Применение миграций (alembic)"
        echo "  7. Создание systemd сервиса"
        echo "  8. Настройка UFW"
        echo "  9. Создание бэкап/restore/healthcheck скриптов"
        echo "  10. Настройка logrotate"
        if [[ -n "${DOMAIN:-}" ]]; then
            echo "  11. Nginx + SSL для $DOMAIN"
        fi
        echo ""
        info "[DRY RUN] Никаких изменений не внесено."
        exit 0
    fi

    collect_input
    preflight_checks
    install_dependencies
    setup_user_and_dirs
    sync_project_files
    setup_postgresql
    setup_redis

    # Создание .env
    log "Создание .env..."
    cat > "$ENV_FILE" <<EOF
# Just1kBot Configuration
# Generated: $(date -Iseconds)
EOF
    if [[ -z "${DB_ENCRYPTION_KEY:-}" ]]; then
        DB_ENCRYPTION_KEY=$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')
    fi
    local db_password_encoded redis_password_encoded
    db_password_encoded=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$DB_PASSWORD")
    redis_password_encoded=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$REDIS_PASSWORD")
    write_env_var "BOT_TOKEN" "$BOT_TOKEN"
    write_env_var "DATABASE_URL" "postgresql+asyncpg://just1kbot:${db_password_encoded}@localhost:5432/just1kbot_bot"
    write_env_var "DB_ENCRYPTION_KEY" "$DB_ENCRYPTION_KEY"
    write_env_var "REDIS_URL" "redis://:${redis_password_encoded}@localhost:6379/0"
    write_env_var "REDIS_PASSWORD" "$REDIS_PASSWORD"
    write_env_var "ADMIN_IDS" "$ADMIN_IDS"
    write_env_var "AMNEZIA_API_URL" "$AMNEZIA_API_URL"
    write_env_var "AMNEZIA_API_KEY" "$AMNEZIA_API_KEY"
    write_env_var "YOOKASSA_SHOP_ID" "$YOOKASSA_SHOP_ID"
    write_env_var "YOOKASSA_SECRET_KEY" "$YOOKASSA_SECRET_KEY"
    if [[ -n "$DOMAIN" ]]; then
        write_env_var "WEBHOOK_URL" "https://${DOMAIN}/webhook"
        write_env_var "DOMAIN" "$DOMAIN"
    fi
    chown "$BOT_USER:$BOT_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    log ".env создан"

    setup_venv
    init_database
    setup_systemd
    setup_nginx_ssl
    setup_logrotate
    create_backup_script
    create_restore_script
    create_healthcheck
    setup_firewall

    if start_bot; then
        show_status
        print_result
    else
        rollback "Бот не запустился после деплоя"
        exit 1
    fi
}

main "$@"
