#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Главный диспетчер и панель управления серверными узлами
# =============================================================================
set -euo pipefail

# Принудительная UTF-8 локаль и режим UTF-8 в Python (PEP 540)
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8


# Определение каталога скрипта с защитой от запуска через pipe (curl | bash)
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SCRIPT_SOURCE" && "$SCRIPT_SOURCE" != "bash" && "$SCRIPT_SOURCE" != "-bash" ]]; then
    if command -v realpath >/dev/null 2>&1; then
        resolved_source="$(realpath "$SCRIPT_SOURCE" 2>/dev/null || echo "$SCRIPT_SOURCE")"
    elif command -v readlink >/dev/null 2>&1; then
        resolved_source="$(readlink -f "$SCRIPT_SOURCE" 2>/dev/null || echo "$SCRIPT_SOURCE")"
    else
        resolved_source="$SCRIPT_SOURCE"
    fi
    SCRIPT_DIR="$(cd "$(dirname "$resolved_source")" 2>/dev/null && pwd)"
fi

# Если скрипт запущен через pipe (curl | bash) или модули не найдены локально:
# выполняем автономную загрузку модулей в /opt/just1knode и перезапускаем
if [[ -z "$SCRIPT_DIR" || ! -f "${SCRIPT_DIR}/lib/common.sh" ]]; then
    INSTALL_DIR="${INSTALL_DIR:-/opt/just1knode}"
    mkdir -p "$INSTALL_DIR"
    
    echo -e "\033[1;34m==>\033[0m \033[1mJUST1KNODE: Инициализация и развертывание модулей в ${INSTALL_DIR}...\033[0m"
    
    if [[ -d "/app/just1knode" && -f "/app/just1knode/lib/common.sh" ]]; then
        cp -a /app/just1knode/. "$INSTALL_DIR/"
        if [[ -d "/app/scripts/xray_api" ]]; then
            mkdir -p "${XRAY_API_DIR:-/opt/xray-api}"
            cp -a /app/scripts/xray_api/. "${XRAY_API_DIR:-/opt/xray-api}/"
        fi
        if [[ -d "/app/scripts/amnezia_api" ]]; then
            mkdir -p "${AMNEZIA_API_DIR:-/opt/amnezia-api}"
            cp -a /app/scripts/amnezia_api/. "${AMNEZIA_API_DIR:-/opt/amnezia-api}/"
            mkdir -p "$INSTALL_DIR/scripts/amnezia_api"
            cp -a /app/scripts/amnezia_api/. "$INSTALL_DIR/scripts/amnezia_api/"
        fi
    else
        JUST1KBOT_REPO_URL="${JUST1KBOT_REPO_URL:-https://github.com/justik13/just1kbot}"
        JUST1KBOT_REF="${JUST1KBOT_REF:-main}"
        
        if [[ "$JUST1KBOT_REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
            archive_url="${JUST1KBOT_REPO_URL}/archive/${JUST1KBOT_REF}.tar.gz"
        else
            archive_url="${JUST1KBOT_REPO_URL}/archive/refs/heads/${JUST1KBOT_REF}.tar.gz"
        fi
        
        tmp_tar="/tmp/just1knode_boot_$$.tar.gz"
        tmp_extract="/tmp/just1knode_extract_$$"
        rm -rf "$tmp_tar" "$tmp_extract"
        mkdir -p "$tmp_extract"
        
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL "$archive_url" -o "$tmp_tar"
        elif command -v wget >/dev/null 2>&1; then
            wget -qO "$tmp_tar" "$archive_url"
        else
            echo "Ошибка: для установки требуется curl или wget." >&2
            exit 1
        fi
        
        tar -xzf "$tmp_tar" -C "$tmp_extract" --strip-components=1
        cp -a "$tmp_extract/just1knode/." "$INSTALL_DIR/"
        if [[ -d "$tmp_extract/scripts/xray_api" ]]; then
            mkdir -p "${XRAY_API_DIR:-/opt/xray-api}"
            cp -a "$tmp_extract/scripts/xray_api/." "${XRAY_API_DIR:-/opt/xray-api}/"
        fi
        if [[ -d "$tmp_extract/scripts/amnezia_api" ]]; then
            mkdir -p "${AMNEZIA_API_DIR:-/opt/amnezia-api}"
            cp -a "$tmp_extract/scripts/amnezia_api/." "${AMNEZIA_API_DIR:-/opt/amnezia-api}/"
            mkdir -p "$INSTALL_DIR/scripts/amnezia_api"
            cp -a "$tmp_extract/scripts/amnezia_api/." "$INSTALL_DIR/scripts/amnezia_api/"
        fi
        rm -rf "$tmp_tar" "$tmp_extract"
    fi
    
    chmod +x "$INSTALL_DIR/just1knode.sh"
    ln -sf "$INSTALL_DIR/just1knode.sh" /usr/local/bin/just1knode
    
    echo -e "\033[1;32m✔\033[0m Модули успешно установлены в ${INSTALL_DIR}"
    echo -e "\033[1;32m✔\033[0m Команда зарегистрирована: \033[1;36mjust1knode\033[0m"
    echo ""
    
    if [[ -t 0 ]]; then
        exec "$INSTALL_DIR/just1knode.sh" "$@"
    elif (exec </dev/tty) 2>/dev/null; then
        exec "$INSTALL_DIR/just1knode.sh" "$@" </dev/tty
    else
        exec "$INSTALL_DIR/just1knode.sh" "$@"
    fi
fi

VERSION_FILE="${SCRIPT_DIR}/VERSION"
JUST1KNODE_VERSION="2.0.0"
if [[ -f "$VERSION_FILE" ]]; then
    JUST1KNODE_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
fi

# Git-коммит рядом с VERSION: доступен при запуске из репозитория.
# На проде (/opt/just1knode без .git) пусто — тогда показываем только версию.
JUST1KNODE_COMMIT=""
if [[ -n "${SCRIPT_DIR:-}" ]]; then
    JUST1KNODE_COMMIT="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
fi

# Единая метка версии: "v2.1.2 (abc1234)" или "v2.1.2" без коммита.
node_version_label() {
    if [[ -n "${JUST1KNODE_COMMIT:-}" ]]; then
        echo "v${JUST1KNODE_VERSION} (${JUST1KNODE_COMMIT})"
    else
        echo "v${JUST1KNODE_VERSION}"
    fi
}

# Строка версии внутри бокса панели: центрирование под ширину 61.
print_node_version_box_line() {
    local label pad_left pad_right
    label="Версия: $(node_version_label)"
    pad_left=$(( (61 - ${#label}) / 2 ))
    pad_right=$(( 61 - ${#label} - pad_left ))
    if [[ $pad_left -lt 0 || $pad_right -lt 0 ]]; then
        label="${label:0:61}"
        pad_left=0
        pad_right=0
    fi
    printf '│%*s%s%*s│\n' "$pad_left" "" "$label" "$pad_right" ""
}

# Best-effort проверка новой версии при входе в меню. Только информация:
# один короткий запрос к VERSION на main и сравнение, не вызывает update_node.
# Уведомляет только когда remote строго новее локальной (semver-направление);
# равные, более старые и невалидные значения молча пропускаются.
check_node_update_on_entry() {
    local repo_url ref remote_ver newest
    repo_url="${JUST1KBOT_REPO_URL:-https://github.com/justik13/just1kbot}"
    ref="${JUST1KBOT_REF:-main}"
    if [[ "$repo_url" != "https://github.com/justik13/just1kbot" || "$ref" != "main" ]]; then
        return 0
    fi
    command -v curl >/dev/null 2>&1 || return 0
    remote_ver="$(curl -fsSL --max-time 5 "https://raw.githubusercontent.com/justik13/just1kbot/main/just1knode/VERSION" 2>/dev/null | tr -d '[:space:]' || true)"
    [[ "$remote_ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 0
    [[ "$JUST1KNODE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 0
    newest="$(printf '%s\n%s\n' "$JUST1KNODE_VERSION" "$remote_ver" | sort -V | tail -n 1)"
    if [[ "$newest" == "$remote_ver" && "$remote_ver" != "$JUST1KNODE_VERSION" ]]; then
        echo -e "  ${YELLOW}⚠️  Доступна новая версия just1knode: v${remote_ver} (у вас v${JUST1KNODE_VERSION}). Воспользуйтесь пунктом «Обновить утилиту».${NC}"
    fi
    return 0
}

# Подключение библиотек
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/backup.sh
source "${SCRIPT_DIR}/lib/backup.sh"
# shellcheck source=lib/state.sh
source "${SCRIPT_DIR}/lib/state.sh"
# shellcheck source=lib/ssl.sh
source "${SCRIPT_DIR}/lib/ssl.sh"
# shellcheck source=lib/traffic_watchdog.sh
source "${SCRIPT_DIR}/lib/traffic_watchdog.sh"

# Подключение модулей
# shellcheck source=modules/xray/core.sh
source "${SCRIPT_DIR}/modules/xray/core.sh"
# shellcheck source=modules/xray/api.sh
source "${SCRIPT_DIR}/modules/xray/api.sh"
# shellcheck source=modules/xray/origin.sh
source "${SCRIPT_DIR}/modules/xray/origin.sh"
# shellcheck source=modules/xray/relay.sh
source "${SCRIPT_DIR}/modules/xray/relay.sh"
# shellcheck source=modules/xray/relays_manage.sh
source "${SCRIPT_DIR}/modules/xray/relays_manage.sh"
# shellcheck source=modules/amnezia/amnezia.sh
source "${SCRIPT_DIR}/modules/amnezia/amnezia.sh"

# Автоматическая регистрация команды в /usr/local/bin
ensure_global_symlink() {
    local target="/usr/local/bin/just1knode"
    local current_bin="${SCRIPT_DIR}/just1knode.sh"
    if [[ ! -L "$target" ]] || [[ "$(readlink -f "$target" 2>/dev/null || true)" != "$current_bin" ]]; then
        ln -sf "$current_bin" "$target" 2>/dev/null || true
    fi
}

show_status() {
    title "СТАТУС СЕРВЕРНОГО УЗЛА"
    check_root
    init_state_dir

    local role
    role="$(get_state_val "role" "не настроен")"
    echo -e "  Версия just1knode:    ${BOLD}${CYAN}$(node_version_label)${NC}"
    echo -e "  Роль узла:            ${BOLD}${GREEN}${role}${NC}"

    if [[ "$role" == "origin" ]]; then
        local domain cdn_domain api_url secret_path
        domain="$(get_state_val "domain" "-")"
        cdn_domain="$(get_state_val "cdn_domain" "-")"
        api_url="$(get_state_val "api_url" "-")"
        secret_path="$(get_state_val "secret_base_path" "-")"

        echo -e "  Origin Домен:         ${CYAN}${domain}${NC}"
        echo -e "  CDN Домен:            ${CYAN}${cdn_domain}${NC}"
        echo -e "  API URL:              ${CYAN}${api_url}${NC}"
        echo -e "  Секретный префикс:    ${MAGENTA}${secret_path}${NC}"

        echo -e "\n  Службы:"
        systemctl is-active --quiet xray && echo -e "    Xray Core:   ${GREEN}● Активен${NC}" || echo -e "    Xray Core:   ${RED}○ Не работает${NC}"
        systemctl is-active --quiet xray-api && echo -e "    xray-api:    ${GREEN}● Активен${NC}" || echo -e "    xray-api:    ${RED}○ Не работает${NC}"
        systemctl is-active --quiet nginx && echo -e "    Nginx:       ${GREEN}● Активен${NC}" || echo -e "    Nginx:       ${RED}○ Не работает${NC}"

        list_relays
    elif [[ "$role" == "relay" ]]; then
        local r_port r_orig r_sni r_sec
        r_port="$(get_state_val "relay_port" "-")"
        r_orig="$(get_state_val "origin_ip" "-")"
        r_sni="$(get_state_val "sni" "-")"
        r_sec="$(get_state_val "security" "tls")"

        echo -e "  Порт туннеля:         ${CYAN}${r_port}${NC} (${r_sec^^})"
        echo -e "  Разрешенный Origin:   ${CYAN}${r_orig}${NC}"
        echo -e "  Домен / SNI:          ${CYAN}${r_sni}${NC}"

        echo -e "\n  Службы:"
        systemctl is-active --quiet xray && echo -e "    Xray Relay:  ${GREEN}● Активен${NC}" || echo -e "    Xray Relay:  ${RED}○ Не работает${NC}"
    elif [[ "$role" == "awg" ]]; then
        local a_url a_port
        a_url="$(get_state_val "awg_api_url" "-")"
        a_port="$(get_state_val "awg_port" "8443")"

        echo -e "  Amnezia API URL:      ${CYAN}${a_url}${NC}"
        echo -e "  HTTPS Порт:           ${CYAN}${a_port}${NC}"

        echo -e "\n  Службы:"
        local c_name
        c_name="$(detect_amnezia_container 2>/dev/null || echo "amnezia-awg2")"
        if is_amnezia_container_running 2>/dev/null; then
            echo -e "    Docker (${c_name}): ${GREEN}● Активен${NC}"
        else
            echo -e "    Docker (${c_name}): ${RED}○ Не запущен${NC}"
        fi
        systemctl is-active --quiet amnezia-api && echo -e "    amnezia-api:          ${GREEN}● Активен${NC}" || echo -e "    amnezia-api:          ${RED}○ Не работает${NC}"
        systemctl is-active --quiet nginx && echo -e "    Nginx (8443):         ${GREEN}● Активен${NC}" || echo -e "    Nginx (8443):         ${RED}○ Не работает${NC}"
    elif [[ "$role" == "dual" ]]; then
        local r_port r_orig r_sni a_url a_port r_sec
        r_port="$(get_state_val "relay_port" "-")"
        r_orig="$(get_state_val "origin_ip" "-")"
        r_sni="$(get_state_val "sni" "-")"
        r_sec="$(get_state_val "security" "tls")"
        a_url="$(get_state_val "awg_api_url" "-")"
        a_port="$(get_state_val "awg_port" "8443")"

        echo -e "  [Relay] Порт туннеля: ${CYAN}${r_port}${NC} (${r_sec^^}, Origin: ${r_orig}, SNI: ${r_sni})"
        echo -e "  [AWG]   API URL:      ${CYAN}${a_url}${NC} (HTTPS Порт: ${a_port})"

        echo -e "\n  Службы:"
        systemctl is-active --quiet xray && echo -e "    Xray Relay:           ${GREEN}● Активен${NC}" || echo -e "    Xray Relay:           ${RED}○ Не работает${NC}"
        local c_name_dual
        c_name_dual="$(detect_amnezia_container 2>/dev/null || echo "amnezia-awg2")"
        if is_amnezia_container_running 2>/dev/null; then
            echo -e "    Docker (${c_name_dual}): ${GREEN}● Активен${NC}"
        else
            echo -e "    Docker (${c_name_dual}): ${RED}○ Не запущен${NC}"
        fi
        systemctl is-active --quiet amnezia-api && echo -e "    amnezia-api:          ${GREEN}● Активен${NC}" || echo -e "    amnezia-api:          ${RED}○ Не работает${NC}"
        systemctl is-active --quiet nginx && echo -e "    Nginx (8443):         ${GREEN}● Активен${NC}" || echo -e "    Nginx (8443):         ${RED}○ Не работает${NC}"
    fi

    local t_status
    t_status="$(get_state_val "traffic_limit_status" "disabled")"
    if [[ "$t_status" == "enabled" ]]; then
        echo -e "\n  Контроль трафика:"
        local lim_gb
        lim_gb="$(get_state_val "traffic_limit_gb" "-")"
        local r_day
        r_day="$(get_state_val "traffic_reset_day" "1")"
        echo -e "    Лимит хостинга: ${CYAN}${lim_gb} ГБ${NC} (сброс: ${r_day}-е число)"
    fi
}

show_bot_credentials() {
    title "ДАННЫЕ ДЛЯ ДОБАВЛЕНИЯ В TELEGRAM-БОТ (/admin)"
    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        warn "Данные для бота доступны только на сервере с ролью Origin."
        return
    fi

    local domain cdn_domain api_url api_key secret_path bot_ip bot_domain
    domain="$(get_state_val "domain" "-")"
    cdn_domain="$(get_state_val "cdn_domain" "-")"
    api_url="$(get_state_val "api_url" "-")"
    api_key="$(get_state_val "api_key" "-")"
    secret_path="$(get_state_val "secret_base_path" "-")"
    bot_ip="$(get_state_val "bot_ip" "-")"
    bot_domain="$(get_state_val "bot_domain" "-")"

    echo -e "  🌐 Origin Домен:      ${CYAN}${domain}${NC}"
    echo -e "  ☁️ CDN Домен:         ${CYAN}${cdn_domain}${NC}"
    echo -e "  🔗 API URL бота:      ${CYAN}${api_url}${NC}"
    echo -e "  🤖 BOT IP:            ${CYAN}${bot_ip}${NC}"
    echo -e "  🤖 BOT Домен:         ${CYAN}${bot_domain}${NC}"
    echo -e "  🔑 API Ключ:          ${YELLOW}${api_key}${NC}"
    echo -e "  🛡️ Секретный префикс: ${MAGENTA}${secret_path}${NC}"
    echo -e "  🩺 Проверка CDN:      curl -X OPTIONS https://${cdn_domain}/cdn-check\n"
}

show_relay_credentials() {
    title "ДАННЫЕ ПОДКЛЮЧЕНИЯ RELAY (КОМАНДА ДЛЯ ORIGIN)"
    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "relay" && "$role" != "dual" ]]; then
        warn "Данные доступны только на сервере с ролью Relay или Dual."
        return
    fi

    local my_ip r_port r_uuid r_sec r_pubkey r_shortid r_sni
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"
    r_port="$(get_state_val "relay_port" "10443")"
    r_uuid="$(get_state_val "tunnel_uuid")"
    r_sec="$(get_state_val "security" "")"
    r_pubkey="$(get_state_val "public_key" "")"
    r_shortid="$(get_state_val "short_id" "")"
    r_sni="$(get_state_val "sni" "")"

    # Определение режима и нормализация для узлов v2.1.2 (где security не сохранялось в state.json)
    if [[ -z "$r_sec" ]]; then
        if [[ -n "$r_pubkey" && "$r_pubkey" != "-" ]]; then
            r_sec="reality"
        elif [[ -f "$XRAY_CONFIG" ]]; then
            r_sec="$(python3 -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
    for ib in cfg.get('inbounds', []):
        sec = ib.get('streamSettings', {}).get('security')
        if sec in ('tls', 'reality'):
            print(sec)
            sys.exit(0)
except Exception:
    pass
print('reality')
" "$XRAY_CONFIG" 2>/dev/null || echo "reality")"
        else
            r_sec="reality"
        fi
        set_state_val "security" "$r_sec" 2>/dev/null || true
    fi

    [[ -z "$r_pubkey" ]] && r_pubkey="-"
    [[ -z "$r_shortid" ]] && r_shortid="-"

    local detected_code=""
    local geo_json
    geo_json="$(curl -s --max-time 3 "https://ipinfo.io/${my_ip}/json" 2>/dev/null || true)"
    if [[ -n "$geo_json" ]]; then
        local c_code
        c_code="$(python3 -c "import json, sys; d=json.loads(sys.argv[1]); print(d.get('country','').lower())" "$geo_json" 2>/dev/null || true)"
        [[ -n "$c_code" && "$c_code" != "ru" ]] && detected_code="$c_code"
    fi
    if [[ -z "$detected_code" && -n "$r_sni" ]]; then
        local first_label="${r_sni%%.*}"
        if [[ "$first_label" =~ ^[a-zA-Z0-9_-]+$ ]]; then
            detected_code="${first_label,,}"
        fi
    fi
    if [[ -z "$detected_code" ]]; then
        detected_code="relay-01"
    fi

    echo -e "${BOLD}Скопируйте и выполните эту команду на вашем Origin-сервере:${NC}"
    if [[ "$r_sec" == "tls" ]]; then
        echo -e "${GREEN}just1knode relay add \"${detected_code^^}\" ${my_ip} ${r_port} \"${r_uuid}\" \"${detected_code}\" \"tls\" \"-\" \"-\" \"${r_sni}\"${NC}\n"
        echo -e "${BOLD}Или, если релей уже был добавлен ранее, обновите SNI на Origin:${NC}"
        echo -e "${CYAN}just1knode relay sni ${detected_code} ${r_sni} tls${NC}\n"
    else
        echo -e "${GREEN}just1knode relay add \"${detected_code^^}\" ${my_ip} ${r_port} \"${r_uuid}\" \"${detected_code}\" \"reality\" \"${r_pubkey}\" \"${r_shortid}\" \"${r_sni}\"${NC}\n"
    fi
}

run_doctor() {
    title "КОМПЛЕКСНАЯ САМОДИАГНОСТИКА (DOCTOR)"
    local failed=0
    local role
    role="$(get_state_val "role" "не определена")"

    log "1. Проверка системных служб..."
    local services_to_check=()
    if [[ "$role" == "origin" ]]; then
        services_to_check+=("xray" "nginx" "xray-api")
    elif [[ "$role" == "relay" ]]; then
        services_to_check+=("xray")
    elif [[ "$role" == "awg" ]]; then
        services_to_check+=("amnezia-api" "nginx")
    elif [[ "$role" == "dual" ]]; then
        services_to_check+=("xray" "amnezia-api" "nginx")
    fi
    for srv in "${services_to_check[@]}"; do
        if systemctl is-active --quiet "$srv" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Служба $srv активна"
        else
            if systemctl is-failed --quiet "$srv" 2>/dev/null; then
                echo -e "  ${RED}✗${NC} Служба $srv в состоянии FAILED (ошибка запуска или start-limit-hit)"
            else
                echo -e "  ${RED}✗${NC} Служба $srv не активна"
            fi
            failed=$((failed + 1))
        fi
    done

    # gRPC проверяется только на Origin узле
    if [[ "$role" == "origin" ]]; then
        log "2. Проверка gRPC порта Xray (127.0.0.1:10085)..."
        if python3 -c "import socket; s = socket.create_connection(('127.0.0.1', 10085), timeout=2); s.close()" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} gRPC сокет Xray отвечает"
        else
            echo -e "  ${RED}✗${NC} gRPC сокет Xray недоступен"
            failed=$((failed + 1))
        fi
    elif [[ "$role" == "relay" || "$role" == "dual" ]]; then
        log "2. Проверка Relay инбаунд порта..."
        local r_port
        r_port="$(get_state_val "relay_port" "10443")"
        if ss -tln 2>/dev/null | grep -qE "[:\s]${r_port}\b" || python3 -c "import socket; s = socket.create_connection(('127.0.0.1', ${r_port}), timeout=2); s.close()" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Порт $r_port прослушивается Xray Relay"
        else
            echo -e "  ${YELLOW}!${NC} Порт $r_port не найден в ss"
        fi
    fi

    if [[ "$role" == "awg" || "$role" == "dual" ]]; then
        log "2b. Проверка Amnezia API сокета (127.0.0.1:4001)..."
        if python3 -c "import socket; s = socket.create_connection(('127.0.0.1', 4001), timeout=2); s.close()" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Локальный порт 4001 (amnezia-api) отвечает"
        else
            echo -e "  ${RED}✗${NC} Локальный порт 4001 (amnezia-api) недоступен"
            failed=$((failed + 1))
        fi
    fi

    if [[ "$role" == "origin" || "$role" == "relay" || "$role" == "dual" ]]; then
        log "3. Проверка конфигурации Xray..."
        if [[ -f "$XRAY_CONFIG" ]] && "$XRAY_BIN" run -test -config "$XRAY_CONFIG" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Конфигурация Xray валидна"
        else
            echo -e "  ${RED}✗${NC} Ошибка конфигурации Xray"
            failed=$((failed + 1))
        fi
    fi

    if [[ "$role" == "awg" || "$role" == "dual" ]]; then
        log "3b. Проверка контейнера и конфигурации AmneziaWG..."
        local c_doc
        c_doc="$(detect_amnezia_container 2>/dev/null || echo "amnezia-awg2")"
        if is_amnezia_container_running 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Docker контейнер: ${c_doc} (активен)"
        else
            echo -e "  ${RED}✗${NC} Docker контейнер: ${c_doc} (не запущен)"
            failed=$((failed + 1))
        fi
        local conf_name="awg0.conf"
        local conf_found=false
        if docker exec "$c_doc" test -f "/opt/amnezia/awg/$conf_name" 2>/dev/null; then
            conf_found=true
        elif [[ -f "/opt/amnezia/awg/$conf_name" ]]; then
            conf_found=true
        fi
        if [[ "$conf_found" == "true" ]]; then
            echo -e "  ${GREEN}✔${NC} Конфигурационный файл ${conf_name} найден"
            local proto_id proto_display
            proto_id="$(detect_awg_protocol_version 2>/dev/null || echo "amneziawg2")"
            case "$proto_id" in
                "amneziawg3.1") proto_display="AmneziaWG 3.1" ;;
                "amneziawg3")   proto_display="AmneziaWG 3.0" ;;
                *)              proto_display="AmneziaWG 2.0" ;;
            esac
            echo -e "  ${GREEN}✔${NC} Протокол: ${proto_display} (${proto_id})"
            if docker exec "$c_doc" awg show awg0 >/dev/null 2>&1; then
                echo -e "  ${GREEN}✔${NC} Интерфейс awg0 активен в ядре"
            fi
        else
            echo -e "  ${RED}✗${NC} Конфигурационный файл ${conf_name} отсутствует"
            failed=$((failed + 1))
        fi
    fi

    if [[ "$role" == "origin" || "$role" == "awg" || "$role" == "dual" ]]; then
        log "4. Проверка синтаксиса Nginx..."
        if nginx -t 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Конфигурация Nginx корректна"
        else
            echo -e "  ${RED}✗${NC} Ошибка синтаксиса Nginx"
            failed=$((failed + 1))
        fi
    fi

    log "5. Проверка SSL сертификатов Let's Encrypt..."
    local domain
    domain="$(get_state_val "domain")"
    if [[ -n "$domain" && -f "/etc/letsencrypt/live/${domain}/fullchain.pem" ]]; then
        local cert_file="/etc/letsencrypt/live/${domain}/fullchain.pem"
        local exp_date
        exp_date="$(openssl x509 -enddate -noout -in "$cert_file" 2>/dev/null | cut -d= -f2 || echo "НЕИЗВЕСТНО")"
        
        # Проверка истечения срока действия (F21)
        if ! openssl x509 -checkend 0 -noout -in "$cert_file" 2>/dev/null; then
            echo -e "  ${RED}✗${NC} SSL сертификат для $domain истек ($exp_date)!"
            failed=$((failed + 1))
        elif ! openssl x509 -checkend 2592000 -noout -in "$cert_file" 2>/dev/null; then
            echo -e "  ${YELLOW}!${NC} SSL сертификат для $domain истекает менее чем через 30 дней: $exp_date"
        else
            echo -e "  ${GREEN}✔${NC} SSL сертификат для $domain валиден до: $exp_date"
        fi

        # Проверка соответствия домена SAN / CN (F21)
        local cert_text
        cert_text="$(openssl x509 -noout -text -in "$cert_file" 2>/dev/null || true)"
        if echo "$cert_text" | grep -qE "DNS:${domain}\b|CN\s*=\s*${domain}\b"; then
            echo -e "  ${GREEN}✔${NC} Домен $domain подтвержден в сертификате (SAN/CN)"
        else
            echo -e "  ${RED}✗${NC} Домен $domain не найден в SAN/CN сертификата!"
            failed=$((failed + 1))
        fi
    else
        echo -e "  ${YELLOW}i${NC} SSL сертификат для домена $domain не найден (нормально для Relay)"
    fi

    log "6. Проверка UFW фаервола..."
    if ufw status 2>/dev/null | grep -qi "Status: active"; then
        echo -e "  ${GREEN}✔${NC} UFW фаервол активен"
        local ufw_out
        ufw_out="$(ufw status verbose 2>/dev/null || ufw status 2>/dev/null || true)"

        if [[ "$role" == "origin" ]]; then
            local bot_ip
            bot_ip="$(get_state_val "bot_ip")"
            if echo "$ufw_out" | grep -E "8444(/tcp)?\s+ALLOW\s+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                echo -e "  ${RED}✗${NC} УЯЗВИМОСТЬ: Порт 8444 открыт для всех (0.0.0.0/0)!"
                failed=$((failed + 1))
            elif [[ -n "$bot_ip" ]] && echo "$ufw_out" | grep -F "$bot_ip" | grep -q "8444"; then
                echo -e "  ${GREEN}✔${NC} Порт 8444 защищен и доступен только с BOT_IP ($bot_ip)"
            elif [[ -n "$bot_ip" ]]; then
                echo -e "  ${YELLOW}!${NC} Правило для BOT_IP ($bot_ip) на порт 8444 не найдено в UFW"
                failed=$((failed + 1))
            else
                echo -e "  ${YELLOW}!${NC} BOT_IP не настроен в state.json"
            fi
        elif [[ "$role" == "awg" || "$role" == "dual" ]]; then
            local awg_p bot_ip
            awg_p="$(get_state_val "awg_port" "8443")"
            bot_ip="$(get_state_val "bot_ip")"

            if echo "$ufw_out" | grep -E "${awg_p}(/tcp)?\s+ALLOW\s+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                echo -e "  ${YELLOW}!${NC} Порт API AmneziaWG $awg_p открыт для всех (рекомендуется ограничить: just1knode set-bot-ip <IP>)"
            elif [[ -n "$bot_ip" ]] && echo "$ufw_out" | grep -F "$bot_ip" | grep -q "$awg_p"; then
                echo -e "  ${GREEN}✔${NC} Порт API AmneziaWG $awg_p защищен и доступен только с BOT_IP ($bot_ip)"
            elif [[ -n "$bot_ip" ]]; then
                echo -e "  ${YELLOW}!${NC} Правило для BOT_IP ($bot_ip) на порт $awg_p не найдено в UFW"
                failed=$((failed + 1))
            fi

            if [[ "$role" == "dual" ]]; then
                local relay_port origin_ip
                relay_port="$(get_state_val "relay_port" "10443")"
                origin_ip="$(get_state_val "origin_ip")"

                if echo "$ufw_out" | grep -E "${relay_port}(/tcp)?\s+ALLOW\s+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                    echo -e "  ${RED}✗${NC} УЯЗВИМОСТЬ: Порт релея $relay_port открыт для всех (0.0.0.0/0)!"
                    failed=$((failed + 1))
                elif [[ -n "$origin_ip" ]] && echo "$ufw_out" | grep -F "$origin_ip" | grep -q "$relay_port"; then
                    echo -e "  ${GREEN}✔${NC} Порт $relay_port защищен и доступен только с ORIGIN_IP ($origin_ip)"
                fi
            fi
        elif [[ "$role" == "relay" ]]; then
            local relay_port origin_ip
            relay_port="$(get_state_val "relay_port" "10443")"
            origin_ip="$(get_state_val "origin_ip")"

            if echo "$ufw_out" | grep -E "${relay_port}(/tcp)?\s+ALLOW\s+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                echo -e "  ${RED}✗${NC} УЯЗВИМОСТЬ: Порт релея $relay_port открыт для всех (0.0.0.0/0)!"
                failed=$((failed + 1))
            elif [[ -n "$origin_ip" ]] && echo "$ufw_out" | grep -F "$origin_ip" | grep -q "$relay_port"; then
                echo -e "  ${GREEN}✔${NC} Порт $relay_port защищен и доступен только с ORIGIN_IP ($origin_ip)"
            fi
        fi
    else
        echo -e "  ${YELLOW}!${NC} UFW фаервол не активен"
    fi

    if [[ "$role" == "origin" && -f "$RELAYS_FILE" ]]; then
        log "7. Проверка доступности подключенных Relay-узлов..."
        auto_heal_relays_registry
        local relay_probe_res
        relay_probe_res=$(python3 -c "
import json, socket, sys, os
rf = sys.argv[1]
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8') as f:
            relays = json.load(f)
        for r in relays:
            if not isinstance(r, dict): continue
            name = r.get('name', '-')
            code = r.get('code', '-')
            ip = r.get('ip', '')
            port = int(r.get('port', 10443))
            if not ip: continue
            try:
                s = socket.create_connection((ip, port), timeout=3)
                s.close()
                print(f'OK\t{name}\t{code}\t{ip}\t{port}')
            except Exception as e:
                print(f'FAIL\t{name}\t{code}\t{ip}\t{port}\t{e}')
    except Exception as e:
        print(f'ERROR\t{e}')
" "$RELAYS_FILE" 2>/dev/null || true)
        if [[ -n "$relay_probe_res" ]]; then
            while IFS=$'\t' read -r status name code ip port err; do
                if [[ "$status" == "OK" ]]; then
                    echo -e "  ${GREEN}✔${NC} Relay '$name' ($code: $ip:$port) доступен по сети"
                elif [[ "$status" == "FAIL" ]]; then
                    echo -e "  ${RED}✗${NC} Relay '$name' ($code: $ip:$port) НЕ ОТВЕЧАЕТ (${err:-timeout})!"
                    failed=$((failed + 1))
                fi
            done <<< "$relay_probe_res"
        else
            echo -e "  ${YELLOW}i${NC} Нет зарегистрированных Relay-узлов для проверки"
        fi
    fi

    if [[ "$role" == "origin" ]]; then
        local sub_prefix
        sub_prefix="$(get_state_val "sub_path_prefix" 2>/dev/null || true)"
        if [[ -z "$sub_prefix" ]]; then
            sub_prefix="${WHITE_INTERNET_SUB_PATH_PREFIX:-/sub/wl}"
        fi
        sub_prefix="${sub_prefix%/}"
        [[ ! "$sub_prefix" =~ ^/ ]] && sub_prefix="/$sub_prefix"

        log "8. Проверка Nginx-проксирования подписок (${sub_prefix})..."
        local check_target="${domain:-localhost}"
        local sub_code
        local sub_err=0
        sub_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 --resolve "${check_target}:443:127.0.0.1" "https://${check_target}${sub_prefix}/ping" 2>/dev/null)" || sub_err=$?
        if [[ "$sub_code" == "200" && $sub_err -eq 0 ]]; then
            echo -e "  ${GREEN}✔${NC} Nginx прокси подписок (${sub_prefix}/ping) отвечает 200 OK (TLS валиден)"
        elif [[ $sub_err -eq 60 ]]; then
            echo -e "  ${RED}✗${NC} ОШИБКА TLS: Сертификат для https://${check_target} недействителен или просрочен (curl error 60)!"
            failed=$((failed + 1))
        elif [[ "$sub_code" == "502" ]]; then
            local current_bot_domain
            current_bot_domain="$(get_state_val "bot_domain" "<не задан>")"
            echo -e "  ${RED}✗${NC} ОШИБКА 502 Bad Gateway: Nginx не может связаться с ботом (bot_domain: '$current_bot_domain')!"
            echo -e "      ${YELLOW}→${NC} Проверьте цепочку SSL (proxy_ssl_verify_depth), DNS и логи: tail -n 10 /var/log/nginx/error.log"
            failed=$((failed + 1))
        elif [[ "$sub_code" == "504" ]]; then
            local current_bot_domain
            current_bot_domain="$(get_state_val "bot_domain" "<не задан>")"
            echo -e "  ${RED}✗${NC} ОШИБКА 504 Gateway Timeout: Nginx не дождался ответа от бота (bot_domain: '$current_bot_domain')!"
            echo -e "      ${YELLOW}→${NC} Возможные причины: IP бота блокируется ТСПУ (TCP SYN drop), бот перегружен/не отвечает или сбой DNS."
            echo -e "      ${YELLOW}→${NC} Проверьте логи: tail -n 10 /var/log/nginx/error.log и убедитесь, что домен бота проксируется через CDN/Cloudflare."
            failed=$((failed + 1))
        elif [[ "$sub_code" == "404" ]]; then
            echo -e "  ${RED}✗${NC} ОШИБКА 404 Not Found: Nginx прокси отвечает 404 (эндпоинт ${sub_prefix}/ping не найден на боте)!"
            failed=$((failed + 1))
        elif [[ $sub_err -eq 28 ]]; then
            echo -e "  ${RED}✗${NC} ТАЙМАУТ ПОДКЛЮЧЕНИЯ (curl error 28): Запрос к https://${check_target}${sub_prefix}/ping превысил лимит времени!"
            echo -e "      ${YELLOW}→${NC} Nginx или вышестоящий бот зависли при обработке. Проверьте: tail -n 10 /var/log/nginx/error.log"
            failed=$((failed + 1))
        elif [[ "$sub_code" == "000" || $sub_err -ne 0 ]]; then
            local insecure_code
            insecure_code="$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 10 --resolve "${check_target}:443:127.0.0.1" "https://${check_target}${sub_prefix}/ping" 2>/dev/null || echo "000")"
            if [[ "$insecure_code" == "200" ]]; then
                echo -e "  ${RED}✗${NC} TLS ОШИБКА: Nginx отвечает 200 OK только без проверки сертификата (curl -k). Проверьте Let's Encrypt / CA!"
                failed=$((failed + 1))
            else
                echo -e "  ${RED}✗${NC} Не удалось выполнить запрос к https://${check_target}${sub_prefix}/ping (Nginx недоступен, код: $sub_code, err: $sub_err)"
                failed=$((failed + 1))
            fi
        else
            echo -e "  ${RED}✗${NC} ОШИБКА: Nginx прокси вернул неожиданный HTTP код: $sub_code (ожидался 200 OK)!"
            failed=$((failed + 1))
        fi

        local cdn_domain
        cdn_domain="$(get_state_val "cdn_domain" "")"
        if [[ -n "$cdn_domain" && "$cdn_domain" != "$check_target" && "$cdn_domain" != "-" ]]; then
            log "9. Проверка доступности CDN подписок (${cdn_domain})..."
            local cdn_code
            local cdn_err=0
            cdn_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${cdn_domain}${sub_prefix}/ping" 2>/dev/null)" || cdn_err=$?
            if [[ "$cdn_code" == "200" && $cdn_err -eq 0 ]]; then
                echo -e "  ${GREEN}✔${NC} Публичный CDN прокси (${sub_prefix}/ping) отвечает 200 OK (TLS валиден)"
            elif [[ $cdn_err -eq 60 ]]; then
                echo -e "  ${RED}✗${NC} ОШИБКА TLS CDN: Сертификат для https://${cdn_domain} не прошел валидацию (curl error 60)!"
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "502" ]]; then
                echo -e "  ${RED}✗${NC} CDN вернул 502 Bad Gateway (проверьте Origin и CDN кэш)!"
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "504" ]]; then
                echo -e "  ${RED}✗${NC} CDN вернул 504 Gateway Timeout: Origin или вышестоящий бот не ответили вовремя!"
                echo -e "      ${YELLOW}→${NC} Проверьте доступность Origin и домена бота, а также логи /var/log/nginx/error.log на Origin."
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "404" ]]; then
                echo -e "  ${RED}✗${NC} CDN вернул 404 Not Found (эндпоинт ${sub_prefix}/ping не найден на CDN/Origin)!"
                failed=$((failed + 1))
            elif [[ $cdn_err -eq 28 ]]; then
                echo -e "  ${RED}✗${NC} ТАЙМАУТ CDN (curl error 28): https://${cdn_domain}${sub_prefix}/ping не ответил вовремя!"
                echo -e "      ${YELLOW}→${NC} Проверьте статус сети CDN и доступность домена из РФ."
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "000" || $cdn_err -ne 0 ]]; then
                local cdn_insecure
                cdn_insecure="$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${cdn_domain}${sub_prefix}/ping" 2>/dev/null || echo "000")"
                if [[ "$cdn_insecure" == "200" ]]; then
                    echo -e "  ${RED}✗${NC} TLS ОШИБКА CDN: ${cdn_domain} отвечает 200 OK только без проверки SSL (curl -k)!"
                    failed=$((failed + 1))
                else
                    echo -e "  ${RED}✗${NC} CDN ${cdn_domain} недоступен по сети с этого узла (код: $cdn_code, err: $cdn_err)"
                    failed=$((failed + 1))
                fi
            else
                echo -e "  ${RED}✗${NC} ОШИБКА: CDN вернул неожиданный HTTP код: $cdn_code (ожидался 200 OK)!"
                failed=$((failed + 1))
            fi
        fi
    fi

    if [[ "$role" == "awg" || "$role" == "dual" ]]; then
        log "10. Проверка правил сетевой защиты Anti-Abuse..."
        if check_amnezia_abuse_rules; then
            echo -e "  ${GREEN}✔${NC} Сетевая защита Anti-Abuse активна (SMTP:25 + BitTorrent L7 TCP/UDP/DHT)"
        else
            echo -e "  ${YELLOW}!${NC} Сетевая защита Anti-Abuse неполная или отсутствует в iptables"
            warn "ВНИМАНИЕ: Сетевая защита Anti-Abuse не активна! Запуск автоматического восстановления (Auto-Heal)..."
            if apply_amnezia_abuse_protection && check_amnezia_abuse_rules; then
                echo -e "  ${GREEN}✔${NC} Правила сетевой защиты Anti-Abuse успешно восстановлены и активны."
            else
                echo -e "  ${RED}✗${NC} ОШИБКА: Не удалось восстановить правила Anti-Abuse (проверьте модуль ядра xt_string)!"
                failed=$((failed + 1))
            fi
        fi
    fi

    log "11. Проверка сетевого стелс-режима (ICMP Echo)..."
    local icmp_val="0"
    if [[ -f /proc/sys/net/ipv4/icmp_echo_ignore_all ]]; then
        icmp_val="$(cat /proc/sys/net/ipv4/icmp_echo_ignore_all 2>/dev/null || echo "0")"
    fi
    local sysctl_conf="${JUST1KNODE_SYSCTL_IPV6_CONF:-/etc/sysctl.d/99-disable-ipv6.conf}"
    local icmp_persisted=0
    if [[ -f "$sysctl_conf" ]] && grep -Eq '^[[:space:]]*net\.ipv4\.icmp_echo_ignore_all[[:space:]]*=[[:space:]]*1' "$sysctl_conf" 2>/dev/null; then
        icmp_persisted=1
    fi

    if [[ "$icmp_val" == "1" && "$icmp_persisted" -eq 1 ]]; then
        echo -e "  ${GREEN}✔${NC} ICMP Echo отключен (стелс-режим активен в ядре и сохранен в drop-in)"
    elif [[ "$icmp_val" == "1" ]]; then
        echo -e "  ${RED}✗${NC} ICMP Echo отключен в ядре, но не зафиксирован в $sysctl_conf (до перезагрузки, выполните: just1knode update)"
        failed=$((failed + 1))
    else
        echo -e "  ${RED}✗${NC} ICMP Echo активен (стелс-режим выключен, выполните: just1knode update)"
        failed=$((failed + 1))
    fi

    if [[ $failed -eq 0 ]]; then
        echo -e "\n${BOLD}${GREEN}Все проверки пройдены успешно! Узел полностью здоров.${NC}\n"
    else
        echo -e "\n${BOLD}${RED}Обнаружено ошибок: ${failed}. Требуется внимание администратора.${NC}\n"
    fi
}

reset_node() {
    title "СБРОС И ПЕРЕУСТАНОВКА УЗЛА"
    check_root
    warn "ВНИМАНИЕ! Это действие остановит службы и очистит конфигурации just1knode."
    read -rp "Вы уверены, что хотите сбросить узел? (введите 'yes' для подтверждения): " confirm
    if [[ "$confirm" != "yes" ]]; then
        info "Сброс отменен."
        return
    fi

    systemctl stop xray xray-api amnezia-api 2>/dev/null || true
    systemctl disable xray xray-api amnezia-api 2>/dev/null || true
    remove_traffic_watchdog_timer
    remove_amnezia_abuse_protection 2>/dev/null || true
    rm -f /etc/nginx/sites-enabled/just1k-origin.conf /etc/nginx/sites-available/just1k-origin.conf /etc/nginx/sites-enabled/just1k-amnezia.conf /etc/nginx/sites-available/just1k-amnezia.conf /etc/nginx/conf.d/xhttp-map.conf /etc/letsencrypt/renewal-hooks/deploy/restart-xray-nginx.sh /etc/letsencrypt/renewal-hooks/deploy/restart-amnezia-nginx.sh /etc/letsencrypt/renewal-hooks/pre/01-stop-port80-docker.sh /etc/letsencrypt/renewal-hooks/post/01-start-port80-docker.sh /etc/letsencrypt/renewal-hooks/pre/stop-port80-docker.sh /etc/letsencrypt/renewal-hooks/post/start-port80-docker.sh 2>/dev/null || true
    rm -rf /etc/nginx/just1k_relays.d /etc/just1knode /etc/xray-api /etc/amnezia-api /opt/amnezia-api /etc/ssl/just1k_amnezia 2>/dev/null || true
    if [[ ! -e /etc/nginx/sites-enabled/default ]]; then
        if [[ -f /etc/nginx/sites-available/default.user.bak ]]; then
            cp -a /etc/nginx/sites-available/default.user.bak /etc/nginx/sites-available/default 2>/dev/null || true
            ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default 2>/dev/null || true
            rm -f /etc/nginx/sites-available/default.user.bak 2>/dev/null || true
        fi
    fi
    systemctl reload nginx 2>/dev/null || true
    log "Узел успешно сброшен в исходное состояние."
}

# =============================================================================
# ПОЛНОЕ БЕЗВОЗВРАТНОЕ УДАЛЕНИЕ JUST1KNODE (UNINSTALL)
# =============================================================================
uninstall_node() {
    title "ПОЛНОЕ БЕЗВОЗВРАТНОЕ УДАЛЕНИЕ JUST1KNODE (UNINSTALL)"
    check_root
    init_state_dir

    local force=false
    local confirm_code=""
    local purge_backups=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --force|-f)
                force=true
                shift
                ;;
            --confirm=*)
                confirm_code="${1#*=}"
                shift
                ;;
            --confirm)
                if [[ $# -ge 2 && "$2" != --* ]]; then
                    confirm_code="$2"
                    shift 2
                else
                    shift
                fi
                ;;
            --purge-backups)
                purge_backups=true
                shift
                ;;
            --uninstall)
                shift
                ;;
            --yes|-y)
                force=true
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    echo ""
    echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${RED}🚨 ВНИМАНИЕ: ПОЛНОЕ И БЕЗВОЗВРАТНОЕ УДАЛЕНИЕ JUST1KNODE (UNINSTALL)${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
    echo -e "Вы собираетесь полностью удалить узел just1knode с данного сервера."
    echo ""
    echo -e "${BOLD}Что будет остановлено и удалено без остатка:${NC}"
    echo -e "  1. ${BOLD}Системные службы systemd:${NC} остановка и отключение xray.service, xray-api.service"
    echo -e "  2. ${BOLD}Юниты systemd:${NC} /etc/systemd/system/xray*.service (целевой reset-failed)"
    echo -e "  3. ${BOLD}Процессы:${NC} завершение всех активных фоновых процессов Xray и Uvicorn API"
    echo -e "  4. ${BOLD}Пользователь и группа:${NC} системная учетная запись 'xrayapi'"
    echo -e "  5. ${BOLD}Исполняемые файлы и базы:${NC} ${XRAY_BIN:-/usr/local/bin/xray}, ${XRAY_SHARE_DIR:-/usr/local/share/xray} (geoip/geosite)"
    echo -e "  6. ${BOLD}Конфигурации Xray:${NC} ${XRAY_CONFIG_DIR:-/usr/local/etc/xray}"
    echo -e "  7. ${BOLD}Агент Xray-API:${NC} ${XRAY_API_DIR:-/opt/xray-api}, ${XRAY_API_ETC:-/etc/xray-api}, ${XRAY_API_LIB:-/var/lib/xray-api}"
    echo -e "  8. ${BOLD}Веб-сервер Nginx:${NC} виртуальный хост just1k-origin.conf, conf.d/xhttp-map.conf, just1k_relays.d"
    echo -e "     (атомарное восстановление default сайта, если создавалась резервная копия default.user.bak)"
    echo -e "  9. ${BOLD}Let's Encrypt renewal hook:${NC} deploy-скрипт перезапуска служб"
    echo -e "  10. ${BOLD}Камуфляжный сайт:${NC} /var/www/html/index.html (если создан just1knode)"
    echo -e "  11. ${BOLD}Конфигурация ядра:${NC} ${JUST1KNODE_SYSCTL_IPV6_CONF:-/etc/sysctl.d/99-disable-ipv6.conf} (восстановление IPv6 в runtime)"
    echo -e "  12. ${BOLD}Фаервол UFW:${NC} удаление открытых портов (8444, Relay tunnel)"
    echo -e "  13. ${BOLD}Каталог состояния и бэкапов:${NC} ${STATE_DIR:-/etc/just1knode} (бэкапы в ${BACKUP_DIR:-/var/backups/just1knode} сохраняются без --purge-backups)"
    echo -e "  14. ${BOLD}Глобальная команда:${NC} /usr/local/bin/just1knode"
    echo -e "  15. ${BOLD}Директория утилиты:${NC} ${INSTALL_DIR:-/opt/just1knode}"
    echo ""
    echo -e "${BOLD}${RED}⚠️  ВНИМАНИЕ: СЕРВЕР ПЕРЕСТАНЕТ ПРИНИМАТЬ VPN-ТРАФИК И ОБСЛУЖИВАТЬ КЛИЕНТОВ!${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}"
    echo ""

    if [[ "$confirm_code" == "DELETE" || "$confirm_code" == "УДАЛИТЬ" ]]; then
        info "Подтверждение удаления получено через аргумент командной строки (--confirm)."
    elif [[ "$force" == "true" ]]; then
        error "Для удаления с флагом --force / --yes требуется явное подтверждение: --confirm=DELETE (или --confirm=УДАЛИТЬ). Процедура прервана (Fail-Closed)."
        return 1
    else
        # Confirmation step 1
        local c1="n"
        if ! read -r -t 60 -p "Вы действительно хотите начать процедуру полного удаления just1knode? [y/N]: " c1 2>/dev/null; then
            error "В неинтерактивном режиме для удаления требуется явный флаг: --confirm=DELETE (или --confirm=УДАЛИТЬ). Процедура прервана (Fail-Closed)."
            return 1
        fi
        if [[ ! "$c1" =~ ^[Yy]$ ]]; then
            info "Удаление отменено пользователем."
            return 0
        fi

        # Confirmation step 2 (strict keyword match)
        echo ""
        echo -e "${BOLD}${RED}ФИНАЛЬНОЕ ПОДТВЕРЖДЕНИЕ! Это действие необратимо.${NC}"
        local c2=""
        if ! read -r -t 60 -p "Для подтверждения введите заглавными буквами слово 'УДАЛИТЬ' или 'DELETE': " c2 2>/dev/null; then
            c2=""
        fi
        if [[ "$c2" != "DELETE" && "$c2" != "УДАЛИТЬ" ]]; then
            warn "Подтверждение не совпало (введено: '$c2'). Удаление отменено!"
            return 0
        fi
    fi

    local node_cleanup_errors=()

    # Валидация системных путей (Defense-in-Depth защита от удаления критических каталогов ОС)
    local protected_node_paths=(
        "${XRAY_CONFIG_DIR:-/usr/local/etc/xray}"
        "${XRAY_SHARE_DIR:-/usr/local/share/xray}"
        "${XRAY_API_DIR:-/opt/xray-api}"
        "${XRAY_API_ETC:-/etc/xray-api}"
        "${XRAY_API_LIB:-/var/lib/xray-api}"
        "${AMNEZIA_API_DIR:-/opt/amnezia-api}"
        "${AMNEZIA_API_ETC:-/etc/amnezia-api}"
        "${STATE_DIR:-/etc/just1knode}"
        "${BACKUP_DIR:-/var/backups/just1knode}"
    )
    for p in "${protected_node_paths[@]}"; do
        local norm_p="${p%/}"
        if [[ -z "$norm_p" || "$norm_p" =~ ^(/|/etc|/var|/usr|/usr/local|/root|/home|/tmp|/opt)$ ]]; then
            error "Попытка удаления защищенного системного каталога ($p)! Процедура удаления прервана (Fail-Closed)."
            return 1
        fi
    done

    info "1/11. Остановка и отключение системных служб systemd..."
    remove_traffic_watchdog_timer
    systemctl stop xray xray-api amnezia-api 2>/dev/null || true
    systemctl disable xray xray-api amnezia-api 2>/dev/null || true
    rm -f "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/xray.service" "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/xray-api.service" "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/amnezia-api.service" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    systemctl reset-failed xray xray-api amnezia-api just1knode-traffic 2>/dev/null || true

    info "2/11. Завершение активных процессов ядра и API..."
    local xray_proc_name
    xray_proc_name="$(basename "${XRAY_BIN:-xray}")"
    pkill -9 -x "$xray_proc_name" 2>/dev/null || true
    pkill -9 -u xrayapi 2>/dev/null || true

    info "3/11. Удаление пользователя и группы xrayapi..."
    if id -u xrayapi >/dev/null 2>&1; then
        userdel -f xrayapi 2>/dev/null || userdel xrayapi 2>/dev/null || node_cleanup_errors+=("Не удалось удалить системного пользователя xrayapi")
    fi
    if getent group xrayapi >/dev/null 2>&1; then
        groupdel xrayapi 2>/dev/null || true
    fi

    info "4/11. Удаление бинарных файлов и конфигураций Xray..."
    rm -f "${XRAY_BIN:-/usr/local/bin/xray}" 2>/dev/null || true
    rm -rf "${XRAY_CONFIG_DIR:-/usr/local/etc/xray}" 2>/dev/null || true
    rm -rf "${XRAY_SHARE_DIR:-/usr/local/share/xray}" 2>/dev/null || true

    info "5/11. Удаление агентов API (Xray-API, Amnezia-API) и виртуальных окружений..."
    rm -rf "${XRAY_API_DIR:-/opt/xray-api}" 2>/dev/null || true
    rm -rf "${XRAY_API_ETC:-/etc/xray-api}" 2>/dev/null || true
    rm -rf "${XRAY_API_LIB:-/var/lib/xray-api}" 2>/dev/null || true
    rm -rf "${AMNEZIA_API_DIR:-/opt/amnezia-api}" 2>/dev/null || true
    rm -rf "${AMNEZIA_API_ETC:-/etc/amnezia-api}" 2>/dev/null || true

    info "6/11. Очистка конфигурации Nginx и сетевых правил..."
    local nginx_conf_dir="${NGINX_CONF_DIR:-/etc/nginx}"
    rm -f "${nginx_conf_dir}/sites-enabled/just1k-origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/sites-available/just1k-origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/sites-enabled/just1k-amnezia.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/sites-available/just1k-amnezia.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/just1k-origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/just1k-bootstrap.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/xhttp-map.conf" 2>/dev/null || true
    rm -rf "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" 2>/dev/null || true
    remove_amnezia_abuse_protection 2>/dev/null || true

    if [[ -f "${nginx_conf_dir}/sites-available/default.user.bak" ]]; then
        info "Восстановление исходного default сайта в Nginx..."
        if cp -a "${nginx_conf_dir}/sites-available/default.user.bak" "${nginx_conf_dir}/sites-available/default" 2>/dev/null; then
            ln -sf "${nginx_conf_dir}/sites-available/default" "${nginx_conf_dir}/sites-enabled/default" 2>/dev/null || true
            rm -f "${nginx_conf_dir}/sites-available/default.user.bak" 2>/dev/null || true
        else
            node_cleanup_errors+=("Не удалось восстановить default.user.bak в Nginx")
        fi
    elif [[ -f "${nginx_conf_dir}/sites-available/default" && ! -e "${nginx_conf_dir}/sites-enabled/default" ]]; then
        info "Восстановление стандартного default сайта в Nginx..."
        ln -sf "${nginx_conf_dir}/sites-available/default" "${nginx_conf_dir}/sites-enabled/default" 2>/dev/null || true
    fi

    if command -v nginx >/dev/null 2>&1; then
        if systemctl is-active --quiet nginx 2>/dev/null; then
            if nginx -t >/dev/null 2>&1; then
                systemctl reload nginx 2>/dev/null || true
            fi
        fi
    fi

    info "7/11. Удаление веб-файлов и хуков Let's Encrypt..."
    local www_index="${WWW_HTML_DIR:-/var/www/html}/index.html"
    if [[ -f "$www_index" ]] && (grep -q "Cloud Ingress Network Node" "$www_index" 2>/dev/null || grep -q "SimpleCalc" "$www_index" 2>/dev/null); then
        rm -f "$www_index" 2>/dev/null || true
    fi
    local certbot_dir="${CERTBOT_DIR:-/var/www/certbot}"
    if [[ -d "$certbot_dir" ]] && [[ -z "$(ls -A "$certbot_dir" 2>/dev/null)" ]]; then
        rmdir "$certbot_dir" 2>/dev/null || true
    fi
    rm -f "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/deploy/restart-xray-nginx.sh" \
          "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/deploy/restart-amnezia-nginx.sh" \
          "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/pre/01-stop-port80-docker.sh" \
          "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/post/01-start-port80-docker.sh" \
          "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/pre/stop-port80-docker.sh" \
          "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/post/start-port80-docker.sh" 2>/dev/null || true

    info "8/11. Удаление конфигурации ядра sysctl и восстановление параметров сети..."
    local sysctl_ipv6_conf="${JUST1KNODE_SYSCTL_IPV6_CONF:-/etc/sysctl.d/99-disable-ipv6.conf}"
    local sysctl_cleaned=0
    if [[ -f "$sysctl_ipv6_conf" ]]; then
        rm -f "$sysctl_ipv6_conf" 2>/dev/null || true
        sysctl_cleaned=1
    fi
    local ufw_conf="${JUST1KNODE_UFW_SYSCTL_CONF:-/etc/ufw/sysctl.conf}"
    if [[ -f "$ufw_conf" ]]; then
        sed -i -E '/^[#[:space:]]*net\/ipv4\/icmp_echo_ignore_all[[:space:]]*=/d' "$ufw_conf" 2>/dev/null || true
    fi
    if command -v sysctl >/dev/null 2>&1; then
        sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.lo.disable_ipv6=0 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.icmp_echo_ignore_all=0 >/dev/null 2>&1 || true
        if [[ $sysctl_cleaned -eq 1 ]]; then
            sysctl --system >/dev/null 2>&1 || true
        fi
    fi

    info "9/11. Очистка правил фаервола (UFW)..."
    if command -v ufw >/dev/null 2>&1; then
        local st_relay_port st_origin_ip st_bot_ip st_awg_port
        st_relay_port="$(get_state_val "relay_port" 2>/dev/null || true)"
        st_origin_ip="$(get_state_val "origin_ip" 2>/dev/null || true)"
        st_bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"
        st_awg_port="$(get_state_val "awg_port" 2>/dev/null || true)"
        [[ -z "$st_awg_port" && (-f /etc/nginx/sites-available/just1k-amnezia.conf || -f /etc/systemd/system/amnezia-api.service) ]] && st_awg_port="8443"

        if [[ -n "$st_relay_port" ]]; then
            if [[ -n "$st_origin_ip" ]]; then
                ufw delete allow from "$st_origin_ip" to any port "$st_relay_port" proto tcp 2>/dev/null || true
            fi
            ufw delete allow "$st_relay_port"/tcp 2>/dev/null || true
            ufw delete allow "$st_relay_port" 2>/dev/null || true
        fi
        if [[ -n "$st_bot_ip" ]]; then
            ufw delete allow from "$st_bot_ip" to any port 8444 proto tcp 2>/dev/null || true
            if [[ -n "$st_awg_port" ]]; then
                ufw delete allow from "$st_bot_ip" to any port "$st_awg_port" proto tcp 2>/dev/null || true
            fi
        fi
        ufw delete allow 8444/tcp 2>/dev/null || true
        ufw delete allow 8444 2>/dev/null || true
        if [[ -n "$st_awg_port" ]]; then
            ufw delete allow "${st_awg_port}/tcp" 2>/dev/null || true
            ufw delete allow "${st_awg_port}" 2>/dev/null || true
        fi
    fi

    info "10/11. Удаление состояния, бэкапов и блокировок..."
    rm -rf "${STATE_DIR:-/etc/just1knode}" 2>/dev/null || true
    if [[ "$purge_backups" == "true" ]]; then
        rm -rf "${BACKUP_DIR:-/var/backups/just1knode}" 2>/dev/null || true
        log "Каталог бэкапов ${BACKUP_DIR:-/var/backups/just1knode} удален (--purge-backups)."
    else
        log "Каталог бэкапов сохранен: ${BACKUP_DIR:-/var/backups/just1knode} (используйте --purge-backups для удаления)."
    fi
    rm -rf /run/lock/just1knode /tmp/just1knode* 2>/dev/null || true

    info "11/11. Удаление глобальной команды и каталога установки..."
    local global_bin="${JUST1KNODE_GLOBAL_BIN:-/usr/local/bin/just1knode}"
    rm -f "$global_bin" 2>/dev/null || true

    local install_dir="${INSTALL_DIR:-/opt/just1knode}"
    cd /tmp || cd /
    if [[ "$install_dir" == "/opt/just1knode" || -n "${JUST1KNODE_ALLOW_CUSTOM_INSTALL_RM:-}" ]]; then
        if [[ -d "$install_dir" ]]; then
            rm -rf "$install_dir" 2>/dev/null || true
        fi
    fi

    # Post-Uninstall Verification & Fail-Closed Reporting
    info "Верификация чистоты системы после удаления (Post-Verification)..."
    if [[ -d "$install_dir" && "$install_dir" == "/opt/just1knode" ]]; then
        node_cleanup_errors+=("Директория установки все еще существует: $install_dir")
    fi
    if [[ -e "$global_bin" || -L "$global_bin" ]]; then
        node_cleanup_errors+=("Глобальный исполняемый файл все еще существует: $global_bin")
    fi
    if [[ -f "$sysctl_ipv6_conf" ]]; then
        node_cleanup_errors+=("Файл sysctl $sysctl_ipv6_conf все еще существует")
    fi
    if [[ -e "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/xray.service" ]]; then
        node_cleanup_errors+=("Служба systemd xray.service все еще существует")
    fi
    if [[ -e "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/xray-api.service" ]]; then
        node_cleanup_errors+=("Служба systemd xray-api.service все еще существует")
    fi
    if [[ -e "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/amnezia-api.service" ]]; then
        node_cleanup_errors+=("Служба systemd amnezia-api.service все еще существует")
    fi

    echo ""
    if [[ ${#node_cleanup_errors[@]} -gt 0 ]]; then
        warn "Внимание: удаление just1knode завершено с ошибками (обнаружены остаточные ресурсы)!"
        for err in "${node_cleanup_errors[@]}"; do
            echo -e "  ${RED}• $err${NC}"
        done
        error "Процедура удаления завершилась со статусом FAIL-CLOSED (код 1). Устраните указанные остатки вручную."
        return 1
    fi

    log "✨ just1knode успешно и полностью удален с сервера без остатков (верификация пройдена)."
    exit 0
}

manage_traffic_limit_menu() {
    title "УПРАВЛЕНИЕ ЛИМИТОМ ТРАФИКА"
    check_root
    show_traffic_limit_status
    echo ""
    echo -e "  ${BOLD}[1]${NC} ⚙️  Включить / настроить лимит"
    echo -e "  ${BOLD}[2]${NC} ⚪ Отключить лимит (Безлимитный режим)"
    echo -e "  ${BOLD}[3]${NC} 🔄 Проверить текущий трафик"
    echo -e "  ${BOLD}[0]${NC} ⬅️  Назад"
    echo ""
    read -rp "Выберите действие [0-3]: " t_act
    case "$t_act" in
        1)
            read -rp "Введите лимит трафика в ГБ (например: 8000 для 8 ТБ): " lim_in
            read -rp "День месяца сброса биллинга у провайдера [по умолчанию: 1]: " day_in
            day_in="${day_in:-1}"
            read -rp "Telegram Bot Token для уведомлений (опционально, Enter для пропуска): " tok_in
            local chat_in=""
            if [[ -n "$tok_in" ]]; then
                read -rp "Telegram Chat ID администратора: " chat_in
            fi
            set_traffic_limit "$lim_in" "$day_in" "$tok_in" "$chat_in"
            ;;
        2)
            disable_traffic_limit
            ;;
        3)
            check_traffic_limit
            show_traffic_limit_status
            ;;
        *)
            return
            ;;
    esac
}

# =============================================================================
# ДИНАМИЧЕСКОЕ КОНТЕКСТНОЕ МЕНЮ
# =============================================================================
main_menu() {
    check_root
    init_state_dir
    ensure_global_symlink

    # Однократная best-effort проверка обновлений при входе (не в цикле).
    local _node_update_notice
    _node_update_notice="$(check_node_update_on_entry || true)"

    while true; do
        clear
        echo -e "${BOLD}${BLUE}"
        echo "┌─────────────────────────────────────────────────────────────┐"
        echo "│                 🚀 JUST1KNODE CONTROL PANEL                 │"
        echo "│              Менеджер серверных узлов Just1kBot             │"
        print_node_version_box_line
        echo "└─────────────────────────────────────────────────────────────┘"
        echo -e "${NC}"
        if [[ -n "$_node_update_notice" ]]; then
            echo -e "${_node_update_notice}"
            echo ""
        fi

        local status
        status="$(get_node_status)"

        if [[ "$status" == "unconfigured" ]]; then
            echo -e "  Статус текущего сервера: ${BOLD}${YELLOW}⚪ НЕ НАСТРОЕН${NC}\n"
            echo -e "  ${BOLD}[1]${NC} 🌐 Установить Origin узел (Белый Интернет — Входной шлюз в РФ)"
            echo -e "  ${BOLD}[2]${NC} 🛡️  Установить Relay узел (Белый Интернет — Зарубежный выход VLESS TLS)"
            echo -e "  ${BOLD}[3]${NC} ⚡ Настроить AmneziaWG узел (Зарубежный выход AmneziaWG API)"
            echo -e "  ${BOLD}[4]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-4]: " choice

            case "$choice" in
                1) install_xray_origin_node; read -rp "Нажмите Enter для продолжения...";;
                2) install_xray_relay_node; read -rp "Нажмите Enter для продолжения...";;
                3) install_amnezia_node; read -rp "Нажмите Enter для продолжения...";;
                4) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac

        elif [[ "$status" == "origin" ]]; then
            local domain cdn_domain bot_dom
            domain="$(get_state_val "domain" "-")"
            cdn_domain="$(get_state_val "cdn_domain" "-")"
            bot_dom="$(get_state_val "bot_domain" "-")"

            echo -e "  Статус текущего сервера: ${BOLD}${GREEN}🇷🇺 ORIGIN (Шлюз РФ)${NC}"
            echo -e "  Origin: ${CYAN}${domain}${NC}  |  CDN: ${CYAN}${cdn_domain}${NC}  |  Bot: ${CYAN}${bot_dom}${NC}\n"

            echo -e "  ${BOLD}[1]${NC} 🔄 Управление Relay-узлами на Origin (Добавить / Удалить / Список)"
            echo -e "  ${BOLD}[2]${NC} 📊 Статус узла и подключенные клиенты"
            echo -e "  ${BOLD}[3]${NC} 🩺 Комплексная самодиагностика (Doctor)"
            echo -e "  ${BOLD}[4]${NC} 🔑 Показать данные для Telegram-бота (/admin)"
            echo -e "  ${BOLD}[5]${NC} 🤖 Изменить IP Telegram-бота (BOT_IP фаервола 8444)"
            echo -e "  ${BOLD}[6]${NC} ⏱️  Лимит сетевого трафика (Traffic Limit)"
            echo -e "  ${BOLD}[7]${NC} 🔄 Обновить утилиту и конфигурацию узла (Auto-Heal & Update)"
            echo -e "  ${BOLD}[8]${NC} ⚡ Обновить ядро Xray-core"
            echo -e "  ${BOLD}[9]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[10]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-10]: " choice

            case "$choice" in
                1) manage_relays_menu; read -rp "Нажмите Enter для продолжения...";;
                2) show_status; read -rp "Нажмите Enter для продолжения...";;
                3) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                4) show_bot_credentials; read -rp "Нажмите Enter для продолжения...";;
                5) set_origin_bot_ip; read -rp "Нажмите Enter для продолжения...";;
                6) manage_traffic_limit_menu; read -rp "Нажмите Enter для продолжения...";;
                7) update_node "all" "1"; read -rp "Нажмите Enter для продолжения...";;
                8) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                9) reset_node; read -rp "Нажмите Enter для продолжения...";;
                10) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac

        elif [[ "$status" == "relay" ]]; then
            local r_port r_orig r_sni r_sec
            r_port="$(get_state_val "relay_port" "10443")"
            r_orig="$(get_state_val "origin_ip" "-")"
            r_sni="$(get_state_val "sni" "-")"
            r_sec="$(get_state_val "security" "tls")"

            echo -e "  Статус текущего сервера: ${BOLD}${GREEN}🛡️ RELAY (Зарубежный выход)${NC}"
            echo -e "  Порт: ${CYAN}${r_port}${NC} (${r_sec^^})  |  Origin IP: ${CYAN}${r_orig}${NC}  |  SNI: ${CYAN}${r_sni}${NC}\n"

            echo -e "  ${BOLD}[1]${NC} 📋 Показать данные подключения (команда для Origin)"
            echo -e "  ${BOLD}[2]${NC} 🔐 Настроить персональный домен Relay (VLESS+TLS)"
            echo -e "  ${BOLD}[3]${NC} 📊 Статус туннеля и сетевой трафик"
            echo -e "  ${BOLD}[4]${NC} ⚡ Добавить AmneziaWG на этот сервер (Режим Dual)"
            echo -e "  ${BOLD}[5]${NC} ⏱️  Лимит сетевого трафика (Traffic Limit)"
            echo -e "  ${BOLD}[6]${NC} 🩺 Комплексная самодиагностика (Doctor)"
            echo -e "  ${BOLD}[7]${NC} 🔄 Обновить утилиту и конфигурацию узла (Auto-Heal & Update)"
            echo -e "  ${BOLD}[8]${NC} ⚡ Обновить ядро Xray-core"
            echo -e "  ${BOLD}[9]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[10]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-10]: " choice

            case "$choice" in
                1) show_relay_credentials; read -rp "Нажмите Enter для продолжения...";;
                2) setup_relay_domain; read -rp "Нажмите Enter для продолжения...";;
                3) show_status; read -rp "Нажмите Enter для продолжения...";;
                4) install_amnezia_node; read -rp "Нажмите Enter для продолжения...";;
                5) manage_traffic_limit_menu; read -rp "Нажмите Enter для продолжения...";;
                6) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                7) update_node "all" "1"; read -rp "Нажмите Enter для продолжения...";;
                8) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                9) reset_node; read -rp "Нажмите Enter для продолжения...";;
                10) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac

        elif [[ "$status" == "awg" ]]; then
            local a_url
            a_url="$(get_state_val "awg_api_url" "-")"

            echo -e "  Статус текущего сервера: ${BOLD}${GREEN}⚡ AMNEZIAWG (Зарубежный выход)${NC}"
            echo -e "  API URL: ${CYAN}${a_url}${NC}\n"

            echo -e "  ${BOLD}[1]${NC} 🔑 Показать данные для Telegram-бота (/admin)"
            echo -e "  ${BOLD}[2]${NC} 🤖 Настроить / обновить IP Telegram-бота (BOT_IP)"
            echo -e "  ${BOLD}[3]${NC} 📊 Статус узла и активные клиенты"
            echo -e "  ${BOLD}[4]${NC} 🛡️  Добавить Relay на этот сервер (Режим Dual)"
            echo -e "  ${BOLD}[5]${NC} ⏱️  Лимит сетевого трафика (Traffic Limit)"
            echo -e "  ${BOLD}[6]${NC} 🩺 Комплексная самодиагностика (Doctor)"
            echo -e "  ${BOLD}[7]${NC} 🔄 Обновить утилиту и конфигурацию узла (Auto-Heal & Update)"
            echo -e "  ${BOLD}[8]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[9]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-9]: " choice

            case "$choice" in
                1) show_amnezia_bot_credentials; read -rp "Нажмите Enter для продолжения...";;
                2) set_origin_bot_ip; read -rp "Нажмите Enter для продолжения...";;
                3) show_status; read -rp "Нажмите Enter для продолжения...";;
                4) install_xray_relay_node; read -rp "Нажмите Enter для продолжения...";;
                5) manage_traffic_limit_menu; read -rp "Нажмите Enter для продолжения...";;
                6) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                7) update_node "all" "1"; read -rp "Нажмите Enter для продолжения...";;
                8) reset_node; read -rp "Нажмите Enter для продолжения...";;
                9) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac

        elif [[ "$status" == "dual" ]]; then
            local r_port a_url r_sni r_sec
            r_port="$(get_state_val "relay_port" "10443")"
            a_url="$(get_state_val "awg_api_url" "-")"
            r_sni="$(get_state_val "sni" "-")"
            r_sec="$(get_state_val "security" "tls")"

            echo -e "  Статус текущего сервера: ${BOLD}${GREEN}⚡🛡️ DUAL (Relay + AmneziaWG)${NC}"
            echo -e "  Relay: ${CYAN}${r_port}${NC} (${r_sec^^}, SNI: ${r_sni})  |  Amnezia API: ${CYAN}${a_url}${NC}\n"

            echo -e "  ${BOLD}[1]${NC} 📋 Показать данные подключения Relay (для Origin)"
            echo -e "  ${BOLD}[2]${NC} 🔐 Настроить персональный домен Relay (VLESS+TLS)"
            echo -e "  ${BOLD}[3]${NC} 🔑 Показать данные AmneziaWG для Telegram-бота (/admin)"
            echo -e "  ${BOLD}[4]${NC} 🤖 Настроить / обновить IP Telegram-бота (BOT_IP)"
            echo -e "  ${BOLD}[5]${NC} 📊 Статус всех служб и сетевой трафик"
            echo -e "  ${BOLD}[6]${NC} ⏱️  Лимит сетевого трафика (Traffic Limit)"
            echo -e "  ${BOLD}[7]${NC} 🩺 Комплексная самодиагностика (Doctor)"
            echo -e "  ${BOLD}[8]${NC} 🔄 Обновить утилиту и конфигурацию узла (Auto-Heal & Update)"
            echo -e "  ${BOLD}[9]${NC} ⚡ Обновить ядро Xray-core"
            echo -e "  ${BOLD}[10]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[11]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-11]: " choice

            case "$choice" in
                1) show_relay_credentials; read -rp "Нажмите Enter для продолжения...";;
                2) setup_relay_domain; read -rp "Нажмите Enter для продолжения...";;
                3) show_amnezia_bot_credentials; read -rp "Нажмите Enter для продолжения...";;
                4) set_origin_bot_ip; read -rp "Нажмите Enter для продолжения...";;
                5) show_status; read -rp "Нажмите Enter для продолжения...";;
                6) manage_traffic_limit_menu; read -rp "Нажмите Enter для продолжения...";;
                7) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                8) update_node "all" "1"; read -rp "Нажмите Enter для продолжения...";;
                9) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                10) reset_node; read -rp "Нажмите Enter для продолжения...";;
                11) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac
        fi
    done
}

# --- Точка входа CLI ---
if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" || -z "${BASH_SOURCE[0]:-}" ]]; then
    if [[ $# -eq 0 ]]; then
        main_menu
    else
        case "$1" in
            install)
                case "${2:-}" in
                    origin|xray-origin) install_xray_origin_node "${3:-}" "${4:-}" "${5:-}" "${6:-}" "${7:-}" "${8:-}" "${9:-}" ;;
                    relay|xray-relay|exit|xray-exit) install_xray_relay_node "${3:-10443}" "${4:-}" "${5:-}" "${6:-tls}" ;;
                    amnezia|awg) install_amnezia_node "${3:-}" "${4:-}" "${5:-}" ;;
                    *) error "Неизвестный тип установки: $2. Доступно: origin, relay, amnezia, awg" ;;
                esac
                ;;
            setup-domain|relay-domain|setup_domain)
                setup_relay_domain "${2:-}"
                ;;
            relay)
                case "${2:-}" in
                    add) add_relay_node "${3:-}" "${4:-}" "${5:-10443}" "${6:-}" "${7:-de}" "${8:-tls}" "${9:-}" "${10:-}" "${11:-}" "${12:-}" ;;
                    remove|del) remove_relay_node "${3:-}" ;;
                    rename) rename_relay_node "${3:-}" "${4:-}" ;;
                    sni|domain) update_relay_sni "${3:-}" "${4:-}" "${5:-tls}" "${6:-}" ;;
                    list) list_relays ;;
                    *) manage_relays_menu ;;
                esac
                ;;
            amnezia|awg)
                case "${2:-}" in
                    install|setup) install_amnezia_node "${3:-}" "${4:-}" "${5:-}" ;;
                    status) show_amnezia_status ;;
                    creds|bot) show_amnezia_bot_credentials ;;
                    bot-ip|set-bot-ip) set_origin_bot_ip "${3:-}" ;;
                    backup) backup_amnezia_node "${3:-}" ;;
                    restore) restore_amnezia_node "${3:-}" ;;
                    uninstall|remove) uninstall_amnezia_component ;;
                    *) install_amnezia_node "${2:-}" "${3:-}" "${4:-}" ;;
                esac
                ;;
            backup)
                case "${2:-}" in
                    amnezia|awg) backup_amnezia_node "${3:-}" ;;
                    *) backup_amnezia_node "${2:-}" ;;
                esac
                ;;
            restore)
                case "${2:-}" in
                    amnezia|awg) restore_amnezia_node "${3:-}" ;;
                    *) restore_amnezia_node "${2:-}" ;;
                esac
                ;;
            limit|traffic)
                case "${2:-}" in
                    set) set_traffic_limit "${3:-}" "${4:-1}" "${5:-}" "${6:-}" ;;
                    disable|off) disable_traffic_limit ;;
                    check) check_traffic_limit ;;
                    status|show|"") show_traffic_limit_status ;;
                    *) error "Использование: just1knode limit [status|set <GB> [reset_day] [tg_token] [tg_chat]|disable|check]" ;;
                esac
                ;;
            status) show_status ;;
            doctor|test) run_doctor ;;
            set-bot-ip|bot-ip)
                set_origin_bot_ip "${2:-}"
                ;;
            anti-abuse|antiabuse|apply-abuse-protection)
                check_root
                apply_amnezia_abuse_protection
                ;;
            remove-anti-abuse|remove-antiabuse|disable-anti-abuse)
                check_root
                remove_amnezia_abuse_protection
                ;;
            update-post)
                check_root
                shift
                update_node_post "${1:-all}" "${2:-0}"
                ;;
            update)
                case "${2:-}" in
                    core|xray) update_xray_core ;;
                    config|heal)
                        role="$(get_state_val "role")"
                        if [[ "$role" == "origin" ]]; then
                            heal_and_update_origin_config
                        elif [[ "$role" == "relay" ]]; then
                            heal_and_update_relay_config
                        elif [[ "$role" == "dual" ]]; then
                            heal_and_update_relay_config
                            apply_amnezia_abuse_protection
                        elif [[ "$role" == "awg" ]]; then
                            apply_amnezia_abuse_protection
                            log "Сетевая защита AmneziaWG актуализирована."
                        else
                            error "Узел не настроен."
                        fi
                        ;;
                    *) update_node "${2:-all}" ;;
                esac
                ;;
            reset) reset_node ;;
            uninstall|remove|purge)
                shift
                uninstall_node "$@"
                ;;
            *) error "Неизвестная команда: $1. Запустите 'just1knode' без аргументов для входа в меню." ;;
        esac
    fi
fi
