#!/usr/bin/env bash
# =============================================================================
# JUST1KBOT - Утилита управления серверными узлами (just1knode)
# =============================================================================
#
# Поддерживаемые команды:
#   just1knode install amnezia     - Установка только Amnezia API с Nginx и Certbot
#   just1knode install xray-origin - Установка Xray Origin (Xray, Geodata, XHTTP, API)
#   just1knode install xray-exit   - Установка Xray Exit (Xray Vision Inbound, Firewall)
#   just1knode update xray         - Безопасное обновление ядра Xray с rollback
#   just1knode doctor              - Комплексная самодиагностика узла
#   just1knode status              - Текущее состояние и конфигурация узла
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="/etc/just1knode"
STATE_FILE="${STATE_DIR}/state.json"
XRAY_VERSION_PINNED="26.7.28"
XRAY_BIN="/usr/local/bin/xray"
XRAY_CONFIG_DIR="/usr/local/etc/xray"
XRAY_CONFIG="${XRAY_CONFIG_DIR}/config.json"
XRAY_SHARE_DIR="/usr/local/share/xray"
CERTBOT_WEBROOT="/var/www/certbot"
XRAY_API_DIR="/opt/xray-api"

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

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Скрипт должен быть запущен с правами суперпользователя (root или sudo)."
    fi
}

# --- Управление состоянием узла (/etc/just1knode/state.json) ---
init_state_file() {
    mkdir -p "$STATE_DIR"
    chmod 700 "$STATE_DIR"
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "{}" > "$STATE_FILE"
        chmod 600 "$STATE_FILE"
    fi
}

set_state_val() {
    local key="$1"
    local val="$2"
    init_state_file
    python3 -c "
import sys, json, os, tempfile
from datetime import datetime, timezone
f, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(f, 'r', encoding='utf-8') as fp:
        data = json.load(fp)
except Exception:
    data = {}
data[k] = v
data['updated_at'] = datetime.now(timezone.utc).isoformat()
dir_name = os.path.dirname(f)
tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix='state_', suffix='.tmp')
with os.fdopen(tmp_fd, 'w', encoding='utf-8') as fp:
    json.dump(data, fp, indent=2)
    fp.flush()
    os.fsync(fp.fileno())
os.replace(tmp_path, f)
try:
    dfd = os.open(dir_name, os.O_RDONLY)
    os.fsync(dfd)
    os.close(dfd)
except Exception:
    pass
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
    with open(f, 'r', encoding='utf-8') as fp:
        data = json.load(fp)
    print(data.get(k, d))
except Exception:
    print(d)
" "$STATE_FILE" "$key" "$default_val"
}


show_state() {
    title "СОСТОЯНИЕ УЗЛА (${STATE_FILE})"
    if [[ -f "$STATE_FILE" ]]; then
        python3 -m json.tool "$STATE_FILE" 2>/dev/null || cat "$STATE_FILE"
    else
        warn "Файл состояния не найден. Узел еще не настроен."
    fi
}

# --- Определение архитектуры процессора ---
get_arch() {
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64)
            echo "64"
            ;;
        aarch64|arm64)
            echo "arm64-v8a"
            ;;
        *)
            error "Неподдерживаемая архитектура: $arch"
            ;;
    esac
}

# --- Установка базовых пакетов ОС ---
install_common_deps() {
    info "Обновление индексов пакетов и установка зависимостей..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl wget unzip nginx certbot ufw python3 python3-pip python3-venv socat
    mkdir -p "$CERTBOT_WEBROOT"
    chmod 755 "$CERTBOT_WEBROOT"
}

# --- Проверка контрольной суммы SHA-256 Xray ---
verify_xray_checksum() {
    local file_path="$1"
    local version="$2"
    local arch="$3"
    local expected_sha=""
    if [[ "$version" == "26.7.28" ]]; then
        case "$arch" in
            64) expected_sha="8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40" ;;
            arm64-v8a) expected_sha="f5698bb218ada3b4022db26fafc39601c5f53b46b19eb76c9616325985807501" ;;
        esac
    fi
    if [[ -n "$expected_sha" ]]; then
        local actual_sha
        actual_sha="$(sha256sum "$file_path" | awk '{print $1}')"
        if [[ "$actual_sha" != "$expected_sha" ]]; then
            error "Контрольная сумма SHA-256 для $file_path не совпадает! Ожидалось: $expected_sha, получено: $actual_sha"
        fi
        log "Контрольная сумма SHA-256 проверена и совпадает: ${actual_sha}"
    else
        local dgst_url
        dgst_url="https://github.com/XTLS/Xray-core/releases/download/v${version}/$(basename "$file_path").dgst"
        local tmp_dgst="${file_path}.dgst"
        if ! wget -q --timeout=15 -O "$tmp_dgst" "$dgst_url"; then
            rm -f "$tmp_dgst"
            error "Не удалось скачать файл контрольных сумм .dgst из официального репозитория GitHub (${dgst_url}). Установка прервана."
        fi
        local expected_sha_dgst
        expected_sha_dgst="$(grep -i 'SHA2-256=' "$tmp_dgst" 2>/dev/null | awk '{print $2}')"
        rm -f "$tmp_dgst"
        if [[ -z "$expected_sha_dgst" ]]; then
            error "В файле .dgst не найдена контрольная сумма SHA2-256. Установка прервана."
        fi
        local actual_sha
        actual_sha="$(sha256sum "$file_path" | awk '{print $1}')"
        if [[ "$actual_sha" != "$expected_sha_dgst" ]]; then
            error "Контрольная сумма SHA-256 из .dgst не совпадает! Ожидалось: $expected_sha_dgst, получено: $actual_sha"
        fi
        log "Контрольная сумма SHA-256 из официального .dgst проверена: ${actual_sha}"
    fi
}

# --- Скачивание и установка бинарника Xray ---
install_xray_core() {
    local version="${1:-$XRAY_VERSION_PINNED}"
    local arch
    arch="$(get_arch)"
    local zip_name="Xray-linux-${arch}.zip"
    local download_url="https://github.com/XTLS/Xray-core/releases/download/v${version}/${zip_name}"
    local tmp_dir="/tmp/xray-install-$$"

    info "Скачивание Xray ${version} (${arch})..."
    mkdir -p "$tmp_dir"
    if ! wget -q --timeout=30 -O "${tmp_dir}/${zip_name}" "$download_url"; then
        error "Не удалось скачать Xray core ${version} из официального релиза GitHub (${download_url}). Установка прервана."
    fi

    verify_xray_checksum "${tmp_dir}/${zip_name}" "$version" "$arch"


    unzip -q -o "${tmp_dir}/${zip_name}" -d "$tmp_dir"
    install -m 755 "${tmp_dir}/xray" "$XRAY_BIN"
    mkdir -p "$XRAY_CONFIG_DIR" "$XRAY_SHARE_DIR" "/var/log/xray"
    rm -rf "$tmp_dir"

    log "Xray ${version} успешно установлен в ${XRAY_BIN}"
    "$XRAY_BIN" version | head -n 2
}


# --- Скачивание Geodata (geoip.dat, geosite.dat от Loyalsoldier) ---
install_geodata() {
    info "Установка актуальных geodata (Loyalsoldier/v2ray-rules-dat)..."
    mkdir -p "$XRAY_SHARE_DIR"
    local base_url="https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download"
    local tmp_dir="/tmp/geodata-$$"
    mkdir -p "$tmp_dir"

    for file in geoip.dat geosite.dat; do
        if ! curl -fsSL --connect-timeout 30 -o "${tmp_dir}/${file}" "${base_url}/${file}"; then
            error "Не удалось скачать ${file} из ${base_url}/${file}. Установка прервана."
        fi
        if ! curl -fsSL --connect-timeout 20 -o "${tmp_dir}/${file}.sha256sum" "${base_url}/${file}.sha256sum"; then
            error "Не удалось скачать контрольную сумму ${file}.sha256sum. Установка прервана."
        fi
        (cd "$tmp_dir" && sha256sum -c "${file}.sha256sum" >/dev/null 2>&1) || error "Контрольная сумма SHA-256 для ${file} не совпала! Установка прервана."
        log "Контрольная сумма ${file} проверена [OK]"
        install -m 644 "${tmp_dir}/${file}" "${XRAY_SHARE_DIR}/${file}"
    done
    rm -rf "$tmp_dir"
    log "Geodata успешно проверены и размещены в ${XRAY_SHARE_DIR}"
}

# --- Создание systemd-сервиса Xray ---
setup_xray_service() {
    info "Настройка systemd сервиса Xray..."
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
ExecStart=${XRAY_BIN} run -config ${XRAY_CONFIG}
Restart=on-failure
RestartPreventExitStatus=23
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
}

# --- Определение публичного IP текущего сервера ---
get_public_ip() {
    local ip
    ip="$(curl -s4 --max-time 4 https://api.ipify.org 2>/dev/null || curl -s4 --max-time 4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
    echo "${ip:-127.0.0.1}"
}

# --- Проверка формата UUIDv4 (RFC 4122) ---
validate_uuid() {
    local u="$1"
    if [[ "$u" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
        return 0
    fi
    return 1
}

# --- Проверка формата IP или Домена ---
validate_host_or_ip() {
    local h="$1"
    if [[ -z "$h" ]]; then
        return 1
    fi
    if python3 -c "
import sys, ipaddress, re
val = sys.argv[1].strip()
try:
    ipaddress.ip_address(val)
    sys.exit(0)
except ValueError:
    pass
domain_regex = re.compile(r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$')
if domain_regex.match(val):
    sys.exit(0)
sys.exit(1)
" "$h" 2>/dev/null; then
        return 0
    fi
    return 1
}

# --- Проверка резолвинга DNS перед вызовом Certbot ---
check_dns_resolves_to_me() {
    local domain="$1"
    local my_ip
    my_ip="$(get_public_ip)"
    info "Проверка DNS-записи для домена ${domain}..."
    local resolved_ip
    resolved_ip="$(python3 -c "
import sys, socket
try:
    print(socket.gethostbyname(sys.argv[1].strip()))
except Exception:
    pass
" "$domain" 2>/dev/null)"

    if [[ -z "$resolved_ip" ]]; then
        warn "Домен ${domain} пока не резолвится в DNS (запись A не найдена или не обновилась)."
        info "Текущий публичный IP этого сервера: ${my_ip}"
        info "Создайте запись A: '${domain}' ➔ '${my_ip}' в панели управления доменом."
        if [[ -t 0 ]]; then
            read -r -p "Продолжить попытку выпуска сертификата (y/N)? " answer
            if [[ ! "$answer" =~ ^[Yy]$ ]]; then
                error "Установка прервана для настройки DNS."
            fi
        fi
    elif [[ -n "$my_ip" && "$resolved_ip" != "$my_ip" && "$my_ip" != "127.0.0.1" ]]; then
        warn "Внимание: домен ${domain} указывает в DNS на ${resolved_ip}, но публичный IP этого сервера — ${my_ip}!"
        warn "Если трафик не проксируется на этот сервер, Certbot завершится ошибкой валидации."
        if [[ -t 0 ]]; then
            read -r -p "Все равно попытаться выпустить сертификат (y/N)? " answer
            if [[ ! "$answer" =~ ^[Yy]$ ]]; then
                error "Установка прервана для корректировки DNS-записи."
            fi
        fi
    else
        log "DNS проверен: ${domain} успешно указывает на IP этого сервера (${resolved_ip}) [OK]"
    fi
}

# --- Проверка доступности порта Exit-сервера (TCP 10443) ---
check_exit_port_reachable() {
    local host="$1"
    local port="${2:-10443}"
    info "Тестирование сетевой доступности Exit-сервера (${host}:${port})..."
    if python3 -c "
import sys, socket
h, p = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(4.0)
try:
    s.connect((h, p))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" "$host" "$port" 2>/dev/null; then
        log "Exit-сервер (${host}:${port}) доступен и отвечает по TCP [OK]"
    else
        warn "Порт ${port} на Exit-сервере (${host}) сейчас не отвечает."
        info "Возможные причины:"
        info "  1. На Exit-сервере еще не запущен Xray (команда 'just1knode install xray-exit')."
        info "  2. В фаерволе (UFW) Exit-сервера не разрешен IP этого Origin-сервера ($(get_public_ip))."
        info "Установка Origin будет продолжена, но проверьте связь после запуска Exit-сервера."
    fi
}

# --- Получение SSL-сертификата Certbot через единый webroot ---
obtain_ssl_cert() {
    local domain="$1"
    local email="$2"
    check_dns_resolves_to_me "$domain"

    mkdir -p "$CERTBOT_WEBROOT"
    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/conf.d

    # Создаем базовый конфиг HTTP для прохождения ACME челленджа
    cat > /etc/nginx/conf.d/acme-challenge.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    location ^~ /.well-known/acme-challenge/ {
        root ${CERTBOT_WEBROOT};
        default_type "text/plain";
        allow all;
    }
}
EOF
    nginx -t && systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true

    info "Запрос SSL-сертификата Let's Encrypt для домена ${domain}..."
    if certbot certonly --webroot -w "$CERTBOT_WEBROOT" \
        --non-interactive --agree-tos --email "$email" -d "$domain"; then
        log "SSL-сертификат для ${domain} успешно получен!"
    else
        error "Certbot не смог выпустить сертификат для ${domain}. Проверьте A-запись DNS и доступность порта 80. Установка прервана."
    fi

    # Настройка автоматического хука перезагрузки Nginx и Xray при продлении
    mkdir -p /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/reload-all.sh <<'EOF'
#!/bin/sh
set -e
systemctl reload nginx 2>/dev/null || true
systemctl restart xray 2>/dev/null || true
EOF
    chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-all.sh
}

render_nginx_modular_config() {
    local domain="$1"
    local cert_file="$2"
    local key_file="$3"
    local include_amnezia="${4:-false}"
    local include_xray="${5:-false}"
    local xray_api_port="${6:-8444}"

    mkdir -p /etc/nginx/just1k.d /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/conf.d

    cat > /etc/nginx/conf.d/xhttp_map.conf <<'EOF'
map $request_method $just1k_xhttp_proxy_method {
    default $request_method;
    OPTIONS POST;
}
EOF

    if [[ "$include_amnezia" == "true" ]]; then
        cat > /etc/nginx/just1k.d/amnezia-locations.conf <<'EOF'
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
EOF
    fi

    if [[ "$include_xray" == "true" ]]; then
        cat > /etc/nginx/just1k.d/xray-locations.conf <<'EOF'
    location = /cdn-check {
        access_log off;
        add_header X-CDN-Origin "ok" always;
        add_header X-Origin-Method $request_method always;
        return 204;
    }

    location /api/v3/de {
        access_log off;
        proxy_pass http://127.0.0.1:8003;
        proxy_method $just1k_xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass_request_headers on;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /api/v3/nl {
        access_log off;
        proxy_pass http://127.0.0.1:8004;
        proxy_method $just1k_xhttp_proxy_method;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_pass_request_headers on;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
EOF
    fi


    local config_file="/etc/nginx/sites-available/just1k-${domain}.conf"
    cat > "$config_file" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    location ^~ /.well-known/acme-challenge/ {
        root ${CERTBOT_WEBROOT};
        default_type "text/plain";
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${domain};

    ssl_certificate ${cert_file};
    ssl_certificate_key ${key_file};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 0;
    client_header_buffer_size 64k;
    large_client_header_buffers 8 128k;

    proxy_buffering off;
    proxy_request_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;

EOF

    if [[ "$include_xray" == "true" ]]; then
        echo "    include /etc/nginx/just1k.d/xray-locations.conf;" >> "$config_file"
    fi
    if [[ "$include_amnezia" == "true" ]]; then
        echo "    include /etc/nginx/just1k.d/amnezia-locations.conf;" >> "$config_file"
    fi

    cat >> "$config_file" <<EOF
}
EOF

    if [[ "$include_xray" == "true" ]]; then
        cat >> "$config_file" <<EOF

server {
    listen ${xray_api_port} ssl;
    listen [::]:${xray_api_port} ssl;
    server_name ${domain};

    ssl_certificate ${cert_file};
    ssl_certificate_key ${key_file};
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF
    fi

    ln -sfn "$config_file" "/etc/nginx/sites-enabled/just1k-${domain}.conf"
    rm -f /etc/nginx/conf.d/acme-challenge.conf
    nginx -t && systemctl reload nginx
}

# =============================================================================
# 1. КОМАНДА: install amnezia
# =============================================================================
cmd_install_amnezia() {
    title "УСТАНОВКА AMNEZIA API УЗЛА"
    check_root
    install_common_deps

    local domain="${1:-}"
    local email="${2:-}"

    if [[ -z "$domain" ]]; then
        read -r -p "Введите домен для Amnezia API: " domain
    fi
    if [[ -z "$email" ]]; then
        read -r -p "Введите Email для Let's Encrypt: " email
    fi

    [[ -n "$domain" ]] || error "Домен обязателен для установки."
    [[ -n "$email" ]] || error "Email обязателен для установки."

    obtain_ssl_cert "$domain" "$email"

    set_state_val "has_amnezia" "true"
    set_state_val "amnezia_domain" "$domain"
    set_state_val "email" "$email"

    local has_xray
    has_xray="$(get_state_val "has_xray_origin" "false")"
    local xray_dom
    xray_dom="$(get_state_val "origin_domain" "")"
    local include_xray="false"
    if [[ "$has_xray" == "true" && "$xray_dom" == "$domain" ]]; then
        include_xray="true"
    fi

    local cert_dir="/etc/letsencrypt/live/${domain}"
    local cert_file="${cert_dir}/fullchain.pem"
    local key_file="${cert_dir}/privkey.pem"

    render_nginx_modular_config "$domain" "$cert_file" "$key_file" "true" "$include_xray" "8444"

    # Настройка Firewall UFW
    info "Настройка сетевого экрана UFW..."
    ufw allow OpenSSH 2>/dev/null || ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    if [[ "$include_xray" == "true" ]]; then
        local saved_bot_ip
        saved_bot_ip="$(get_state_val "bot_ip" "")"
        if [[ -n "$saved_bot_ip" ]]; then
            ufw allow from "$saved_bot_ip" to any port 8444 proto tcp
        fi
    fi
    ufw --force enable


    # Сохранение состояния
    set_state_val "role" "amnezia"
    set_state_val "domain" "$domain"
    set_state_val "email" "$email"

    set_state_val "certbot_webroot" "$CERTBOT_WEBROOT"
    from_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    set_state_val "installed_at" "$from_ts"

    log "Установка Amnezia API завершена. Домен: https://${domain}"
}

# =============================================================================
# 2. КОМАНДА: install xray-origin
# =============================================================================
cmd_install_xray_origin() {
    title "УСТАНОВКА XRAY ORIGIN УЗЛА"
    check_root
    install_common_deps

    local domain=""
    local bot_ip=""
    local exit_de_host=""
    local exit_de_uuid=""
    local exit_nl_host=""
    local exit_nl_uuid=""
    local email=""
    local api_key=""

    # Разбор параметров CLI (позиционные или именованные)
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --domain) domain="$2"; shift 2 ;;
            --bot-ip) bot_ip="$2"; shift 2 ;;
            --exit-de-host) exit_de_host="$2"; shift 2 ;;
            --exit-de-uuid) exit_de_uuid="$2"; shift 2 ;;
            --exit-nl-host) exit_nl_host="$2"; shift 2 ;;
            --exit-nl-uuid) exit_nl_uuid="$2"; shift 2 ;;
            --email) email="$2"; shift 2 ;;
            --api-key) api_key="$2"; shift 2 ;;
            *)
                # Если позиционные аргументы
                if [[ -z "$domain" ]]; then domain="$1"
                elif [[ -z "$bot_ip" ]]; then bot_ip="$1"
                elif [[ -z "$exit_de_host" ]]; then exit_de_host="$1"
                elif [[ -z "$exit_de_uuid" ]]; then exit_de_uuid="$1"
                elif [[ -z "$exit_nl_host" ]]; then exit_nl_host="$1"
                elif [[ -z "$exit_nl_uuid" ]]; then exit_nl_uuid="$1"
                fi
                shift
                ;;
        esac
    done

    # Интерактивный запрос недостающих параметров с подробными подсказками
    if [[ -z "$domain" ]]; then
        echo -e "${CYAN}📌 Шаг 1/7: Origin-домен${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} Поддомен в вашей DNS-панели (напр. origin.example.com), направленный A-записью на IP этого сервера ($(get_public_ip))."
        read -r -p "Введите Origin-домен: " domain
    fi
    if [[ -z "$bot_ip" ]]; then
        echo -e "${CYAN}📌 Шаг 2/7: IP-адрес хоста Telegram-бота${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} Публичный IP сервера, где запущен бот (выполните 'curl ifconfig.me' на сервере бота)."
        echo -e "   ${YELLOW}🔒 Безопасность:${NC} Порт API 8444 будет открыт в UFW строго для этого IP-адреса."
        read -r -p "Введите IP-адрес Telegram-бота: " bot_ip
    fi
    if [[ -z "$exit_de_host" ]]; then
        echo -e "${CYAN}📌 Шаг 3/7: Хост Exit-сервера Германии (DE)${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} Доменное имя (relay.example.com) или IP-адрес вашего Exit-сервера в Германии."
        read -r -p "Введите хост/IP Exit-сервера Германии: " exit_de_host
    fi
    if [[ -z "$exit_de_uuid" ]]; then
        echo -e "${CYAN}📌 Шаг 4/7: VLESS UUID Exit-сервера Германии${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} UUID, который вы указали при установке 'just1knode install xray-exit' на сервере Германии."
        read -r -p "Введите VLESS UUID Exit DE: " exit_de_uuid
    fi
    if [[ -z "$exit_nl_host" ]]; then
        echo -e "${CYAN}📌 Шаг 5/7: Хост Exit-сервера Нидерландов (NL)${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} Домен/IP второго Exit-сервера (если сервер один, укажите тот же хост Германии: ${exit_de_host})."
        read -r -p "Введите хост/IP Exit NL [по умолчанию: ${exit_de_host}]: " exit_nl_host
        exit_nl_host="${exit_nl_host:-$exit_de_host}"
    fi
    if [[ -z "$exit_nl_uuid" ]]; then
        echo -e "${CYAN}📌 Шаг 6/7: VLESS UUID Exit-сервера Нидерландов${NC}"
        read -r -p "Введите VLESS UUID Exit NL [по умолчанию: ${exit_de_uuid}]: " exit_nl_uuid
        exit_nl_uuid="${exit_nl_uuid:-$exit_de_uuid}"
    fi
    if [[ -z "$email" ]]; then
        echo -e "${CYAN}📌 Шаг 7/7: Email для SSL-сертификата Let's Encrypt${NC}"
        read -r -p "Введите Email [admin@${domain}]: " email
        email="${email:-admin@${domain}}"
    fi
    if [[ -z "$api_key" ]]; then
        api_key="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
        info "Сгенерирован новый XRAY_API_KEY (сохранен в защищенный файл конфигурации)."
    fi

    # Строгая валидация введенных параметров
    [[ -n "$domain" ]] || error "Домен обязателен."
    validate_host_or_ip "$domain" || error "Некорректный формат домена: ${domain}"
    [[ -n "$bot_ip" ]] || error "IP-адрес бота обязателен."
    validate_host_or_ip "$bot_ip" || error "Некорректный IP-адрес бота: ${bot_ip}"
    [[ -n "$exit_de_host" ]] || error "Хост Exit DE обязателен."
    validate_host_or_ip "$exit_de_host" || error "Некорректный хост/IP Exit DE: ${exit_de_host}"
    [[ -n "$exit_de_uuid" ]] || error "UUID Exit DE обязателен."
    validate_uuid "$exit_de_uuid" || error "Некорректный формат UUID для Exit DE: ${exit_de_uuid} (ожидается формат RFC 4122: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
    [[ -n "$exit_nl_host" ]] || error "Хост Exit NL обязателен."
    validate_host_or_ip "$exit_nl_host" || error "Некорректный хост/IP Exit NL: ${exit_nl_host}"
    [[ -n "$exit_nl_uuid" ]] || error "UUID Exit NL обязателен."
    validate_uuid "$exit_nl_uuid" || error "Некорректный формат UUID для Exit NL: ${exit_nl_uuid}"

    # Проверка сетевой связности до Exit-сервера
    check_exit_port_reachable "$exit_de_host" 10443

    # 1. Установка Xray (зафиксированная версия XRAY_VERSION_PINNED)
    install_xray_core "$XRAY_VERSION_PINNED"
    install_geodata
    setup_xray_service

    # 2. Получение SSL сертификата
    obtain_ssl_cert "$domain" "$email"
    local cert_file="/etc/letsencrypt/live/${domain}/fullchain.pem"
    local key_file="/etc/letsencrypt/live/${domain}/privkey.pem"

    # 3. Генерация конфигурации Xray Origin
    info "Генерация конфигурации Xray Origin (${XRAY_CONFIG})..."
    cat > "$XRAY_CONFIG" <<EOF
{
  "log": {
    "loglevel": "warning",
    "access": "/var/log/xray/access.log",
    "error": "/var/log/xray/error.log"
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
      "statsInboundDownlink": true,
      "statsOutboundUplink": true,
      "statsOutboundDownlink": true
    }
  },
  "dns": {
    "servers": [
      {
        "address": "77.88.8.8",
        "port": 53,
        "domains": [
          "domain:ru",
          "geosite:category-ru",
          "geosite:category-gov-ru"
        ],
        "expectIPs": [
          "geoip:ru"
        ]
      },
      {
        "address": "https://1.1.1.1/dns-query",
        "domains": [
          "geosite:geolocation-!cn"
        ]
      },
      {
        "address": "https://dns.google/dns-query",
        "domains": []
      },
      "1.1.1.1",
      "8.8.8.8"
    ]
  },
  "inbounds": [
    {
      "tag": "inbound-de",
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
          "path": "/api/v3/de",
          "mode": "packet-up",
          "uplinkHTTPMethod": "POST",
          "xPaddingObfsMode": true,
          "xPaddingKey": "dc",
          "xPaddingHeader": "X-Cache",
          "xPaddingMethod": "tokenish",
          "xPaddingPlacement": "header"
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"]
      }
    },
    {
      "tag": "inbound-nl",
      "listen": "127.0.0.1",
      "port": 8004,
      "protocol": "vless",
      "settings": {
        "clients": [],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "xhttp",
        "xhttpSettings": {
          "path": "/api/v3/nl",
          "mode": "packet-up",
          "uplinkHTTPMethod": "POST",
          "xPaddingObfsMode": true,
          "xPaddingKey": "dc",
          "xPaddingHeader": "X-Cache",
          "xPaddingMethod": "tokenish",
          "xPaddingPlacement": "header"
        }
      },

      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"]
      }
    },
    {
      "tag": "api",
      "listen": "127.0.0.1",
      "port": 10085,
      "protocol": "dokodemo-door",
      "settings": {
        "address": "127.0.0.1"
      }
    }
  ],
  "outbounds": [
    {
      "tag": "to-exit-de",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${exit_de_host}",
            "port": 10443,
            "users": [
              {
                "id": "${exit_de_uuid}",
                "flow": "xtls-rprx-vision",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "serverName": "${exit_de_host}",
          "allowInsecure": false
        }
      }
    },
    {
      "tag": "to-exit-nl",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${exit_nl_host}",
            "port": 10443,
            "users": [
              {
                "id": "${exit_nl_uuid}",
                "flow": "xtls-rprx-vision",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "serverName": "${exit_nl_host}",
          "allowInsecure": false
        }
      }
    },
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
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      {
        "type": "field",
        "inboundTag": ["api"],
        "outboundTag": "api"
      },
      {
        "type": "field",
        "domain": [
          "domain:ru",
          "geosite:category-ru",
          "geosite:category-gov-ru"
        ],
        "outboundTag": "direct"
      },
      {
        "type": "field",
        "ip": [
          "geoip:ru",
          "geoip:private"
        ],
        "outboundTag": "direct"
      },
      {
        "type": "field",
        "inboundTag": ["inbound-de"],
        "outboundTag": "to-exit-de"
      },
      {
        "type": "field",
        "inboundTag": ["inbound-nl"],
        "outboundTag": "to-exit-nl"
      }
    ]
  }
}
EOF

    # Проверка конфигурации Xray
    info "Проверка синтаксиса конфигурации Xray..."
    "$XRAY_BIN" run -test -config "$XRAY_CONFIG"

    # 4. Конфигурация Nginx Origin (Modular Coexistence)
    info "Настройка модульной конфигурации Nginx..."
    set_state_val "has_xray_origin" "true"
    set_state_val "origin_domain" "$domain"
    set_state_val "role" "xray-origin"

    local has_amn
    has_amn="$(get_state_val "has_amnezia" "false")"
    local amn_dom
    amn_dom="$(get_state_val "amnezia_domain" "")"
    local include_amn="false"
    if [[ "$has_amn" == "true" && "$amn_dom" == "$domain" ]]; then
        include_amn="true"
    fi

    render_nginx_modular_config "$domain" "$cert_file" "$key_file" "$include_amn" "true" "8444"

    # 5. Установка автономного агента xray-api
    info "Установка сервиса xray-api в ${XRAY_API_DIR}..."
    mkdir -p "$XRAY_API_DIR"
    cp -r "${SCRIPT_DIR}/xray_api/"* "$XRAY_API_DIR/"

    python3 -m venv "${XRAY_API_DIR}/venv"
    "${XRAY_API_DIR}/venv/bin/pip" install --upgrade pip -q
    "${XRAY_API_DIR}/venv/bin/pip" install -r "${XRAY_API_DIR}/requirements.txt" -q

    mkdir -p /etc/xray-api
    cat > /etc/xray-api/config.env <<EOF
XRAY_API_KEY=${api_key}
XRAY_GRPC_HOST=127.0.0.1
XRAY_GRPC_PORT=10085
XRAY_INBOUND_TAGS=inbound-de,inbound-nl
EPOCH_FILE_PATH=/var/lib/xray-api/epoch.json
EOF
    chmod 600 /etc/xray-api/config.env

    cp "${XRAY_API_DIR}/xray-api.service" /etc/systemd/system/xray-api.service
    systemctl daemon-reload
    systemctl enable --now xray-api
    systemctl enable --now xray
    systemctl restart xray
    systemctl restart xray-api

    # 6. Настройка UFW
    info "Настройка сетевого экрана UFW..."
    ufw allow OpenSSH 2>/dev/null || ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    # Порт 8444 разрешить СТРОГО для IP бота
    ufw delete allow 8444/tcp 2>/dev/null || true
    if [[ -n "${bot_ip}" ]]; then
        ufw allow from "${bot_ip}" to any port 8444 proto tcp
    fi
    ufw --force enable


    # 7. Сохранение состояния
    set_state_val "bot_ip" "$bot_ip"
    set_state_val "exit_de_host" "$exit_de_host"
    set_state_val "exit_de_uuid" "$exit_de_uuid"
    set_state_val "exit_nl_host" "$exit_nl_host"
    set_state_val "exit_nl_uuid" "$exit_nl_uuid"
    set_state_val "xray_version" "$XRAY_VERSION_PINNED"
    set_state_val "api_port" "8444"
    from_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    set_state_val "installed_at" "$from_ts"

    # 8. Боевое самотестирование API и ядра Xray
    info "Выполнение боевого самотестирования xray-api..."
    sleep 2
    local health_response
    health_response="$(curl -s --max-time 5 -H "X-API-Key: ${api_key}" "http://127.0.0.1:8444/v1/health" 2>/dev/null || true)"
    if echo "$health_response" | grep -q '"status":"ok"'; then
        log "Боевой тест пройден: xray-api успешно отвечает, gRPC подключен к Xray, эпоха ноды инициализирована [OK]"
    else
        warn "xray-api пока не вернул статус 'ok'. Ответ: ${health_response}"
        info "Проверьте логи службы: journalctl -u xray-api -n 20"
    fi

    log "Установка Xray Origin успешно завершена!"
    info "Xray Inbounds: 8003 (/api/v3/de), 8004 (/api/v3/nl)"
    info "API Agent: http://${domain}:8444 (доступен только с IP: ${bot_ip})"
    info "X-API-Key: ${api_key}"
    info "Ключ сохранен в /etc/xray-api/config.env (права 600)"
}

# =============================================================================

# 3. КОМАНДА: install xray-exit
# =============================================================================
cmd_install_xray_exit() {
    title "УСТАНОВКА XRAY EXIT УЗЛА"
    check_root
    install_common_deps

    local origin_ip=""
    local domain=""
    local client_uuid=""
    local email=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --origin-ip) origin_ip="$2"; shift 2 ;;
            --domain) domain="$2"; shift 2 ;;
            --uuid) client_uuid="$2"; shift 2 ;;
            --email) email="$2"; shift 2 ;;
            *)
                if [[ -z "$origin_ip" ]]; then origin_ip="$1"
                elif [[ -z "$domain" ]]; then domain="$1"
                elif [[ -z "$client_uuid" ]]; then client_uuid="$1"
                fi
                shift
                ;;
        esac
    done

    if [[ -z "$origin_ip" ]]; then
        echo -e "${CYAN}📌 Шаг 1/4: IP-адрес Origin-сервера (РФ / Москва)${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} Публичный IP вашего российского Origin-сервера."
        echo -e "   ${YELLOW}🔒 Безопасность:${NC} Порт 10443 (VLESS Vision) будет открыт в UFW строго для этого IP-адреса."
        read -r -p "Введите IP Origin-сервера: " origin_ip
    fi
    if [[ -z "$domain" ]]; then
        echo -e "${CYAN}📌 Шаг 2/4: Доменное имя Exit-сервера${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} A-запись в DNS (напр. relay.example.com), указывающая на IP этого сервера ($(get_public_ip))."
        read -r -p "Введите домен Exit-сервера: " domain
    fi
    if [[ -z "$client_uuid" ]]; then
        echo -e "${CYAN}📌 Шаг 3/4: VLESS UUID для авторизации Origin${NC}"
        echo -e "   ${YELLOW}💡 Где взять:${NC} Секретный UUID для туннеля (нажмите Enter для автоматической генерации)."
        local generated_uuid
        generated_uuid="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"
        read -r -p "Введите UUID [по умолчанию: ${generated_uuid}]: " client_uuid
        client_uuid="${client_uuid:-$generated_uuid}"
    fi
    if [[ -z "$email" ]]; then
        echo -e "${CYAN}📌 Шаг 4/4: Email для Let's Encrypt сертификата${NC}"
        read -r -p "Введите Email [admin@${domain}]: " email
        email="${email:-admin@${domain}}"
    fi

    # Строгая валидация введенных параметров
    [[ -n "$origin_ip" ]] || error "IP-адрес Origin обязателен."
    validate_host_or_ip "$origin_ip" || error "Некорректный IP-адрес Origin: ${origin_ip}"
    [[ -n "$domain" ]] || error "Домен Exit обязателен."
    validate_host_or_ip "$domain" || error "Некорректный формат домена: ${domain}"
    [[ -n "$client_uuid" ]] || error "UUID клиента обязателен."
    validate_uuid "$client_uuid" || error "Некорректный формат UUID: ${client_uuid} (ожидается формат RFC 4122: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"

    install_xray_core "$XRAY_VERSION_PINNED"
    setup_xray_service

    obtain_ssl_cert "$domain" "$email"
    local cert_file="/etc/letsencrypt/live/${domain}/fullchain.pem"
    local key_file="/etc/letsencrypt/live/${domain}/privkey.pem"

    info "Генерация конфигурации Xray Exit (${XRAY_CONFIG})..."
    cat > "$XRAY_CONFIG" <<EOF
{
  "log": {
    "loglevel": "warning",
    "access": "/var/log/xray/access.log",
    "error": "/var/log/xray/error.log"
  },
  "inbounds": [
    {
      "tag": "from-origin",
      "listen": "0.0.0.0",
      "port": 10443,
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "${client_uuid}",
            "flow": "xtls-rprx-vision",
            "level": 0
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "alpn": ["h2", "http/1.1"],
          "certificates": [
            {
              "certificateFile": "${cert_file}",
              "keyFile": "${key_file}"
            }
          ]
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"]
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
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      {
        "type": "field",
        "ip": ["geoip:private"],
        "outboundTag": "block"
      },
      {
        "type": "field",
        "inboundTag": ["from-origin"],
        "outboundTag": "direct"
      }
    ]
  }
}
EOF

    "$XRAY_BIN" run -test -config "$XRAY_CONFIG"
    systemctl enable --now xray
    systemctl restart xray

    # Настройка UFW: порт 10443 разрешить СТРОГО для Origin IP
    info "Настройка UFW: доступ к порту 10443 только для ${origin_ip}..."
    ufw allow OpenSSH 2>/dev/null || ufw allow 22/tcp
    ufw allow 80/tcp
    ufw delete allow 10443/tcp 2>/dev/null || true
    if [[ -n "${origin_ip}" ]]; then
        ufw allow from "${origin_ip}" to any port 10443 proto tcp
    fi
    ufw --force enable


    set_state_val "role" "xray-exit"
    set_state_val "domain" "$domain"
    set_state_val "origin_ip" "$origin_ip"
    set_state_val "client_uuid" "$client_uuid"
    set_state_val "xray_version" "$XRAY_VERSION_PINNED"
    from_ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    # 5. Боевое самотестирование службы Xray
    if systemctl is-active --quiet xray; then
        log "Служба Xray успешно запущена и слушает порт 10443 VLESS Vision [OK]"
    else
        error "Служба Xray не смогла запуститься. Проверьте: journalctl -u xray -n 30"
    fi

    log "Установка Xray Exit успешно завершена!"
    info "Inbound: port 10443 VLESS Vision TLS (доступен строго для ${origin_ip})"
    info "VLESS UUID: ${client_uuid}"
    info "Запомните этот UUID и домен (${domain}) — они понадобятся при установке Origin-сервера!"
}

# =============================================================================
# 4. КОМАНДА: update xray (безопасное обновление с rollback)
# =============================================================================
cmd_update_xray() {
    title "БЕЗОПАСНОЕ ОБНОВЛЕНИЕ XRAY CORE"
    check_root

    local target_version="${1:-$XRAY_VERSION_PINNED}"
    info "Целевая версия для обновления: ${target_version}"

    local arch
    arch="$(get_arch)"
    local zip_name="Xray-linux-${arch}.zip"
    local download_url="https://github.com/XTLS/Xray-core/releases/download/v${target_version}/${zip_name}"
    local tmp_dir="/tmp/xray-update-$$"

    mkdir -p "$tmp_dir"
    info "1. Скачивание нового ядра в ${tmp_dir}..."
    if ! wget -q --timeout=30 -O "${tmp_dir}/${zip_name}" "$download_url"; then
        rm -rf "$tmp_dir"
        error "Не удалось скачать Xray ${target_version} из официального репозитория GitHub (${download_url}). Обновление отменено."
    fi

    verify_xray_checksum "${tmp_dir}/${zip_name}" "$target_version" "$arch"


    unzip -q -o "${tmp_dir}/${zip_name}" -d "$tmp_dir"
    local new_binary="${tmp_dir}/xray"
    chmod +x "$new_binary"

    info "2. Тестирование конфигурации новым бинарником..."
    if [[ -f "$XRAY_CONFIG" ]]; then
        if ! "$new_binary" run -test -config "$XRAY_CONFIG"; then
            rm -rf "$tmp_dir"
            error "Тест конфигурации новым бинарником завершился с ошибкой. Обновление отменено!"
        fi
        log "Тест конфигурации пройден успешно."
    else
        warn "Конфигурация ${XRAY_CONFIG} не найдена, пропускаем тест конфигурации."
    fi

    info "3. Создание версионированной резервной копии..."
    local backup_id
    backup_id="$(date -u +"%Y%m%d%H%M%S")"
    local backup_dir="/var/backups/just1knode/xray-${backup_id}"
    mkdir -p "$backup_dir"
    if [[ -f "$XRAY_BIN" ]]; then
        cp -f "$XRAY_BIN" "${backup_dir}/xray"
        cp -f "$XRAY_BIN" "${XRAY_BIN}.bak"
    fi
    if [[ -f "$XRAY_CONFIG" ]]; then
        cp -f "$XRAY_CONFIG" "${backup_dir}/config.json"
    fi

    # Ротация резервных копий (сохранять 3 последних)
    local old_backups
    old_backups="$(find /var/backups/just1knode -mindepth 1 -maxdepth 1 -type d -name "xray-*" 2>/dev/null | sort | head -n -3 || true)"
    if [[ -n "$old_backups" ]]; then
        echo "$old_backups" | xargs rm -rf 2>/dev/null || true
    fi

    info "4. Атомарная замена исполняемого файла..."
    install -m 755 "$new_binary" "${XRAY_BIN}.new"
    mv -f "${XRAY_BIN}.new" "$XRAY_BIN"
    rm -rf "$tmp_dir"

    info "5. Перезапуск службы Xray..."
    systemctl restart xray || true
    sleep 2

    info "6. Проверка здоровья узла (gRPC и функциональный тест)..."
    local healthy=false
    if [[ -f "${XRAY_API_DIR}/venv/bin/python3" ]]; then
        if "${XRAY_API_DIR}/venv/bin/python3" -c "
import sys
sys.path.insert(0, '${XRAY_API_DIR}')
from xray_grpc import XrayGrpcClient
c = XrayGrpcClient(timeout=3.0)
sys.exit(0 if c.is_healthy() else 1)
" 2>/dev/null; then
            healthy=true
        fi
    elif systemctl is-active --quiet xray; then
        healthy=true
    fi

    local role
    role="$(get_state_val "role" "")"
    if [[ "$healthy" == "true" && "$role" == "xray-origin" ]]; then
        if command -v curl >/dev/null 2>&1; then
            local check_domain
            check_domain="$(get_state_val "domain" "127.0.0.1")"
            local check_code
            check_code="$(curl -s -k -o /dev/null -w "%{http_code}" "https://${check_domain}/cdn-check" --resolve "${check_domain}:443:127.0.0.1" 2>/dev/null || echo "000")"
            if [[ "$check_code" != "204" ]]; then
                warn "Функциональный тест /cdn-check вернул HTTP ${check_code}, ожидалось 204."
                healthy=false
            fi
        fi
    fi

    if [[ "$healthy" == "true" ]]; then
        log "Обновление Xray до версии ${target_version} прошло успешно!"
        set_state_val "xray_version" "$target_version"
        "$XRAY_BIN" version | head -n 2
    else
        warn "Служба Xray не прошла валидацию после обновления. Выполняется автоматический откат (ROLLBACK)..."
        if [[ -f "${backup_dir}/xray" ]]; then
            install -m 755 "${backup_dir}/xray" "${XRAY_BIN}.new"
            mv -f "${XRAY_BIN}.new" "$XRAY_BIN"
            if [[ -f "${backup_dir}/config.json" ]]; then
                cp -f "${backup_dir}/config.json" "$XRAY_CONFIG"
            fi
            systemctl restart xray
            log "Откат к предыдущей версии выполнен успешно из ${backup_dir}."
        elif [[ -f "${XRAY_BIN}.bak" ]]; then
            install -m 755 "${XRAY_BIN}.bak" "${XRAY_BIN}.new"
            mv -f "${XRAY_BIN}.new" "$XRAY_BIN"
            systemctl restart xray
            log "Откат к предыдущей версии выполнен успешно."
        else
            error "Резервная копия не найдена, откат невозможен."
        fi
        exit 1
    fi

}

# =============================================================================
# 5. КОМАНДА: doctor (комплексная самодиагностика)
# =============================================================================
cmd_doctor() {
    title "КОМПЛЕКСНАЯ ДИАГНОСТИКА УЗЛА (DOCTOR)"
    local issues=0

    # 1. Проверка Nginx
    info "Проверка Nginx..."
    if command -v nginx >/dev/null 2>&1; then
        if nginx -t >/dev/null 2>&1; then
            log "Nginx: синтаксис конфигурации корректен [OK]"
        else
            warn "Nginx: ошибка в конфигурации [FAIL]"
            nginx -t || true
            issues=$((issues + 1))
        fi
        if systemctl is-active --quiet nginx 2>/dev/null; then
            log "Nginx: служба активна [OK]"
        else
            warn "Nginx: служба не запущена [WARN]"
            issues=$((issues + 1))
        fi
    else
        info "Nginx не установлен на узле."
    fi

    # 2. Проверка Xray
    info "Проверка Xray Core..."
    if [[ -f "$XRAY_BIN" ]]; then
        log "Xray: бинарник найден (${XRAY_BIN}) [OK]"
        if [[ -f "$XRAY_CONFIG" ]]; then
            if "$XRAY_BIN" run -test -config "$XRAY_CONFIG" >/dev/null 2>&1; then
                log "Xray: конфигурация валидна [OK]"
            else
                warn "Xray: ошибка валидации конфигурации [FAIL]"
                "$XRAY_BIN" run -test -config "$XRAY_CONFIG" || true
                issues=$((issues + 1))
            fi
        else
            warn "Xray: файл конфигурации ${XRAY_CONFIG} отсутствует [WARN]"
            issues=$((issues + 1))
        fi

        if systemctl is-active --quiet xray 2>/dev/null; then
            log "Xray: служба активна [OK]"
        else
            warn "Xray: служба не запущена [WARN]"
            issues=$((issues + 1))
        fi
    else
        info "Xray Core не установлен на узле."
    fi

    # 3. Проверка сетевых портов
    info "Проверка сетевых портов..."
    check_port() {
        local port="$1"
        local name="$2"
        if ss -tulpn 2>/dev/null | grep -q ":${port} "; then
            log "Порт ${port} (${name}): слушается [OK]"
        else
            warn "Порт ${port} (${name}): НЕ слушается [WARN]"
            issues=$((issues + 1))
        fi
    }

    local role
    role="$(get_state_val 'role' 'unknown')"
    info "Определенная роль узла: ${role}"

    case "$role" in
        xray-origin)
            check_port 80 "HTTP / ACME"
            check_port 443 "HTTPS / XHTTP"
            check_port 8003 "Xray Inbound DE"
            check_port 8004 "Xray Inbound NL"
            check_port 10085 "Xray gRPC API"
            check_port 8444 "Nginx Xray-API proxy"
            check_port 5001 "Xray-API internal"
            ;;
        xray-exit)
            check_port 80 "HTTP / ACME"
            check_port 10443 "Xray Vision Inbound"
            ;;
        amnezia)
            check_port 80 "HTTP / ACME"
            check_port 443 "HTTPS Amnezia Proxy"
            ;;
        *)
            # Общая проверка
            ss -tulpn 2>/dev/null | grep -E ':(80|443|8003|8004|10085|8444|10443|5001)\b' || true
            ;;
    esac

    # 4. Проверка доступности gRPC 127.0.0.1:10085
    if [[ "$role" == "xray-origin" ]]; then
        info "Проверка доступности gRPC 127.0.0.1:10085..."
        if [[ -f "${XRAY_API_DIR}/venv/bin/python3" ]]; then
            if "${XRAY_API_DIR}/venv/bin/python3" -c "
import sys
sys.path.insert(0, '${XRAY_API_DIR}')
from xray_grpc import XrayGrpcClient
c = XrayGrpcClient(timeout=2.0)
sys.exit(0 if c.is_healthy() else 1)
" 2>/dev/null; then
                log "gRPC 127.0.0.1:10085 отвечает на вызовы [OK]"
            else
                warn "gRPC 127.0.0.1:10085 не отвечает [FAIL]"
                issues=$((issues + 1))
            fi
        fi
    fi

    # 5. Проверка Certbot SSL
    info "Проверка сертификатов Certbot..."
    if command -v certbot >/dev/null 2>&1; then
        certbot certificates 2>/dev/null | grep -E "Certificate Name|Expiry Date|Domains" || warn "Сертификаты не обнаружены."
    fi

    # 6. Проверка службы xray-api
    if [[ "$role" == "xray-origin" ]]; then
        info "Проверка службы xray-api..."
        if systemctl is-active --quiet xray-api 2>/dev/null; then
            log "xray-api: служба активна [OK]"
        else
            warn "xray-api: служба не запущена [FAIL]"
            issues=$((issues + 1))
        fi
    fi

    # 7. Проверка сетевого экрана UFW
    info "Проверка статуса UFW..."
    if command -v ufw >/dev/null 2>&1; then
        if ufw status 2>/dev/null | grep -qw "active"; then
            log "UFW: сетевой экран активен и защищает открытые порты [OK]"
        else
            warn "UFW: сетевой экран НЕ активен! Включите его: ufw enable [WARN]"
            issues=$((issues + 1))
        fi
    fi


    echo ""
    if (( issues == 0 )); then
        echo -e "${GREEN}${BOLD}✓ Самодиагностика узла завершена успешно. Замечаний не обнаружено.${NC}\n"
        return 0
    else
        echo -e "${YELLOW}${BOLD}! Самодиагностика выявила ${issues} замечаний.${NC}\n"
        return 1
    fi
}

# =============================================================================
# КОМАНДА: uninstall
# =============================================================================
cmd_uninstall() {
    log "Удаление компонентов just1knode..."
    systemctl stop xray-api 2>/dev/null || true
    systemctl disable xray-api 2>/dev/null || true
    systemctl stop xray 2>/dev/null || true
    systemctl disable xray 2>/dev/null || true
    rm -f /etc/systemd/system/xray-api.service /etc/systemd/system/xray.service
    systemctl daemon-reload
    rm -rf /etc/xray /usr/local/share/xray /opt/xray-api /etc/xray-api /var/lib/xray-api
    rm -f "$STATE_FILE"
    log "Компоненты Xray и Xray API успешно удалены."
}

# =============================================================================
# ИНТЕРАКТИВНОЕ TUI-МЕНЮ
# =============================================================================
interactive_menu() {
    while true; do
        clear 2>/dev/null || true
        echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${BOLD}${BLUE}║${NC}             ${BOLD}${CYAN}JUST1KNODE - МЕНЕДЖЕР СЕРВЕРНОГО УЗЛА VPN / XRAY${NC}                 ${BOLD}${BLUE}║${NC}"
        echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        local role
        role="$(get_state_val 'role' 'не установлен')"
        echo -e "  Текущая роль узла: ${GREEN}${BOLD}${role}${NC}"
        echo ""
        echo -e "  [${BOLD}1${NC}] 📦 ${BOLD}Установить Amnezia API${NC} (Nginx + Certbot webroot)"
        echo -e "  [${BOLD}2${NC}] 🚀 ${BOLD}Установить Xray Origin${NC} (XHTTP + Nginx + gRPC + Xray-API)"
        echo -e "  [${BOLD}3${NC}] 🛡️  ${BOLD}Установить Xray Exit${NC} (Vision Inbound + UFW)"
        echo -e "  [${BOLD}4${NC}] 🔄 ${BOLD}Обновить ядро Xray${NC} (безопасное обновление с rollback)"
        echo -e "  [${BOLD}5${NC}] 🩺 ${BOLD}Самодиагностика узла${NC} (doctor)"
        echo -e "  [${BOLD}6${NC}] 📋 ${BOLD}Показать статус и конфигурацию${NC}"
        echo -e "  [${BOLD}7${NC}] 🗑️  ${BOLD}Удалить компоненты Xray / just1knode${NC}"
        echo -e "  [${BOLD}0${NC}] ❌ ${BOLD}Выход${NC}"
        echo ""
        echo -e "${CYAN}──────────────────────────────────────────────────────────────────────────────${NC}"
        read -r -p "Выберите действие [0-7]: " choice

        case "$choice" in
            1)
                cmd_install_amnezia
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            2)
                cmd_install_xray_origin
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            3)
                cmd_install_xray_exit
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            4)
                read -r -p "Версия Xray для обновления [по умолчанию ${XRAY_VERSION_PINNED}]: " target_v
                cmd_update_xray "${target_v:-$XRAY_VERSION_PINNED}"
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            5)
                cmd_doctor || true
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            6)
                show_state
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            7)
                read -r -p "Вы уверены, что хотите удалить Xray и Xray API? [y/N]: " confirm_del
                if [[ "$confirm_del" =~ ^[Yy]$ ]]; then
                    cmd_uninstall
                fi
                read -r -p "Нажмите Enter для продолжения..."
                ;;
            0|q|exit)
                echo -e "\n${GREEN}До свидания!${NC}\n"
                exit 0
                ;;
            *)
                warn "Неверный выбор."
                sleep 1
                ;;
        esac
    done
}

# =============================================================================
# ДИСПЕТЧЕР CLI КОМАНД
# =============================================================================
main() {
    if [[ $# -eq 0 ]]; then
        interactive_menu
        return
    fi

    local action="$1"
    shift

    case "$action" in
        install)
            local target="${1:-}"
            [[ -n "$target" ]] || error "Укажите компонент для установки: amnezia, xray-origin, xray-exit"
            shift
            case "$target" in
                amnezia)
                    cmd_install_amnezia "$@"
                    ;;
                xray-origin)
                    cmd_install_xray_origin "$@"
                    ;;
                xray-exit)
                    cmd_install_xray_exit "$@"
                    ;;
                *)
                    error "Неизвестный компонент для установки: $target. Допустимо: amnezia, xray-origin, xray-exit"
                    ;;
            esac
            ;;
        update)
            local target="${1:-xray}"
            if [[ "$target" == "xray" ]]; then
                shift 2>/dev/null || true
                cmd_update_xray "$@"
            else
                cmd_update_xray "$target"
            fi
            ;;
        doctor|check)
            cmd_doctor
            ;;
        status|state)
            show_state
            ;;
        uninstall)
            cmd_uninstall
            ;;
        help|-h|--help)
            echo "Использование: just1knode [install amnezia|xray-origin|xray-exit | update xray | doctor | status | uninstall]"
            ;;
        *)
            error "Неизвестная команда: $action. Используйте 'just1knode help'."
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
