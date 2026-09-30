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

    # Единая проверка: корректность FQDN, отсутствие кириллицы и DNS-резолв
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
    ip = socket.gethostbyname(domain)
    if expected and ip != expected:
        print(f'MISMATCH|{ip}')
    else:
        print(f'OK|{ip}')
except Exception as e:
    print(f'ERROR|{e}')
" "$domain" "$expected_ip" 2>/dev/null || true)

        if [[ "$dns_res" == "CYRILLIC" ]]; then
            error "Домен '$domain' содержит русские (кириллические) буквы! Проверьте раскладку клавиатуры и введите латинский домен."
            return 1
        elif [[ "$dns_res" == "INVALID_FQDN" ]]; then
            error "Некорректный формат домена: '$domain'. Ожидается валидное имя FQDN (например: de.yourdomain.com)."
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

        # Если сертификат уже на хосте, разрешаем продолжение
        if [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]]; then
            log "✔ Обнаружен готовый сертификат Let's Encrypt для '$domain'. Проверка DNS пройдена."
            return 0
        fi

        # Интерактивный диалог при задержке DNS-репликации
        if [[ -t 0 ]]; then
            echo -e "\n${YELLOW}Действие:${NC}"
            echo -e "  [1] Повторить проверку DNS через 5 секунд (подождать обновление)"
            echo -e "  [2] Игнорировать и продолжить (если вы уверены, что A-запись верна)"
            echo -e "  [0] Отмена"
            read -rp "Выберите вариант [1/2/0, по умолчанию 1]: " retry_choice || true
            case "${retry_choice:-1}" in
                1) sleep 5; continue ;;
                2) log "Проверка DNS пропущена по выбору пользователя."; return 0 ;;
                *) error "Настройка отменена."; return 1 ;;
            esac
        else
            warn "DNS еще не разрезолвлен в неинтерактивном режиме. Продолжение..."
            return 0
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

    # Создание хука перезапуска Xray при плановом автообновлении (Certbot запускает его автоматически)
    install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/restart-xray.sh <<'EOF'
#!/bin/sh
set -eu
STATE_FILE="/etc/just1knode/state.json"
RELAY_SNI=""
if [ -f "$STATE_FILE" ]; then
    RELAY_SNI=$(grep -o '"sni": *"[^"]*"' "$STATE_FILE" 2>/dev/null | head -n1 | cut -d'"' -f4 || true)
fi

TARGET_DIR="/usr/local/etc/xray/tls"
install -d -m 750 -o root -g nogroup "$TARGET_DIR"

if [ -n "${RENEWED_LINEAGE:-}" ]; then
    if [ -n "$RELAY_SNI" ] && [ -f "/etc/letsencrypt/live/${RELAY_SNI}/fullchain.pem" ]; then
        case "$RENEWED_LINEAGE" in
            *"$RELAY_SNI"*)
                install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_SNI}/fullchain.pem" "${TARGET_DIR}/fullchain.pem"
                install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_SNI}/privkey.pem" "${TARGET_DIR}/privkey.pem"
                systemctl restart xray 2>/dev/null || true
                ;;
        esac
    else
        install -m 640 -o root -g nogroup "${RENEWED_LINEAGE}/fullchain.pem" "${TARGET_DIR}/fullchain.pem"
        install -m 640 -o root -g nogroup "${RENEWED_LINEAGE}/privkey.pem" "${TARGET_DIR}/privkey.pem"
        systemctl restart xray 2>/dev/null || true
    fi
elif [ -n "$RELAY_SNI" ] && [ -f "/etc/letsencrypt/live/${RELAY_SNI}/fullchain.pem" ]; then
    install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_SNI}/fullchain.pem" "${TARGET_DIR}/fullchain.pem"
    install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${RELAY_SNI}/privkey.pem" "${TARGET_DIR}/privkey.pem"
    systemctl restart xray 2>/dev/null || true
fi
EOF
    chmod 755 /etc/letsencrypt/renewal-hooks/deploy/restart-xray.sh

    # Если сертификат для этого домена УЖЕ существует на хосте — мгновенно используем его!
    if [[ -f "/etc/letsencrypt/live/${domain}/fullchain.pem" && -f "/etc/letsencrypt/live/${domain}/privkey.pem" ]]; then
        log "✔ Обнаружен существующий сертификат Let's Encrypt для '$domain'!"
        install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${domain}/fullchain.pem" "${xray_tls_dir}/fullchain.pem"
        install -m 640 -o root -g nogroup "/etc/letsencrypt/live/${domain}/privkey.pem" "${xray_tls_dir}/privkey.pem"
        log "✔ Сертификат успешно привязан к Xray (без повторного обращения к Certbot)."
        return 0
    fi

    log "Проверка наличия Certbot для выпуска SSL Let's Encrypt..."
    if ! command -v certbot >/dev/null 2>&1; then
        log "Установка certbot..."
        apt-get update -qq && apt-get install -y certbot
    fi

    log "Запрос сертификата Let's Encrypt для $domain (Standalone ACME)..."
    log "Zero-Signature: порт 80 открывается только на время проверки и сразу закрывается."

    local was_nginx_active=0
    if systemctl is-active --quiet nginx 2>/dev/null; then
        was_nginx_active=1
        systemctl stop nginx 2>/dev/null || true
    fi

    local ufw_active=0
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
        ufw_active=1
        ufw allow 80/tcp >/dev/null 2>&1 || true
    fi

    local cert_rc=0
    certbot certonly --standalone --non-interactive --agree-tos --register-unsafely-without-email \
        --pre-hook "command -v ufw >/dev/null 2>&1 && ufw allow 80/tcp >/dev/null 2>&1 || true" \
        --post-hook "command -v ufw >/dev/null 2>&1 && { ufw delete allow 80/tcp >/dev/null 2>&1 || true; ufw delete allow 80 >/dev/null 2>&1 || true; }" \
        -d "$domain" || cert_rc=$?

    if [[ $ufw_active -eq 1 ]]; then
        ufw delete allow 80/tcp >/dev/null 2>&1 || true
        ufw delete allow 80 >/dev/null 2>&1 || true
    fi

    if [[ $was_nginx_active -eq 1 ]]; then
        systemctl start nginx 2>/dev/null || true
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
    local sec_mode="${4:-tls}"

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

        echo -e "\n${BOLD}=== НАСТРОЙКА ДОМЕНА ДЛЯ СВЯЗИ ORIGIN ➔ RELAY ===${NC}"
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
        error "Домен Relay обязателен для защиты от блокировок ТСПУ. Использование сторонних SNI запрещено."
        return 1
    fi

    # Валидация DNS A-записи домена
    if ! validate_relay_dns "$dest_server" "$my_ip"; then
        return 1
    fi

    # Выпуск SSL сертификата (Zero-Signature: порт 80 открывается только на пару секунд и сразу закрывается)
    if [[ "$sec_mode" == "tls" ]]; then
        if ! issue_relay_tls_cert "$dest_server"; then
            return 1
        fi
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

    local stream_settings_json
    local public_key=""
    local short_id=""
    if [[ "$sec_mode" == "tls" ]]; then
        stream_settings_json="{
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
    else
        local x25519_out
        x25519_out="$($XRAY_BIN x25519)"
        local private_key
        private_key="$(echo "$x25519_out" | grep -i 'PrivateKey:' | awk '{print $2}')"
        public_key="$(echo "$x25519_out" | grep -iE 'Password|PublicKey' | awk '{print $NF}')"
        short_id="$(python3 -c "import secrets; print(secrets.token_hex(8))")"
        stream_settings_json="{
        \"network\": \"tcp\",
        \"security\": \"reality\",
        \"realitySettings\": {
          \"show\": false,
          \"dest\": \"${dest_server}:443\",
          \"xver\": 0,
          \"serverNames\": [
            \"${dest_server}\"
          ],
          \"privateKey\": \"${private_key}\",
          \"shortIds\": [
            \"${short_id}\"
          ]
        }
      }"
    fi

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
    set_state_val "security" "$sec_mode"
    if [[ "$sec_mode" == "reality" ]]; then
        set_state_val "public_key" "$public_key"
        set_state_val "short_id" "$short_id"
    else
        set_state_val "public_key" "-"
        set_state_val "short_id" "-"
    fi
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
    if [[ "$sec_mode" == "tls" ]]; then
        echo -e "${GREEN}just1knode relay add \"${detected_country}\" ${my_ip} ${relay_port} ${tunnel_uuid} \"${detected_code}\" \"tls\" \"-\" \"-\" \"${dest_server}\"${NC}\n"
    else
        echo -e "${GREEN}just1knode relay add \"${detected_country}\" ${my_ip} ${relay_port} ${tunnel_uuid} \"${detected_code}\" \"reality\" \"${public_key}\" \"${short_id}\" \"${dest_server}\"${NC}\n"
    fi
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

    manifest_commit

    set_state_val "security" "tls"
    set_state_val "sni" "$domain"
    set_state_val "public_key" "-"
    set_state_val "short_id" "-"

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
        if [[ ${#first_label} -eq 2 ]]; then
            detected_code="${first_label,,}"
        else
            detected_code="<код_релея>"
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

    if ! python3 -c "
import json, os, sys, tempfile
cfg_file = sys.argv[1]
with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

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

print('[+] Xray Relay config успешно оптимизирован (UseIPv4 + Независимый DNS)')
" "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка выполнения Python-скрипта реконсиляции Relay."
    fi

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
