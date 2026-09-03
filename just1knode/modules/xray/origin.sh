#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Установка Origin Узла (modules/xray/origin.sh)
# =============================================================================

install_xray_origin_node() {
    title "УСТАНОВКА ORIGIN УЗЛА (Белый Интернет — Входной шлюз в РФ)"
    check_root
    init_state_dir
    install_base_deps

    local domain="${1:-}"
    local email="${2:-}"
    local api_key="${3:-}"
    local secret_path="${4:-}"
    local bot_ip="${5:-${BOT_IP:-}}"
    local cdn_domain="${6:-}"

    # Интерактивный опросник параметров
    if [[ -z "$domain" ]]; then
        read -rp "Введите домен Origin-сервера (например: origin.example.com): " domain || true
    fi
    if [[ -z "$domain" ]]; then error "Домен не может быть пустым."; fi

    if [[ -z "$cdn_domain" ]]; then
        if [[ $# -eq 0 ]]; then
            read -rp "Введите домен Yandex Cloud CDN (CNAME) [по умолчанию: ${domain}]: " input_cdn || true
            cdn_domain="${input_cdn:-$domain}"
        else
            cdn_domain="$domain"
        fi
    fi

    if [[ -z "$email" ]]; then
        read -rp "Введите Email для SSL Let's Encrypt [по умолчанию: admin@${domain}]: " email || true
        email="${email:-admin@${domain}}"
    fi
    if [[ -z "$email" ]]; then error "Email не может быть пустым."; fi

    if [[ -z "$bot_ip" ]]; then
        read -rp "Введите IP-адрес Telegram-бота (для защиты порта 8444): " bot_ip || true
    fi
    if [[ -z "$bot_ip" ]]; then
        error "BOT_IP обязателен для безопасной настройки порта 8444."
    fi

    if [[ -z "$secret_path" ]]; then
        local existing_secret_path
        existing_secret_path="$(get_state_val "secret_base_path" 2>/dev/null || true)"
        if [[ -n "$existing_secret_path" ]]; then
            secret_path="$existing_secret_path"
            info "Используется существующий префикс пути XHTTP: $secret_path"
        else
            local rnd_hex
            rnd_hex="$(python3 -c "import secrets; print(secrets.token_hex(8))")"
            read -rp "Секретный префикс пути XHTTP [по умолчанию: /w_${rnd_hex}]: " input_path || true
            secret_path="${input_path:-/w_${rnd_hex}}"
        fi
    fi

    if [[ -z "$api_key" ]]; then
        local existing_api_key
        existing_api_key="$(get_state_val "api_key" 2>/dev/null || true)"
        if [[ -n "$existing_api_key" ]]; then
            api_key="$existing_api_key"
            info "Используется существующий API-ключ узла."
        else
            api_key="$(python3 -c "import secrets; print(secrets.token_hex(32))")"
        fi
    fi

    apt-get install -y -qq nginx certbot python3-certbot-nginx
    mkdir -p "${CERTBOT_DIR}" "${NGINX_CONF_DIR}/conf.d"
    chmod 755 "${CERTBOT_DIR}" 2>/dev/null || true
    configure_safe_ufw "80/tcp"

    # Bootstrap HTTP block для ACME challenge в Nginx
    cat > "${NGINX_CONF_DIR}/conf.d/just1k-bootstrap.conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    location ^~ /.well-known/acme-challenge/ {
        root ${CERTBOT_DIR};
        default_type "text/plain";
    }
}
EOF
    rm -f "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
    systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true

    obtain_ssl_certificate "$domain" "$email"
    rm -f "${NGINX_CONF_DIR}/conf.d/just1k-bootstrap.conf" 2>/dev/null || true

    install_xray_binaries
    create_backup "$XRAY_CONFIG"

    log "Формирование конфигурации Xray Origin (Surgical Merge)..."
    python3 -c "
import sys, json, os

config_file = sys.argv[1]
secret_path = sys.argv[2]

existing = {}
if os.path.exists(config_file):
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        existing = {}

inbounds = [
    ib for ib in existing.get('inbounds', [])
    if ib.get('tag') not in ('just1k-wl-api-grpc', 'just1k-wl-default')
]
outbounds = [
    ob for ob in existing.get('outbounds', [])
    if ob.get('tag') not in ('just1k-wl-direct', 'just1k-wl-block')
]
existing_rules = existing.get('routing', {}).get('rules', [])
rules = [
    r for r in existing_rules
    if not (
        (isinstance(r.get('inboundTag'), list) and any(t in ('just1k-wl-api-grpc', 'just1k-wl-default') for t in r.get('inboundTag')))
        or r.get('outboundTag') == 'just1k-wl-api'
    )
]

inbounds.insert(0, {
    'tag': 'just1k-wl-api-grpc',
    'listen': '127.0.0.1',
    'port': 10085,
    'protocol': 'dokodemo-door',
    'settings': {'address': '127.0.0.1'}
})

inbounds.append({
    'tag': 'just1k-wl-default',
    'listen': '127.0.0.1',
    'port': 8003,
    'protocol': 'vless',
    'settings': {'clients': [], 'decryption': 'none'},
    'streamSettings': {
        'network': 'xhttp',
        'xhttpSettings': {
            'mode': 'packet-up',
            'path': f'{secret_path}/default',
            'xPaddingObfsMode': True,
            'xPaddingKey': 'dc',
            'xPaddingHeader': 'X-Cache',
            'xPaddingMethod': 'tokenish',
            'xPaddingPlacement': 'queryInHeader'
        }
    },
    'sniffing': {'enabled': True, 'destOverride': ['tls', 'http']}
})

if not any(ob.get('tag') == 'just1k-wl-direct' for ob in outbounds):
    outbounds.append({
        'tag': 'just1k-wl-direct',
        'protocol': 'freedom',
        'settings': {'domainStrategy': 'UseIP'}
    })

if not any(ob.get('tag') == 'just1k-wl-block' for ob in outbounds):
    outbounds.append({
        'tag': 'just1k-wl-block',
        'protocol': 'blackhole',
        'settings': {'response': {'type': 'none'}}
    })

rules.append({
    'type': 'field',
    'inboundTag': ['just1k-wl-api-grpc'],
    'outboundTag': 'just1k-wl-api'
})

# Split-Routing: прямой выход в Рунет с московского IP Origin-сервера
# Split-Routing: прямой выход в Рунет с московского IP Origin-сервера
rules.append({
    'type': 'field',
    'inboundTag': ['just1k-wl-default', 'just1k-wl-inbounds'],
    'domain': [
        'geosite:category-ru',
        'geosite:tld-ru',
        'domain:ru',
        'domain:su',
        'domain:xn--p1ai'
    ],
    'outboundTag': 'just1k-wl-direct'
})
rules.append({
    'type': 'field',
    'inboundTag': ['just1k-wl-default', 'just1k-wl-inbounds'],
    'ip': ['geoip:ru'],
    'outboundTag': 'just1k-wl-direct'
})

# Standalone Origin: блокировать весь зарубежный трафик клиентов до подключения зарубежного Relay
rules.append({
    'type': 'field',
    'inboundTag': ['just1k-wl-default'],
    'outboundTag': 'just1k-wl-block'
})

final_config = dict(existing)
api_conf = dict(existing.get('api', {}))
api_conf['tag'] = 'just1k-wl-api'
api_conf['services'] = list(set(api_conf.get('services', []) + ['HandlerService', 'StatsService']))
final_config['api'] = api_conf

stats_conf = dict(existing.get('stats', {}))
final_config['stats'] = stats_conf

policy_conf = dict(existing.get('policy', {}))
policy_levels = dict(policy_conf.get('levels', {}))
level_0 = dict(policy_levels.get('0', {}))
level_0['statsUserUplink'] = True
level_0['statsUserDownlink'] = True
policy_levels['0'] = level_0
policy_conf['levels'] = policy_levels
final_config['policy'] = policy_conf

final_config['inbounds'] = inbounds
final_config['outbounds'] = outbounds
final_config['routing'] = existing.get('routing', {})
final_config['routing']['domainStrategy'] = existing.get('routing', {}).get('domainStrategy', 'IPIfNonMatch')
final_config['routing']['rules'] = rules

with open(config_file, 'w', encoding='utf-8') as f:
    json.dump(final_config, f, indent=2)
" "$XRAY_CONFIG" "$secret_path"

    chown root:xrayapi "$XRAY_CONFIG" 2>/dev/null || true
    chmod 640 "$XRAY_CONFIG"

    deploy_xray_systemd_service
    systemctl restart xray

    deploy_xray_api_service "$api_key" "$cdn_domain"

    log "Настройка Nginx (OPTIONS->POST, Zero-Buffering, CDN Ingress)..."
    mkdir -p "${NGINX_CONF_DIR}/conf.d" "${NGINX_CONF_DIR}/sites-available" "${NGINX_CONF_DIR}/sites-enabled" "${CERTBOT_DIR}" "${WWW_HTML_DIR}" "$NGINX_RELAYS_DIR"
    deploy_camouflage_site
    deploy_certbot_renewal_hook

    cat > "${NGINX_CONF_DIR}/conf.d/xhttp-map.conf" <<EOF
map \$request_method \$xhttp_proxy_method {
    OPTIONS POST;
    default \$request_method;
}
EOF

    create_backup "${NGINX_RELAYS_DIR}/default.conf"
    cat > "${NGINX_RELAYS_DIR}/default.conf" <<EOF
    location ^~ ${secret_path}/default {
        proxy_pass http://127.0.0.1:8003;
        proxy_method \$xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass_request_headers on;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        client_max_body_size 0;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF

    local server_name_str="${domain}"
    if [[ -n "$cdn_domain" && "$cdn_domain" != "$domain" ]]; then
        server_name_str="${domain} ${cdn_domain}"
    fi

    cat > "${NGINX_CONF_DIR}/sites-available/just1k-origin.conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${server_name_str};

    location ^~ /.well-known/acme-challenge/ {
        root ${CERTBOT_DIR};
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

# 1. CDN Ingress (Порт 443 — Входящий трафик от клиентов через CDN)
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${server_name_str};

    ssl_certificate /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 0;
    client_body_buffer_size 128k;
    large_client_header_buffers 8 64k;

    location = /cdn-check {
        add_header Content-Type text/plain;
        return 204;
    }

    include ${NGINX_RELAYS_DIR}/*.conf;

    location / {
        root ${WWW_HTML_DIR};
        index index.html index.htm;
        try_files \$uri \$uri/ =404;
    }
}

# 2. Management API (Порт 8444 — Управление нодой для бота)
server {
    listen 8444 ssl http2;
    listen [::]:8444 ssl http2;
    server_name ${domain};

    ssl_certificate /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

    ln -sf "${NGINX_CONF_DIR}/sites-available/just1k-origin.conf" "${NGINX_CONF_DIR}/sites-enabled/"
    nginx -t && systemctl reload nginx

    # Фаервол: порт 8444 открывается СТРОГО для BOT_IP
    configure_safe_ufw "80/tcp" "443/tcp"
    ufw delete allow 8444/tcp 2>/dev/null || true
    ufw delete allow 8444 2>/dev/null || true
    if [[ -n "$bot_ip" && "$bot_ip" != "any" && "$bot_ip" != "0.0.0.0/0" ]]; then
        ufw allow from "$bot_ip" to any port 8444 proto tcp || true
    else
        ufw allow 8444/tcp || true
        warn "BOT_IP не указан. Порт 8444 открыт для всех IP."
    fi

    set_state_val "role" "origin"
    set_state_val "domain" "$domain"
    set_state_val "cdn_domain" "$cdn_domain"
    set_state_val "bot_ip" "$bot_ip"
    set_state_val "secret_base_path" "$secret_path"
    set_state_val "api_url" "https://${domain}:8444"
    set_state_val "api_key" "$api_key"

    title "УСТАНОВКА ORIGIN УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Данные для добавления Origin в Telegram-боте (/admin):${NC}"
    echo -e "  🌐 Origin Домен:      ${CYAN}${domain}${NC}"
    echo -e "  ☁️ CDN Домен:         ${CYAN}${cdn_domain}${NC}"
    echo -e "  🔗 API URL бота:      ${CYAN}https://${domain}:8444${NC}"
    echo -e "  🤖 BOT IP:            ${CYAN}${bot_ip}${NC}"
    echo -e "  🔑 API Ключ:          ${YELLOW}${api_key}${NC}"
    echo -e "  🛡️ Секретный префикс: ${MAGENTA}${secret_path}${NC}"
    echo -e "  🩺 Проверка CDN:      curl -X OPTIONS https://${cdn_domain}/cdn-check\n"
}
