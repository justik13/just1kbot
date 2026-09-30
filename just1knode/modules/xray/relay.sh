#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Установка и настройка Relay Узла (modules/xray/relay.sh)
# =============================================================================

validate_relay_dns() {
    local domain="${1:-}"
    local expected_ip="${2:-}"

    if [[ -z "$domain" ]]; then
        error "Домен Relay не может быть пустым."
        return 1
    fi

    while true; do
        log "Проверка DNS A-записи домена $domain (ожидается IP: ${expected_ip:-текущий хост})..."
        local dns_res
        dns_res=$(python3 -c "
import socket, sys, re
domain = sys.argv[1].strip()
expected = sys.argv[2].strip() if len(sys.argv) > 2 else ''

if re.search(r'[\u0400-\u04FF]', domain):
    print('CYRILLIC')
    sys.exit(0)

if not re.match(r'^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$', domain):
    print('INVALID_FQDN')
    sys.exit(0)

try:
    addr_info = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
    ips = list(dict.fromkeys([ai[4][0] for ai in addr_info if ai[4]]))
    if not ips:
        print('NO_RECORDS')
    elif expected and expected not in ips:
        print(f'MISMATCH|{\",\".join(ips)}')
    else:
        print(f'OK|{ips[0]}')
except Exception as e:
    print(f'ERROR|{e}')
" "$domain" "$expected_ip" 2>/dev/null || true)

        if [[ "$dns_res" == "CYRILLIC" ]]; then
            error "Домен '$domain' содержит русские (кириллические) буквы! Проверьте раскладку клавиатуры и введите латинский домен."
            return 1
        elif [[ "$dns_res" == "INVALID_FQDN" ]]; then
            error "Некорректный формат домена: '$domain'. Ожидается валидное имя FQDN (например: your-relay.yourdomain.com)."
            return 1
        elif echo "$dns_res" | grep -q "^OK|"; then
            local resolved_ip="${dns_res#OK|}"
            log "✔ DNS A-запись подтверждена: $domain ➔ $resolved_ip"
            return 0
        elif echo "$dns_res" | grep -q "^MISMATCH|"; then
            local mismatch_ip="${dns_res#MISMATCH|}"
            warn "DNS A-запись для '$domain' указывает на IP $mismatch_ip, а ожидаемый IP этого сервера: $expected_ip."
            warn "Возможные причины: в Cloudflare включен Proxy (оранжевое облако вместо серого) или не обновился кэш DNS."
        else
            local err_msg="${dns_res#ERROR|}"
            warn "DNS-запись для '$domain' пока не найдена ($err_msg)."
        fi

        # Интерактивный диалог при задержке DNS-репликации
        if [[ -t 0 ]]; then
            echo -e "\n${YELLOW}Действие:${NC}"
            echo -e "  [1] Повторить проверку DNS через 5 секунд (подождать обновление)"
            echo -e "  [2] Игнорировать и продолжить (если вы уверены, что A-запись верна)"
            echo -e "  [0] Отмена"
            local retry_choice=""
            read -rp "Выберите вариант [1/2/0, по умолчанию 1]: " retry_choice || return 1
            case "${retry_choice:-1}" in
                1) sleep 5; continue ;;
                2) log "Проверка DNS пропущена по выбору пользователя."; return 0 ;;
                *) error "Настройка отменена."; return 1 ;;
            esac
        else
            error "DNS-валидация не пройдена в неинтерактивном режиме для '$domain' (ожидался $expected_ip)."
            return 1
        fi
    done
}

issue_relay_tls_cert() {
    local domain="$1"
    if [[ -z "$domain" ]]; then
        error "Домен обязателен для выпуска сертификата."
        return 1
    fi

    local xray_tls_dir="/usr/local/etc/xray/tls"
    install -d -m 750 -o root -g nogroup "$xray_tls_dir"

    # Очистка устаревших глобальных pre/post хуков во избежание остановки веб-серверов сторонних доменов
    rm -f /etc/letsencrypt/renewal-hooks/pre/05-just1knode-nginx.sh \
          /etc/letsencrypt/renewal-hooks/post/05-just1knode-nginx.sh \
          /etc/letsencrypt/renewal-hooks/deploy/restart-xray.sh 2>/dev/null || true

    # Постоянный deploy-хук для Certbot: копирование ключей и перезапуск Xray при автообновлении сертификата Relay
    install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy

    cat > /etc/letsencrypt/renewal-hooks/deploy/20-just1knode-restart-xray.sh <<'EOF'
#!/bin/sh
set -eu
STATE_FILE="/etc/just1knode/state.json"
RELAY_SNI=""
if [ -f "$STATE_FILE" ]; then
    RELAY_SNI=$(grep -o '"sni": *"[^"]*"' "$STATE_FILE" 2>/dev/null | head -n1 | cut -d'"' -f4 || true)
fi

TARGET_DIR="/usr/local/etc/xray/tls"
install -d -m 750 -o root -g nogroup "$TARGET_DIR"

if [ -n "${RENEWED_LINEAGE:-}" ] && [ -n "$RELAY_SNI" ]; then
    case "$RENEWED_LINEAGE" in
        *"$RELAY_SNI"*)
            if [ -f "/etc/letsencrypt/live/${RELAY_SNI}/fullchain.pem" ]; then
                install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_SNI}/fullchain.pem" "${TARGET_DIR}/fullchain.pem"
                install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_SNI}/privkey.pem" "${TARGET_DIR}/privkey.pem"
                systemctl restart xray 2>/dev/null || true
            fi
            ;;
    esac
fi
EOF
    chmod 755 /etc/letsencrypt/renewal-hooks/deploy/20-just1knode-restart-xray.sh

    local pre_hook_cmd='if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then touch /run/just1knode_nginx_was_active && systemctl stop nginx 2>/dev/null || true; fi'
    local post_hook_cmd='if [ -f /run/just1knode_nginx_was_active ]; then rm -f /run/just1knode_nginx_was_active; command -v systemctl >/dev/null 2>&1 && systemctl start nginx 2>/dev/null || true; fi'

    # 2. Если действующий сертификат для этого домена УЖЕ существует на хосте — используем его!
    if [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" && -f "/etc/letsencrypt/live/${domain}/privkey.pem" ]]; then
        if openssl x509 -checkend 86400 -noout -in "/etc/letsencrypt/live/${domain}/fullchain.pem" 2>/dev/null; then
            log "✔ Обнаружен действующий сертификат Let's Encrypt для '$domain'!"
            install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${domain}/fullchain.pem" "${xray_tls_dir}/fullchain.pem"
            install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${domain}/privkey.pem" "${xray_tls_dir}/privkey.pem"
            local ren_conf="/etc/letsencrypt/renewal/${domain}.conf"
            if [[ -f "$ren_conf" ]]; then
                grep -q "pre_hook" "$ren_conf" 2>/dev/null || echo "pre_hook = $pre_hook_cmd" >> "$ren_conf"
                grep -q "post_hook" "$ren_conf" 2>/dev/null || echo "post_hook = $post_hook_cmd" >> "$ren_conf"
            fi
            log "✔ Сертификат успешно привязан к Xray (без повторного обращения к Certbot)."
            return 0
        else
            warn "Сертификат для '$domain' истек или истекает в течение 24 часов. Выполняется перевыпуск..."
        fi
    fi

    log "Проверка наличия Certbot для выпуска SSL Let's Encrypt..."
    if ! command -v certbot >/dev/null 2>&1; then
        log "Установка certbot..."
        apt-get update -qq && apt-get install -y certbot
    fi

    log "Запрос сертификата Let's Encrypt для $domain (Standalone ACME)..."
    log "Zero-Signature: порт 80 открывается только на время проверки и сразу закрывается."

    local ufw_active=0
    local port80_was_open=0
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
        ufw_active=1
        if ufw status 2>/dev/null | grep -E "^80(/tcp)?[[:space:]]" | grep -qi "ALLOW"; then
            port80_was_open=1
        else
            ufw allow 80/tcp comment "just1knode certbot verification" >/dev/null 2>&1 || true
        fi
    fi

    local was_nginx_active=0
    if systemctl is-active --quiet nginx 2>/dev/null; then
        was_nginx_active=1
        systemctl stop nginx 2>/dev/null || true
    fi

    local cert_rc=0
    certbot certonly --standalone --non-interactive --agree-tos --register-unsafely-without-email \
        --pre-hook "$pre_hook_cmd" \
        --post-hook "$post_hook_cmd" \
        -d "$domain" || cert_rc=$?

    if [[ $was_nginx_active -eq 1 ]]; then
        systemctl start nginx 2>/dev/null || true
    fi

    if [[ $ufw_active -eq 1 && $port80_was_open -eq 0 ]]; then
        ufw delete allow 80/tcp >/dev/null 2>&1 || true
        ufw delete allow 80 >/dev/null 2>&1 || true
    fi

    if [[ $cert_rc -ne 0 || ! -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]]; then
        error "Сбой выпуска SSL-сертификата Let's Encrypt для $domain. Убедитесь, что порт 80 свободен и DNS указывает на этот сервер."
        return 1
    fi

    install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${domain}/fullchain.pem" "${xray_tls_dir}/fullchain.pem"
    install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${domain}/privkey.pem" "${xray_tls_dir}/privkey.pem"

    log "SSL-сертификат Let's Encrypt успешно получен и привязан к Xray (автообновление настроено)."
    return 0
}

install_xray_relay_node() {
    title "УСТАНОВКА RELAY УЗЛА (Белый Интернет — Выход VLESS)"
    check_root
    init_state_dir
    install_base_deps

    local prev_role
    prev_role="$(get_node_status)"
    if [[ "$prev_role" == "origin" ]]; then
        error "Узел уже настроен как Origin. Установка Relay на Origin запрещена (контуры строго изолированы)."
        return 1
    fi

    local relay_port="${1:-}"
    local origin_ip="${2:-}"
    local dest_server="${3:-}"
    local sec_mode="${4:-}"

    # Интерактивный опросник, если аргументы не переданы
    if [[ -z "$relay_port" ]]; then
        read -rp "Порт туннеля Relay [по умолчанию: 10443]: " relay_port_in || true
        relay_port="${relay_port_in:-10443}"
    fi

    if [[ -z "$origin_ip" ]]; then
        read -rp "Введите IP-адрес Origin-сервера в РФ (для защиты UFW): " origin_ip || true
    fi
    if [[ -z "$origin_ip" ]]; then
        error "IP-адрес Origin обязателен для настройки фаервола."
        return 1
    fi

    local my_ip
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"

    local sec_mode="tls"

    if [[ -z "$dest_server" ]]; then
        local auto_domain=""
        local cert_dirs=(/etc/letsencrypt/live/*)
        for c_dir in "${cert_dirs[@]}"; do
            if [[ -f "${c_dir}/fullchain.pem" ]]; then
                local cand
                cand="$(basename "$c_dir")"
                if [[ "$cand" != "README" && "$cand" != "*" ]]; then
                    auto_domain="$cand"
                    break
                fi
            fi
        done

        echo -e "\n${BOLD}=== НАСТРОЙКА ДОМЕНА ДЛЯ СВЯЗИ ORIGIN ➔ RELAY (VLESS TLS) ===${NC}"
        echo -e "Для исключения блокировок ТСПУ по сверке SNI ➔ DNS (nDPI NDPI_UNRESOLVED_HOSTNAME)"
        echo -e "релей настраивается на вашем собственном домене с чистым сертификатом Let's Encrypt."
        if [[ -n "$auto_domain" ]]; then
            echo -e "${GREEN}✔ Обнаружен готовый сертификат Let's Encrypt для домена:${NC} ${BOLD}${auto_domain}${NC}"
            read -rp "Использовать этот домен [Enter = ${auto_domain}]: " dest_in || true
            dest_server="${dest_in:-$auto_domain}"
        else
            echo -e "Создайте DNS A-запись у вашего регистратора: ${CYAN}your-relay.yourdomain.com ➔ ${my_ip}${NC}\n"
            read -rp "Введите домен Relay (например: your-relay.yourdomain.com): " dest_in || true
            dest_server="${dest_in:-}"
        fi
    fi
    if [[ -z "$dest_server" ]]; then
        error "Домен Relay обязателен для режима VLESS TLS. Использование сторонних SNI запрещено."
        return 1
    fi

    # Валидация DNS A-записи домена (строго только для TLS)
    if ! validate_relay_dns "$dest_server" "$my_ip"; then
        return 1
    fi

    # Выпуск SSL сертификата (Zero-Signature: порт 80 открывается только на время ACME-челленджа)
    if ! issue_relay_tls_cert "$dest_server"; then
        return 1
    fi

    # Проверка на наличие AmneziaWG (Zero-Collateral-Damage принцип)
    if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Ports}}' 2>/dev/null | grep -q "51820"; then
        info "Обнаружен работающий AmneziaWG (Docker: 51820/udp)."
        log "Zero-Collateral: порт 51820/udp и Amnezia-контейнеры НЕ затрагиваются!"
    fi

    # Проверка доступности порта туннеля Relay
    if ss -tlnp 2>/dev/null | grep -q ":${relay_port} " || netstat -tlnp 2>/dev/null | grep -q ":${relay_port} "; then
        local conflict_proc
        conflict_proc=$(ss -tlnp 2>/dev/null | grep ":${relay_port} " || true)
        if ! echo "$conflict_proc" | grep -q "xray"; then
            error "Порт Relay ${relay_port}/tcp уже занят другим процессом на хосте:\n$conflict_proc\nВыберите свободный порт для Relay (например: 10443)."
            return 1
        fi
    fi

    install_xray_binaries
    create_backup "$XRAY_CONFIG"

    local tunnel_uuid
    tunnel_uuid="$($XRAY_BIN uuid)"

    local stream_settings_json="{
    \"network\": \"tcp\",
    \"security\": \"tls\",
    \"tlsSettings\": {
      \"alpn\": [\"h2\", \"http/1.1\"],
      \"certificates\": [
        {
          \"certificateFile\": \"/usr/local/etc/xray/tls/fullchain.pem\",
          \"keyFile\": \"/usr/local/etc/xray/tls/privkey.pem\"
        }
      ]
    }
  }"

    log "Формирование конфигурации Relay ноды (VLESS ${sec_mode^^})..."
    cat > "$XRAY_CONFIG" <<EOF
{
  "log": {
    "loglevel": "warning"
  },
  "inbounds": [
    {
      "tag": "inbound-${sec_mode}",
      "port": ${relay_port},
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "${tunnel_uuid}",
            "flow": "xtls-rprx-vision"
          }
        ],
        "decryption": "none"
      },
      "streamSettings": ${stream_settings_json},
      "sniffing": {
        "enabled": true,
        "destOverride": ["tls", "http", "quic"],
        "metadataOnly": false
      }
    }
  ],
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      {
        "type": "field",
        "protocol": [
          "bittorrent"
        ],
        "outboundTag": "block"
      }
    ]
  },
  "outbounds": [
    {
      "tag": "direct",
      "protocol": "freedom",
      "settings": {
        "domainStrategy": "UseIPv4"
      }
    },
    {
      "tag": "block",
      "protocol": "blackhole"
    }
  ],
  "dns": {
    "servers": [
      "1.1.1.1",
      "1.0.0.1",
      "8.8.8.8",
      "localhost"
    ],
    "queryStrategy": "UseIPv4"
  }
}
EOF

    chown root:root "$XRAY_CONFIG"
    chmod 640 "$XRAY_CONFIG"

    if [[ $EUID -eq 0 ]]; then
        mkdir -p /etc/sysctl.d 2>/dev/null || true
        cat > /etc/sysctl.d/99-disable-ipv6.conf <<EOF 2>/dev/null || true
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
        sysctl -p /etc/sysctl.d/99-disable-ipv6.conf >/dev/null 2>&1 || true
    fi

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        error "Ошибка тестирования сгенерированной конфигурации Xray на Relay узле. Изменения не применены."
        return 1
    fi

    deploy_xray_systemd_service
    systemctl restart xray

    # Защита порта туннеля через UFW (с сохранением порта Amnezia API при Dual-режиме)
    local extra_ufw_ports=()
    local existing_awg_port
    existing_awg_port="$(get_state_val "awg_port" 2>/dev/null || true)"
    [[ -z "$existing_awg_port" ]] && existing_awg_port="8443"
    local saved_bot_ip
    saved_bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"

    if [[ "$prev_role" == "awg" || "$prev_role" == "dual" || -f "/etc/nginx/sites-available/just1k-amnezia.conf" ]]; then
        if [[ -n "$saved_bot_ip" && "$saved_bot_ip" != "any" && "$saved_bot_ip" != "0.0.0.0/0" ]] && validate_ip "$saved_bot_ip"; then
            : # Не открываем awg_port глобально через configure_safe_ufw, добавим точечное правило для BOT_IP ниже
        else
            extra_ufw_ports+=("${existing_awg_port}/tcp")
        fi
    fi
    configure_safe_ufw "${extra_ufw_ports[@]}"
    if [[ "$prev_role" == "awg" || "$prev_role" == "dual" || -f "/etc/nginx/sites-available/just1k-amnezia.conf" ]]; then
        if [[ -n "$saved_bot_ip" && "$saved_bot_ip" != "any" && "$saved_bot_ip" != "0.0.0.0/0" ]] && validate_ip "$saved_bot_ip"; then
            ufw delete allow "${existing_awg_port}/tcp" 2>/dev/null || true
            ufw delete allow "${existing_awg_port}" 2>/dev/null || true
            ufw allow from "$saved_bot_ip" to any port "$existing_awg_port" proto tcp comment "just1knode amnezia api" >/dev/null 2>&1 || true
            log "Фаервол UFW: подтвержден доступ к порту ${existing_awg_port} строго для BOT_IP (${saved_bot_ip})"
        fi
    fi
    ufw allow from "$origin_ip" to any port "$relay_port" proto tcp || true
    log "Порт туннеля ${relay_port}/tcp открыт строго для ${origin_ip}."

    if [[ "$prev_role" == "awg" || "$prev_role" == "dual" ]]; then
        set_state_val "role" "dual"
        log "Режим узла обновлен до: DUAL (Совмещенный Relay + AmneziaWG)"
    else
        set_state_val "role" "relay"
    fi
    set_state_val "relay_port" "$relay_port"
    set_state_val "origin_ip" "$origin_ip"
    set_state_val "tunnel_uuid" "$tunnel_uuid"
    set_state_val "security" "tls"
    set_state_val "public_key" "-"
    set_state_val "short_id" "-"
    set_state_val "sni" "$dest_server"

    local detected_country="Зарубежный шлюз"
    local detected_code="exit"
    local geo_json
    geo_json="$(curl -s --max-time 3 "https://ipinfo.io/${my_ip}/json" 2>/dev/null || true)"
    if [[ -n "$geo_json" ]]; then
        local c_code
        c_code="$(python3 -c "import json, sys; d=json.loads(sys.argv[1]); print(d.get('country','').lower())" "$geo_json" 2>/dev/null || true)"
        if [[ -n "$c_code" && "$c_code" != "ru" ]]; then
            detected_code="$c_code"
            case "$c_code" in
                de) detected_country="🇩🇪 Германия" ;;
                nl) detected_country="🇳🇱 Нидерланды" ;;
                fi) detected_country="🇫🇮 Финляндия" ;;
                se) detected_country="🇸🇪 Швеция" ;;
                us) detected_country="🇺🇸 США" ;;
                gb|uk) detected_country="🇬🇧 Великобритания" ;;
                fr) detected_country="🇫🇷 Франция" ;;
                tr) detected_country="🇹🇷 Турция" ;;
                kz) detected_country="🇰🇿 Казахстан" ;;
                pl) detected_country="🇵🇱 Польша" ;;
                at) detected_country="🇦🇹 Австрия" ;;
                ch) detected_country="🇨🇭 Швейцария" ;;
                ee) detected_country="🇪🇪 Эстония" ;;
                *) detected_country="${c_code^^}" ;;
            esac
        fi
    fi

    title "УСТАНОВКА RELAY УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Команда для добавления этого Relay на вашем Origin-сервере:${NC}"
    echo -e "${GREEN}just1knode relay add \"${detected_country}\" ${my_ip} ${relay_port} ${tunnel_uuid} \"${detected_code}\" \"tls\" \"-\" \"-\" \"${dest_server}\"${NC}\n"
    echo -e "${YELLOW}Примечание: вы можете заменить название \"${detected_country}\" на любое удобное вам.${NC}\n"
}

setup_relay_domain() {
    title "НАСТРОЙКА ЛИЧНОГО ДОМЕНА ДЛЯ RELAY-УЗЛА (VLESS TLS)"
    check_root
    init_state_dir
    acquire_just1knode_lock

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "relay" && "$role" != "dual" ]]; then
        error "Команда 'setup-domain' предназначена для Relay/Dual узлов (текущая роль: ${role:-не настроен})."
        return 1
    fi

    local relay_port
    relay_port="$(get_state_val "relay_port")"
    [[ -z "$relay_port" ]] && relay_port="10443"

    local tunnel_uuid
    tunnel_uuid="$(get_state_val "tunnel_uuid")"

    # Если UUID не сохранен в state, считываем из config.json
    if [[ -z "$tunnel_uuid" && -f "$XRAY_CONFIG" ]]; then
        tunnel_uuid=$(python3 -c "
import json, sys
try:
    with open('$XRAY_CONFIG') as f:
        c = json.load(f)
    for ib in c.get('inbounds', []):
        clients = ib.get('settings', {}).get('clients', [])
        if clients and clients[0].get('id'):
            print(clients[0]['id'])
            sys.exit(0)
except Exception: pass
print('')
" 2>/dev/null || true)
    fi

    if [[ -z "$tunnel_uuid" ]]; then
        tunnel_uuid="$($XRAY_BIN uuid 2>/dev/null || true)"
        [[ -z "$tunnel_uuid" ]] && tunnel_uuid="$(python3 -c 'import uuid; print(uuid.uuid4())')"
        set_state_val "tunnel_uuid" "$tunnel_uuid"
    fi

    local my_ip
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"

    # Авто-определение уже существующего сертификата Let's Encrypt на сервере
    local auto_domain=""
    local cert_dirs=(/etc/letsencrypt/live/*)
    for c_dir in "${cert_dirs[@]}"; do
        if [[ -f "${c_dir}/fullchain.pem" ]]; then
            local cand
            cand="$(basename "$c_dir")"
            if [[ "$cand" != "README" && "$cand" != "*" ]]; then
                auto_domain="$cand"
                break
            fi
        fi
    done

    local domain="${1:-}"
    if [[ -z "$domain" ]]; then
        echo -e "\n${BOLD}=== НАСТРОЙКА ДОМЕНА RELAY ДЛЯ ЗАЩИТЫ ОТ ТСПУ ===${NC}"
        echo -e "Для устранения сигнатуры nDPI NDPI_UNRESOLVED_HOSTNAME (сверка SNI ➔ DNS)"
        echo -e "релей настраивается на вашем персональном домене с чистым сертификатом Let's Encrypt."
        if [[ -n "$auto_domain" ]]; then
            echo -e "${GREEN}✔ Обнаружен готовый сертификат Let's Encrypt для домена:${NC} ${BOLD}${auto_domain}${NC}"
            read -rp "Использовать этот домен [Enter = ${auto_domain}]: " domain_in
            domain="${domain_in:-$auto_domain}"
        else
            echo -e "Создайте DNS A-запись (DNS-Only / без Cloudflare Proxy):"
            echo -e "  ${CYAN}your-relay.yourdomain.com ➔ ${my_ip}${NC}\n"
            read -rp "Введите персональный домен для этого Relay (например: your-relay.yourdomain.com): " domain_in
            domain="${domain_in:-}"
        fi
    fi

    if [[ -z "$domain" ]]; then
        error "Домен Relay обязателен."
        return 1
    fi

    if ! validate_relay_dns "$domain" "$my_ip"; then
        return 1
    fi

    if ! issue_relay_tls_cert "$domain"; then
        return 1
    fi

    create_backup "$XRAY_CONFIG"
    manifest_begin

    log "Обновление конфигурации Xray на VLESS + TLS..."
    if ! python3 -c "
import json, sys, os, tempfile

cfg_file = sys.argv[1]
port = int(sys.argv[2])
uuid = sys.argv[3]
domain = sys.argv[4]

with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

# Ищем входящий inbound туннеля (по порту или тегу)
ib = None
for i in cfg.get('inbounds', []):
    if i.get('port') == port or i.get('tag') in ('inbound-reality', 'inbound-tls', 'from-origin'):
        ib = i
        break

if not ib:
    ib = {
        'tag': 'inbound-tls',
        'port': port,
        'protocol': 'vless',
        'settings': {
            'clients': [{'id': uuid, 'flow': 'xtls-rprx-vision'}],
            'decryption': 'none'
        }
    }
    cfg.setdefault('inbounds', []).append(ib)

# Сохраняем существующих клиентов (UUID/flow), если они уже есть в inbound
existing_clients = ib.get('settings', {}).get('clients', [])
if not existing_clients:
    existing_clients = [{'id': uuid, 'flow': 'xtls-rprx-vision'}]

ib['tag'] = 'inbound-tls'
ib['port'] = port
ib['protocol'] = 'vless'
ib['settings'] = {
    'clients': existing_clients,
    'decryption': 'none'
}
ib['streamSettings'] = {
    'network': 'tcp',
    'security': 'tls',
    'tlsSettings': {
        'alpn': ['h2', 'http/1.1'],
        'certificates': [
            {
                'certificateFile': '/usr/local/etc/xray/tls/fullchain.pem',
                'keyFile': '/usr/local/etc/xray/tls/privkey.pem'
            }
        ]
    }
}
ib.setdefault('sniffing', {
    'enabled': True,
    'destOverride': ['tls', 'http', 'quic'],
    'metadataOnly': False
})

d = os.path.dirname(os.path.abspath(cfg_file))
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8') as fp:
    json.dump(cfg, fp, indent=2)
    fp.flush()
    os.fsync(fp.fileno())
os.replace(t_path, cfg_file)
try:
    os.chmod(cfg_file, 0o640)
except Exception:
    pass
" "$XRAY_CONFIG" "$relay_port" "$tunnel_uuid" "$domain"; then
        manifest_rollback
        error "Ошибка обновления конфигурации Xray Relay."
        return 1
    fi

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка тестирования сгенерированной конфигурации Xray! Изменения отменены."
        return 1
    fi

    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        manifest_rollback
        error "Xray не смог запуститься с новым сертификатом. Выполнен полный откат."
        return 1
    fi

    set_state_val "security" "tls"
    set_state_val "sni" "$domain"
    set_state_val "public_key" "-"
    set_state_val "short_id" "-"

    manifest_commit

    local detected_code=""
    local geo_json
    geo_json="$(curl -s --max-time 3 "https://ipinfo.io/${my_ip}/json" 2>/dev/null || true)"
    if [[ -n "$geo_json" ]]; then
        local c_code
        c_code="$(python3 -c "import json, sys; d=json.loads(sys.argv[1]); print(d.get('country','').lower())" "$geo_json" 2>/dev/null || true)"
        [[ -n "$c_code" && "$c_code" != "ru" ]] && detected_code="$c_code"
    fi
    if [[ -z "$detected_code" ]]; then
        local first_label="${domain%%.*}"
        if [[ "$first_label" =~ ^[a-zA-Z0-9_-]+$ ]]; then
            detected_code="${first_label,,}"
        else
            detected_code="relay-01"
        fi
    fi

    title "НАСТРОЙКА ДОМЕНА RELAY УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}1. Команда для переключения этого релея на вашем Origin-сервере:${NC}"
    echo -e "${GREEN}just1knode relay sni ${detected_code} ${domain} tls${NC}\n"
    echo -e "${BOLD}2. Если вы настраиваете этот релей на Origin впервые, используйте команду:${NC}"
    echo -e "${CYAN}just1knode relay add \"${detected_code^^}\" ${my_ip} ${relay_port} ${tunnel_uuid} \"${detected_code}\" \"tls\" \"-\" \"-\" \"${domain}\"${NC}\n"
}

heal_and_update_relay_config() {
    title "АВТОМАТИЧЕСКАЯ ОПТИМИЗАЦИЯ И ОБНОВЛЕНИЕ КОНФИГУРАЦИИ RELAY"
    check_root
    init_state_dir
    acquire_just1knode_lock

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "relay" && "$role" != "dual" ]]; then
        error "Функция доступна только на Relay-узле (текущая роль: ${role:-не установлена})."
    fi

    log "Проверка и исправление параметров ядра Xray Relay..."
    if [[ ! -f "$XRAY_CONFIG" ]]; then
        error "Файл конфигурации Xray не найден: $XRAY_CONFIG"
    fi

    create_backup "$XRAY_CONFIG"
    manifest_begin

    # 1. Автоматический перевод Relay на VLESS+TLS, если на хосте уже есть сертификат Let's Encrypt
    local le_domain=""
    local my_ip
    my_ip="$(get_my_ip 2>/dev/null || hostname -I | awk '{print $1}')"
    local cur_sni
    cur_sni="$(get_state_val "sni" "")"
    local cert_dirs=(/etc/letsencrypt/live/*)
    for c_dir in "${cert_dirs[@]}"; do
        if [[ -f "${c_dir}/fullchain.pem" && -f "${c_dir}/privkey.pem" ]]; then
            local cand
            cand="$(basename "$c_dir")"
            if [[ "$cand" != "README" && "$cand" != "*" ]]; then
                if openssl x509 -checkend 86400 -noout -in "${c_dir}/fullchain.pem" 2>/dev/null; then
                    if [[ -n "$cur_sni" && "$cur_sni" != *"google.com"* && "$cur_sni" == "$cand" ]]; then
                        le_domain="$cand"
                        break
                    elif [[ -n "$my_ip" ]] && validate_relay_dns "$cand" "$my_ip" >/dev/null 2>&1; then
                        le_domain="$cand"
                        break
                    fi
                fi
            fi
        fi
    done

    if [[ -n "$le_domain" ]]; then
        local cur_sec
        cur_sec="$(get_state_val "security" "")"
        if [[ "$cur_sec" != "tls" || "$cur_sni" != "$le_domain" || ! -f "/usr/local/etc/xray/tls/fullchain.pem" ]]; then
            log "✔ Обнаружен действующий сертификат Let's Encrypt для '$le_domain'."
            log "Автоматический перевод входящего туннеля Relay на VLESS + TLS (Zero-Manual-Commands)..."
            if issue_relay_tls_cert "$le_domain"; then
                set_state_val "security" "tls"
                set_state_val "sni" "$le_domain"
                set_state_val "public_key" "-"
                set_state_val "short_id" "-"
            fi
        fi
    fi

    local current_sec
    current_sec="$(get_state_val "security" "")"

    if ! python3 -c "
import json, os, sys, tempfile
cfg_file = sys.argv[1]
sec_mode = sys.argv[2] if len(sys.argv) > 2 else ''
with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

# Если узел переведен на TLS и сертификаты присутствуют, гарантируем, что входящий инбаунд настроен на VLESS+TLS
tls_cert_file = '/usr/local/etc/xray/tls/fullchain.pem'
tls_key_file = '/usr/local/etc/xray/tls/privkey.pem'
if sec_mode == 'tls' and os.path.exists(tls_cert_file) and os.path.exists(tls_key_file):
    for ib in cfg.get('inbounds', []):
        if ib.get('tag') in ('inbound-reality', 'inbound-tls', 'from-origin') or ib.get('port') in (10443, 443):
            ib['tag'] = 'inbound-tls'
            st = ib.setdefault('streamSettings', {})
            st['network'] = 'tcp'
            st['security'] = 'tls'
            st.pop('realitySettings', None)
            st['tlsSettings'] = {
                'alpn': ['h2', 'http/1.1'],
                'certificates': [
                    {
                        'certificateFile': tls_cert_file,
                        'keyFile': tls_key_file
                    }
                ]
            }

for ob in cfg.get('outbounds', []):
    if ob.get('tag') == 'direct' or ob.get('protocol') == 'freedom':
        ob.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'

has_block = any(ob.get('tag') == 'block' for ob in cfg.get('outbounds', []))
if not has_block:
    cfg.setdefault('outbounds', []).append({
        'tag': 'block',
        'protocol': 'blackhole'
    })

routing = cfg.setdefault('routing', {})
routing.setdefault('domainStrategy', 'IPIfNonMatch')
rules = routing.setdefault('rules', [])
has_bt_proto = any(r.get('type') == 'field' and 'bittorrent' in r.get('protocol', []) for r in rules)
if not has_bt_proto:
    rules.insert(0, {
        'type': 'field',
        'protocol': ['bittorrent'],
        'outboundTag': 'block'
    })

for ib in cfg.get('inbounds', []):
    sniff = ib.setdefault('sniffing', {})
    sniff['enabled'] = True
    sniff.setdefault('destOverride', ['tls', 'http', 'quic'])
    sniff.setdefault('metadataOnly', False)

cfg['dns'] = {
    'servers': ['1.1.1.1', '1.0.0.1', '8.8.8.8', 'localhost'],
    'queryStrategy': 'UseIPv4'
}

d = os.path.dirname(os.path.abspath(cfg_file))
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(t_path, cfg_file)
try:
    os.chmod(cfg_file, 0o640)
except Exception:
    pass

print('[+] Xray Relay config успешно оптимизирован (UseIPv4 + Независимый DNS + VLESS TLS)')
" "$XRAY_CONFIG" "$current_sec"; then
        manifest_rollback
        error "Ошибка выполнения Python-скрипта реконсиляции Relay."
    fi

    chmod 640 "$XRAY_CONFIG" 2>/dev/null || true

    if [[ $EUID -eq 0 ]]; then
        mkdir -p /etc/sysctl.d 2>/dev/null || true
        cat > /etc/sysctl.d/99-disable-ipv6.conf <<EOF 2>/dev/null || true
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
EOF
        sysctl -p /etc/sysctl.d/99-disable-ipv6.conf >/dev/null 2>&1 || true
    fi

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка валидации Xray Relay после оптимизации! Выполнен полный откат."
    fi

    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        warn "Служба Xray Relay не запустилась. Выполняется полный откат..."
        manifest_rollback
        error "Откат выполнен: служба Xray Relay не смогла запуститься с новой конфигурацией."
    fi

    manifest_commit
    log "Оптимизация и обновление конфигурации Relay завершены успешно!"
}
