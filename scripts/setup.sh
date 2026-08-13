#!/bin/bash
# =============================================================================
# JUST1KBOT - Интерактивный установщик и мастер первичной настройки
# =============================================================================
#
# Быстрый запуск на сервере:
#   git clone https://github.com/justik13/just1kbot.git
#   cd just1kbot
#   sudo bash ./scripts/setup.sh
#
# =============================================================================

set -euo pipefail

# Определение директорий
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Цветовая палитра
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[+]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!] ВНИМАНИЕ:${NC} $1"
}

error() {
    echo -e "${RED}[✗] ОШИБКА:${NC} $1" >&2
    exit 1
}

info() {
    echo -e "${CYAN}[i]${NC} $1"
}

title() {
    echo -e "\n${BOLD}${BLUE}=== $1 ===${NC}\n"
}

# --- Проверка root прав ---
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Установщик должен быть запущен с правами суперпользователя (root или sudo)."
    fi
}

# --- Проверка ОС и установка системных пакетов ---
install_dependencies() {
    title "1/6. Проверка окружения и зависимостей"

    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        log "Определена операционная система: ${PRETTY_NAME:-$ID}"
    else
        warn "Не удалось точно определить ОС через /etc/os-release. Продолжаем..."
    fi

    log "Обновление списков пакетов и установка системных утилит..."
    apt-get update -qq
    apt-get install -y -qq curl openssl age dnsutils cron git ca-certificates gnupg >/dev/null 2>&1
    log "Системные утилиты установлены (curl, openssl, age, dnsutils, cron, git)."

    # Проверка / установка Docker
    if ! command -v docker >/dev/null 2>&1; then
        info "Docker не обнаружен. Начинаем автоматическую установку Docker Engine..."
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
        sh /tmp/get-docker.sh >/dev/null 2>&1
        rm -f /tmp/get-docker.sh
        systemctl enable docker >/dev/null 2>&1 || true
        systemctl start docker >/dev/null 2>&1 || true
        log "Docker успешно установлен."
    else
        log "Docker уже установлен: $(docker --version)"
    fi

    # Проверка Docker Compose
    if ! docker compose version >/dev/null 2>&1; then
        info "Установка плагина docker-compose-plugin..."
        apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || {
            error "Не удалось установить docker-compose-plugin. Установите Docker Compose вручную."
        }
    fi
    log "Docker Compose готов к работе: $(docker compose version)"

    # Проверка занятости портов 80 и 443
    for port in 80 443; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} " || netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
            # Проверяем, не docker ли занимает порт
            local proc
            proc=$(ss -tlnp 2>/dev/null | grep ":${port} " || true)
            if echo "$proc" | grep -qv "docker"; then
                warn "Порт $port занят сторонним процессом:"
                echo "$proc"
                read -r -p "Остановить конфликтующие сервисы (например, apache2/nginx) перед запуском? (y/N): " stop_conflicts
                if [[ "$stop_conflicts" =~ ^[Yy]$ ]]; then
                    systemctl stop apache2 2>/dev/null || true
                    systemctl stop nginx 2>/dev/null || true
                    log "Сервисы apache2/nginx остановлены."
                fi
            fi
        fi
    done
}

# --- Интерактивный опросник ---
run_interactive_wizard() {
    title "2/6. Настройка параметров Telegram и YooKassa"

    echo -e "Пожалуйста, ответьте на несколько вопросов для генерации рабочей конфигурации.\n"

    # 1. Telegram Bot Token
    while true; do
        read -r -p "Введите Telegram Bot Token (от @BotFather): " BOT_TOKEN
        BOT_TOKEN="$(echo "$BOT_TOKEN" | tr -d " '\"")"

        if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            warn "Неверный формат токена. Формат: 123456789:ABCDefghIJklmn..."
            continue
        fi

        info "Проверка токена через Telegram Bot API..."
        local me_resp
        me_resp=$(curl -s --max-time 10 "https://api.telegram.org/bot${BOT_TOKEN}/getMe" || true)

        if echo "$me_resp" | grep -q '"ok":true'; then
            BOT_USERNAME=$(echo "$me_resp" | grep -o '"username":"[^"]*' | cut -d'"' -f4)
            BOT_NAME=$(echo "$me_resp" | grep -o '"first_name":"[^"]*' | cut -d'"' -f4)
            log "Токен подтвержден! Бот: ${BOLD}${BOT_NAME}${NC} (@${BOT_USERNAME})"
            break
        else
            warn "Telegram API отклонил токен (токен недействителен или Telegram заблокирован). Попробуйте еще раз."
        fi
    done

    echo ""

    # 2. Admin IDs
    while true; do
        echo -e "${CYAN}💡 Узнайте свой Telegram ID через бота @userinfobot (только цифры).${NC}"
        read -r -p "Введите Telegram ID администратора (или несколько через запятую): " ADMIN_RAW
        ADMIN_RAW="$(echo "$ADMIN_RAW" | tr -d " '\"[]")"

        if [[ -z "$ADMIN_RAW" ]]; then
            warn "ID администратора не может быть пустым."
            continue
        fi

        # Парсим числа
        local valid_ids=()
        IFS=',' read -ra ADDR <<< "$ADMIN_RAW"
        local parse_error=false
        for id_item in "${ADDR[@]}"; do
            local clean_id
            clean_id="$(echo "$id_item" | tr -d ' ')"
            if [[ "$clean_id" =~ ^[0-9]+$ ]] && [ "$clean_id" -gt 0 ]; then
                valid_ids+=("$clean_id")
            else
                warn "Некорректный ID: '$clean_id' (должно быть положительным числом)."
                parse_error=true
                break
            fi
        done

        if [[ "$parse_error" == "false" ]] && [ "${#valid_ids[@]}" -gt 0 ]; then
            # Собираем в JSON-массив
            ADMIN_IDS="[$(IFS=,; echo "${valid_ids[*]}") ]"
            # Форматируем компактно
            ADMIN_IDS="[$(echo "${valid_ids[*]}" | tr ' ' ',')]"
            log "Администраторы: $ADMIN_IDS"
            break
        fi
    done

    echo ""

    # 3. Support Username
    while true; do
        read -r -p "Введите Telegram username службы поддержки (например @just1k_support): " SUPPORT_USERNAME
        SUPPORT_USERNAME="$(echo "$SUPPORT_USERNAME" | tr -d " '@\"")"

        if [[ -z "$SUPPORT_USERNAME" ]]; then
            warn "Username поддержки не может быть пустым."
            continue
        fi

        if [[ "$SUPPORT_USERNAME" =~ ^[A-Za-z][A-Za-z0-9_]{0,31}$ ]] && [[ "$SUPPORT_USERNAME" != "support" ]]; then
            log "Контакт поддержки: @$SUPPORT_USERNAME"
            break
        else
            warn "Некорректный username. Должен начинаться с буквы и содержать от 1 до 32 символов (a-z, 0-9, _)."
        fi
    done

    echo ""

    # 4. Domain & DNS Check
    title "3/6. Настройка доменного имени и SSL"

    local server_ip
    server_ip=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')

    while true; do
        read -r -p "Введите ваш привязанный домен (например vpn.example.com): " DOMAIN
        DOMAIN="$(echo "$DOMAIN" | tr -d " '\"" | tr '[:upper:]' '[:lower:]')"

        if [[ ! "$DOMAIN" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; then
            warn "Некорректный формат домена: '$DOMAIN'."
            continue
        fi

        info "Проверка DNS A-записи для $DOMAIN..."
        local domain_ip=""
        if command -v dig >/dev/null 2>&1; then
            domain_ip=$(dig +short @8.8.8.8 "$DOMAIN" A 2>/dev/null | tail -1)
        elif command -v host >/dev/null 2>&1; then
            domain_ip=$(host -t A "$DOMAIN" 8.8.8.8 2>/dev/null | grep "has address" | awk '{print $NF}' | tail -1)
        elif command -v nslookup >/dev/null 2>&1; then
            domain_ip=$(nslookup "$DOMAIN" 8.8.8.8 2>/dev/null | grep -A1 "Name:" | grep "Address" | awk '{print $2}' | tail -1)
        fi

        if [[ -n "$domain_ip" ]] && [[ "$domain_ip" == "$server_ip" ]]; then
            log "DNS подтвержден: $DOMAIN -> $server_ip"
            break
        elif [[ -n "$domain_ip" ]]; then
            warn "DNS A-запись ($domain_ip) не совпадает с IP сервера ($server_ip)."
            echo -e "Если вы используете Cloudflare Proxy (CDN) или сервер находится за NAT, это нормально."
            read -r -p "Продолжить с этим доменом? (y/N): " confirm_domain
            if [[ "$confirm_domain" =~ ^[Yy]$ ]]; then
                break
            fi
        else
            warn "Не удалось получить A-запись для $DOMAIN (DNS ещё не обновился)."
            read -r -p "Продолжить без подтверждения DNS? (y/N): " confirm_domain
            if [[ "$confirm_domain" =~ ^[Yy]$ ]]; then
                break
            fi
        fi
    done

    echo ""

    # 5. SSL Email
    while true; do
        read -r -p "Введите ваш Email для сертификатов Let's Encrypt: " SSL_EMAIL
        SSL_EMAIL="$(echo "$SSL_EMAIL" | tr -d " '\"" | tr '[:upper:]' '[:lower:]')"

        if [[ "$SSL_EMAIL" =~ ^[^@\s]+@[^@\s]+\.[^@\s]+$ ]] && [[ "$SSL_EMAIL" != "owner@example.com" ]] && [[ "$SSL_EMAIL" != "admin@example.com" ]]; then
            log "Email для SSL: $SSL_EMAIL"
            break
        else
            warn "Введите настоящий рабочий email (не example.com)."
        fi
    done

    echo ""

    # 6. YooKassa Credentials
    title "4/6. Настройка платежной системы YooKassa"
    echo -e "${CYAN}💡 Данные можно получить в личном кабинете ЮKassa (Раздел «Интеграция» ➔ «Ключи API»).${NC}"

    while true; do
        read -r -p "Введите YooKassa Shop ID (число): " YOOKASSA_SHOP_ID
        YOOKASSA_SHOP_ID="$(echo "$YOOKASSA_SHOP_ID" | tr -d " '\"")"

        if [[ -n "$YOOKASSA_SHOP_ID" ]] && [[ ! "$YOOKASSA_SHOP_ID" =~ [Cc][Hh][Aa][Nn][Gg][Ee] ]]; then
            log "Shop ID: $YOOKASSA_SHOP_ID"
            break
        else
            warn "Shop ID обязателен для запуска бота."
        fi
    done

    while true; do
        read -r -s -p "Введите YooKassa Secret Key (live_... или test_...): " YOOKASSA_SECRET_KEY
        echo ""
        YOOKASSA_SECRET_KEY="$(echo "$YOOKASSA_SECRET_KEY" | tr -d " '\"")"

        if [[ -n "$YOOKASSA_SECRET_KEY" ]] && [[ ! "$YOOKASSA_SECRET_KEY" =~ [Cc][Hh][Aa][Nn][Gg][Ee] ]]; then
            log "Secret Key принят."
            break
        else
            warn "Secret Key обязателен для запуска бота."
        fi
    done

    YOOKASSA_RETURN_URL="https://t.me/${BOT_USERNAME:-{bot_username}}"
    YOOKASSA_WEBHOOK_PORT=8080
}

# --- Автоматическая генерация криптографических секретов ---
generate_secrets() {
    title "5/6. Автоматическая генерация криптографических ключей"

    log "Генерация 32-байтного ключа шифрования базы данных (DB_ENCRYPTION_KEY)..."
    DB_ENCRYPTION_KEY=$(openssl rand -base64 32)

    log "Генерация паролей для PostgreSQL и Redis..."
    POSTGRES_PASSWORD=$(openssl rand -hex 16)
    REDIS_PASSWORD=$(openssl rand -hex 16)
    POSTGRES_USER="just1kbot"
    POSTGRES_DB="just1kbot_bot"

    log "Генерация ключевой пары age для зашифрованных бэкапов БД..."
    AGE_KEY_FILE="${PROJECT_DIR}/backup_private_key.txt"
    age-keygen -o "$AGE_KEY_FILE" >/dev/null 2>&1
    chmod 600 "$AGE_KEY_FILE"

    BACKUP_AGE_RECIPIENT=$(sed -n 's/^# public key: //p' "$AGE_KEY_FILE")

    if [[ -z "$BACKUP_AGE_RECIPIENT" ]]; then
        error "Не удалось сгенерировать публичный ключ age."
    fi

    log "Публичный ключ бэкапов: $BACKUP_AGE_RECIPIENT"

    echo ""
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║                     🔐 ВАЖНО: ПРИВАТНЫЙ КЛЮЧ ДЛЯ БЭКАПОВ                     ║${NC}"
    echo -e "${YELLOW}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${YELLOW}║ Сохраните этот ключ на свой локальный ПК прямо сейчас!                      ║${NC}"
    echo -e "${YELLOW}║ Без него восстановить базу данных из зашифрованных бэкапов будет невозможно:║${NC}"
    echo -e "${YELLOW}║                                                                              ║${NC}"
    while IFS= read -r line; do
        echo -e "${BOLD}${GREEN}  $line${NC}"
    done < "$AGE_KEY_FILE"
    echo -e "${YELLOW}║                                                                              ║${NC}"
    echo -e "${YELLOW}║ Копия ключа временно сохранена в: ${BOLD}${AGE_KEY_FILE}${NC}${YELLOW}   ║${NC}"
    echo -e "${YELLOW}║ Скачайте этот файл к себе и удалите его с сервера!                           ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# --- Запись .env и регистрация утилит ---
save_configuration() {
    log "Создание файла .env..."

    cat > "${PROJECT_DIR}/.env" <<EOF
# ============================================================
# Just1kBot Production Configuration
# Сгенерировано автоматически: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# ============================================================

# ------------------------------------------------------------
# Telegram Bot
# ------------------------------------------------------------
BOT_TOKEN='${BOT_TOKEN}'
ADMIN_IDS='${ADMIN_IDS}'
SUPPORT_USERNAME='${SUPPORT_USERNAME}'

# ------------------------------------------------------------
# База данных PostgreSQL
# ------------------------------------------------------------
DATABASE_URL='postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}'
POSTGRES_USER='${POSTGRES_USER}'
POSTGRES_PASSWORD='${POSTGRES_PASSWORD}'
POSTGRES_DB='${POSTGRES_DB}'

# ------------------------------------------------------------
# Шифрование чувствительных данных в БД
# ------------------------------------------------------------
DB_ENCRYPTION_KEY='${DB_ENCRYPTION_KEY}'

# ------------------------------------------------------------
# Redis
# ------------------------------------------------------------
REDIS_URL='redis://:${REDIS_PASSWORD}@localhost:6379/0'
REDIS_PASSWORD='${REDIS_PASSWORD}'

# ------------------------------------------------------------
# YooKassa и внутренний баланс
# ------------------------------------------------------------
YOOKASSA_SHOP_ID='${YOOKASSA_SHOP_ID}'
YOOKASSA_SECRET_KEY='${YOOKASSA_SECRET_KEY}'
YOOKASSA_RETURN_URL='${YOOKASSA_RETURN_URL}'
YOOKASSA_WEBHOOK_PORT=${YOOKASSA_WEBHOOK_PORT}

# ------------------------------------------------------------
# Домен и сертификаты Let's Encrypt
# ------------------------------------------------------------
DOMAIN='${DOMAIN}'
SSL_EMAIL='${SSL_EMAIL}'

# ------------------------------------------------------------
# Зашифрованные бэкапы
# ------------------------------------------------------------
BACKUP_AGE_RECIPIENT='${BACKUP_AGE_RECIPIENT}'
BACKUP_REMOTE_URI=''
BACKUP_REMOTE_TOKEN=''

# ------------------------------------------------------------
# Лимиты пополнения баланса
# ------------------------------------------------------------
BALANCE_MIN_TOPUP_RUB=10
BALANCE_MAX_CUSTOM_TOPUP_RUB=5000
BALANCE_MAX_AVAILABLE_RUB=10000
BALANCE_MAX_PRESET_RUB=1000
BALANCE_MAX_UNFINISHED_TOPUPS=3
BALANCE_MAX_TOPUP_CREATIONS_24H=10
BALANCE_MAX_PRESET_OPTIONS=6

# ------------------------------------------------------------
# Безопасность
# ------------------------------------------------------------
ALLOW_LOCAL_HTTP=false
ALLOW_LOCAL_HTTPS=false
EOF

    chmod 600 "${PROJECT_DIR}/.env"
    log "Конфигурация успешно сохранена в ${PROJECT_DIR}/.env (chmod 600)."

    # Настройка cron для ночных бэкапов
    log "Настройка расписания автоматических бэкапов (cron)..."
    local cron_job="0 2 * * * flock -n /tmp/just1kbot-backup.lock sh -c 'cd ${PROJECT_DIR} && docker compose --profile tools run --rm backup >> ${PROJECT_DIR}/backups/backup.log 2>&1'"

    # Проверяем, есть ли уже такая запись
    if ! crontab -l 2>/dev/null | grep -Fq "just1kbot-backup.lock"; then
        (crontab -l 2>/dev/null || true; echo "$cron_job") | crontab -
        log "Cron задача для бэкапов в 02:00 успешно установлена."
    else
        log "Cron задача для бэкапов уже присутствует."
    fi

    # Установка исполняемых прав и регистрация CLI команды just1kbot
    chmod +x "${PROJECT_DIR}/scripts/cli.sh"
    chmod +x "${PROJECT_DIR}/scripts/docker/backup.sh"

    # Создаем глобальный скрипт-обертку /usr/local/bin/just1kbot
    cat > /usr/local/bin/just1kbot <<EOF
#!/bin/bash
export JUST1KBOT_DIR="${PROJECT_DIR}"
exec "${PROJECT_DIR}/scripts/cli.sh" "\$@"
EOF
    chmod 755 /usr/local/bin/just1kbot
    log "Команда 'just1kbot' зарегистрирована глобально в /usr/local/bin/just1kbot."
}

# --- Запуск Docker Compose и проверка работоспособности ---
start_project() {
    title "6/6. Сборка и запуск проекта в Docker"

    cd "$PROJECT_DIR"

    log "Запуск сборки контейнеров (docker compose up -d --build)..."
    docker compose up -d --build

    echo ""
    info "Ожидание готовности сервисов (healthcheck)..."

    local timeout=90
    local elapsed=0

    while [ "$elapsed" -lt "$timeout" ]; do
        local db_health
        local redis_health
        local bot_health
        local caddy_status

        db_health="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_db 2>/dev/null || echo starting)"
        redis_health="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_redis 2>/dev/null || echo starting)"
        bot_health="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_app 2>/dev/null || echo starting)"
        caddy_status="$(docker inspect --format='{{.State.Status}}' just1kbot_caddy 2>/dev/null || echo starting)"

        if [ "$db_health" = "healthy" ] && [ "$redis_health" = "healthy" ] && [ "$bot_health" = "healthy" ] && [ "$caddy_status" = "running" ]; then
            echo ""
            log "Все сервисы успешно запущены и находятся в статусе Healthy!"
            break
        fi

        # Проверка на падение контейнеров
        local failed_containers
        failed_containers=$(docker compose ps --status exited --format "{{.Service}}" 2>/dev/null || true)
        if [[ -n "$failed_containers" ]] && ! echo "$failed_containers" | grep -q "^migrate$"; then
            echo ""
            warn "Один или несколько контейнеров завершились с ошибкой: $failed_containers"
            docker compose ps
            echo ""
            docker compose logs --tail=30
            error "Запуск завершился ошибкой. Проверьте логи выше."
        fi

        printf "."
        sleep 3
        elapsed=$((elapsed + 3))
    done

    if [ "$elapsed" -ge "$timeout" ]; then
        echo ""
        warn "Таймаут ожидания статуса healthy. Текущее состояние контейнеров:"
        docker compose ps
    fi

    # Финальный баннер
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                       🎉 ПОЗДРАВЛЯЕМ! УСТАНОВКА ЗАВЕРШЕНА!                   ║${NC}"
    echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${GREEN}║                                                                              ║${NC}"
    echo -e "${GREEN}║  🤖 Бот активен в Telegram: ${BOLD}https://t.me/${BOT_USERNAME:-bot}${NC}${GREEN}                    ║${NC}"
    echo -e "${GREEN}║  🌐 Домен и Webhooks:      ${BOLD}https://${DOMAIN}${NC}${GREEN}                                ║${NC}"
    echo -e "${GREEN}║                                                                              ║${NC}"
    echo -e "${GREEN}║  💡 ДЛЯ УПРАВЛЕНИЯ БОТОМ ВВЕДИТЕ В ТЕРМИНАЛЕ:                                ║${NC}"
    echo -e "${GREEN}║     ${BOLD}${CYAN}just1kbot${NC}${GREEN}                                                                ║${NC}"
    echo -e "${GREEN}║                                                                              ║${NC}"
    echo -e "${GREEN}║  СЛЕДУЮЩИЕ ШАГИ:                                                             ║${NC}"
    echo -e "${GREEN}║  1. Откройте бота в Telegram и отправьте команду: ${BOLD}/admin${NC}${GREEN}                     ║${NC}"
    echo -e "${GREEN}║  2. Настройте ноду Amnezia API на вашем VPN-сервере                          ║${NC}"
    echo -e "${GREEN}║     (используйте скрипт ./setup-amnezia-api.sh на ноде)                      ║${NC}"
    echo -e "${GREEN}║  3. Добавьте сервер через Telegram: «Управление серверами» ➔ «Добавить»       ║${NC}"
    echo -e "${GREEN}║                                                                              ║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

main() {
    check_root
    install_dependencies
    run_interactive_wizard
    generate_secrets
    save_configuration
    start_project
}

main "$@"
