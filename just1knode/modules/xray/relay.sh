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
      "protocol": "freedom"
    },
    {
      "tag": "block",
      "protocol": "blackhole"
    }
  ]
}
EOF

    chown root:root "$XRAY_CONFIG"
    chmod 640 "$XRAY_CONFIG"

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

    title "УСТАНОВКА RELAY УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Команда для добавления этого Relay на вашем Origin-сервере:${NC}"
    echo -e "${GREEN}just1knode relay add \"Германия\" ${my_ip} ${relay_port} ${tunnel_uuid} \"de\" \"reality\" \"${public_key}\" \"${short_id}\" \"${dest_server}\"${NC}\n"
}
