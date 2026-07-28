#!/bin/bash
# =============================================================================
# JUST1KBOT - Настройка Amnezia API с Nginx + SSL
# =============================================================================
# Использование:
#   sudo ./setup-amnezia-api.sh --domain api.example.com --email admin@example.com
#   sudo ./setup-amnezia-api.sh --domain api.example.com --email admin@example.com --port 9443
#   sudo ./setup-amnezia-api.sh --uninstall
#
# Что делает:
#   1. Проверяет что Amnezia API запущен на 127.0.0.1:4001
#   2. Устанавливает и настраивает Nginx как reverse proxy
#   3. Получает SSL сертификат от Let's Encrypt
#   4. Настраивает HTTPS на порту 8443 (или указанном)
#
# Результат:
#   Amnezia API доступен по https://domain:8443
#   Внутренний API остаётся на 127.0.0.1:4001 (недоступен извне)
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

# --- Константы ---
AMNEZIA_PORT=4001
DEFAULT_PUBLIC_PORT=8443
LOG_FILE="/var/log/just1kbot-amnezia-setup.log"

# --- Цвета ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Логирование ---
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${GREEN}[$ts]${NC} $1"
    echo "[$ts] $1" >> "$LOG_FILE"
}

warn() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${YELLOW}[$ts] ВНИМАНИЕ:${NC} $1"
    echo "[$ts] WARNING: $1" >> "$LOG_FILE"
}

error() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${RED}[$ts] ОШИБКА:${NC} $1" >&2
    echo "[$ts] ERROR: $1" >> "$LOG_FILE"
    exit 1
}

info() {
    echo -e "${BLUE}$1${NC}"
}

# --- Проверка root ---
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Ошибка: запустите с sudo.${NC}"
    exit 1
fi

# --- Аргументы ---
DOMAIN=""
EMAIL=""
PUBLIC_PORT="$DEFAULT_PUBLIC_PORT"
UNINSTALL=false

print_help() {
    cat <<EOF
Использование: sudo $0 [OPTIONS]

Опции:
  --domain DOMAIN    Домен для SSL (обязательно)
  --email EMAIL      Email для Let's Encrypt (обязательно)
  --port PORT        Публичный HTTPS порт (по умолчанию: $DEFAULT_PUBLIC_PORT)
  --uninstall        Удалить конфигурацию Nginx и сертификат
  -h, --help         Показать справку

Пример:
  sudo $0 --domain api.myvpn.com --email admin@myvpn.com
  sudo $0 --domain api.myvpn.com --email admin@myvpn.com --port 9443
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --domain)
                [[ $# -ge 2 ]] || error "Для --domain требуется значение"
                DOMAIN="$2"
                shift 2
                ;;
            --email)
                [[ $# -ge 2 ]] || error "Для --email требуется значение"
                EMAIL="$2"
                shift 2
                ;;
            --port)
                [[ $# -ge 2 ]] || error "Для --port требуется значение"
                PUBLIC_PORT="$2"
                shift 2
                ;;
            --uninstall)
                UNINSTALL=true
                shift
                ;;
            -h|--help)
                print_help
                exit 0
                ;;
            *)
                echo -e "${RED}Неизвестный аргумент: $1${NC}"
                print_help
                exit 1
                ;;
        esac
    done
}

# --- Uninstall ---
do_uninstall() {
    info "=== Удаление конфигурации Amnezia API Nginx ==="
    echo ""

    # Определяем домен из существующего конфига
    local conf_file=""
    for f in /etc/nginx/sites-available/just1kbot-amnezia-*; do
        if [[ -f "$f" ]]; then
            conf_file="$f"
            break
        fi
    done

    if [[ -z "$conf_file" ]]; then
        warn "Конфигурация не найдена. Нечего удалять."
        exit 0
    fi

    local domain_name
    domain_name=$(basename "$conf_file" | sed 's/just1kbot-amnezia-//')

    read -p "Удалить конфигурацию для $domain_name? (yes/N): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Отменено."
        exit 0
    fi

    log "Удаление конфигурации для $domain_name..."

    # Удаляем symlink и конфиг
    rm -f "/etc/nginx/sites-enabled/just1kbot-amnezia-${domain_name}"
    rm -f "$conf_file"

    # Удаляем сертификат
    certbot delete --cert-name "$domain_name" --non-interactive 2>/dev/null || true

    # Перезапускаем nginx
    if nginx -t 2>/dev/null; then
        systemctl reload nginx
    fi

    log "Конфигурация удалена"
    echo ""
    info "Amnezia API продолжает работать на 127.0.0.1:$AMNEZIA_PORT"
    exit 0
}

# --- Проверки ---
check_prerequisites() {
    log "Проверка prerequisites..."

    # Валидация аргументов
    if [[ -z "$DOMAIN" ]]; then
        error "Укажите --domain. Используйте --help для справки."
    fi
    if [[ -z "$EMAIL" ]]; then
        error "Укажите --email. Используйте --help для справки."
    fi

    # Валидация домена
    if [[ ! "$DOMAIN" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$ ]]; then
        error "Некорректный домен: $DOMAIN"
    fi
    if [[ ! "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
        error "Некорректный email: $EMAIL"
    fi

    # Валидация порта
    if [[ ! "$PUBLIC_PORT" =~ ^[0-9]+$ ]] || [[ "$PUBLIC_PORT" -lt 1 ]] || [[ "$PUBLIC_PORT" -gt 65535 ]]; then
        error "Некорректный порт: $PUBLIC_PORT (должен быть 1-65535)"
    fi

    # Проверка Amnezia API
    log "Проверка Amnezia API на 127.0.0.1:$AMNEZIA_PORT..."
    if ! curl -s --max-time 5 "http://127.0.0.1:${AMNEZIA_PORT}/health" > /dev/null 2>&1; then
        if ! curl -s --max-time 5 "http://127.0.0.1:${AMNEZIA_PORT}/" > /dev/null 2>&1; then
            error "Amnezia API не отвечает на 127.0.0.1:$AMNEZIA_PORT. Запустите API перед настройкой."
        fi
    fi
    log "Amnezia API — OK"

    # Проверка DNS
    log "Проверка DNS для $DOMAIN..."
    local server_ip
    server_ip=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')

    local domain_ip=""
    if command -v dig &>/dev/null; then
        domain_ip=$(dig +short "$DOMAIN" A 2>/dev/null | tail -1)
    elif command -v host &>/dev/null; then
        domain_ip=$(host -t A "$DOMAIN" 2>/dev/null | grep "has address" | awk '{print $NF}' | tail -1)
    elif command -v nslookup &>/dev/null; then
        domain_ip=$(nslookup "$DOMAIN" 2>/dev/null | grep -A1 "Name:" | grep "Address" | awk '{print $2}' | tail -1)
    fi

    if [[ -z "$domain_ip" ]]; then
        error "Не удалось резолвить $DOMAIN. Создайте A-запись: $DOMAIN → $server_ip"
    fi

    if [[ "$domain_ip" != "$server_ip" ]]; then
        error "DNS mismatch: $DOMAIN → $domain_ip, но IP сервера: $server_ip. Обновите A-запись."
    fi
    log "DNS — OK ($DOMAIN → $server_ip)"

    # Проверка занятости порта
    if ss -tlnp 2>/dev/null | grep -q ":${PUBLIC_PORT} " || \
       netstat -tlnp 2>/dev/null | grep -q ":${PUBLIC_PORT} "; then
        warn "Порт $PUBLIC_PORT уже занят."
        read -p "Продолжить? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 0
        fi
    fi

    # Проверка UFW
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "active"; then
        if ! ufw status 2>/dev/null | grep -q "$PUBLIC_PORT"; then
            warn "UFW активен, но порт $PUBLIC_PORT не открыт. Открываю..."
            ufw allow "$PUBLIC_PORT/tcp" > /dev/null 2>&1
            log "UFW: порт $PUBLIC_PORT открыт"
        fi
        if ! ufw status 2>/dev/null | grep -q "80"; then
            ufw allow 80/tcp > /dev/null 2>&1
            log "UFW: порт 80 открыт (для certbot)"
        fi
    fi

    log "Все проверки пройдены"
}

# --- Установка пакетов ---
install_packages() {
    log "Установка Nginx и Certbot..."
    apt-get update -qq
    apt-get install -y -qq nginx certbot python3-certbot-nginx dnsutils > /dev/null 2>&1
    systemctl enable nginx > /dev/null 2>&1
    log "Пакеты установлены"
}

# --- Конфигурация Nginx ---
setup_nginx() {
    log "Настройка Nginx reverse proxy..."

    local conf_name="just1kbot-amnezia-${DOMAIN}"
    local conf_path="/etc/nginx/sites-available/${conf_name}"

    # Предупреждение о существующем конфиге
    if [[ -f "$conf_path" ]]; then
        warn "Конфигурация уже существует: $conf_path"
        read -p "Перезаписать? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log "Оставлена существующая конфигурация"
            return
        fi
    fi

    # Rate limiting zone
    if ! grep -q "just1kbot_amnezia_api" /etc/nginx/nginx.conf 2>/dev/null; then
        sed -i '/http {/a \    limit_req_zone $binary_remote_addr zone=just1kbot_amnezia_api:10m rate=30r/s;' /etc/nginx/nginx.conf
    fi

    # HTTP конфиг (для certbot validation)
    cat > "$conf_path" <<EOF
# Just1kBot - Amnezia API Reverse Proxy
# Domain: $DOMAIN
# Port: $PUBLIC_PORT -> 127.0.0.1:$AMNEZIA_PORT
# Generated: $(date -Iseconds)

server {
    listen 80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host:$PUBLIC_PORT\$request_uri;
    }
}
EOF

    ln -sf "$conf_path" "/etc/nginx/sites-enabled/${conf_name}"
    mkdir -p /var/www/certbot

    nginx -t 2>> "$LOG_FILE"
    systemctl reload nginx

    log "Nginx HTTP конфиг создан"
}

# --- SSL сертификат ---
setup_ssl() {
    log "Получение SSL сертификата для $DOMAIN..."

    # Проверяем существующий сертификат
    if certbot certificates 2>/dev/null | grep -q "$DOMAIN"; then
        warn "Сертификат для $DOMAIN уже существует. Обновляю..."
        certbot renew --cert-name "$DOMAIN" --quiet 2>> "$LOG_FILE" || true
    fi

    if certbot --nginx \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        --domains "$DOMAIN" \
        --redirect \
        2>> "$LOG_FILE"; then
        log "SSL сертификат получен/обновлён"
    else
        error "Не удалось получить сертификат. Проверьте DNS, firewall и доступность порта 80."
    fi

    # Автообновление
    systemctl enable certbot.timer > /dev/null 2>&1
    systemctl start certbot.timer 2>/dev/null || true
}

# --- Финальный HTTPS конфиг ---
setup_https_config() {
    log "Настройка HTTPS конфига..."

    local conf_name="just1kbot-amnezia-${DOMAIN}"
    local conf_path="/etc/nginx/sites-available/${conf_name}"

    cat > "$conf_path" <<EOF
# Just1kBot - Amnezia API Reverse Proxy (HTTPS)
# Domain: $DOMAIN
# Port: $PUBLIC_PORT -> 127.0.0.1:$AMNEZIA_PORT
# Generated: $(date -Iseconds)

# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host:$PUBLIC_PORT\$request_uri;
    }
}

# HTTPS
server {
    listen $PUBLIC_PORT ssl http2;
    server_name $DOMAIN;

    # SSL
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # OCSP Stapling
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 8.8.8.8 8.8.4.4 valid=300s;
    resolver_timeout 5s;

    # Security headers
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Rate limiting
    limit_req zone=just1kbot_amnezia_api burst=50 nodelay;
    limit_req_status 429;

    # Proxy to Amnezia API
    location / {
        proxy_pass http://127.0.0.1:$AMNEZIA_PORT;
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Request-ID \$request_id;

        # Timeouts
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;

        # WebSocket support
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Скрытие служебных эндпоинтов
    location ~ ^/(docs|redoc|openapi.json|metrics) {
        deny all;
        return 404;
    }

    # Healthcheck
    location = /health {
        proxy_pass http://127.0.0.1:$AMNEZIA_PORT/health;
        access_log off;
    }

    # Логи
    access_log /var/log/nginx/amnezia-api-access.log;
    error_log /var/log/nginx/amnezia-api-error.log;
}
EOF

    nginx -t 2>> "$LOG_FILE"
    systemctl reload nginx

    log "HTTPS конфиг применён"
}

# --- Итоговый вывод ---
print_result() {
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  AMNEZIA API — НАСТРОЙКА ЗАВЕРШЕНА${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo "  Публичный URL:   https://$DOMAIN:$PUBLIC_PORT"
    echo "  Внутренний:      http://127.0.0.1:$AMNEZIA_PORT"
    echo "  Healthcheck:     https://$DOMAIN:$PUBLIC_PORT/health"
    echo ""
    echo "  Конфиг:          /etc/nginx/sites-available/just1kbot-amnezia-$DOMAIN"
    echo "  Сертификат:      /etc/letsencrypt/live/$DOMAIN/"
    echo "  Логи:            /var/log/nginx/amnezia-api-*.log"
    echo ""
    echo "  Обновление .env бота:"
    echo "    AMNEZIA_API_URL=https://$DOMAIN:$PUBLIC_PORT"
    echo ""
    echo "  Удаление:"
    echo "    sudo $0 --uninstall"
    echo ""
}

# --- Main ---
main() {
    parse_args "$@"

    # Обработка --uninstall
    if [[ "$UNINSTALL" == true ]]; then
        do_uninstall
        exit 0
    fi

    echo ""
    info "=== Настройка Amnezia API: Nginx + SSL ==="
    echo ""

    check_prerequisites
    install_packages
    setup_nginx
    setup_ssl
    setup_https_config
    print_result
}

main "$@"
