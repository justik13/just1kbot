#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Установка Origin Узла (modules/xray/origin.sh)
# =============================================================================

normalize_domain() {
    local raw="${1:-}"
    raw="$(echo "$raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's|^https\?://||' -e 's|/.*$||')"
    echo "$raw"
}

validate_fqdn() {
    local host="${1:-}"
    [[ -z "$host" ]] && return 1
    # Reject forbidden characters: spaces, control characters, quotes, semicolons, dollar, backticks, braces, slashes
    if [[ "$host" =~ [[:space:]\;\"\'\$\`\{\}\\\<\>\?\&\*\/] ]]; then
        return 1
    fi
    # Reject localhost or pure IPv4
    if [[ "$host" == "localhost" || "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        return 1
    fi
    # Must match valid FQDN structure: labels separated by dots, only alphanumeric and hyphen
    if [[ ! "$host" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$ ]]; then
        return 1
    fi
    return 0
}

validate_sub_prefix() {
    local prefix="${1:-}"
    [[ -z "$prefix" ]] && return 1
    # Reject forbidden characters or .. (path traversal)
    if [[ "$prefix" =~ [[:space:]\;\"\'\$\`\{\}\\\<\>\?\&\*] || "$prefix" =~ \.\. ]]; then
        return 1
    fi
    # Must start with / and contain only safe URI path characters
    if [[ ! "$prefix" =~ ^/[a-zA-Z0-9_/-]+$ ]]; then
        return 1
    fi
    return 0
}

deploy_subscription_proxy_conf() {
    local target_host="${1:-}"
    local custom_prefix="${2:-}"
    if [[ -z "$target_host" ]]; then
        target_host="$(get_state_val "bot_domain" 2>/dev/null || true)"
    fi
    if [[ -z "$target_host" ]]; then
        target_host="${BOT_DOMAIN:-}"
    fi
    target_host="$(normalize_domain "$target_host")"

    if ! validate_fqdn "$target_host"; then
        if [[ -t 0 ]]; then
            warn "Указан недопустимый хост для проксирования подписок: '${target_host:-<пусто>}' (требуется FQDN с валидным TLS)."
            read -rp "Введите домен Telegram-бота (например: just1k.best): " prompt_target || true
            target_host="$(normalize_domain "$prompt_target")"
        fi
    fi

    if ! validate_fqdn "$target_host"; then
        error "Для настройки безопасного Nginx-проксирования подписок требуется валидный домен бота (BOT_DOMAIN FQDN). Указан недопустимый хост: '${target_host:-<пусто>}'. Проксирование HTTPS-upstream по IP без проверки TLS запрещено."
    fi

    set_state_val "bot_domain" "$target_host" 2>/dev/null || true

    local sub_prefix="${custom_prefix:-}"
    if [[ -z "$sub_prefix" ]]; then
        sub_prefix="$(get_state_val "sub_path_prefix" 2>/dev/null || true)"
    fi
    if [[ -z "$sub_prefix" ]]; then
        sub_prefix="${WHITE_INTERNET_SUB_PATH_PREFIX:-/sub/wl}"
    fi
    sub_prefix="${sub_prefix%/}"
    [[ ! "$sub_prefix" =~ ^/ ]] && sub_prefix="/$sub_prefix"

    if ! validate_sub_prefix "$sub_prefix"; then
        error "Указан недопустимый префикс пути подписок: '$sub_prefix' (должен начинаться с / и содержать только безопасные URI-символы)."
    fi

    set_state_val "sub_path_prefix" "$sub_prefix" 2>/dev/null || true

    mkdir -p "$NGINX_RELAYS_DIR"
    create_backup "${NGINX_RELAYS_DIR}/sub-wl.conf"
    cat > "${NGINX_RELAYS_DIR}/sub-wl.conf" <<EOF
    location ^~ ${sub_prefix} {
        resolver 1.1.1.1 8.8.8.8 77.88.8.8 valid=30s ipv6=off;
        set \$bot_upstream "https://${target_host}";
        proxy_pass \$bot_upstream;
        proxy_ssl_server_name on;
        proxy_ssl_name ${target_host};
        proxy_ssl_verify on;
        proxy_ssl_verify_depth 5;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
        proxy_set_header Host ${target_host};
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }
EOF
    info "Сконфигурировано Nginx-проксирование подписок (${sub_prefix} -> dynamic resolver -> https://${target_host} [TLS verified])"
}

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
    local bot_domain="${7:-${BOT_DOMAIN:-}}"

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

    if [[ -z "$bot_domain" ]]; then
        local existing_bot_domain
        existing_bot_domain="$(get_state_val "bot_domain" 2>/dev/null || true)"
        if [[ -n "$existing_bot_domain" && ! "$existing_bot_domain" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$existing_bot_domain" != "localhost" ]]; then
            bot_domain="$existing_bot_domain"
        else
            if [[ -n "$existing_bot_domain" ]]; then
                warn "Обнаружено устаревшее значение bot_domain в state.json (IP: '$existing_bot_domain'). Для защищенного TLS-проксирования требуется FQDN."
            fi
            read -rp "Введите домен Telegram-бота (например: just1k.best): " input_bot_domain || true
            bot_domain="${input_bot_domain:-}"
        fi
    fi

    bot_domain="$(normalize_domain "$bot_domain")"

    if [[ -z "$bot_domain" ]]; then
        error "BOT_DOMAIN обязателен для настройки безопасного проксирования подписок."
    fi

    if ! validate_fqdn "$bot_domain"; then
        error "BOT_DOMAIN должен быть доменным именем (FQDN) с доверенным SSL-сертификатом, а не IP-адресом или недопустимым хостом: '$bot_domain'"
    fi

    if [[ -n "$bot_domain" ]]; then
        info "Проверка связи с ботом через эндпоинт https://${bot_domain}/health..."
        local health_code
        health_code="$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" "https://${bot_domain}/health" 2>/dev/null || echo "000")"
        if [[ "$health_code" == "200" ]]; then
            log "Эндпоинт бота https://${bot_domain}/health доступен (HTTP 200)."
        else
            warn "Эндпоинт бота https://${bot_domain}/health вернул код: $health_code (или недоступен). Проверьте DNS, валидность TLS-сертификата (редиректы запрещены) и статус бота."
        fi
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

    apt-get install -y -qq nginx certbot python3-certbot-nginx ca-certificates
    mkdir -p "${CERTBOT_DIR}" "${NGINX_CONF_DIR}/conf.d"
    chmod 755 "${CERTBOT_DIR}" 2>/dev/null || true
    configure_safe_ufw "80/tcp"

    # Проверка наличия существующих сайтов до настройки Nginx
    local pre_existing_sites=()
    while IFS= read -r s; do
        [[ -n "$s" ]] && pre_existing_sites+=("$s")
    done < <(detect_existing_nginx_sites 2>/dev/null || true)
    if [[ ${#pre_existing_sites[@]} -gt 0 ]]; then
        info "В системном Nginx обнаружены существующие сайты:"
        for s in "${pre_existing_sites[@]}"; do
            echo -e "    ${BOLD}• $s${NC}"
        done
        log "Настройка Origin выполняется без остановки Nginx и с сохранением всех существующих сайтов."
    fi

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
    local default_was_linked=0
    if [[ -f "${NGINX_CONF_DIR}/sites-enabled/default" ]]; then
        default_was_linked=1
        if grep -Eq '(^|[[:space:]])server_name[[:space:]]+[^_;]' "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null; then
            warn "Файл ${NGINX_CONF_DIR}/sites-enabled/default содержит пользовательские домены. Создаём резервную копию default.user.bak в sites-available."
            cp -a "${NGINX_CONF_DIR}/sites-enabled/default" "${NGINX_CONF_DIR}/sites-available/default.user.bak"
        fi
        rm -f "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
    fi
    if ! nginx -t 2>/dev/null; then
        warn "Ошибка синтаксиса Nginx (nginx -t) с bootstrap-конфигурацией. Откат изменений..."
        rm -f "${NGINX_CONF_DIR}/conf.d/just1k-bootstrap.conf" 2>/dev/null || true
        if [[ $default_was_linked -eq 1 ]]; then
            if [[ -f "${NGINX_CONF_DIR}/sites-available/default.user.bak" ]]; then
                cp -a "${NGINX_CONF_DIR}/sites-available/default.user.bak" "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
            elif [[ -f "${NGINX_CONF_DIR}/sites-available/default" ]]; then
                ln -sf "${NGINX_CONF_DIR}/sites-available/default" "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
            fi
        fi
        error "Ошибка синтаксиса Nginx (nginx -t) до применения сертификата. Установка Origin прервана."
    fi
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
    'sniffing': {'enabled': True, 'destOverride': ['tls', 'http', 'quic'], 'routeOnly': False}
})

if not any(ob.get('tag') == 'just1k-wl-direct' for ob in outbounds):
    outbounds.append({
        'tag': 'just1k-wl-direct',
        'protocol': 'freedom',
        'settings': {'domainStrategy': 'UseIPv4'}
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
rules.append({
    'type': 'field',
    'inboundTag': ['just1k-wl-default', 'just1k-wl-inbounds'],
    'domain': [
        'geosite:category-ru',
        'geosite:tld-ru',
        'domain:ru',
        'domain:su',
        'domain:xn--p1ai',
        'domain:2ip.ru'
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

dns_conf = dict(existing.get('dns', {}))
dns_conf['servers'] = [
    {
        'address': '77.88.8.8',
        'port': 53,
        'domains': [
            'geosite:category-ru',
            'geosite:tld-ru',
            'domain:ru',
            'domain:su',
            'domain:xn--p1ai',
            'domain:2ip.ru'
        ],
        'skipFallback': True
    },
    '1.1.1.1',
    'localhost'
]
dns_conf['queryStrategy'] = 'UseIPv4'
final_config['dns'] = dns_conf

final_config['inbounds'] = inbounds
final_config['outbounds'] = outbounds
final_config['routing'] = existing.get('routing', {})
final_config['routing']['domainStrategy'] = existing.get('routing', {}).get('domainStrategy', 'IPIfNonMatch')
final_config['routing']['rules'] = rules

with open(config_file, 'w', encoding='utf-8') as f:
    json.dump(final_config, f, indent=2)
" "$XRAY_CONFIG" "$secret_path"

    chown root:xrayapi "$XRAY_CONFIG" 2>/dev/null || true
    chmod 640 "$XRAY_CONFIG" 2>/dev/null || true
    chmod 755 "$(dirname "$XRAY_CONFIG")" 2>/dev/null || true

    if [[ $EUID -eq 0 ]]; then
        mkdir -p /etc/sysctl.d 2>/dev/null || true
        cat > /etc/sysctl.d/99-disable-ipv6.conf <<EOF 2>/dev/null || true
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
        sysctl -p /etc/sysctl.d/99-disable-ipv6.conf >/dev/null 2>&1 || true
    fi

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

    deploy_subscription_proxy_conf "$bot_domain"

    local server_name_str="${domain}"
    if [[ -n "$cdn_domain" && "$cdn_domain" != "$domain" ]]; then
        server_name_str="${domain} ${cdn_domain}"
    fi

    local existing_sites=()
    while IFS= read -r s; do
        [[ -n "$s" ]] && existing_sites+=("$s")
    done < <(detect_existing_nginx_sites 2>/dev/null || true)
    if [[ ${#existing_sites[@]} -gt 0 ]]; then
        info "В системном Nginx обнаружены существующие сайты:"
        for s in "${existing_sites[@]}"; do
            echo -e "    ${BOLD}• $s${NC}"
        done
        log "Настройка Origin узла выполняется в изолированном виртуальном хосте (just1k-origin.conf). Ваши существующие сайты продолжат работать параллельно."
    fi

    rm -f "${NGINX_CONF_DIR}/conf.d/just1k-bootstrap.conf" "${NGINX_CONF_DIR}/conf.d/just1k-origin.conf" "${NGINX_CONF_DIR}/conf.d/origin.conf" 2>/dev/null || true
    local default_was_linked_origin=0
    if [[ -f "${NGINX_CONF_DIR}/sites-enabled/default" ]]; then
        default_was_linked_origin=1
        if grep -Eq '(^|[[:space:]])server_name[[:space:]]+[^_;]' "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null; then
            warn "Файл ${NGINX_CONF_DIR}/sites-enabled/default содержит пользовательские домены. Создаём резервную копию default.user.bak в sites-available."
            cp -a "${NGINX_CONF_DIR}/sites-enabled/default" "${NGINX_CONF_DIR}/sites-available/default.user.bak"
        fi
        rm -f "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
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
    if ! nginx -t 2>/dev/null; then
        rm -f "${NGINX_CONF_DIR}/sites-enabled/just1k-origin.conf" 2>/dev/null || true
        if [[ -f "${NGINX_CONF_DIR}/sites-available/default.user.bak" ]]; then
            cp -a "${NGINX_CONF_DIR}/sites-available/default.user.bak" "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
        elif [[ $default_was_linked_origin -eq 1 && -f "${NGINX_CONF_DIR}/sites-available/default" && ! -f "${NGINX_CONF_DIR}/sites-enabled/default" ]]; then
            ln -sf "${NGINX_CONF_DIR}/sites-available/default" "${NGINX_CONF_DIR}/sites-enabled/default" 2>/dev/null || true
        fi
        error "Ошибка валидации синтаксиса Nginx (nginx -t)! Перезагрузка Nginx отменена для сохранения доступности работающих сайтов."
    fi
    systemctl reload nginx

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
    set_state_val "bot_domain" "$bot_domain"
    set_state_val "secret_base_path" "$secret_path"
    set_state_val "api_url" "https://${domain}:8444"
    set_state_val "api_key" "$api_key"

    title "УСТАНОВКА ORIGIN УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Данные для добавления Origin в Telegram-боте (/admin):${NC}"
    echo -e "  🌐 Origin Домен:      ${CYAN}${domain}${NC}"
    echo -e "  ☁️ CDN Домен:         ${CYAN}${cdn_domain}${NC}"
    echo -e "  🔗 API URL бота:      ${CYAN}https://${domain}:8444${NC}"
    echo -e "  🤖 BOT IP:            ${CYAN}${bot_ip}${NC}"
    echo -e "  🤖 BOT Домен:         ${CYAN}${bot_domain}${NC}"
    echo -e "  🔑 API Ключ:          ${YELLOW}${api_key}${NC}"
    echo -e "  🛡️ Секретный префикс: ${MAGENTA}${secret_path}${NC}"
    echo -e "  🩺 Проверка CDN:      curl -X OPTIONS https://${cdn_domain}/cdn-check\n"
}

heal_and_update_origin_config() {
    title "АВТОМАТИЧЕСКАЯ ОПТИМИЗАЦИЯ И ВОССТАНОВЛЕНИЕ КОНФИГУРАЦИИ ORIGIN"
    check_root
    init_state_dir
    acquire_just1knode_lock

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Функция доступна только на Origin-узле (текущая роль: ${role:-не установлена})."
    fi

    log "Проверка и исправление параметров ядра Xray Origin (Desired-State Reconciliation)..."
    if [[ ! -f "$XRAY_CONFIG" ]]; then
        error "Файл конфигурации Xray не найден: $XRAY_CONFIG"
    fi

    create_backup "$XRAY_CONFIG"
    manifest_begin
    auto_heal_relays_registry

    if ! python3 -c "
import json, os, sys, tempfile

cfg_file = sys.argv[1]
relays_file = sys.argv[2]
state_file = sys.argv[3]

with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

# Читаем зарегистрированные релеи
relays = []
if os.path.exists(relays_file):
    try:
        with open(relays_file, 'r', encoding='utf-8') as rf:
            relays = json.load(rf)
    except:
        relays = []

# Читаем базовый путь из state.json
secret_base = '/stream'
if os.path.exists(state_file):
    try:
        with open(state_file, 'r', encoding='utf-8') as sf:
            s_data = json.load(sf)
            secret_base = s_data.get('secret_base_path', '/stream')
    except:
        secret_base = '/stream'

# 1. OUTBOUNDS: гарантия наличия и параметров
outbounds = cfg.setdefault('outbounds', [])

# 1.1. just1k-wl-direct (freedom, UseIPv4)
direct_ob = next((ob for ob in outbounds if ob.get('tag') == 'just1k-wl-direct'), None)
if not direct_ob:
    direct_ob = {'tag': 'just1k-wl-direct', 'protocol': 'freedom', 'settings': {}}
    outbounds.insert(0, direct_ob)
direct_ob.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'

# 1.2. Принудительный UseIPv4 на всех freedom outbounds
for ob in outbounds:
    if ob.get('protocol') == 'freedom':
        ob.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'

# 1.3. just1k-wl-block (blackhole)
if not any(ob.get('tag') == 'just1k-wl-block' for ob in outbounds):
    outbounds.append({
        'tag': 'just1k-wl-block',
        'protocol': 'blackhole',
        'settings': {'response': {'type': 'none'}}
    })

# 1.4. just1k-wl-api (blackhole)
if not any(ob.get('tag') == 'just1k-wl-api' for ob in outbounds):
    outbounds.append({
        'tag': 'just1k-wl-api',
        'protocol': 'blackhole'
    })

# 2. INBOUNDS: гарантия наличия базовых инбаундов и правильный sniffing
inbounds = cfg.setdefault('inbounds', [])

# 2.1. just1k-wl-api-grpc (локальный gRPC API)
api_ib = next((ib for ib in inbounds if ib.get('tag') == 'just1k-wl-api-grpc'), None)
if not api_ib:
    api_ib = {
        'tag': 'just1k-wl-api-grpc',
        'listen': '127.0.0.1',
        'port': 10085,
        'protocol': 'dokodemo-door',
        'settings': {'address': '127.0.0.1'}
    }
    inbounds.append(api_ib)
# ИСКЛЮЧАЕМ sniffing с внутреннего API
api_ib.pop('sniffing', None)

# 2.2. just1k-wl-default (основной клиентский инбаунд)
def_ib = next((ib for ib in inbounds if ib.get('tag') == 'just1k-wl-default'), None)
if not def_ib:
    def_ib = {
        'tag': 'just1k-wl-default',
        'listen': '127.0.0.1',
        'port': 8003,
        'protocol': 'vless',
        'settings': {'clients': [], 'decryption': 'none'},
        'streamSettings': {
            'network': 'xhttp',
            'xhttpSettings': {
                'mode': 'packet-up',
                'path': secret_base + '/default',
                'xPaddingObfsMode': True,
                'xPaddingKey': 'dc',
                'xPaddingHeader': 'X-Cache',
                'xPaddingMethod': 'tokenish',
                'xPaddingPlacement': 'queryInHeader'
            }
        }
    }
    inbounds.append(def_ib)

# 2.3. Sniffing ТОЛЬКО для клиентских инбаундов (default + релейные инбаунды)
for ib in inbounds:
    tag = ib.get('tag', '')
    if tag == 'just1k-wl-api-grpc':
        ib.pop('sniffing', None)
    elif tag == 'just1k-wl-default' or tag.startswith('just1k-wl-inbound-'):
        ib['sniffing'] = {
            'enabled': True,
            'destOverride': ['tls', 'http', 'quic'],
            'routeOnly': False
        }

# 3. DNS: Split-DNS с UseIPv4 и skipFallback для доменов РФ
cfg['dns'] = {
    'servers': [
        {
            'address': '77.88.8.8',
            'port': 53,
            'domains': [
                'geosite:category-ru',
                'geosite:tld-ru',
                'domain:ru',
                'domain:su',
                'domain:xn--p1ai',
                'domain:2ip.ru'
            ],
            'skipFallback': True
        },
        '1.1.1.1',
        'localhost'
    ],
    'queryStrategy': 'UseIPv4'
}

# 4. ROUTING: Восстановление инвариантов маршрутизации
cfg.setdefault('routing', {})['domainStrategy'] = 'IPIfNonMatch'
rules = cfg.setdefault('routing', {}).setdefault('rules', [])

# Собираем актуальные теги всех входящих подключений клиентов
known_client_inbounds = [
    ib.get('tag') for ib in inbounds
    if ib.get('tag') == 'just1k-wl-default' or ib.get('tag', '').startswith('just1k-wl-inbound-')
]

# 4.1. Правило API
if not any(r.get('outboundTag') == 'just1k-wl-api' for r in rules):
    rules.insert(0, {
        'type': 'field',
        'inboundTag': ['just1k-wl-api-grpc'],
        'outboundTag': 'just1k-wl-api'
    })

# 4.2. Правило Direct для доменов РФ
ru_domains = [
    'geosite:category-ru',
    'geosite:tld-ru',
    'domain:ru',
    'domain:su',
    'domain:xn--p1ai',
    'domain:2ip.ru'
]
dom_rule = next((r for r in rules if r.get('outboundTag') == 'just1k-wl-direct' and 'domain' in r), None)
if not dom_rule:
    dom_rule = {
        'type': 'field',
        'inboundTag': list(known_client_inbounds),
        'domain': ru_domains,
        'outboundTag': 'just1k-wl-direct'
    }
    rules.insert(1, dom_rule)
else:
    dom_rule['domain'] = list(dict.fromkeys(dom_rule.get('domain', []) + ru_domains))
    curr_ib = dom_rule.get('inboundTag', [])
    dom_rule['inboundTag'] = list(dict.fromkeys((curr_ib if isinstance(curr_ib, list) else [curr_ib]) + known_client_inbounds))

# 4.3. Правило Direct для IP РФ (geoip:ru)
ip_rule = next((r for r in rules if r.get('outboundTag') == 'just1k-wl-direct' and 'ip' in r), None)
if not ip_rule:
    ip_rule = {
        'type': 'field',
        'inboundTag': list(known_client_inbounds),
        'ip': ['geoip:ru'],
        'outboundTag': 'just1k-wl-direct'
    }
    dom_idx = rules.index(dom_rule)
    rules.insert(dom_idx + 1, ip_rule)
else:
    if 'geoip:ru' not in ip_rule.get('ip', []):
        ip_rule.setdefault('ip', []).append('geoip:ru')
    curr_ib = ip_rule.get('inboundTag', [])
    ip_rule['inboundTag'] = list(dict.fromkeys((curr_ib if isinstance(curr_ib, list) else [curr_ib]) + known_client_inbounds))

# 4.4. Дефолтное правило для just1k-wl-default
def_rule = next((r for r in rules if r.get('inboundTag') == ['just1k-wl-default'] and 'domain' not in r and 'ip' not in r), None)
first_relay_tag = ('just1k-wl-outbound-' + str(relays[0]['code'])) if relays and relays[0].get('code') else 'just1k-wl-block'
if not def_rule:
    rules.append({
        'type': 'field',
        'inboundTag': ['just1k-wl-default'],
        'outboundTag': first_relay_tag
    })
else:
    curr_out = def_rule.get('outboundTag')
    if not any(ob.get('tag') == curr_out for ob in outbounds):
        def_rule['outboundTag'] = first_relay_tag

# 4.5. Правила маршрутизации для каждого индивидуального релея
for r in relays:
    if not isinstance(r, dict): continue
    code = r.get('code')
    if not code: continue
    in_tag = 'just1k-wl-inbound-' + str(code)
    out_tag = 'just1k-wl-outbound-' + str(code)
    if not any(rl.get('inboundTag') == [in_tag] and rl.get('outboundTag') == out_tag for rl in rules):
        rules.append({
            'type': 'field',
            'inboundTag': [in_tag],
            'outboundTag': out_tag
        })

# Атомарное сохранение конфигурации Xray
d = os.path.dirname(os.path.abspath(cfg_file))
os.makedirs(d, exist_ok=True)
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(t_path, cfg_file)
try:
    import shutil
    shutil.chown(cfg_file, user='root', group='xrayapi')
    os.chmod(cfg_file, 0o640)
    os.chmod(d, 0o755)
except Exception:
    pass

print('[+] Xray Origin config успешно согласован с эталоном (Desired-State Reconciliation)')
" "$XRAY_CONFIG" "$RELAYS_FILE" "${STATE_DIR}/state.json"; then
        manifest_rollback
        error "Ошибка выполнения Python-скрипта реконсиляции Origin."
    fi

    chown root:xrayapi "$XRAY_CONFIG" 2>/dev/null || true
    chmod 640 "$XRAY_CONFIG" 2>/dev/null || true
    chmod 755 "$(dirname "$XRAY_CONFIG")" 2>/dev/null || true

    # Авто-восстановление Nginx location файлов для всех релеев
    if [[ -f "$RELAYS_FILE" ]]; then
        mkdir -p "$NGINX_RELAYS_DIR"
        python3 -c "
import json, sys, os
rf, nginx_dir = sys.argv[1], sys.argv[2]
try:
    with open(rf, 'r', encoding='utf-8') as f:
        relays = json.load(f)
    for r in relays:
        if not isinstance(r, dict): continue
        code, path = r.get('code'), r.get('path')
        port = r.get('inbound_port') or r.get('port')
        if not code or not path or not port: continue
        cf_path = os.path.join(nginx_dir, f'{code}.conf')
        if not os.path.exists(cf_path):
            with open(cf_path, 'w', encoding='utf-8') as cf:
                cf.write(f'''location ^~ {path} {{
    proxy_pass http://127.0.0.1:{port};
    proxy_method \$xhttp_proxy_method;
    proxy_http_version 1.1;
    proxy_set_header Connection \"\";
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
}}
''')
            print(f'[+] Восстановлен Nginx конфиг для релея {code}')
except Exception:
    pass
" "$RELAYS_FILE" "$NGINX_RELAYS_DIR" 2>/dev/null || true
    fi

    # Авто-восстановление Nginx-проксирования подписок Белого Интернета
    local heal_bot_domain
    heal_bot_domain="$(get_state_val "bot_domain" 2>/dev/null || true)"
    local env_bot_domain
    env_bot_domain="$(normalize_domain "${BOT_DOMAIN:-}")"

    # Если в BOT_DOMAIN передан валидный FQDN — используем его в приоритете (override оператора)
    if validate_fqdn "$env_bot_domain"; then
        heal_bot_domain="$env_bot_domain"
    fi

    heal_bot_domain="$(normalize_domain "$heal_bot_domain")"
    if validate_fqdn "$heal_bot_domain"; then
        deploy_subscription_proxy_conf "$heal_bot_domain"
    else
        if [[ -n "$heal_bot_domain" && "$heal_bot_domain" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            warn "Внимание: в state.json обнаружен устаревший bot_domain в виде IP ($heal_bot_domain). Проксирование подписок с TLS требует FQDN. Авто-восстановление sub-wl.conf пропущено (укажите BOT_DOMAIN для обновления)."
        else
            warn "BOT_DOMAIN не настроен или не является валидным FQDN, пропуск авто-восстановления Nginx-проксирования подписок."
        fi
    fi

    # Системное отключение IPv6
    if [[ $EUID -eq 0 ]]; then
        mkdir -p /etc/sysctl.d 2>/dev/null || true
        cat > /etc/sysctl.d/99-disable-ipv6.conf <<EOF 2>/dev/null || true
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
        sysctl -p /etc/sysctl.d/99-disable-ipv6.conf >/dev/null 2>&1 || true
    fi

    # Валидация Xray и Nginx
    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка валидации Xray после оптимизации! Выполнен полный откат."
    fi

    if ! nginx -t; then
        manifest_rollback
        error "Ошибка валидации Nginx после оптимизации! Выполнен полный откат."
    fi

    nginx -t && systemctl reload nginx
    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        warn "Служба Xray не запустилась. Выполняется полный откат..."
        manifest_rollback
        error "Откат выполнен: служба Xray не смогла запуститься с новой конфигурацией."
    fi

    if systemctl is-active --quiet xray-api; then
        systemctl restart xray-api 2>/dev/null || true
    fi

    manifest_commit
    log "Оптимизация и восстановление конфигурации Origin завершены успешно!"
}

