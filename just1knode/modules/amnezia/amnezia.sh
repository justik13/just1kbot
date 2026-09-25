#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль AmneziaWG (modules/amnezia/amnezia.sh)
# Управление микросервисом AmneziaWG API, Nginx reverse proxy и защитой от абуза
# =============================================================================

AMNEZIA_API_DIR="${AMNEZIA_API_DIR:-/opt/amnezia-api}"
AMNEZIA_API_ETC="${AMNEZIA_API_ETC:-/etc/amnezia-api}"
AMNEZIA_AWG_DIR="${AMNEZIA_AWG_DIR:-/opt/amnezia/awg}"
AMNEZIA_AWG_CONF="${AMNEZIA_AWG_CONF:-${AMNEZIA_AWG_DIR}/wg0.conf}"
AMNEZIA_CONTAINER="${AMNEZIA_CONTAINER:-amnezia-awg}"
AMNEZIA_PUBLIC_PORT="${AMNEZIA_PUBLIC_PORT:-8443}"
AMNEZIA_LOCAL_PORT="${AMNEZIA_LOCAL_PORT:-4001}"

# Проверка наличия и активности Docker-контейнера amnezia-awg
is_amnezia_container_running() {
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi
    docker ps --filter "name=^/${AMNEZIA_CONTAINER}$" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^${AMNEZIA_CONTAINER}$"
}

# =============================================================================
# ЗАЩИТА ОТ АБУЗА (NETFILTER / IPTABLES)
# 1. Блокировка исходящего SMTP (порт 25) с tcp-reset
# 2. Блокировка BitTorrent L7 хэндшейка и DHT пакетов через xt_string
# =============================================================================
apply_amnezia_abuse_protection() {
    log "Настройка сетевой защиты (Anti-Abuse: SMTP 25 + BitTorrent L7)..."

    # Блокировка SMTP порт 25 (защита от почтового спама)
    if ! iptables -C FORWARD -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null; then
        iptables -I FORWARD 1 -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null || true
    fi
    if iptables -L DOCKER-USER >/dev/null 2>&1; then
        if ! iptables -C DOCKER-USER -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null; then
            iptables -I DOCKER-USER 1 -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null || true
        fi
    fi

    # Блокировка BitTorrent L7 (xt_string)
    modprobe xt_string 2>/dev/null || true
    if iptables -m string --help 2>&1 | grep -q "\-\-algo"; then
        # TCP BitTorrent handshake
        if ! iptables -C FORWARD -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null; then
            iptables -I FORWARD 2 -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
        fi
        # UDP uTP BitTorrent handshake
        if ! iptables -C FORWARD -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null; then
            iptables -I FORWARD 3 -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
        fi
        # UDP DHT announce
        if ! iptables -C FORWARD -p udp -m string --string "d1:ad2:id20:" --algo bm -j DROP 2>/dev/null; then
            iptables -I FORWARD 4 -p udp -m string --string "d1:ad2:id20:" --algo bm -j DROP 2>/dev/null || true
        fi

        # DOCKER-USER chain
        if iptables -L DOCKER-USER >/dev/null 2>&1; then
            if ! iptables -C DOCKER-USER -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null; then
                iptables -I DOCKER-USER 2 -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
            fi
            if ! iptables -C DOCKER-USER -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null; then
                iptables -I DOCKER-USER 3 -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
            fi
        fi
        log "✔ Правила фильтрации BitTorrent и блокировки SMTP:25 активированы."
    else
        warn "Модуль ядра xt_string недоступен. Блокировка SMTP:25 установлена, BitTorrent L7 пропущен."
    fi

    # Сохранение правил iptables для переживания перезагрузки
    if command -v netfilter-persistent >/dev/null 2>&1; then
        netfilter-persistent save >/dev/null 2>&1 || true
    elif [[ -d /etc/iptables ]]; then
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    fi
}

remove_amnezia_abuse_protection() {
    log "Очистка правил сетевой защиты AmneziaWG..."
    iptables -D FORWARD -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null || true
    iptables -D DOCKER-USER -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null || true

    iptables -D FORWARD -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
    iptables -D FORWARD -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
    iptables -D FORWARD -p udp -m string --string "d1:ad2:id20:" --algo bm -j DROP 2>/dev/null || true

    if iptables -L DOCKER-USER >/dev/null 2>&1; then
        iptables -D DOCKER-USER -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
        iptables -D DOCKER-USER -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null || true
    fi

    if command -v netfilter-persistent >/dev/null 2>&1; then
        netfilter-persistent save >/dev/null 2>&1 || true
    fi
}

# =============================================================================
# УСТАНОВКА И НАСТРОЙКА AMNEZIAWG УЗЛА
# =============================================================================
install_amnezia_node() {
    title "НАСТРОЙКА И ИНТЕГРАЦИЯ УЗЛА AMNEZIAWG"
    check_root
    init_state_dir
    install_base_deps

    # 1. Проверка наличия работающего контейнера amnezia-awg
    if ! is_amnezia_container_running; then
        echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
        echo -e "${BOLD}${RED}✗ КОНТЕЙНЕР '${AMNEZIA_CONTAINER}' НЕ ОБНАРУЖЕН В DOCKER${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
        echo -e "Для интеграции AmneziaWG с just1knode выполните первоначальную установку:"
        echo -e "  1. Скачайте официальное приложение ${BOLD}AmneziaVPN${NC} на ваш ПК."
        echo -e "  2. Добавьте этот сервер (IP, root, пароль/SSH-ключ) в режиме ${BOLD}Self-hosted${NC}."
        echo -e "  3. Выберите протокол ${BOLD}AmneziaWG${NC} и дождитесь завершения установки."
        echo -e "  4. После того как контейнер '${AMNEZIA_CONTAINER}' запустится, повторите запуск данного меню."
        echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}\n"
        return 1
    fi

    # 2. Проверка конфигурационного файла на хосте
    if [[ ! -f "$AMNEZIA_AWG_CONF" ]]; then
        error "Файл конфигурации $AMNEZIA_AWG_CONF не найден. Убедитесь, что AmneziaWG развернут через AmneziaVPN."
        return 1
    fi

    log "✔ Контейнер ${AMNEZIA_CONTAINER} активен, конфигурация ${AMNEZIA_AWG_CONF} найдена."

    # 3. Интерактивный опрос: домен и порт
    local my_ip
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"

    echo ""
    read -rp "Введите доменное имя для API (например: fi.example.com, либо нажмите Enter для IP): " domain_in || true
    local api_domain="${domain_in:-$my_ip}"

    read -rp "Публичный HTTPS порт для API [по умолчанию: ${AMNEZIA_PUBLIC_PORT}]: " port_in || true
    local public_port="${port_in:-$AMNEZIA_PUBLIC_PORT}"

    # 4. Проверка доступности публичного порта
    if ss -tlnp 2>/dev/null | grep -q ":${public_port} "; then
        local conflict_proc
        conflict_proc=$(ss -tlnp 2>/dev/null | grep ":${public_port} " || true)
        if ! echo "$conflict_proc" | grep -qE "nginx|amnezia"; then
            error "Порт ${public_port}/tcp уже занят другим процессом на хосте:\n$conflict_proc"
        fi
    fi

    # 5. Развертывание файлов scripts/amnezia_api в /opt/amnezia-api
    mkdir -p "$AMNEZIA_API_DIR" "$AMNEZIA_API_ETC"
    local source_api_dir="${SCRIPT_DIR}/../scripts/amnezia_api"
    if [[ ! -d "$source_api_dir" ]]; then
        source_api_dir="/opt/just1knode/scripts/amnezia_api"
    fi
    if [[ ! -d "$source_api_dir" && -d "/app/scripts/amnezia_api" ]]; then
        source_api_dir="/app/scripts/amnezia_api"
    fi

    if [[ -d "$source_api_dir" ]]; then
        cp -a "$source_api_dir/." "$AMNEZIA_API_DIR/"
    else
        log "Загрузка скриптов amnezia_api из репозитория..."
        local tmp_dl="/tmp/amnezia_api_$$.tar.gz"
        local archive_url="https://github.com/justik13/just1kbot/archive/refs/heads/main.tar.gz"
        curl -fsSL "$archive_url" -o "$tmp_dl" 2>/dev/null || wget -qO "$tmp_dl" "$archive_url" 2>/dev/null || true
        if [[ -f "$tmp_dl" ]]; then
            tar -xzf "$tmp_dl" --strip-components=2 -C "$AMNEZIA_API_DIR" "*/scripts/amnezia_api" 2>/dev/null || true
            rm -f "$tmp_dl"
        fi
    fi

    if [[ ! -f "$AMNEZIA_API_DIR/app.py" ]]; then
        error "Не удалось найти $AMNEZIA_API_DIR/app.py. Проверьте репозиторий."
    fi

    # 6. Установка зависимостей и venv
    log "Настройка виртуального окружения Python (/opt/amnezia-api/venv)..."
    if [[ ! -d "$AMNEZIA_API_DIR/venv" ]]; then
        python3 -m venv "$AMNEZIA_API_DIR/venv"
    fi
    "$AMNEZIA_API_DIR/venv/bin/pip" install --no-cache-dir -r "$AMNEZIA_API_DIR/requirements.txt" --quiet

    # 7. Генерация API-ключа
    local api_key=""
    if [[ -f "$AMNEZIA_API_ETC/config.env" ]]; then
        api_key="$(grep "^AMNEZIA_API_KEY=" "$AMNEZIA_API_ETC/config.env" | cut -d= -f2- || true)"
    fi
    if [[ -z "$api_key" ]]; then
        api_key="$(openssl rand -hex 24)"
    fi

    # Запись конфигурации окружения
    cat > "$AMNEZIA_API_ETC/config.env" <<EOF
AMNEZIA_API_KEY=${api_key}
AWG_DIR=${AMNEZIA_AWG_DIR}
AWG_CONF_PATH=${AMNEZIA_AWG_CONF}
AWG_CONTAINER_NAME=${AMNEZIA_CONTAINER}
SERVER_HOST_NAME=${api_domain}
SERVER_DNS1=1.1.1.1
SERVER_DNS2=1.0.0.1
EOF
    chmod 600 "$AMNEZIA_API_ETC/config.env"

    # 8. Установка systemd службы
    cp "$AMNEZIA_API_DIR/amnezia-api.service" /etc/systemd/system/amnezia-api.service
    systemctl daemon-reload
    systemctl enable amnezia-api.service
    systemctl restart amnezia-api.service

    # Ожидание старта сервиса
    local started=0
    for _ in {1..15}; do
        if curl -s "http://127.0.0.1:${AMNEZIA_LOCAL_PORT}/healthz" 2>/dev/null | grep -q "amnezia-api"; then
            started=1
            break
        fi
        sleep 1
    done

    if [[ $started -ne 1 ]]; then
        error "Сервис amnezia-api не запустился на 127.0.0.1:${AMNEZIA_LOCAL_PORT}. Проверьте: journalctl -u amnezia-api -n 30"
    fi
    log "✔ Служба amnezia-api успешно запущена на 127.0.0.1:${AMNEZIA_LOCAL_PORT}"

    # 9. Настройка Nginx reverse proxy и SSL
    log "Настройка веб-сервера Nginx (порт ${public_port})..."
    install_nginx_if_missing

    local cert_file=""
    local key_file=""

    # Проверка Let's Encrypt для домена
    if [[ -n "$domain_in" && -f "/etc/letsencrypt/live/${domain_in}/fullchain.pem" ]]; then
        cert_file="/etc/letsencrypt/live/${domain_in}/fullchain.pem"
        key_file="/etc/letsencrypt/live/${domain_in}/privkey.pem"
        log "✔ Используется существующий Let's Encrypt SSL сертификат для ${domain_in}"
    elif [[ -n "$domain_in" ]]; then
        log "Попытка получения Let's Encrypt SSL сертификата для ${domain_in}..."
        if command -v certbot >/dev/null 2>&1; then
            systemctl stop nginx 2>/dev/null || true
            if certbot certonly --standalone -d "$domain_in" --non-interactive --agree-tos --register-unsafely-without-email 2>/dev/null; then
                cert_file="/etc/letsencrypt/live/${domain_in}/fullchain.pem"
                key_file="/etc/letsencrypt/live/${domain_in}/privkey.pem"
                log "✔ SSL сертификат Let's Encrypt успешно получен для ${domain_in}"
            fi
            systemctl start nginx 2>/dev/null || true
        fi
    fi

    # Fallback: генерация самоподписанного сертификата
    if [[ -z "$cert_file" || ! -f "$cert_file" ]]; then
        local ssl_dir="/etc/ssl/just1k_amnezia"
        mkdir -p "$ssl_dir"
        cert_file="${ssl_dir}/server.crt"
        key_file="${ssl_dir}/server.key"
        if [[ ! -f "$cert_file" ]]; then
            log "Генерация надежного SSL-сертификата для HTTPS (${api_domain})..."
            openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
                -keyout "$key_file" -out "$cert_file" \
                -subj "/CN=${api_domain}" 2>/dev/null || true
        fi
    fi

    # Генерация Nginx конфигурации
    local nginx_conf="/etc/nginx/sites-available/just1k-amnezia.conf"
    cat > "$nginx_conf" <<EOF
# JUST1KNODE: AmneziaWG API Reverse Proxy
server {
    listen ${public_port} ssl;
    server_name ${api_domain} _;

    ssl_certificate ${cert_file};
    ssl_certificate_key ${key_file};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:${AMNEZIA_LOCAL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }
}
EOF

    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$nginx_conf" /etc/nginx/sites-enabled/just1k-amnezia.conf
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        log "✔ Nginx reverse proxy успешно настроен и перезагружен"
    else
        warn "Ошибка проверки конфигурации Nginx. Проверьте: nginx -t"
    fi

    # 10. Открытие порта в UFW если фаервол активен
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
        ufw allow "${public_port}/tcp" comment "just1knode amnezia api" >/dev/null 2>&1 || true
    fi

    # 11. Активация защиты от абуза (SMTP 25 + BitTorrent)
    apply_amnezia_abuse_protection

    # 12. Обновление состояния и определение мультироли (Coexistence)
    local prev_role
    prev_role="$(get_node_status)"
    if [[ "$prev_role" == "relay" ]]; then
        set_state_val "role" "dual"
        log "Режим узла обновлен до: DUAL (Совмещенный Relay + AmneziaWG)"
    else
        set_state_val "role" "awg"
        log "Режим узла установлен: AMNEZIAWG"
    fi

    local final_api_url="https://${api_domain}:${public_port}"
    set_state_val "awg_api_url" "$final_api_url"
    set_state_val "awg_api_key" "$api_key"
    set_state_val "awg_domain" "$api_domain"
    set_state_val "awg_port" "$public_port"
    set_state_val "awg_installed" "true"

    # Вывод карточки подключения
    show_amnezia_bot_credentials
}

# =============================================================================
# ОТОБРАЖЕНИЕ РЕКВИЗИТОВ ДЛЯ TELEGRAM-БОТА
# =============================================================================
show_amnezia_bot_credentials() {
    title "ДАННЫЕ ДЛЯ ДОБАВЛЕНИЯ В TELEGRAM-БОТ (/admin)"
    local api_url api_key
    api_url="$(get_state_val "awg_api_url" "-")"
    api_key="$(get_state_val "awg_api_key" "-")"

    if [[ "$api_url" == "-" ]]; then
        local my_ip
        my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"
        api_url="https://${my_ip}:${AMNEZIA_PUBLIC_PORT}"
    fi

    echo -e "  🌐 Протокол:           ${BOLD}${GREEN}AmneziaWG (amneziawg2)${NC}"
    echo -e "  🔗 API URL бота:       ${CYAN}${api_url}${NC}"
    echo -e "  🔑 API Ключ:           ${YELLOW}${api_key}${NC}"
    echo -e "  🩺 Проверка API:       curl -k -H \"x-api-key: ${api_key}\" ${api_url}/healthz\n"
}

# =============================================================================
# ОТОБРАЖЕНИЕ СТАТУСА AMNEZIAWG
# =============================================================================
show_amnezia_status() {
    title "СТАТУС УЗЛА AMNEZIAWG"
    check_root
    init_state_dir

    local api_url api_key
    api_url="$(get_state_val "awg_api_url" "-")"
    api_key="$(get_state_val "awg_api_key" "-")"

    echo -e "  API URL:              ${CYAN}${api_url}${NC}"

    echo -e "\n  Службы:"
    if is_amnezia_container_running; then
        echo -e "    Docker (amnezia-awg): ${GREEN}● Активен${NC}"
    else
        echo -e "    Docker (amnezia-awg): ${RED}○ Не запущен${NC}"
    fi

    if systemctl is-active --quiet amnezia-api 2>/dev/null; then
        echo -e "    amnezia-api:          ${GREEN}● Активен (127.0.0.1:${AMNEZIA_LOCAL_PORT})${NC}"
    else
        echo -e "    amnezia-api:          ${RED}○ Не работает${NC}"
    fi

    if systemctl is-active --quiet nginx 2>/dev/null; then
        echo -e "    Nginx Reverse Proxy:  ${GREEN}● Активен${NC}"
    else
        echo -e "    Nginx Reverse Proxy:  ${RED}○ Не работает${NC}"
    fi

    # Клиенты и трафик из clientsTable
    local clients_file="${AMNEZIA_AWG_DIR}/clientsTable"
    if [[ -f "$clients_file" ]]; then
        local clients_count
        clients_count=$(python3 -c "
import json, sys
try:
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        data = json.load(f)
        print(len(data))
except Exception:
    print(0)
" "$clients_file" 2>/dev/null || echo "0")
        echo -e "\n  Статистика клиентов:"
        echo -e "    Всего клиентов:       ${CYAN}${clients_count}${NC}"
    fi

    echo -e "\n  Сетевая защита (Anti-Abuse):"
    if iptables -C FORWARD -p tcp --dport 25 -j REJECT --reject-with tcp-reset 2>/dev/null; then
        echo -e "    Блокировка SMTP:25:   ${GREEN}✔ Включена (tcp-reset)${NC}"
    else
        echo -e "    Блокировка SMTP:25:   ${YELLOW}! Не найдена${NC}"
    fi
    if iptables -C FORWARD -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP 2>/dev/null; then
        echo -e "    Фильтрация BitTorrent:${GREEN}✔ Включена (xt_string L7)${NC}\n"
    else
        echo -e "    Фильтрация BitTorrent:${YELLOW}! Не найдена${NC}\n"
    fi
}

# =============================================================================
# ДЕИНСТАЛЛЯЦИЯ КОМПОНЕНТА AMNEZIAWG
# =============================================================================
uninstall_amnezia_component() {
    title "УДАЛЕНИЕ КОМПОНЕНТА AMNEZIAWG API"
    check_root
    init_state_dir

    systemctl stop amnezia-api 2>/dev/null || true
    systemctl disable amnezia-api 2>/dev/null || true
    rm -f /etc/systemd/system/amnezia-api.service 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true

    rm -rf "$AMNEZIA_API_DIR" "$AMNEZIA_API_ETC" 2>/dev/null || true
    rm -f /etc/nginx/sites-enabled/just1k-amnezia.conf /etc/nginx/sites-available/just1k-amnezia.conf 2>/dev/null || true
    if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || true
    fi

    remove_amnezia_abuse_protection

    local prev_role
    prev_role="$(get_node_status)"
    if [[ "$prev_role" == "dual" ]]; then
        set_state_val "role" "relay"
        log "Режим узла переключен обратно на: RELAY"
    else
        set_state_val "role" "unconfigured"
        log "Режим узла сброшен в: НЕ НАСТРОЕН"
    fi

    set_state_val "awg_installed" "false"
    set_state_val "awg_api_url" ""
    set_state_val "awg_api_key" ""

    log "✔ Компонент AmneziaWG API успешно удален с сервера."
}
