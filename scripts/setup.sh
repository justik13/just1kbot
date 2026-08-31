#!/bin/bash
# =============================================================================
# JUST1KBOT - Интерактивный установщик и мастер первичной настройки
# =============================================================================
#
# Быстрый запуск на сервере (Ubuntu 20.04/22.04/24.04 или Debian 11/12):
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

# --- Гарантия персистентного значения vm.overcommit_memory=1 ---
# Boot-time источник для systemd: /etc/sysctl.d/*.conf (sysctl.d(5)); файлы
# упорядочиваются лексикографически и более позднее имя побеждает, поэтому
# 99-just1kbot.conf перекрывает типовые дистрибутивные файлы (10-90).
# /etc/sysctl.conf нормализуется ДОПОЛНИТЕЛЬНО: его читает procps-ng
# (sysctl --system/-p, применяет последним) и старые сборки systemd —
# так stale `= 0` не сможет выиграть ни в одном из путей загрузки.
# Свертываются ВСЕ записи в одну `= 1`: наличие `= 1` не гарантирует
# ничего, если за ней следует `= 0` (sysctl применяет файл последовательно).
normalize_overcommit_file() {
    local file="$1"
    mkdir -p "$(dirname "$file")" 2>/dev/null || return 1
    if grep -Eq '^[[:space:]]*vm\.overcommit_memory[[:space:]]*=' "$file" 2>/dev/null; then
        sed -i -E '/^[[:space:]]*vm\.overcommit_memory[[:space:]]*=/d' "$file" || return 1
    fi
    echo "vm.overcommit_memory = 1" >> "$file" 2>/dev/null || return 1
    return 0
}

ensure_overcommit_persistence() {
    normalize_overcommit_file "${JUST1KBOT_SYSCTL_D_CONF:-/etc/sysctl.d/99-just1kbot.conf}" || return 1
    normalize_overcommit_file "${JUST1KBOT_SYSCTL_CONF:-/etc/sysctl.conf}" || return 1
    return 0
}

# --- Гарантия настройки vm.overcommit_memory=1 (runtime + persistence) ---
# Persistence проверяется и чинится ВСЕГДА, независимо от текущего runtime:
# сценарий «runtime=1 (например, установлен вручную до запуска установщика),
# persistent=0» иначе пережил бы установку и откатился после перезагрузки.
# Пути переопределяются для тестируемости:
#   JUST1KBOT_PROC_OVERCOMMIT - stub /proc/sys/vm/overcommit_memory
#   JUST1KBOT_SYSCTL_CONF     - stub /etc/sysctl.conf
configure_overcommit_memory() {
    local runtime_file="${JUST1KBOT_PROC_OVERCOMMIT:-/proc/sys/vm/overcommit_memory}"
    local conf_file="${JUST1KBOT_SYSCTL_CONF:-/etc/sysctl.conf}"
    [[ -f "$runtime_file" ]] || return 0

    local current_overcommit
    current_overcommit="$(cat "$runtime_file" 2>/dev/null || echo "0")"
    if [[ "$current_overcommit" != "1" ]]; then
        info "Включение vm.overcommit_memory=1 для стабильной работы Redis..."
        # Honest failure handling: a silent false positive here would let
        # Redis BGSAVE fail under memory pressure after install.
        if ! sysctl -w vm.overcommit_memory=1 >/dev/null 2>&1; then
            error "Не удалось применить 'sysctl -w vm.overcommit_memory=1' (проверьте права root и ограничения хоста). Настройте параметр вручную и запустите установщик снова."
        fi
    fi

    # Persistence must pin the VALUE 1 unconditionally: a pre-existing `= 0`
    # entry must be replaced, not merely detected, and this must happen even
    # when the runtime value is already 1.
    if ! ensure_overcommit_persistence "$conf_file"; then
        error "Не удалось закрепить vm.overcommit_memory=1 в персистентной конфигурации — настройка не переживёт перезагрузку. Добавьте её вручную и запустите установщик снова."
    fi

    # Belt-and-braces: runtime must be 1 after all of the above.
    current_overcommit="$(cat "$runtime_file" 2>/dev/null || echo "0")"
    if [[ "$current_overcommit" != "1" ]]; then
        error "vm.overcommit_memory=1 не применилось к runtime — проверьте 'sysctl -w vm.overcommit_memory=1' вручную и запустите установщик снова."
    fi

    log "Параметр vm.overcommit_memory=1 настроен (runtime + persistence)."
}

# --- Очистка временных ресурсов при сбое ---
cleanup_on_exit() {
    local exit_code=$?
    if (( exit_code != 0 )); then
        warn "Скрипт установки завершился с ошибкой (код $exit_code)."
        rm -f /tmp/get-docker.sh 2>/dev/null || true
    fi
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Проверка root прав ---
check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Установщик должен быть запущен с правами суперпользователя (root или sudo)."
    fi
}

# --- Проверка повторной установки (Idempotency) ---
check_existing_install() {
    if [[ -f "${PROJECT_DIR}/.env" ]]; then
        warn "Обнаружен существующий файл конфигурации ${PROJECT_DIR}/.env."
        read -r -p "Перезаписать конфигурацию и перенастроить проект заново? (y/N): " confirm_reinstall
        if [[ ! "$confirm_reinstall" =~ ^[Yy]$ ]]; then
            info "Установка прервана по запросу пользователя. Существующий .env сохранен."
            exit 0
        fi
        log "Будет создана новая конфигурация .env."
    fi
}

# --- Проверка занятости apt/dpkg блокировок ---
check_apt_locked() {
    local lock_files=(
        /var/lib/dpkg/lock-frontend
        /var/lib/dpkg/lock
        /var/lib/apt/lists/lock
        /var/cache/apt/archives/lock
    )
    if command -v fuser >/dev/null 2>&1; then
        if fuser "${lock_files[@]}" >/dev/null 2>&1; then
            return 0
        fi
    fi
    if command -v pgrep >/dev/null 2>&1; then
        if pgrep -f '(apt-get|dpkg|unattended-upgrade|apt\.systemd\.daily)' >/dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# --- Ожидание освобождения apt/dpkg блокировок ---
# shellcheck disable=SC2120
wait_for_apt_locks() {
    local max_wait="${1:-300}"
    local waited=0

    while check_apt_locked; do
        if (( waited == 0 )); then
            warn "apt/dpkg занят другим процессом (например, автоматическим обновлением). Ожидаем освобождения блокировки..."
        fi
        sleep 5
        waited=$((waited + 5))
        if (( waited >= max_wait )); then
            error "Не удалось дождаться освобождения apt/dpkg lock за ${max_wait} секунд. Попробуйте снова позже."
            # shellcheck disable=SC2317
            return 1
        fi
    done
}

# --- Проверка ОС и установка системных пакетов ---
install_dependencies() {
    title "1/6. Проверка окружения и зависимостей"

    if [[ ! -f /etc/os-release ]]; then
        error "Не найден файл /etc/os-release. Автоматическая установка поддерживает только Ubuntu и Debian."
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    local os_id="${ID:-}"
    local os_like="${ID_LIKE:-}"

    if [[ "$os_id" != "ubuntu" ]] && [[ "$os_id" != "debian" ]] && [[ "$os_like" != *"debian"* ]] && [[ "$os_like" != *"ubuntu"* ]]; then
        error "Автоматический установщик поддерживает только Ubuntu (20.04/22.04/24.04) и Debian (11/12). Ваша ОС: ${PRETTY_NAME:-$os_id}."
    fi

    log "Определена операционная система: ${PRETTY_NAME:-$os_id}"

    wait_for_apt_locks
    log "Обновление списков пакетов и установка системных утилит..."
    apt-get update -qq
    apt-get install -y -qq curl openssl age dnsutils cron git ca-certificates gnupg python3 psmisc >/dev/null 2>&1
    log "Системные утилиты установлены (curl, openssl, age, dnsutils, cron, git, python3, psmisc)."

    # Проверка / установка Docker
    if ! command -v docker >/dev/null 2>&1; then
        info "Docker не обнаружен. Начинаем автоматическую установку Docker Engine..."
        wait_for_apt_locks
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
        wait_for_apt_locks
        apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || {
            error "Не удалось установить docker-compose-plugin. Установите Docker Compose вручную."
        }
    fi
    log "Docker Compose готов к работе: $(docker compose version)"

    # Настройка брандмауэра UFW (если активен)
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        info "Брандмауэр UFW активен. Открытие портов 80/tcp, 443/tcp и 443/udp (QUIC/HTTP3) для Caddy..."
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        ufw allow 443/udp >/dev/null 2>&1 || true
        log "Порты 80/tcp, 443/tcp и 443/udp разрешены в UFW."
    fi

    # Настройка ядра для Redis (overcommit_memory)
    configure_overcommit_memory

    # Проверка занятости портов 80 и 443 сторонними процессами
    for port in 80 443; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} " || netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
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

    echo -e "Пожалуйста, введите параметры конфигурации бота.\n"

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

        if [[ "$SUPPORT_USERNAME" =~ ^[A-Za-z][A-Za-z0-9_]{0,31}$ ]] && [[ "$SUPPORT_USERNAME" != "support" ]] && [[ "$SUPPORT_USERNAME" != "change_me_support_username" ]]; then
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

        if [[ ! "$DOMAIN" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ ]]; then
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

        if [[ "$SSL_EMAIL" =~ ^[^@\s]+@[^@\s]+\.[^@\s]+$ ]] && [[ "$SSL_EMAIL" != "owner@example.com" ]] && [[ "$SSL_EMAIL" != "admin@example.com" ]] && [[ "$SSL_EMAIL" != "change_me@example.com" ]]; then
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

        if [[ "$YOOKASSA_SHOP_ID" =~ ^[0-9]+$ ]] && [ "$YOOKASSA_SHOP_ID" -gt 0 ]; then
            log "Shop ID: $YOOKASSA_SHOP_ID"
            break
        else
            warn "Shop ID должен быть положительным числом (например, 123456)."
        fi
    done

    while true; do
        read -r -s -p "Введите YooKassa Secret Key (live_... или test_...): " YOOKASSA_SECRET_KEY
        echo ""
        YOOKASSA_SECRET_KEY="$(echo "$YOOKASSA_SECRET_KEY" | tr -d " '\"")"

        if [[ -n "$YOOKASSA_SECRET_KEY" ]] && [[ "$YOOKASSA_SECRET_KEY" =~ ^(test_|live_)?[A-Za-z0-9_-]{16,}$ ]] && [[ ! "$YOOKASSA_SECRET_KEY" =~ [Cc][Hh][Aa][Nn][Gg][Ee] ]]; then
            log "Secret Key принят."
            break
        else
            warn "Некорректный Secret Key (должен содержать не менее 16 символов без пробелов)."
        fi
    done

    YOOKASSA_RETURN_URL='https://t.me/{bot_username}'
    YOOKASSA_WEBHOOK_PORT=8080
}

# --- Автоматическая генерация криптографических секретов ---
generate_secrets() {
    title "5/6. Автоматическая генерация криптографических ключей"

    log "Генерация 32-байтного URL-safe Fernet ключа шифрования БД (DB_ENCRYPTION_KEY)..."
    # Гарантируем URL-safe Base64 для Fernet через Python
    DB_ENCRYPTION_KEY=$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())" 2>/dev/null || openssl rand -base64 32 | tr '+/' '-_')

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
    echo -e "${YELLOW}║ Без него расшифровать бэкапы базы данных в случае аварии будет невозможно:   ║${NC}"
    echo -e "${YELLOW}║                                                                              ║${NC}"
    while IFS= read -r line; do
        echo -e "${BOLD}${GREEN}  $line${NC}"
    done < "$AGE_KEY_FILE"
    echo -e "${YELLOW}║                                                                              ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    read -r -p "Вы скопировали приватный ключ? Удалить файл backup_private_key.txt с сервера в целях безопасности? (Y/n): " confirm_delete_key
    if [[ ! "$confirm_delete_key" =~ ^[Nn]$ ]]; then
        rm -f "$AGE_KEY_FILE"
        log "Локальный файл приватного ключа удален с сервера."
    else
        warn "Файл приватного ключа сохранен в: $AGE_KEY_FILE (chmod 600). Обязательно скачайте и удалите его вручную!"
        warn "Политика проекта: приватный ключ age НЕ должен храниться на production-сервере."
        warn "Файл исключён из git и Docker-образа (.gitignore/.dockerignore), но правильное решение — перенести ключ на локальный ПК и удалить его здесь."
    fi
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
# При запуске в Docker Compose параметры хостов автоматически
# переопределяются на db:5432 и redis:6379 скриптом docker-entrypoint.sh.
# ------------------------------------------------------------
DATABASE_URL='postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}'
POSTGRES_USER='${POSTGRES_USER}'
POSTGRES_PASSWORD='${POSTGRES_PASSWORD}'
POSTGRES_DB='${POSTGRES_DB}'

# ------------------------------------------------------------
# Шифрование чувствительных данных в БД (URL-safe Fernet)
# ------------------------------------------------------------
DB_ENCRYPTION_KEY='${DB_ENCRYPTION_KEY}'
TRUSTED_PROXIES='127.0.0.1,::1,172.16.0.0/12'

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
    local success=false

    while [ "$elapsed" -lt "$timeout" ]; do
        local db_health
        local redis_health
        local bot_health
        local caddy_status
        local migrate_state

        db_health="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_db 2>/dev/null || echo starting)"
        redis_health="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_redis 2>/dev/null || echo starting)"
        bot_health="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_app 2>/dev/null || echo starting)"
        caddy_status="$(docker inspect --format='{{.State.Status}}' just1kbot_caddy 2>/dev/null || echo starting)"
        migrate_state="$(docker inspect --format='{{.State.Status}}/{{.State.ExitCode}}' just1kbot_migrate 2>/dev/null || echo missing)"

        # Проверка на ошибку миграции
        if [[ "$migrate_state" == "exited/"* ]] && [[ "$migrate_state" != "exited/0" ]]; then
            echo ""
            error "Сервис миграций (migrate) завершился с ошибкой: $migrate_state. Проверьте: docker compose logs migrate"
        fi

        # Проверка на падение постоянных сервисов
        if [ "$db_health" = "unhealthy" ] || [ "$redis_health" = "unhealthy" ] || [ "$bot_health" = "unhealthy" ] || [ "$caddy_status" = "exited" ] || [ "$caddy_status" = "dead" ]; then
            echo ""
            docker compose ps
            echo ""
            docker compose logs --tail=50
            error "Один из ключевых сервисов завершился аварийно или не прошел healthcheck."
        fi

        if [ "$db_health" = "healthy" ] && [ "$redis_health" = "healthy" ] && [ "$bot_health" = "healthy" ] && [ "$caddy_status" = "running" ]; then
            echo ""
            log "Все сервисы успешно запущены и находятся в статусе Healthy!"
            success=true
            break
        fi

        printf "."
        sleep 3
        elapsed=$((elapsed + 3))
    done

    if [ "$success" = "false" ]; then
        echo ""
        docker compose ps
        echo ""
        docker compose logs --tail=50
        error "Таймаут ожидания перехода сервисов в статус healthy. Проверьте логи выше."
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
    check_existing_install
    install_dependencies
    run_interactive_wizard
    generate_secrets
    save_configuration
    start_project
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
