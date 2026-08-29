#!/bin/bash
# =============================================================================
# JUST1KBOT - Настройка Amnezia API с Nginx + SSL
# =============================================================================
#
# ПРИМЕЧАНИЕ: Этот скрипт НЕ является частью Telegram-бота just1kbot.
# Это автономная утилита для быстрого развёртывания VPN-ноды (сервера).
# Запускается вручную на каждом VPN-сервере один раз при его настройке.
# К коду бота (docker-compose.yml, bot/, services/ и т.д.) отношения не имеет.
#
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
ALLOW_IP=""
UNINSTALL=false

print_help() {
    cat <<EOF
Использование: sudo $0 [OPTIONS]

Опции:
  --domain DOMAIN        Домен для SSL (обязательно)
  --email EMAIL          Email для Let's Encrypt (обязательно)
  --port PORT            Публичный HTTPS порт (по умолчанию: $DEFAULT_PUBLIC_PORT)
  --allow-ip IP_OR_CIDR  Ограничить доступ к порту в UFW только для IP бота (рекомендуется)
  --uninstall            Удалить конфигурацию Nginx и сертификат
  -h, --help             Показать справку

Пример:
  sudo $0 --domain api.myvpn.com --email admin@myvpn.com
  sudo $0 --domain api.myvpn.com --email admin@myvpn.com --port 9443 --allow-ip 198.51.100.10
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
            --allow-ip|--allow-from-ip)
                [[ $# -ge 2 ]] || error "Для --allow-ip требуется значение IP или CIDR"
                ALLOW_IP="$2"
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

    read -r -p "Удалить конфигурацию для $domain_name? (yes/N): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Отменено."
        exit 0
    fi

    log "Удаление конфигурации для $domain_name..."

    # Удаляем symlink и конфиг
    rm -f "/etc/nginx/sites-enabled/just1kbot-amnezia-${domain_name}"
    rm -f "$conf_file"

    # Удаляем rate limit только если не осталось других активных сайтов just1kbot-amnezia
    if ! ls /etc/nginx/sites-available/just1kbot-amnezia-* >/dev/null 2>&1; then
        rm -f /etc/nginx/conf.d/just1kbot_amnezia_api_limit.conf
    fi

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
        warn "Не удалось резолвить $DOMAIN. Убедитесь, что A-запись существует: $DOMAIN → $server_ip"
    elif [[ "$domain_ip" != "$server_ip" ]]; then
        warn "DNS mismatch: $DOMAIN → $domain_ip, IP сервера (предположительно): $server_ip. Если сервер за NAT или используется прокси (Cloudflare), это предупреждение можно игнорировать."
    else
        log "DNS — OK ($DOMAIN → $server_ip)"
    fi

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

    # Проверка и настройка UFW
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "active"; then
        if [[ -n "$ALLOW_IP" ]]; then
            local is_valid_ip=false
            if command -v python3 &>/dev/null; then
                if python3 -c "import ipaddress, sys; ipaddress.ip_network(sys.argv[1], strict=False)" "$ALLOW_IP" 2>/dev/null; then
                    is_valid_ip=true
                fi
            else
                local ip_regex='^[0-9a-fA-F.:]+(/[0-9]{1,3})?$'
                if [[ $ALLOW_IP =~ $ip_regex ]]; then
                    is_valid_ip=true
                fi
            fi

            if [[ "$is_valid_ip" != "true" ]]; then
                error "Невалидный IP-адрес или CIDR подсеть для --allow-ip: $ALLOW_IP"
            fi
            warn "UFW активен. Ограничиваю доступ к порту $PUBLIC_PORT только с $ALLOW_IP..."
            # Удаляем открытые широкие правила для этого порта (IPv4 и IPv6)
            ufw delete allow "$PUBLIC_PORT/tcp" >/dev/null 2>&1 || true
            ufw delete allow "$PUBLIC_PORT" >/dev/null 2>&1 || true
            ufw delete allow proto tcp from any to any port "$PUBLIC_PORT" >/dev/null 2>&1 || true
            if ! ufw allow from "$ALLOW_IP" to any port "$PUBLIC_PORT" proto tcp >/dev/null 2>&1; then
                error "Не удалось применить правило UFW для $ALLOW_IP на порту $PUBLIC_PORT"
            fi
            # Проверяем, что не осталось широких правил для порта
            if ufw status 2>/dev/null | grep -E "${PUBLIC_PORT}(/tcp)?\s+ALLOW\s+(Anywhere|0.0.0.0/0|::/0)" >/dev/null 2>&1; then
                warn "Обнаружены дополнительные широкие правила для $PUBLIC_PORT в UFW, удаляю..."
                ufw delete allow "$PUBLIC_PORT/tcp" >/dev/null 2>&1 || true
                ufw delete allow "$PUBLIC_PORT" >/dev/null 2>&1 || true
            fi
            log "UFW: порт $PUBLIC_PORT настроен для $ALLOW_IP"
        else
            warn "UFW активен. Открываю порт $PUBLIC_PORT для всех..."
            if ! ufw allow "$PUBLIC_PORT/tcp" >/dev/null 2>&1; then
                error "Не удалось открыть порт $PUBLIC_PORT в UFW"
            fi
            log "UFW: порт $PUBLIC_PORT открыт"
        fi
        if ! ufw status 2>/dev/null | grep -q "80"; then
            ufw allow 80/tcp > /dev/null 2>&1 || true
            log "UFW: порт 80 открыт (для certbot)"
        fi
    fi

    log "Все проверки пройдены"
}

# --- Установка пакетов ---
install_packages() {
    log "Установка Nginx, Certbot и утилит..."
    apt-get update -qq
    apt-get install -y -qq nginx certbot python3-certbot-nginx dnsutils curl > /dev/null 2>&1
    systemctl enable nginx > /dev/null 2>&1
    systemctl start nginx > /dev/null 2>&1 || true
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

    # Очищаем возможные дублирующие зоны из главного nginx.conf
    sed -i '/limit_req_zone.*just1kbot_amnezia_api/d' /etc/nginx/nginx.conf 2>/dev/null || true

    # Rate limiting zone (в отдельный файл)
    local rate_limit_conf="/etc/nginx/conf.d/just1kbot_amnezia_api_limit.conf"
    # shellcheck disable=SC2016
    echo 'limit_req_zone $binary_remote_addr zone=just1kbot_amnezia_api:10m rate=30r/s;' > "$rate_limit_conf"

    local redirect_url="https://\$host:${PUBLIC_PORT}\$request_uri"
    if [[ "$PUBLIC_PORT" == "443" ]]; then
        redirect_url="https://\$host\$request_uri"
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
        return 301 $redirect_url;
    }
}
EOF

    ln -sf "$conf_path" "/etc/nginx/sites-enabled/${conf_name}"
    mkdir -p /var/www/certbot

    nginx -t 2>> "$LOG_FILE"
    systemctl reload nginx 2>/dev/null || systemctl restart nginx

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

    # Используем certonly --webroot чтобы certbot не лез изменять наши nginx конфиги
    if certbot certonly --webroot -w /var/www/certbot \
        --deploy-hook "systemctl reload nginx" \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        --cert-name "$DOMAIN" \
        -d "$DOMAIN" \
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

    local redirect_url="https://\$host:${PUBLIC_PORT}\$request_uri"
    if [[ "$PUBLIC_PORT" == "443" ]]; then
        redirect_url="https://\$host\$request_uri"
    fi

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
        return 301 $redirect_url;
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
    ssl_prefer_server_ciphers on;
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

        # WebSocket support (conditional)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$http_upgrade;
    }

    # Скрытие служебных эндпоинтов
    location ~ ^/(docs|redoc|openapi.json|metrics) {
        deny all;
        return 404;
    }

    # Healthcheck
    location = /health {
        proxy_pass http://127.0.0.1:$AMNEZIA_PORT;
        access_log off;
    }

    # Логи
    access_log /var/log/nginx/amnezia-api-access.log;
    error_log /var/log/nginx/amnezia-api-error.log;
}
EOF

    nginx -t 2>> "$LOG_FILE"
    systemctl reload nginx 2>/dev/null || systemctl restart nginx

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
    echo "  Добавление сервера в бот:"
    echo "    1. Откройте бота и выполните /admin"
    echo "    2. Перейдите в '🖥 Серверы' -> '➕ Добавить сервер'"
    echo "    3. Укажите URL: https://$DOMAIN:$PUBLIC_PORT и ваш API-ключ"
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
    fi

    echo ""
    info "=== Настройка Amnezia API: Nginx + SSL ==="
    echo ""

    install_packages
    check_prerequisites
    setup_nginx
    setup_ssl
    setup_https_config
    print_result
}

main "$@"
