#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Установка Relay Узла (modules/xray/relay.sh)
# =============================================================================

install_xray_relay_node() {
    title "УСТАНОВКА RELAY УЗЛА (Белый Интернет — Выход VLESS REALITY)"
    check_root
    init_state_dir
    install_base_deps

    local relay_port="${1:-}"
    local origin_ip="${2:-}"
    local dest_server="${3:-}"

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
    fi

    if [[ -z "$dest_server" ]]; then
        read -rp "Домен маскировки REALITY (SNI) [по умолчанию: www.google.com]: " dest_in || true
        dest_server="${dest_in:-www.google.com}"
    fi

    # Проверка на наличие AmneziaWG (Zero-Collateral-Damage принцип)
    if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Ports}}' 2>/dev/null | grep -q "51820"; then
        info "Обнаружен работающий AmneziaWG (Docker: 51820/udp)."
        log "Zero-Collateral: порт 51820/udp и Amnezia-контейнеры НЕ затрагиваются!"
    fi

    install_xray_binaries
    create_backup "$XRAY_CONFIG"

    local tunnel_uuid
    tunnel_uuid="$($XRAY_BIN uuid)"

    local x25519_out
    x25519_out="$($XRAY_BIN x25519)"
    local private_key
    private_key="$(echo "$x25519_out" | grep -i 'PrivateKey:' | awk '{print $2}')"
    local public_key
    public_key="$(echo "$x25519_out" | grep -iE 'Password|PublicKey' | awk '{print $NF}')"

    local short_id
    short_id="$(python3 -c "import secrets; print(secrets.token_hex(8))")"

    log "Формирование конфигурации Relay ноды (VLESS REALITY)..."
    cat > "$XRAY_CONFIG" <<EOF
{
  "log": {
    "loglevel": "warning"
  },
  "inbounds": [
    {
      "tag": "inbound-reality",
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
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "${dest_server}:443",
          "xver": 0,
          "serverNames": [
            "${dest_server}"
          ],
          "privateKey": "${private_key}",
          "shortIds": [
            "${short_id}"
          ]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["tls", "http"]
      }
    }
  ],
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

    deploy_xray_systemd_service
    systemctl restart xray

    # Защита порта туннеля через UFW
    configure_safe_ufw
    ufw allow from "$origin_ip" to any port "$relay_port" proto tcp || true
    log "Порт туннеля ${relay_port}/tcp открыт строго для ${origin_ip}."

    local my_ip
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"

    set_state_val "role" "relay"
    set_state_val "relay_port" "$relay_port"
    set_state_val "origin_ip" "$origin_ip"
    set_state_val "tunnel_uuid" "$tunnel_uuid"
    set_state_val "public_key" "$public_key"
    set_state_val "short_id" "$short_id"
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
                de) detected_country="Германия" ;;
                nl) detected_country="Нидерланды" ;;
                fi) detected_country="Финляндия" ;;
                se) detected_country="Швеция" ;;
                us) detected_country="США" ;;
                gb|uk) detected_country="Великобритания" ;;
                fr) detected_country="Франция" ;;
                tr) detected_country="Турция" ;;
                kz) detected_country="Казахстан" ;;
                pl) detected_country="Польша" ;;
                at) detected_country="Австрия" ;;
                ch) detected_country="Швейцария" ;;
                *) detected_country="${c_code^^}" ;;
            esac
        fi
    fi

    title "УСТАНОВКА RELAY УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Команда для добавления этого Relay на вашем Origin-сервере:${NC}"
    echo -e "${GREEN}just1knode relay add \"${detected_country}\" ${my_ip} ${relay_port} ${tunnel_uuid} \"${detected_code}\" \"reality\" \"${public_key}\" \"${short_id}\" \"${dest_server}\"${NC}\n"
    echo -e "${YELLOW}Примечание: вы можете заменить название \"${detected_country}\" на любое удобное вам.${NC}\n"
}

heal_and_update_relay_config() {
    title "АВТОМАТИЧЕСКАЯ ОПТИМИЗАЦИЯ И ОБНОВЛЕНИЕ КОНФИГУРАЦИИ RELAY"
    check_root
    init_state_dir

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "relay" ]]; then
        error "Функция доступна только на Relay-узле (текущая роль: ${role:-не установлена})."
    fi

    log "Проверка и исправление параметров ядра Xray Relay..."
    if [[ ! -f "$XRAY_CONFIG" ]]; then
        error "Файл конфигурации Xray не найден: $XRAY_CONFIG"
    fi

    create_backup "$XRAY_CONFIG"

    python3 -c "
import json, sys
cfg_file = sys.argv[1]
with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

for ob in cfg.get('outbounds', []):
    if ob.get('tag') == 'direct' or ob.get('protocol') == 'freedom':
        ob.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'

cfg['dns'] = {
    'servers': ['1.1.1.1', '1.0.0.1', '8.8.8.8', 'localhost'],
    'queryStrategy': 'UseIPv4'
}

with open(cfg_file, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)

print('[+] Xray Relay config успешно оптимизирован (UseIPv4 + Независимый DNS)')
" "$XRAY_CONFIG"

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
        manifest_rollback 2>/dev/null || true
        error "Ошибка валидации Xray Relay после оптимизации!"
    fi

    systemctl restart xray
    log "Оптимизация и обновление конфигурации Relay завершены успешно!"
}

