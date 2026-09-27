#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль AmneziaWG (modules/amnezia/amnezia.sh)
# Управление микросервисом AmneziaWG API, Nginx reverse proxy и защитой от абуза
# =============================================================================

AMNEZIA_API_DIR="${AMNEZIA_API_DIR:-/opt/amnezia-api}"
AMNEZIA_API_ETC="${AMNEZIA_API_ETC:-/etc/amnezia-api}"
AMNEZIA_AWG_DIR="${AMNEZIA_AWG_DIR:-/opt/amnezia/awg}"
AMNEZIA_CONTAINER_OVERRIDE="${AMNEZIA_CONTAINER:-}"
AMNEZIA_PUBLIC_PORT="${AMNEZIA_PUBLIC_PORT:-8443}"
AMNEZIA_LOCAL_PORT="${AMNEZIA_LOCAL_PORT:-4001}"

# Определение актуального имени контейнера: в приоритете amnezia-awg2 (AWG 2.0 / 3.x), затем amnezia-awg
detect_amnezia_container() {
    if [[ -n "$AMNEZIA_CONTAINER_OVERRIDE" ]]; then
        echo "$AMNEZIA_CONTAINER_OVERRIDE"
        return 0
    fi
    if command -v docker >/dev/null 2>&1; then
        if docker ps --filter "name=^/amnezia-awg2$" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^amnezia-awg2$"; then
            echo "amnezia-awg2"
            return 0
        fi
        if docker ps --filter "name=^/amnezia-awg$" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^amnezia-awg$"; then
            echo "amnezia-awg"
            return 0
        fi
        if docker ps -a --filter "name=^/amnezia-awg2$" --format '{{.Names}}' 2>/dev/null | grep -q "^amnezia-awg2$"; then
            echo "amnezia-awg2"
            return 0
        fi
        if docker ps -a --filter "name=^/amnezia-awg$" --format '{{.Names}}' 2>/dev/null | grep -q "^amnezia-awg$"; then
            echo "amnezia-awg"
            return 0
        fi
    fi
    echo "amnezia-awg2"
}

# Проверка наличия и активности Docker-контейнера AmneziaWG
is_amnezia_container_running() {
    if ! command -v docker >/dev/null 2>&1; then
        return 1
    fi
    local c
    c="$(detect_amnezia_container)"
    docker ps --filter "name=^/${c}$" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"
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
    fi
    if [[ -d /etc/iptables ]]; then
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
    if [[ -d /etc/iptables ]]; then
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    fi
}

deploy_amnezia_certbot_renewal_hook() {
    local hook_dir="${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/deploy"
    mkdir -p "$hook_dir"
    cat > "${hook_dir}/restart-amnezia-nginx.sh" <<'EOF'
#!/bin/bash
systemctl reload nginx 2>/dev/null || true
systemctl restart amnezia-api 2>/dev/null || true
EOF
    chmod +x "${hook_dir}/restart-amnezia-nginx.sh"
}

# =============================================================================
# УСТАНОВКА И НАСТРОЙКА AMNEZIAWG УЗЛА
# =============================================================================
install_amnezia_node() {
    local arg_domain="${1:-}"
    local arg_port="${2:-}"

    title "НАСТРОЙКА И ИНТЕГРАЦИЯ УЗЛА AMNEZIAWG"
    check_root
    init_state_dir
    install_base_deps

    local prev_role
    prev_role="$(get_node_status)"
    if [[ "$prev_role" == "origin" ]]; then
        error "Узел уже настроен как Origin (Белый Интернет). Установка AmneziaWG на Origin запрещена (контуры строго изолированы)."
        return 1
    fi

    local target_container
    target_container="$(detect_amnezia_container)"

    # 1. Проверка наличия работающего контейнера AmneziaWG
    if ! is_amnezia_container_running; then
        echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
        echo -e "${BOLD}${RED}✗ КОНТЕЙНЕР '${target_container}' НЕ ОБНАРУЖЕН В DOCKER${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
        echo -e "Для интеграции AmneziaWG выполните первоначальную установку:"
        echo -e "  1. Скачайте официальное приложение Amnezia на ваш ПК."
        echo -e "  2. Добавьте этот сервер (IP, root, пароль/SSH-ключ) в режиме Self-hosted."
        echo -e "  3. Выберите протокол AmneziaWG (Awg2) и дождитесь завершения установки."
        echo -e "  4. После того как контейнер '${target_container}' запустится, повторите запуск данного меню."
        echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}\n"
        return 1
    fi

    # 2. Проверка конфигурационного файла внутри контейнера (или на хосте)
    local conf_in_container="/opt/amnezia/awg/awg0.conf"
    if [[ "$target_container" == "amnezia-awg" ]]; then
        conf_in_container="/opt/amnezia/awg/wg0.conf"
    fi

    local conf_found=0
    if docker exec "$target_container" test -f "$conf_in_container" 2>/dev/null; then
        conf_found=1
    elif [[ -f "/opt/amnezia/awg/awg0.conf" || -f "/opt/amnezia/awg/wg0.conf" ]]; then
        conf_found=1
    fi

    if [[ $conf_found -ne 1 ]]; then
        error "Файл конфигурации $conf_in_container не найден внутри контейнера $target_container. Убедитесь, что AmneziaWG развернут."
        return 1
    fi

    log "✔ Контейнер ${target_container} активен, конфигурация ${conf_in_container} найдена."

    # 2b. Определение существующей конфигурации или публичного IP узла
    local saved_api_key=""
    local saved_host=""
    local saved_max_peers=""
    if [[ -f "$AMNEZIA_API_ETC/config.env" ]]; then
        saved_api_key="$(grep -E "^(AMNEZIA_API_KEY|FASTIFY_API_KEY)=" "$AMNEZIA_API_ETC/config.env" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d ' "\r\n' || true)"
        saved_host="$(grep -E "^(SERVER_PUBLIC_HOST|SERVER_HOST_NAME)=" "$AMNEZIA_API_ETC/config.env" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d ' "\r\n' || true)"
        saved_max_peers="$(grep -E "^SERVER_MAX_PEERS=" "$AMNEZIA_API_ETC/config.env" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d ' "\r\n' || true)"
    fi

    # Определение публичного IP узла
    local my_ip
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"
    [[ -z "$my_ip" ]] && my_ip="127.0.0.1"

    local chosen_api_key="${saved_api_key:-}"
    local api_domain="${arg_domain:-${saved_host:-}}"
    if [[ -n "$chosen_api_key" ]]; then
        log "✔ Обнаружена сохранённая конфигурация API-ключа ($AMNEZIA_API_ETC/config.env)."
    fi

    local was_service_active=0
    if systemctl is-active --quiet amnezia-api.service 2>/dev/null; then
        was_service_active=1
    fi

    rollback_amnezia_if_needed() {
        if [[ "$was_service_active" -eq 1 ]]; then
            warn "Откат установки: восстанавливаем ранее активную службу amnezia-api.service..."
            systemctl enable amnezia-api.service >/dev/null 2>&1 || true
            systemctl start amnezia-api.service >/dev/null 2>&1 || true
        else
            systemctl stop amnezia-api.service >/dev/null 2>&1 || true
            systemctl disable amnezia-api.service >/dev/null 2>&1 || true
        fi
    }

    # 3. Домен и порт API (интерактивный опросник или дефолт)
    if [[ -z "$api_domain" ]]; then
        if [[ -n "$arg_domain" ]]; then
            api_domain="$arg_domain"
        elif [[ -t 0 ]]; then
            echo ""
            echo -e "${BOLD}Выберите тип адреса для подключения Telegram-бота:${NC}"
            echo -e "  ${GREEN}[1]${NC} Доменное имя с доверенным Let's Encrypt SSL ${GREEN}[Рекомендуется]${NC}"
            echo -e "  ${CYAN}[2]${NC} IP-адрес сервера (${my_ip}) с самоподписанным SSL-сертификатом"
            echo ""
            read -rp "Ваш выбор [по умолчанию: 1]: " conn_type || true
            conn_type="${conn_type:-1}"
            if [[ "$conn_type" == "1" ]]; then
                read -rp "Введите доменное имя (например: node.example.com): " domain_in || true
                domain_in="$(echo "$domain_in" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's|^https\?://||' -e 's|/.*$||')"
                api_domain="${domain_in:-$my_ip}"
            else
                api_domain="$my_ip"
            fi
        else
            api_domain="$my_ip"
        fi
    fi

    local default_port="${arg_port:-$AMNEZIA_PUBLIC_PORT}"
    local public_port="$default_port"
    if [[ -z "$arg_port" && -t 0 ]]; then
        read -rp "Публичный HTTPS порт для API [по умолчанию: ${default_port}]: " port_in || true
        public_port="${port_in:-$default_port}"
    fi

    local enable_abuse="Y"
    if [[ -t 0 ]]; then
        read -rp "Активировать защиту от спама и торрентов (SMTP:25 + BitTorrent L7)? [Y/n]: " abuse_in || true
        abuse_in="${abuse_in:-Y}"
        if [[ "$abuse_in" =~ ^[Nn] ]]; then
            enable_abuse="N"
        fi
    fi

    # 4. Проверка доступности публичного порта
    if ss -tlnp 2>/dev/null | grep -q ":${public_port} "; then
        local conflict_proc
        conflict_proc=$(ss -tlnp 2>/dev/null | grep ":${public_port} " || true)
        if ! echo "$conflict_proc" | grep -qE "nginx|amnezia"; then
            rollback_amnezia_if_needed
            error "Порт ${public_port}/tcp уже занят другим процессом на хосте:\n$conflict_proc"
            return 1
        fi
    fi

    # Проверка доступности локального порта API (4001)
    if ss -tlnp 2>/dev/null | grep -q ":${AMNEZIA_LOCAL_PORT} "; then
        local local_conflict
        local_conflict=$(ss -tlnp 2>/dev/null | grep ":${AMNEZIA_LOCAL_PORT} " || true)
        if ! echo "$local_conflict" | grep -qE "amnezia|uvicorn|python"; then
            rollback_amnezia_if_needed
            error "Локальный порт ${AMNEZIA_LOCAL_PORT}/tcp уже занят другим процессом на хосте:\n$local_conflict"
            return 1
        fi
    fi

    # 5. Развертывание файлов scripts/amnezia_api в /opt/amnezia-api
    mkdir -p "$AMNEZIA_API_DIR" "$AMNEZIA_API_ETC"
    chmod 750 "$AMNEZIA_API_DIR" 2>/dev/null || true

    # Проверка полной валидности комплекта файлов микросервиса
    is_amnezia_api_valid() {
        local target="${1:-$AMNEZIA_API_DIR}"
        [[ -f "${target}/app.py" && \
           -f "${target}/requirements.txt" && \
           -f "${target}/amnezia-api.service" ]]
    }

    # Поиск валидного локального источника микросервиса (все 3 обязательных файла)
    local source_api_dir=""
    local cand_dirs=(
        "${SCRIPT_DIR}/scripts/amnezia_api"
        "${INSTALL_DIR:-/opt/just1knode}/scripts/amnezia_api"
        "/opt/just1knode/scripts/amnezia_api"
        "/app/scripts/amnezia_api"
        "${SCRIPT_DIR}/../scripts/amnezia_api"
    )
    for cand in "${cand_dirs[@]}"; do
        if is_amnezia_api_valid "$cand"; then
            source_api_dir="$cand"
            break
        fi
    done

    local copy_ok=0
    if [[ -n "$source_api_dir" ]]; then
        log "Использование локального источника микросервиса: ${source_api_dir}"
        if cp -a "${source_api_dir}/." "$AMNEZIA_API_DIR/" 2>/dev/null; then
            if is_amnezia_api_valid "$AMNEZIA_API_DIR"; then
                copy_ok=1
            fi
        fi
    fi

    # Если в целевом каталоге комплект не полон -> Сетевой fallback
    if ! is_amnezia_api_valid "$AMNEZIA_API_DIR"; then
        log "Локальные файлы микросервиса не найдены или неполны. Загрузка из репозитория GitHub..."
        local tmp_extract
        tmp_extract="$(mktemp -d /tmp/amnezia_extract.XXXXXXXXXX 2>/dev/null || mktemp -d)"
        local tmp_dl="${tmp_extract}/archive.tar.gz"
        local repo_url="${JUST1KBOT_REPO_URL:-https://github.com/justik13/just1kbot}"
        local repo_ref="${JUST1KBOT_REF:-main}"
        local archive_url
        if [[ "$repo_ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
            archive_url="${repo_url%.git}/archive/${repo_ref}.tar.gz"
        elif [[ "$repo_ref" =~ ^v[0-9] ]]; then
            archive_url="${repo_url%.git}/archive/refs/tags/${repo_ref}.tar.gz"
        else
            archive_url="${repo_url%.git}/archive/refs/heads/${repo_ref}.tar.gz"
        fi

        if curl -fsSL --connect-timeout 10 --max-time 60 "$archive_url" -o "$tmp_dl" 2>/dev/null || \
           wget -q --timeout=60 -O "$tmp_dl" "$archive_url" 2>/dev/null; then
            local extract_dir="${tmp_extract}/src"
            mkdir -p "$extract_dir"
            if tar -xzf "$tmp_dl" -C "$extract_dir" --strip-components=1 2>/dev/null; then
                local found_scripts="${extract_dir}/scripts/amnezia_api"
                if is_amnezia_api_valid "$found_scripts"; then
                    cp -a "${found_scripts}/." "$AMNEZIA_API_DIR/" 2>/dev/null || true
                    # Атомарный прогрев локального кэша для будущих запусков
                    local cache_target="${SCRIPT_DIR}/scripts/amnezia_api"
                    local cache_stage="${SCRIPT_DIR}/scripts/.amnezia_cache_stage_$$"
                    rm -rf "$cache_stage"
                    mkdir -p "$cache_stage" 2>/dev/null || true
                    if cp -a "${found_scripts}/." "$cache_stage/" 2>/dev/null; then
                        if is_amnezia_api_valid "$cache_stage"; then
                            rm -rf "${cache_target}.old"
                            [[ -d "$cache_target" ]] && mv "$cache_target" "${cache_target}.old" 2>/dev/null || true
                            mv "$cache_stage" "$cache_target" 2>/dev/null || true
                            rm -rf "${cache_target}.old"
                        fi
                    fi
                    rm -rf "$cache_stage"
                fi
            fi
        fi
        rm -rf "$tmp_extract"
    fi

    # Авто-восстановление в случае, если файлы оказались во вложенной папке amnezia_api (от старых версий)
    if ! is_amnezia_api_valid "$AMNEZIA_API_DIR" && [[ -d "$AMNEZIA_API_DIR/amnezia_api" ]]; then
        if is_amnezia_api_valid "$AMNEZIA_API_DIR/amnezia_api"; then
            if cp -a "$AMNEZIA_API_DIR/amnezia_api/." "$AMNEZIA_API_DIR/" 2>/dev/null; then
                if is_amnezia_api_valid "$AMNEZIA_API_DIR"; then
                    rm -rf "$AMNEZIA_API_DIR/amnezia_api"
                fi
            fi
        fi
    fi

    if ! is_amnezia_api_valid "$AMNEZIA_API_DIR"; then
        rollback_amnezia_if_needed
        error "Не удалось развернуть компоненты amnezia-api в $AMNEZIA_API_DIR (файлы app.py, requirements.txt или amnezia-api.service отсутствуют). Проверьте доступ к сети или репозиторию."
        return 1
    fi

    # 6. Установка зависимостей и venv
    log "Настройка виртуального окружения Python (/opt/amnezia-api/venv)..."
    if [[ ! -d "$AMNEZIA_API_DIR/venv" ]]; then
        python3 -m venv "$AMNEZIA_API_DIR/venv"
    fi
    if ! "$AMNEZIA_API_DIR/venv/bin/pip" install --no-cache-dir -r "$AMNEZIA_API_DIR/requirements.txt" --quiet; then
        rollback_amnezia_if_needed
        error "Не удалось установить зависимости Python для amnezia-api."
        return 1
    fi

    # 7. Определение API-ключа (приоритет: chosen_api_key -> existing config.env -> генерация нового)
    local api_key="$chosen_api_key"
    if [[ -z "$api_key" && -f "$AMNEZIA_API_ETC/config.env" ]]; then
        api_key="$(grep -E "^(AMNEZIA_API_KEY|FASTIFY_API_KEY)=" "$AMNEZIA_API_ETC/config.env" | head -n1 | cut -d= -f2- | tr -d ' "\r\n' || true)"
    fi
    if [[ -z "$api_key" ]]; then
        api_key="$(openssl rand -hex 24)"
    fi

    # Запись конфигурации окружения
    cat > "$AMNEZIA_API_ETC/config.env" <<EOF
AMNEZIA_API_KEY=${api_key}
FASTIFY_API_KEY=${api_key}
AWG_DIR=${AMNEZIA_AWG_DIR}
AWG_CONF_PATH=${conf_in_container}
AWG_CONTAINER_NAME=${target_container}
SERVER_HOST_NAME=${api_domain}
SERVER_PUBLIC_HOST=${api_domain}
SERVER_DNS1=8.8.8.8
SERVER_DNS2=8.8.4.4
EOF
    if [[ -n "${saved_max_peers:-}" ]]; then
        echo "SERVER_MAX_PEERS=${saved_max_peers}" >> "$AMNEZIA_API_ETC/config.env"
    fi
    chmod 600 "$AMNEZIA_API_ETC/config.env"

    # 8. Установка systemd службы
    cp "$AMNEZIA_API_DIR/amnezia-api.service" /etc/systemd/system/amnezia-api.service
    systemctl daemon-reload
    systemctl enable amnezia-api.service
    systemctl restart amnezia-api.service

    # Ожидание старта сервиса и проверка работоспособности
    local started=0
    for _ in {1..20}; do
        local health_resp
        health_resp="$(curl -s --max-time 3 "http://127.0.0.1:${AMNEZIA_LOCAL_PORT}/healthz" 2>/dev/null || true)"
        if echo "$health_resp" | grep -q '"service":"amnezia-api"' && echo "$health_resp" | grep -q '"container_running":true'; then
            started=1
            break
        fi
        sleep 1
    done

    if [[ $started -ne 1 ]]; then
        rollback_amnezia_if_needed
        error "Сервис amnezia-api не запустился или контейнер недоступен на 127.0.0.1:${AMNEZIA_LOCAL_PORT}. Проверьте: journalctl -u amnezia-api -n 30"
        return 1
    fi
    log "✔ Служба amnezia-api успешно запущена и контейнер ${target_container} активен"

    # 9. Настройка Nginx reverse proxy и SSL
    log "Настройка веб-сервера Nginx (порт ${public_port})..."
    install_nginx_if_missing

    local cert_file=""
    local key_file=""

    # Проверка Let's Encrypt для домена (если это не IP адрес)
    local is_ip=0
    if [[ "$api_domain" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        is_ip=1
    fi

    if [[ $is_ip -eq 0 && -f "/etc/letsencrypt/live/${api_domain}/fullchain.pem" ]]; then
        cert_file="/etc/letsencrypt/live/${api_domain}/fullchain.pem"
        key_file="/etc/letsencrypt/live/${api_domain}/privkey.pem"
        log "✔ Используется существующий Let's Encrypt SSL сертификат для ${api_domain}"
    elif [[ $is_ip -eq 0 && -n "$api_domain" ]]; then
        log "Попытка получения Let's Encrypt SSL сертификата для ${api_domain}..."
        if command -v certbot >/dev/null 2>&1; then
            mkdir -p "${CERTBOT_DIR:-/var/www/certbot}"
            local cert_ok=0
            if nginx -t >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
                if certbot certonly --nginx -d "$api_domain" --non-interactive --agree-tos --register-unsafely-without-email 2>/dev/null; then
                    cert_ok=1
                fi
            fi
            if [[ $cert_ok -eq 0 && -d "${CERTBOT_DIR:-/var/www/certbot}" ]]; then
                if certbot certonly --webroot -w "${CERTBOT_DIR:-/var/www/certbot}" -d "$api_domain" --non-interactive --agree-tos --register-unsafely-without-email 2>/dev/null; then
                    cert_ok=1
                fi
            fi
            if [[ $cert_ok -eq 0 ]]; then
                local was_active=0
                systemctl is-active --quiet nginx 2>/dev/null && was_active=1
                [[ $was_active -eq 1 ]] && systemctl stop nginx 2>/dev/null || true
                if certbot certonly --standalone -d "$api_domain" --non-interactive --agree-tos --register-unsafely-without-email 2>/dev/null; then
                    cert_ok=1
                fi
                [[ $was_active -eq 1 ]] && systemctl start nginx 2>/dev/null || true
            fi
            if [[ $cert_ok -eq 1 && -f "/etc/letsencrypt/live/${api_domain}/fullchain.pem" ]]; then
                cert_file="/etc/letsencrypt/live/${api_domain}/fullchain.pem"
                key_file="/etc/letsencrypt/live/${api_domain}/privkey.pem"
                log "✔ SSL сертификат Let's Encrypt успешно получен для ${api_domain}"
            fi
        fi
    fi

    # Fallback: генерация самоподписанного сертификата
    if [[ -z "$cert_file" || ! -f "$cert_file" ]]; then
        local ssl_dir="/etc/ssl/just1k_amnezia"
        mkdir -p "$ssl_dir"
        cert_file="${ssl_dir}/server.crt"
        key_file="${ssl_dir}/server.key"
        if [[ ! -f "$cert_file" ]]; then
            log "Генерация SSL-сертификата для HTTPS (${api_domain})..."
            openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
                -keyout "$key_file" -out "$cert_file" \
                -subj "/CN=${api_domain}" 2>/dev/null || true
        fi
    fi

    # Настройка зоны rate-limiting в Nginx conf.d
    mkdir -p /etc/nginx/conf.d
    cat > "/etc/nginx/conf.d/amnezia-ratelimit.conf" <<'EOF'
# JUST1KNODE: AmneziaWG API Rate Limiting Zone
limit_req_zone $binary_remote_addr zone=just1k_amnezia_api:10m rate=30r/s;
EOF

    # Генерация Nginx конфигурации
    local nginx_conf="/etc/nginx/sites-available/just1k-amnezia.conf"
    cat > "$nginx_conf" <<EOF
# JUST1KNODE: AmneziaWG API Reverse Proxy
server {
    listen ${public_port} ssl;
    server_name ${api_domain};

    ssl_certificate ${cert_file};
    ssl_certificate_key ${key_file};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # Защитные заголовки
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;

    client_max_body_size 10M;

    # Блокировка публичной документации и метрик
    location ~ ^/(docs|redoc|openapi.json|metrics) {
        default_type text/plain;
        return 404 "Not Found\n";
    }

    location / {
        limit_req zone=just1k_amnezia_api burst=50 nodelay;
        limit_req_status 429;

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

    # Удаление конфликтующих старых симлинков amnezia в sites-enabled
    rm -f /etc/nginx/sites-enabled/amnezia-api* /etc/nginx/sites-enabled/just1kbot-amnezia* /etc/nginx/sites-enabled/amnezia* 2>/dev/null || true
    mkdir -p /etc/nginx/sites-enabled
    ln -sf "$nginx_conf" /etc/nginx/sites-enabled/just1k-amnezia.conf
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
        log "✔ Nginx reverse proxy успешно настроен и перезагружен"
        deploy_amnezia_certbot_renewal_hook
    else
        rm -f /etc/nginx/sites-enabled/just1k-amnezia.conf "$nginx_conf" 2>/dev/null || true
        rollback_amnezia_if_needed
        error "Ошибка проверки конфигурации Nginx (nginx -t). Установка прервана."
        return 1
    fi

    # 10. Открытие порта в UFW если фаервол активен
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
        ufw allow "${public_port}/tcp" comment "just1knode amnezia api" >/dev/null 2>&1 || true
    fi

    # 11. Активация защиты от абуза (SMTP 25 + BitTorrent)
    if [[ "$enable_abuse" == "Y" ]]; then
        apply_amnezia_abuse_protection
    else
        log "Защита от абуза (SMTP 25 / BitTorrent) пропущена по выбору пользователя"
    fi

    # 12. Обновление состояния и определение мультироли (Coexistence)
    if [[ "$prev_role" == "relay" || "$prev_role" == "dual" ]]; then
        set_state_val "role" "dual"
        log "Режим узла обновлен: DUAL (Совмещенный Relay + AmneziaWG)"
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

    local container_name
    container_name="$(detect_amnezia_container)"

    echo -e "  🌐 Протокол:           ${BOLD}${GREEN}AmneziaWG (${container_name})${NC}"
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

    local api_url
    api_url="$(get_state_val "awg_api_url" "-")"
    local container_name
    container_name="$(detect_amnezia_container)"

    echo -e "  API URL:              ${CYAN}${api_url}${NC}"

    echo -e "\n  Службы:"
    if is_amnezia_container_running; then
        echo -e "    Docker (${container_name}): ${GREEN}● Активен${NC}"
    else
        echo -e "    Docker (${container_name}): ${RED}○ Не запущен${NC}"
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

    # Опрос live stats через healthz
    local health_json
    health_json="$(curl -s --max-time 3 "http://127.0.0.1:${AMNEZIA_LOCAL_PORT}/healthz" 2>/dev/null || true)"
    if echo "$health_json" | grep -q '"interface_ready":true'; then
        echo -e "    Ядро (Интерфейс):     ${GREEN}● Готов к приему пиров${NC}"
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
# РЕЗЕРВНОЕ КОПИРОВАНИЕ И ВОССТАНОВЛЕНИЕ (BACKUP & RESTORE)
# =============================================================================
backup_amnezia_node() {
    local target_file="${1:-}"
    init_state_dir
    local api_key
    api_key="$(get_state_val "awg_api_key" "")"
    if [[ -z "$api_key" && -f "${AMNEZIA_API_ETC}/config.env" ]]; then
        api_key="$(grep "^AMNEZIA_API_KEY=" "${AMNEZIA_API_ETC}/config.env" 2>/dev/null | cut -d= -f2- | tr -d '"'\'' ' || true)"
    fi
    if [[ -z "$api_key" && -f "${AMNEZIA_API_ETC}/config.env" ]]; then
        api_key="$(grep "^FASTIFY_API_KEY=" "${AMNEZIA_API_ETC}/config.env" 2>/dev/null | cut -d= -f2- | tr -d '"'\'' ' || true)"
    fi

    local backup_json=""
    if [[ -n "$api_key" ]]; then
        backup_json="$(curl -s --max-time 10 -H "X-API-Key: ${api_key}" "http://127.0.0.1:${AMNEZIA_LOCAL_PORT}/server/backup" 2>/dev/null || true)"
    fi

    # Fallback to direct container read if API is not responding
    if ! echo "$backup_json" | grep -q '"conf_content"'; then
        local c
        c="$(detect_amnezia_container)"
        if is_amnezia_container_running; then
            local conf_file="/opt/amnezia/awg/awg0.conf"
            if [[ "$c" == "amnezia-awg" ]]; then
                conf_file="/opt/amnezia/awg/wg0.conf"
            fi
            local conf_txt
            conf_txt="$(docker exec "$c" cat "$conf_file" 2>/dev/null || docker exec "$c" cat /opt/amnezia/awg/awg0.conf 2>/dev/null || docker exec "$c" cat /opt/amnezia/awg/wg0.conf 2>/dev/null || true)"
            local table_txt
            table_txt="$(docker exec "$c" cat /opt/amnezia/awg/clientsTable 2>/dev/null || echo "[]")"
            local psk_txt
            psk_txt="$(docker exec "$c" cat /opt/amnezia/awg/wireguard_psk.key 2>/dev/null || docker exec "$c" cat /opt/amnezia/awg/psk.key 2>/dev/null || echo "")"
            if [[ -n "$conf_txt" ]]; then
                backup_json="$(python3 -c "
import json, sys, time
try:
    tbl = json.loads(sys.argv[3]) if sys.argv[3].strip() else []
except Exception:
    tbl = []
print(json.dumps({
    'version': 1,
    'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'container': sys.argv[1],
    'conf_content': sys.argv[2],
    'clients_table': tbl,
    'server_psk': sys.argv[4].strip(),
}, indent=2))
" "$c" "$conf_txt" "$table_txt" "$psk_txt" 2>/dev/null || true)"
            fi
        fi
    fi

    if [[ -z "$backup_json" ]] || ! echo "$backup_json" | grep -q '"conf_content"'; then
        error "Не удалось сформировать резервную копию AmneziaWG (сервис и контейнер недоступны)"
        return 1
    fi

    if [[ -n "$target_file" ]]; then
        echo "$backup_json" > "$target_file"
        log "✔ Резервная копия сохранена в файл: $target_file"
    else
        echo "$backup_json"
    fi
    return 0
}

restore_amnezia_node() {
    local source_file="${1:-}"
    if [[ -z "$source_file" || ! -f "$source_file" ]]; then
        error "Укажите существующий файл резервной копии: just1knode amnezia restore <backup.json>"
        return 1
    fi
    check_root
    init_state_dir

    local api_key
    api_key="$(get_state_val "awg_api_key" "")"
    if [[ -z "$api_key" && -f "${AMNEZIA_API_ETC}/config.env" ]]; then
        api_key="$(grep "^AMNEZIA_API_KEY=" "${AMNEZIA_API_ETC}/config.env" 2>/dev/null | cut -d= -f2- | tr -d '"'\'' ' || true)"
    fi

    local resp=""
    if [[ -n "$api_key" ]]; then
        resp="$(curl -s --max-time 15 -H "X-API-Key: ${api_key}" -H "Content-Type: application/json" -d @"${source_file}" "http://127.0.0.1:${AMNEZIA_LOCAL_PORT}/server/backup" 2>/dev/null || true)"
    fi

    if echo "$resp" | grep -q '"status":"ok"'; then
        log "✔ Резервная копия успешно восстановлена через API."
        return 0
    fi

    # Fallback to direct container write
    local c
    c="$(detect_amnezia_container)"
    if is_amnezia_container_running; then
        log "Восстановление напрямую в Docker контейнер ${c}..."
        if python3 -c "
import json, sys, subprocess

with open(sys.argv[1], 'r', encoding='utf-8') as f:
    data = json.load(f)

c = sys.argv[2]
conf = data.get('conf_content') or (data.get('amnezia') or {}).get('config')
table = data.get('clients_table') or (data.get('amnezia') or {}).get('clientsTable')
psk = data.get('server_psk')

if not conf or '[Interface]' not in conf:
    sys.exit(1)

conf_name = 'wg0.conf' if 'awg2' not in c else 'awg0.conf'
iface = 'wg0' if 'awg2' not in c else 'awg0'
tool = 'wg' if 'awg2' not in c else 'awg'
conf_path = f'/opt/amnezia/awg/{conf_name}'

p = subprocess.Popen(['docker', 'exec', '-i', c, 'sh', '-c', f'cat > {conf_path}'], stdin=subprocess.PIPE)
p.communicate(conf.encode('utf-8'))
if p.returncode != 0:
    sys.exit(2)

if table is not None:
    p = subprocess.Popen(['docker', 'exec', '-i', c, 'sh', '-c', 'cat > /opt/amnezia/awg/clientsTable'], stdin=subprocess.PIPE)
    p.communicate(json.dumps(table).encode('utf-8'))

if psk:
    p = subprocess.Popen(['docker', 'exec', '-i', c, 'sh', '-c', 'cat > /opt/amnezia/awg/wireguard_psk.key'], stdin=subprocess.PIPE)
    p.communicate((psk.strip() + '\n').encode('utf-8'))

subprocess.run(['docker', 'exec', c, tool, 'syncconf', iface, conf_path], check=False)
" "$source_file" "$c"; then
            log "✔ Резервная копия успешно восстановлена напрямую в контейнер ${c}."
            return 0
        fi
    fi

    error "Не удалось восстановить резервную копию AmneziaWG."
    return 1
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

    rm -rf "$AMNEZIA_API_DIR" "$AMNEZIA_API_ETC" /etc/ssl/just1k_amnezia 2>/dev/null || true
    rm -f /etc/nginx/conf.d/amnezia-ratelimit.conf /etc/nginx/sites-enabled/just1k-amnezia.conf /etc/nginx/sites-available/just1k-amnezia.conf 2>/dev/null || true
    rm -f "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/deploy/restart-amnezia-nginx.sh" 2>/dev/null || true
    if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || true
    fi

    # Закрытие порта в UFW
    local pub_port
    pub_port="$(get_state_val "awg_port" "${AMNEZIA_PUBLIC_PORT}")"
    if command -v ufw >/dev/null 2>&1 && [[ -n "$pub_port" && "$pub_port" != "-" ]]; then
        ufw delete allow "${pub_port}/tcp" >/dev/null 2>&1 || true
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
    set_state_val "awg_domain" ""
    set_state_val "awg_port" ""

    log "✔ Компонент AmneziaWG API успешно удален с сервера."
}
