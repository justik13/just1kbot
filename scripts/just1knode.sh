#!/usr/bin/env bash
# =============================================================================
# JUST1KBOT - Интерактивный менеджер серверных узлов (just1knode)
# =============================================================================
#
# Поддерживаемые режимы:
#   1. Amnezia API Node (AmneziaWG 2.0 для стандартной подписки)
#   2. White Internet Origin Node (Шлюз в РФ, Nginx OPTIONS->POST, Xray XHTTP, xray-api)
#   3. White Internet Relay Node (Зарубежный выход, Xray Vision REALITY, UFW Origin-Only)
#   4. Управление Relay-нодами на Origin (Добавить / Удалить / Список)
#   5. Статус и активные клиенты
#   6. Комплексная самодиагностика (Doctor)
#   7. Безопасное обновление Xray-core
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="/etc/just1knode"
STATE_FILE="${STATE_DIR}/state.json"
CLIENTS_FILE="${STATE_DIR}/clients.json"
RELAYS_FILE="${STATE_DIR}/relays.json"
BACKUP_DIR="/var/backups/just1knode"

XRAY_VERSION_PINNED="26.7.28"
XRAY_SHA256_64="8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40"
XRAY_SHA256_ARM64="f5698bb218ada3b4022db26fafc39601c5f53b46b19eb76c9616325985807501"

XRAY_BIN="/usr/local/bin/xray"
XRAY_CONFIG_DIR="/usr/local/etc/xray"
XRAY_CONFIG="${XRAY_CONFIG_DIR}/config.json"
XRAY_SHARE_DIR="/usr/local/share/xray"
XRAY_API_DIR="/opt/xray-api"

# Цвета терминала
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
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

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Скрипт должен быть запущен с правами root (используйте: sudo just1knode)"
    fi
}

get_arch() {
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) echo "64" ;;
        aarch64|arm64) echo "arm64-v8a" ;;
        *) error "Неподдерживаемая архитектура: $arch" ;;
    esac
}

# --- Инициализация и сохранение состояния ---
init_state_dir() {
    mkdir -p "$STATE_DIR" "$BACKUP_DIR"
    chmod 700 "$STATE_DIR" "$BACKUP_DIR"
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "{}" > "$STATE_FILE"
        chmod 600 "$STATE_FILE"
    fi
    if [[ ! -f "$CLIENTS_FILE" ]]; then
        echo '{"clients": [], "updated_at": 0, "count": 0}' > "$CLIENTS_FILE"
        chmod 600 "$CLIENTS_FILE"
    fi
    if [[ ! -f "$RELAYS_FILE" ]]; then
        echo '[]' > "$RELAYS_FILE"
        chmod 600 "$RELAYS_FILE"
    fi
}

set_state_val() {
    local key="$1"
    local val="$2"
    init_state_dir
    python3 -c "
import sys, json, os, tempfile
try:
    import fcntl
except ImportError:
    fcntl = None

f, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
lock_file = f + '.lock'
lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
if fcntl:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    data = {}
    if os.path.exists(f):
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
        except Exception:
            data = {}
    data[k] = v
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(f), suffix='.tmp')
    with os.fdopen(tmp_fd, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp_path, f)
finally:
    if fcntl:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
" "$STATE_FILE" "$key" "$val"
}

get_state_val() {
    local key="$1"
    local default_val="${2:-}"
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "$default_val"
        return
    fi
    python3 -c "
import sys, json
f, k, d = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(f, 'r', encoding='utf-8') as fp: data = json.load(fp)
    print(data.get(k, d))
except Exception: print(d)
" "$STATE_FILE" "$key" "$default_val"
}

# --- Проверка системных пакетов ---
install_base_deps() {
    log "Проверка и установка базовых системных зависимостей..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl wget jq ufw socat unzip ca-certificates python3 python3-pip python3-venv openssl git
}

# --- Безопасная загрузка и проверка SHA-256 Xray-core ---
download_and_verify_xray() {
    local dest_zip="$1"
    local arch
    arch="$(get_arch)"
    local expected_sha
    if [[ "$arch" == "64" ]]; then
        expected_sha="$XRAY_SHA256_64"
    else
        expected_sha="$XRAY_SHA256_ARM64"
    fi

    local zip_url="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION_PINNED}/Xray-linux-${arch}.zip"
    log "Загрузка Xray-core v${XRAY_VERSION_PINNED} (${arch})..."
    curl -sSL --fail --retry 3 "$zip_url" -o "$dest_zip"

    log "Проверка целостности SHA-256..."
    local actual_sha
    actual_sha="$(sha256sum "$dest_zip" | awk '{print $1}')"
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        rm -f "$dest_zip"
        error "Несовпадение SHA-256 хеша! Ожидалось: $expected_sha, получено: $actual_sha. Установка прервана."
    fi
    log "SHA-256 хеш подтвержден: $actual_sha"
}

# =============================================================================
# РЕЖИМ 1: УСТАНОВКА AMNEZIA API УЗЛА (AWG 2.0)
# =============================================================================
install_amnezia_api_node() {
    title "УСТАНОВКА AMNEZIA API УЗЛА (AmneziaWG 2.0)"
    check_root
    init_state_dir
    install_base_deps

    local domain="${1:-}"
    local email="${2:-}"

    if [[ -z "$domain" ]]; then
        read -rp "Введите домен для Amnezia API (например: awg.example.com): " domain
    fi
    if [[ -z "$domain" ]]; then error "Домен не может быть пустым."; fi

    if [[ -z "$email" ]]; then
        read -rp "Введите Email для SSL Let's Encrypt (например: admin@example.com): " email
    fi
    if [[ -z "$email" ]]; then error "Email не может быть пустым."; fi

    # Установка Node.js LTS если отсутствует
    if ! command -v node &>/dev/null; then
        log "Установка Node.js LTS..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y -qq nodejs
    fi

    # Установка Nginx и Certbot
    apt-get install -y -qq nginx certbot python3-certbot-nginx

    # Выпуск SSL сертификата
    log "Выпуск SSL сертификата для $domain..."
    systemctl stop nginx || true
    certbot certonly --standalone -d "$domain" --non-interactive --agree-tos -m "$email" --keep-until-expiring

    # Клонирование amnezia-api
    local amnezia_dir="/opt/amnezia-api"
    log "Развертывание amnezia-api в $amnezia_dir..."
    if [[ -d "$amnezia_dir" ]]; then
        git -C "$amnezia_dir" pull || true
    else
        git clone https://github.com/kyoresuas/amnezia-api.git "$amnezia_dir"
    fi

    (cd "$amnezia_dir" && npm install --production)

    # Генерация API-ключа
    local api_key
    api_key="$(python3 -c "import secrets; print(secrets.token_hex(32))")"

    # Конфигурация amnezia-api
    cat > "$amnezia_dir/.env" <<EOF
PORT=8080
FASTIFY_API_KEY=${api_key}
EOF

    # Systemd служба для amnezia-api
    cat > /etc/systemd/system/amnezia-api.service <<EOF
[Unit]
Description=Amnezia API Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${amnezia_dir}
EnvironmentFile=${amnezia_dir}/.env
ExecStart=/usr/bin/npm start
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now amnezia-api

    # Настройка Nginx reverse proxy на порту 8443
    log "Настройка Nginx reverse proxy (порт 8443)..."
    cat > /etc/nginx/sites-available/amnezia-api.conf <<EOF
server {
    listen 8443 ssl http2;
    server_name ${domain};

    ssl_certificate /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

    ln -sf /etc/nginx/sites-available/amnezia-api.conf /etc/nginx/sites-enabled/
    nginx -t
    systemctl restart nginx

    # UFW фаервол
    log "Настройка UFW фаервола..."
    ufw allow 22/tcp || true
    ufw allow 8443/tcp || true
    ufw --force enable || true

    set_state_val "role" "amnezia"
    set_state_val "domain" "$domain"
    set_state_val "api_url" "https://${domain}:8443"
    set_state_val "api_key" "$api_key"

    title "УСТАНОВКА AMNEZIA API УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Данные для добавления сервера в Telegram-боте (/admin):${NC}"
    echo -e "  🌐 Домен сервера:    ${CYAN}${domain}${NC}"
    echo -e "  🔗 API URL:          ${CYAN}https://${domain}:8443${NC}"
    echo -e "  🔑 API Ключ:          ${YELLOW}${api_key}${NC}\n"
}

# =============================================================================
# РЕЖИМ 2: УСТАНОВКА ORIGIN УЗЛА (БЕЛЫЙ ИНТЕРНЕТ — ВХОДНОЙ ШЛЮЗ В РФ)
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

    if [[ -z "$domain" ]]; then
        read -rp "Введите домен Origin-сервера (например: origin.example.com): " domain
    fi
    if [[ -z "$domain" ]]; then error "Домен не может быть пустым."; fi

    if [[ -z "$email" ]]; then
        read -rp "Введите Email для SSL Let's Encrypt: " email
    fi
    if [[ -z "$email" ]]; then error "Email не может быть пустым."; fi

    if [[ -z "$secret_path" ]]; then
        local rnd_hex
        rnd_hex="$(python3 -c "import secrets; print(secrets.token_hex(4))")"
        read -rp "Секретный префикс пути XHTTP [по умолчанию: /w_${rnd_hex}]: " input_path
        secret_path="${input_path:-/w_${rnd_hex}}"
    fi

    if [[ -z "$api_key" ]]; then
        api_key="$(python3 -c "import secrets; print(secrets.token_hex(32))")"
    fi

    # Установка Nginx и Certbot
    apt-get install -y -qq nginx certbot python3-certbot-nginx

    # Выпуск SSL сертификата
    log "Выпуск SSL сертификата для $domain..."
    systemctl stop nginx || true
    certbot certonly --standalone -d "$domain" --non-interactive --agree-tos -m "$email" --keep-until-expiring

    # Установка Xray-core с проверкой SHA-256
    local tmp_zip="/tmp/xray_origin.zip"
    download_and_verify_xray "$tmp_zip"

    mkdir -p "$XRAY_CONFIG_DIR" "$XRAY_SHARE_DIR"
    unzip -q -o "$tmp_zip" xray -d /usr/local/bin/
    unzip -q -o "$tmp_zip" geoip.dat geosite.dat -d "$XRAY_SHARE_DIR/" || true
    rm -f "$tmp_zip"
    chmod +x "$XRAY_BIN"

    # Базовая конфигурация Xray Origin с gRPC HandlerService, StatsService и локальным fallback inbound
    log "Формирование конфигурации Xray Origin..."
    cat > "$XRAY_CONFIG" <<EOF
{
  "log": {
    "loglevel": "warning"
  },
  "api": {
    "tag": "api",
    "services": [
      "HandlerService",
      "StatsService"
    ]
  },
  "stats": {},
  "policy": {
    "levels": {
      "0": {
        "statsUserUplink": true,
        "statsUserDownlink": true
      }
    },
    "system": {
      "statsInboundUplink": true,
      "statsInboundDownlink": true
    }
  },
  "inbounds": [
    {
      "tag": "api-grpc",
      "listen": "127.0.0.1",
      "port": 10085,
      "protocol": "dokodemo-door",
      "settings": {
        "address": "127.0.0.1"
      }
    },
    {
      "tag": "inbound-default",
      "listen": "127.0.0.1",
      "port": 8003,
      "protocol": "vless",
      "settings": {
        "clients": [],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "xhttp",
        "xhttpSettings": {
          "mode": "packet-up",
          "path": "${secret_path}/default"
        }
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
  ],
  "routing": {
    "rules": [
      {
        "type": "field",
        "inboundTag": ["api-grpc"],
        "outboundTag": "api"
      },
      {
        "type": "field",
        "inboundTag": ["inbound-default"],
        "outboundTag": "direct"
      }
    ]
  }
}
EOF

    # Systemd служба для Xray
    cat > /etc/systemd/system/xray.service <<EOF
[Unit]
Description=Xray Service
Documentation=https://github.com/xtls
After=network.target nss-lookup.target

[Service]
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartPreventExitStatus=23
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now xray

    # Развертывание легкого Python xray-api агента
    log "Развертывание агента xray-api..."
    mkdir -p "$XRAY_API_DIR"
    mkdir -p /etc/xray-api

    cat > /etc/xray-api/config.env <<EOF
XRAY_API_KEY=${api_key}
XRAY_GRPC_HOST=127.0.0.1
XRAY_GRPC_PORT=10085
CLIENTS_FILE_PATH=/etc/just1knode/clients.json
RELAYS_FILE_PATH=/etc/just1knode/relays.json
XRAY_INBOUND_TAGS=inbound-default
EOF

    # Копирование исходных файлов xray-api
    if [[ -d "${SCRIPT_DIR}/xray_api" ]]; then
        cp -r "${SCRIPT_DIR}/xray_api/"* "$XRAY_API_DIR/"
    fi

    # Создание venv для xray-api и установка точных версий
    python3 -m venv "${XRAY_API_DIR}/venv"
    "${XRAY_API_DIR}/venv/bin/pip" install --upgrade pip -q
    "${XRAY_API_DIR}/venv/bin/pip" install fastapi==0.115.6 uvicorn==0.34.0 grpcio==1.68.1 protobuf==7.35.1 pydantic==2.10.4 -q

    # Systemd служба для xray-api
    cat > /etc/systemd/system/xray-api.service <<EOF
[Unit]
Description=Just1kBot Xray API Agent
After=network.target xray.service

[Service]
Type=simple
User=root
WorkingDirectory=${XRAY_API_DIR}
EnvironmentFile=/etc/xray-api/config.env
ExecStart=${XRAY_API_DIR}/venv/bin/uvicorn app:app --host 127.0.0.1 --port 5001 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now xray-api

    # Настройка Nginx с маппингом OPTIONS->POST и Zero Buffering
    log "Настройка Nginx reverse proxy..."
    cat > /etc/nginx/conf.d/xhttp-map.conf <<EOF
map \$request_method \$xhttp_proxy_method {
    OPTIONS POST;
    default \$request_method;
}
EOF

    mkdir -p /etc/nginx/just1k_relays.d

    # Конфигурация default location
    cat > /etc/nginx/just1k_relays.d/default.conf <<EOF
    location ${secret_path}/default {
        proxy_pass http://127.0.0.1:8003;
        proxy_method \$xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass_request_headers on;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF

    cat > /etc/nginx/sites-available/xhttp-origin.conf <<EOF
# 1. CDN Ingress (Порт 443 — входящий трафик от клиентов через CDN)
server {
    listen 443 ssl http2;
    server_name ${domain};

    ssl_certificate /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Эндпоинт для проверки здоровья CDN
    location = /cdn-check {
        add_header Content-Type text/plain;
        return 204;
    }

    # Динамические Relay-локации
    include /etc/nginx/just1k_relays.d/*.conf;

    # Защитный fallback для неопознанных путей
    location / {
        return 404;
    }
}

# 2. Management API (Порт 8444 — управление нодой для Just1kBot)
server {
    listen 8444 ssl http2;
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

    ln -sf /etc/nginx/sites-available/xhttp-origin.conf /etc/nginx/sites-enabled/
    nginx -t
    systemctl restart nginx

    # UFW фаервол
    log "Настройка UFW фаервола..."
    ufw allow 22/tcp || true
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
    ufw allow 8444/tcp || true
    ufw --force enable || true

    set_state_val "role" "origin"
    set_state_val "domain" "$domain"
    set_state_val "secret_base_path" "$secret_path"
    set_state_val "api_url" "https://${domain}:8444"
    set_state_val "api_key" "$api_key"

    title "УСТАНОВКА ORIGIN УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Данные для добавления Origin в Telegram-боте (/admin):${NC}"
    echo -e "  🌐 Origin Домен:      ${CYAN}${domain}${NC}"
    echo -e "  🔗 API URL бота:      ${CYAN}https://${domain}:8444${NC}"
    echo -e "  🔑 API Ключ:          ${YELLOW}${api_key}${NC}"
    echo -e "  🛡️ Секретный префикс: ${MAGENTA}${secret_path}${NC}"
    echo -e "  🩺 Проверка CDN:      curl -X OPTIONS https://${domain}/cdn-check\n"
}

# =============================================================================
# РЕЖИМ 3: УСТАНОВКА WHITE INTERNET RELAY УЗЛА (ЗАРУБЕЖНЫЙ ВЫХОД VLESS REALITY)
# =============================================================================
install_xray_relay_node() {
    title "УСТАНОВКА WHITE INTERNET RELAY УЗЛА (Зарубежный выход VLESS REALITY)"
    check_root
    init_state_dir
    install_base_deps

    local relay_port="${1:-10443}"
    local origin_ip="${2:-}"
    local dest_server="${3:-www.google.com}"

    if [[ -z "$origin_ip" ]]; then
        read -rp "Введите IP-адрес Origin-сервера в РФ (для защиты UFW): " origin_ip
    fi
    if [[ -z "$origin_ip" ]]; then error "IP-адрес Origin обязателен для настройки защиты."; fi

    # Установка Xray-core с проверкой SHA-256
    local tmp_zip="/tmp/xray_relay.zip"
    download_and_verify_xray "$tmp_zip"

    mkdir -p "$XRAY_CONFIG_DIR" "$XRAY_SHARE_DIR"
    unzip -q -o "$tmp_zip" xray -d /usr/local/bin/
    unzip -q -o "$tmp_zip" geoip.dat geosite.dat -d "$XRAY_SHARE_DIR/" || true
    rm -f "$tmp_zip"
    chmod +x "$XRAY_BIN"

    # Генерация UUID и ключей VLESS REALITY
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

    # Конфигурация Relay Inbound (VLESS-Vision REALITY) и Outbound Freedom
    log "Формирование конфигурации Relay ноды (VLESS REALITY)..."
    cat > "$XRAY_CONFIG" <<EOF
{
  "log": {
    "loglevel": "warning"
  },
  "inbounds": [
    {
      "tag": "relay-in",
      "port": ${relay_port},
      "listen": "0.0.0.0",
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
      }
    }
  ],
  "outbounds": [
    {
      "tag": "direct",
      "protocol": "freedom"
    }
  ]
}
EOF

    # Systemd служба
    cat > /etc/systemd/system/xray.service <<EOF
[Unit]
Description=Xray Relay Service
Documentation=https://github.com/xtls
After=network.target nss-lookup.target

[Service]
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartPreventExitStatus=23
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now xray

    # UFW фаервол: закрываем порт туннеля для всех, кроме Origin IP
    log "Настройка UFW фаервола (доступ к порту $relay_port открыт строго с $origin_ip)..."
    ufw allow 22/tcp || true
    ufw allow from "$origin_ip" to any port "$relay_port" proto tcp || true
    ufw --force enable || true

    local my_ip
    my_ip="$(curl -s4 https://api.ipify.org || echo "IP_НЕ_ОПРЕДЕЛЕН")"

    set_state_val "role" "relay"
    set_state_val "relay_port" "$relay_port"
    set_state_val "origin_ip" "$origin_ip"
    set_state_val "tunnel_uuid" "$tunnel_uuid"
    set_state_val "public_key" "$public_key"
    set_state_val "short_id" "$short_id"
    set_state_val "sni" "$dest_server"

    title "УСТАНОВКА RELAY УЗЛА УСПЕШНО ЗАВЕРШЕНА!"
    echo -e "${BOLD}Команда для добавления этого Relay на вашем Origin-сервере:${NC}"
    echo -e "${GREEN}just1knode relay add \"Германия\" ${my_ip} ${relay_port} ${tunnel_uuid} \"de\" \"${public_key}\" \"${short_id}\" \"${dest_server}\"${NC}\n"
}

# =============================================================================
# РЕЖИМ 4: УПРАВЛЕНИЕ RELAY-НОДАМИ НА ORIGIN (ДОБАВИТЬ / УДАЛИТЬ / СПИСОК)
# =============================================================================
manage_relays_menu() {
    title "УПРАВЛЕНИЕ RELAY-НОДАМИ НА ORIGIN"
    check_root
    init_state_dir

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        warn "Данный сервер не настроен как Origin (текущая роль: ${role:-не установлена})."
    fi

    echo -e "  ${BOLD}[1]${NC} ➕ Добавить новый Relay-узел"
    echo -e "  ${BOLD}[2]${NC} ➖ Удалить Relay-узел"
    echo -e "  ${BOLD}[3]${NC} 📋 Список активных Relay-узлов"
    echo -e "  ${BOLD}[0]${NC} ⬅️  Назад в главное меню"
    echo ""
    read -rp "Выберите действие [0-3]: " r_choice

    case "$r_choice" in
        1)
            read -rp "Название локации (например: Германия): " r_name
            read -rp "IP-адрес Relay сервера: " r_ip
            read -rp "Порт Relay сервера [по умолчанию: 10443]: " r_port
            r_port="${r_port:-10443}"
            read -rp "UUID туннеля Relay: " r_uuid
            read -rp "Код страны (например: de, nl, se) [по умолчанию: de]: " r_code
            r_code="${r_code:-de}"
            read -rp "REALITY Public Key: " r_pubkey
            read -rp "REALITY Short ID: " r_shortid
            read -rp "REALITY SNI / ServerName [по умолчанию: www.google.com]: " r_sni
            r_sni="${r_sni:-www.google.com}"
            add_relay_node "$r_name" "$r_ip" "$r_port" "$r_uuid" "$r_code" "$r_pubkey" "$r_shortid" "$r_sni"
            ;;
        2)
            read -rp "Введите код страны или имя Relay для удаления: " r_del
            remove_relay_node "$r_del"
            ;;
        3)
            list_relays
            ;;
        0)
            return
            ;;
        *)
            error "Неверный выбор."
            ;;
    esac
}

add_relay_node() {
    local name="$1"
    local ip="$2"
    local port="$3"
    local uuid="$4"
    local code="${5:-de}"
    local pubkey="${6:-}"
    local shortid="${7:-}"
    local sni="${8:-www.google.com}"

    # Санитизация кода страны во избежание path traversal
    if [[ ! "$code" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        error "Недопустимый код страны: $code (разрешены только буквы, цифры, дефис и подчеркивание)."
    fi

    log "Добавление Relay-узла: $name ($code) -> $ip:$port (REALITY SNI: $sni)..."

    python3 -c "
import sys, json, os

relays_file = '$RELAYS_FILE'
xray_conf_file = '$XRAY_CONFIG'
state_file = '$STATE_FILE'
relays_d = '/etc/nginx/just1k_relays.d'
os.makedirs(relays_d, exist_ok=True)

with open(state_file, 'r', encoding='utf-8') as f:
    state = json.load(f)
base_path = state.get('secret_base_path', '/stream').rstrip('/')

try:
    with open(relays_file, 'r', encoding='utf-8') as f:
        relays = json.load(f)
except Exception:
    relays = []

# Фильтруем дубликаты
relays = [r for r in relays if r.get('code') != '$code']

# Вычисление нового свободного порта (8003, 8004...)
used_ports = [int(r.get('inbound_port', 8003)) for r in relays]
new_port = 8004
while new_port in used_ports:
    new_port += 1

loc_path = f'{base_path}/$code'
new_relay = {
    'name': '$name',
    'code': '$code',
    'ip': '$ip',
    'port': int('$port'),
    'uuid': '$uuid',
    'inbound_port': new_port,
    'inbound_tag': f'inbound-$code',
    'outbound_tag': f'outbound-$code',
    'path': loc_path,
    'public_key': '$pubkey',
    'short_id': '$shortid',
    'sni': '$sni'
}
relays.append(new_relay)

with open(relays_file, 'w', encoding='utf-8') as f:
    json.dump(relays, f, indent=2)

# Обновление Xray конфигурации
with open(xray_conf_file, 'r', encoding='utf-8') as f:
    xray_conf = json.load(f)

xray_conf['inbounds'] = [ib for ib in xray_conf.get('inbounds', []) if ib.get('tag') != f'inbound-$code']
xray_conf['outbounds'] = [ob for ob in xray_conf.get('outbounds', []) if ob.get('tag') != f'outbound-$code']
xray_conf.setdefault('routing', {}).setdefault('rules', [])
xray_conf['routing']['rules'] = [r for r in xray_conf['routing']['rules'] if r.get('outboundTag') != f'outbound-$code']

# Inbound (XHTTP от Nginx)
xray_conf['inbounds'].append({
    'tag': f'inbound-$code',
    'listen': '127.0.0.1',
    'port': new_port,
    'protocol': 'vless',
    'settings': {
        'clients': [],
        'decryption': 'none'
    },
    'streamSettings': {
        'network': 'xhttp',
        'xhttpSettings': {
            'mode': 'packet-up',
            'path': loc_path
        }
    }
})

# Outbound к Relay (VLESS REALITY TCP)
outbound_settings = {
    'vnext': [{
        'address': '$ip',
        'port': int('$port'),
        'users': [{
            'id': '$uuid',
            'flow': 'xtls-rprx-vision',
            'encryption': 'none'
        }]
    }]
}

stream_settings = {
    'network': 'tcp',
    'security': 'reality',
    'realitySettings': {
        'show': False,
        'fingerprint': 'chrome',
        'serverName': '$sni',
        'publicKey': '$pubkey',
        'shortId': '$shortid',
        'spiderX': ''
    }
}

xray_conf['outbounds'].insert(0, {
    'tag': f'outbound-$code',
    'protocol': 'vless',
    'settings': outbound_settings,
    'streamSettings': stream_settings
})

# Правило маршрутизации
xray_conf['routing']['rules'].append({
    'type': 'field',
    'inboundTag': [f'inbound-$code'],
    'outboundTag': f'outbound-$code'
})

with open(xray_conf_file, 'w', encoding='utf-8') as f:
    json.dump(xray_conf, f, indent=2)

# Генерация Nginx Location файла напрямую с точными loc_path и new_port
nginx_loc_content = f'''    location {loc_path} {{
        proxy_pass http://127.0.0.1:{new_port};
        proxy_method \$xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection \"\";
        proxy_pass_request_headers on;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }}
'''
with open(f'{relays_d}/$code.conf', 'w', encoding='utf-8') as f:
    f.write(nginx_loc_content)

# Обновление тегов инбаундов для xray-api
env_file = '/etc/xray-api/config.env'
tags = [r['inbound_tag'] for r in relays]
if 'inbound-default' not in tags and any(ib.get('tag') == 'inbound-default' for ib in xray_conf.get('inbounds', [])):
    tags.insert(0, 'inbound-default')

if os.path.exists(env_file):
    with open(env_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    with open(env_file, 'w', encoding='utf-8') as f:
        for line in lines:
            if line.startswith('XRAY_INBOUND_TAGS='):
                continue
            f.write(line)
        f.write('XRAY_INBOUND_TAGS=' + ','.join(tags) + '\n')
"

    # Валидация и перезапуск служб
    nginx -t && systemctl reload nginx
    "$XRAY_BIN" run -test -config "$XRAY_CONFIG"
    systemctl restart xray
    systemctl restart xray-api

    log "Relay-узел $name ($code) успешно добавлен и активирован!"
}

remove_relay_node() {
    local target="$1"
    log "Удаление Relay-узла: $target..."

    python3 -c "
import sys, json, os

relays_file = '$RELAYS_FILE'
xray_conf_file = '$XRAY_CONFIG'
relays_d = '/etc/nginx/just1k_relays.d'

try:
    with open(relays_file, 'r', encoding='utf-8') as f:
        relays = json.load(f)
except Exception:
    relays = []

matched = [r for r in relays if r.get('code') == '$target' or r.get('name') == '$target']
relays = [r for r in relays if r.get('code') != '$target' and r.get('name') != '$target']

with open(relays_file, 'w', encoding='utf-8') as f:
    json.dump(relays, f, indent=2)

for m in matched:
    code = m.get('code', '$target')
    conf_path = f'{relays_d}/{code}.conf'
    if os.path.exists(conf_path):
        try: os.remove(conf_path)
        except Exception: pass

    # Очистка Xray config
    if os.path.exists(xray_conf_file):
        try:
            with open(xray_conf_file, 'r', encoding='utf-8') as f:
                xray_conf = json.load(f)
            xray_conf['inbounds'] = [ib for ib in xray_conf.get('inbounds', []) if ib.get('tag') != f'inbound-{code}']
            xray_conf['outbounds'] = [ob for ob in xray_conf.get('outbounds', []) if ob.get('tag') != f'outbound-{code}']
            if 'routing' in xray_conf and 'rules' in xray_conf['routing']:
                xray_conf['routing']['rules'] = [r for r in xray_conf['routing']['rules'] if r.get('outboundTag') != f'outbound-{code}']
            with open(xray_conf_file, 'w', encoding='utf-8') as f:
                json.dump(xray_conf, f, indent=2)
        except Exception as e:
            print('Error cleaning xray config:', e)

# Обновление env файла
env_file = '/etc/xray-api/config.env'
tags = [r['inbound_tag'] for r in relays]
if os.path.exists(env_file):
    with open(env_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    with open(env_file, 'w', encoding='utf-8') as f:
        for line in lines:
            if line.startswith('XRAY_INBOUND_TAGS='):
                continue
            f.write(line)
        f.write('XRAY_INBOUND_TAGS=' + ','.join(tags) + '\n')
"

    nginx -t && systemctl reload nginx || true
    "$XRAY_BIN" run -test -config "$XRAY_CONFIG" || true
    systemctl restart xray || true
    systemctl restart xray-api || true
    log "Relay $target удален."
}

list_relays() {
    title "СПИСОК АКТИВНЫХ RELAY-УЗЛОВ"
    python3 -c "
import json
relays_file = '$RELAYS_FILE'
try:
    with open(relays_file, 'r', encoding='utf-8') as f: relays = json.load(f)
    if not relays:
        print('Relay-узлы еще не настроены.')
    else:
        print(f'{\"Страна/Имя\":<18} {\"Код\":<6} {\"IP:Порт\":<22} {\"Локальный\":<10} {\"XHTTP Путь\":<20} {\"REALITY SNI\"}')
        print('-'*95)
        for r in relays:
            print(f\"{r.get('name', ''):<18} {r.get('code', ''):<6} {r.get('ip', '')}:{r.get('port', ''):<15} {r.get('inbound_port', ''):<10} {r.get('path', ''):<20} {r.get('sni', 'www.google.com')}\")
except Exception as e:
    print('Ошибка чтения списка релеев:', e)
"
}

# =============================================================================
# РЕЖИМ 5: СТАТУС УЗЛА И АКТИВНЫЕ КЛИЕНТЫ
# =============================================================================
show_status() {
    title "СТАТУС СЕРВЕРНОГО УЗЛА"
    local role
    role="$(get_state_val "role" "не определена")"
    echo -e "  🔧 Роль узла:        ${BOLD}${CYAN}${role}${NC}"
    echo -e "  🌐 Домен узла:       $(get_state_val "domain" "не настроен")"
    echo -e "  🔗 API URL:          $(get_state_val "api_url" "не настроен")"

    echo -e "\n${BOLD}Состояние служб systemd:${NC}"
    for srv in nginx xray xray-api amnezia-api; do
        if systemctl is-active --quiet "$srv" 2>/dev/null; then
            echo -e "  [✔] ${srv}: ${GREEN}работает (active)${NC}"
        else
            echo -e "  [✗] ${srv}: ${RED}остановлен / не установлен${NC}"
        fi
    done

    echo -e "\n${BOLD}База активных клиентов (Zero-Loss):${NC}"
    if [[ -f "$CLIENTS_FILE" ]]; then
        python3 -c "
import json
with open('$CLIENTS_FILE', 'r') as f: d = json.load(f)
clients = d.get('clients', [])
print(f'  Количество активных UUID: {len(clients)}')
"
    fi

    if [[ "$role" == "origin" ]]; then
        echo ""
        list_relays
    fi
}

# =============================================================================
# РЕЖИМ 6: КОМПЛЕКСНАЯ САМОДИАГНОСТИКА (DOCTOR)
# =============================================================================
run_doctor() {
    title "КОМПЛЕКСНАЯ САМОДИАГНОСТИКА (DOCTOR)"
    local failed=0

    log "1. Проверка системных служб..."
    for srv in nginx xray; do
        if systemctl is-active --quiet "$srv" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Служба $srv активна"
        else
            echo -e "  ${RED}✗${NC} Служба $srv не активна"
            failed=$((failed + 1))
        fi
    done

    log "2. Проверка gRPC порта Xray (127.0.0.1:10085)..."
    if socat - /dev/null,connect_timeout=2 TCP:127.0.0.1:10085 2>/dev/null; then
        echo -e "  ${GREEN}✔${NC} gRPC сокет Xray отвечает"
    else
        echo -e "  ${RED}✗${NC} gRPC сокет Xray недоступен"
        failed=$((failed + 1))
    fi

    log "3. Проверка конфигурации Xray..."
    if [[ -f "$XRAY_CONFIG" ]] && "$XRAY_BIN" run -test -config "$XRAY_CONFIG" 2>/dev/null; then
        echo -e "  ${GREEN}✔${NC} Конфигурация Xray валидна"
    else
        echo -e "  ${RED}✗${NC} Ошибка конфигурации Xray"
        failed=$((failed + 1))
    fi

    log "4. Проверка синтаксиса Nginx..."
    if nginx -t 2>/dev/null; then
        echo -e "  ${GREEN}✔${NC} Конфигурация Nginx корректна"
    else
        echo -e "  ${RED}✗${NC} Ошибка синтаксиса Nginx"
        failed=$((failed + 1))
    fi

    log "5. Проверка SSL сертификатов Let's Encrypt..."
    local domain
    domain="$(get_state_val "domain")"
    if [[ -n "$domain" && -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]]; then
        local exp_date
        exp_date="$(openssl x509 -enddate -noout -in "/etc/letsencrypt/live/${domain}/fullchain.pem" | cut -d= -f2)"
        echo -e "  ${GREEN}✔${NC} SSL сертификат для $domain валиден до: $exp_date"
    else
        echo -e "  ${YELLOW}i${NC} SSL сертификат для домена $domain не найден"
    fi

    log "6. Проверка UFW фаервола..."
    if ufw status | grep -q "Status: active"; then
        echo -e "  ${GREEN}✔${NC} UFW фаервол активен и защищает порты"
    else
        echo -e "  ${YELLOW}!${NC} UFW фаервол не активен"
    fi

    if [[ $failed -eq 0 ]]; then
        echo -e "\n${BOLD}${GREEN}Все проверки пройдены успешно! Узел полностью здоров.${NC}\n"
    else
        echo -e "\n${BOLD}${RED}Обнаружено ошибок: ${failed}. Требуется внимание администратора.${NC}\n"
    fi
}

# =============================================================================
# РЕЖИМ 7: БЕЗОПАСНОЕ ОБНОВЛЕНИЕ XRAY-CORE С ОТКАТОМ
# =============================================================================
update_xray() {
    title "ОБНОВЛЕНИЕ ЯДРА XRAY-CORE"
    check_root
    init_state_dir
    log "Текущая версия Xray: $($XRAY_BIN version 2>/dev/null | head -n 1 || echo 'не установлена')"

    local tmp_zip="/tmp/xray_update.zip"
    download_and_verify_xray "$tmp_zip"

    mkdir -p /tmp/xray_new
    unzip -q -o "$tmp_zip" xray -d /tmp/xray_new/
    chmod +x /tmp/xray_new/xray

    log "Проверка текущей конфигурации новым бинарником..."
    if /tmp/xray_new/xray run -test -config "$XRAY_CONFIG"; then
        log "Тест пройден успешно. Создание резервной копии старого бинарника..."
        local backup_bin
        backup_bin="${BACKUP_DIR}/xray_$(date +%Y%m%d_%H%M%S).bak"
        if [[ -f "$XRAY_BIN" ]]; then
            cp "$XRAY_BIN" "$backup_bin"
        fi

        log "Применение обновления..."
        cp /tmp/xray_new/xray "$XRAY_BIN"
        systemctl restart xray

        if systemctl is-active --quiet xray; then
            log "Обновление завершено успешно! Версия: $($XRAY_BIN version | head -n 1)"
        else
            warn "Xray не запустился после обновления! Выполняем откат на предыдущую версию..."
            if [[ -f "$backup_bin" ]]; then
                cp "$backup_bin" "$XRAY_BIN"
                systemctl restart xray
                log "Откат завершен."
            fi
            error "Обновление прервано из-за сбоя запуска службы."
        fi
    else
        error "Тест новой версии провалился. Обновление отменено."
    fi
    rm -rf "$tmp_zip" /tmp/xray_new
}

# =============================================================================
# ГЛАВНОЕ ИНТЕРАКТИВНОЕ МЕНЮ
# =============================================================================
main_menu() {
    check_root
    init_state_dir

    while true; do
        clear
        echo -e "${BOLD}${BLUE}"
        echo "┌─────────────────────────────────────────────────────────────┐"
        echo "│                 🚀 JUST1KNODE CONTROL PANEL                 │"
        echo "│              Менеджер серверных узлов Just1kBot             │"
        echo "└─────────────────────────────────────────────────────────────┘"
        echo -e "${NC}"

        local cur_role
        cur_role="$(get_state_val "role" "не настроен")"
        echo -e "  Статус текущего сервера: ${BOLD}${CYAN}${cur_role}${NC}\n"

        echo -e "  ${BOLD}[1]${NC} 🚀 Установить Amnezia API узел (AmneziaWG 2.0 для обычной подписки)"
        echo -e "  ${BOLD}[2]${NC} 🌐 Установить Origin узел (Белый Интернет — Входной шлюз в РФ)"
        echo -e "  ${BOLD}[3]${NC} 🛡️  Установить Relay узел (Белый Интернет — Зарубежный выход VLESS REALITY)"
        echo -e "  ${BOLD}[4]${NC} 🔄 Управление Relay-узлами на Origin (Добавить / Удалить / Список)"
        echo -e "  ${BOLD}[5]${NC} 📊 Статус узла и активные клиенты"
        echo -e "  ${BOLD}[6]${NC} 🩺 Комплексная самодиагностика (Doctor: DNS, SSL, Xray, UFW)"
        echo -e "  ${BOLD}[7]${NC} 🔄 Обновление ядра Xray-core"
        echo -e "  ${BOLD}[0]${NC} ❌ Выход"
        echo ""
        read -rp "Выберите действие [0-7]: " choice

        case "$choice" in
            1) install_amnezia_api_node; read -rp "Нажмите Enter для продолжения...";;
            2) install_xray_origin_node; read -rp "Нажмите Enter для продолжения...";;
            3) install_xray_relay_node; read -rp "Нажмите Enter для продолжения...";;
            4) manage_relays_menu; read -rp "Нажмите Enter для продолжения...";;
            5) show_status; read -rp "Нажмите Enter для продолжения...";;
            6) run_doctor; read -rp "Нажмите Enter для продолжения...";;
            7) update_xray; read -rp "Нажмите Enter для продолжения...";;
            0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
            *) warn "Неверный выбор. Повторите ввод."; sleep 1;;
        esac
    done
}

# --- Точка входа ---
if [[ $# -eq 0 ]]; then
    main_menu
else
    case "$1" in
        install)
            case "${2:-}" in
                amnezia) install_amnezia_api_node "${3:-}" "${4:-}" ;;
                origin|xray-origin) install_xray_origin_node "${3:-}" "${4:-}" "${5:-}" "${6:-}" ;;
                relay|xray-relay|exit|xray-exit) install_xray_relay_node "${3:-10443}" "${4:-}" "${5:-www.google.com}" ;;
                *) error "Неизвестный тип установки: $2. Используйте: amnezia, origin, relay" ;;
            esac
            ;;
        relay)
            case "${2:-}" in
                add) add_relay_node "${3:-}" "${4:-}" "${5:-10443}" "${6:-}" "${7:-de}" "${8:-}" "${9:-}" "${10:-www.google.com}" ;;
                remove|del) remove_relay_node "${3:-}" ;;
                list) list_relays ;;
                *) manage_relays_menu ;;
            esac
            ;;
        status) show_status ;;
        doctor) run_doctor ;;
        update) update_xray ;;
        *) error "Неизвестная команда: $1. Запустите 'just1knode' без аргументов для входа в интерактивное меню." ;;
    esac
fi
