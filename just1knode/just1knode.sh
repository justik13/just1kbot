#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Главный диспетчер и панель управления серверными узлами
# =============================================================================
set -euo pipefail

# Определение каталога скрипта с защитой от запуска через pipe (curl | bash)
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SCRIPT_SOURCE" && "$SCRIPT_SOURCE" != "bash" && "$SCRIPT_SOURCE" != "-bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" 2>/dev/null && pwd)"
fi

# Если скрипт запущен через pipe (curl | bash) или модули не найдены локально:
# выполняем автономную загрузку модулей в /opt/just1knode и перезапускаем
if [[ -z "$SCRIPT_DIR" || ! -f "${SCRIPT_DIR}/lib/common.sh" ]]; then
    INSTALL_DIR="/opt/just1knode"
    mkdir -p "$INSTALL_DIR"
    
    echo -e "\033[1;34m==>\033[0m \033[1mJUST1KNODE: Инициализация и развертывание модулей в ${INSTALL_DIR}...\033[0m"
    
    if [[ -d "/app/just1knode" && -f "/app/just1knode/lib/common.sh" ]]; then
        cp -r /app/just1knode/* "$INSTALL_DIR/"
        if [[ -d "/app/scripts/xray_api" ]]; then
            mkdir -p /opt/xray-api
            cp -r /app/scripts/xray_api/* /opt/xray-api/
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
        cp -r "$tmp_extract/just1knode"/* "$INSTALL_DIR/"
        if [[ -d "$tmp_extract/scripts/xray_api" ]]; then
            mkdir -p /opt/xray-api
            cp -r "$tmp_extract/scripts/xray_api"/* /opt/xray-api/
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

# Подключение библиотек
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/backup.sh
source "${SCRIPT_DIR}/lib/backup.sh"
# shellcheck source=lib/state.sh
source "${SCRIPT_DIR}/lib/state.sh"
# shellcheck source=lib/ssl.sh
source "${SCRIPT_DIR}/lib/ssl.sh"

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
    echo -e "  Версия just1knode:    ${BOLD}${CYAN}v${JUST1KNODE_VERSION}${NC}"
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
        local r_port r_orig r_sni
        r_port="$(get_state_val "relay_port" "-")"
        r_orig="$(get_state_val "origin_ip" "-")"
        r_sni="$(get_state_val "sni" "-")"

        echo -e "  Порт REALITY:         ${CYAN}${r_port}${NC}"
        echo -e "  Разрешенный Origin:   ${CYAN}${r_orig}${NC}"
        echo -e "  Маскировка SNI:       ${CYAN}${r_sni}${NC}"

        echo -e "\n  Службы:"
        systemctl is-active --quiet xray && echo -e "    Xray Relay:  ${GREEN}● Активен${NC}" || echo -e "    Xray Relay:  ${RED}○ Не работает${NC}"
        show_amnezia_status
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

    local domain cdn_domain api_url api_key secret_path bot_ip
    domain="$(get_state_val "domain" "-")"
    cdn_domain="$(get_state_val "cdn_domain" "-")"
    api_url="$(get_state_val "api_url" "-")"
    api_key="$(get_state_val "api_key" "-")"
    secret_path="$(get_state_val "secret_base_path" "-")"
    bot_ip="$(get_state_val "bot_ip" "-")"

    echo -e "  🌐 Origin Домен:      ${CYAN}${domain}${NC}"
    echo -e "  ☁️ CDN Домен:         ${CYAN}${cdn_domain}${NC}"
    echo -e "  🔗 API URL бота:      ${CYAN}${api_url}${NC}"
    echo -e "  🤖 BOT IP:            ${CYAN}${bot_ip}${NC}"
    echo -e "  🔑 API Ключ:          ${YELLOW}${api_key}${NC}"
    echo -e "  🛡️ Секретный префикс: ${MAGENTA}${secret_path}${NC}"
    echo -e "  🩺 Проверка CDN:      curl -X OPTIONS https://${cdn_domain}/cdn-check\n"
}

show_relay_credentials() {
    title "ДАННЫЕ ПОДКЛЮЧЕНИЯ RELAY (КОМАНДА ДЛЯ ORIGIN)"
    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "relay" ]]; then
        warn "Данные доступны только на сервере с ролью Relay."
        return
    fi

    local my_ip r_port r_uuid r_pubkey r_shortid r_sni
    my_ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || curl -s --max-time 5 icanhazip.com 2>/dev/null || hostname -I | awk '{print $1}')"
    r_port="$(get_state_val "relay_port" "10443")"
    r_uuid="$(get_state_val "tunnel_uuid")"
    r_pubkey="$(get_state_val "public_key")"
    r_shortid="$(get_state_val "short_id")"
    r_sni="$(get_state_val "sni" "www.google.com")"

    echo -e "${BOLD}Скопируйте и выполните эту команду на вашем Origin-сервере:${NC}"
    echo -e "${GREEN}just1knode relay add \"Локация\" ${my_ip} ${r_port} \"${r_uuid}\" \"de\" \"reality\" \"${r_pubkey}\" \"${r_shortid}\" \"${r_sni}\"${NC}\n"
}

run_doctor() {
    title "КОМПЛЕКСНАЯ САМОДИАГНОСТИКА (DOCTOR)"
    local failed=0
    local role
    role="$(get_state_val "role" "не определена")"

    log "1. Проверка системных служб..."
    local services_to_check=("xray")
    if [[ "$role" == "origin" ]]; then
        services_to_check+=("nginx" "xray-api")
    fi
    for srv in "${services_to_check[@]}"; do
        if systemctl is-active --quiet "$srv" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Служба $srv активна"
        else
            echo -e "  ${RED}✗${NC} Служба $srv не активна"
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
    else
        log "2. Проверка Relay инбаунд порта..."
        local r_port
        r_port="$(get_state_val "relay_port" "10443")"
        if ss -tln 2>/dev/null | grep -qE "[:\s]${r_port}\b" || python3 -c "import socket; s = socket.create_connection(('127.0.0.1', ${r_port}), timeout=2); s.close()" 2>/dev/null; then
            echo -e "  ${GREEN}✔${NC} Порт $r_port прослушивается Xray Relay"
        else
            echo -e "  ${YELLOW}!${NC} Порт $r_port не найден в ss"
        fi
    fi

    log "3. Проверка конфигурации Xray..."
    if [[ -f "$XRAY_CONFIG" ]] && "$XRAY_BIN" run -test -config "$XRAY_CONFIG" 2>/dev/null; then
        echo -e "  ${GREEN}✔${NC} Конфигурация Xray валидна"
    else
        echo -e "  ${RED}✗${NC} Ошибка конфигурации Xray"
        failed=$((failed + 1))
    fi

    if [[ "$role" == "origin" ]]; then
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

    if [[ $failed -eq 0 ]]; then
        echo -e "\n${BOLD}${GREEN}Все проверки пройдены успешно! Узел полностью здоров.${NC}\n"
    else
        echo -e "\n${BOLD}${RED}Обнаружено ошибок: ${failed}. Требуется внимание администратора.${NC}\n"
    fi
}

doctor_self_check() {
    run_doctor "$@"
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

    systemctl stop xray xray-api 2>/dev/null || true
    systemctl disable xray xray-api 2>/dev/null || true
    rm -f /etc/nginx/sites-enabled/just1k-origin.conf /etc/nginx/sites-available/just1k-origin.conf 2>/dev/null || true
    rm -rf /etc/nginx/just1k_relays.d /etc/just1knode /etc/xray-api 2>/dev/null || true
    systemctl reload nginx 2>/dev/null || true
    log "Узел успешно сброшен в исходное состояние."
}

# =============================================================================
# ДИНАМИЧЕСКОЕ КОНТЕКСТНОЕ МЕНЮ
# =============================================================================
main_menu() {
    check_root
    init_state_dir
    ensure_global_symlink

    while true; do
        clear
        echo -e "${BOLD}${BLUE}"
        echo "┌─────────────────────────────────────────────────────────────┐"
        echo "│                 🚀 JUST1KNODE CONTROL PANEL                 │"
        echo "│              Менеджер серверных узлов Just1kBot             │"
        echo "│                        Версия: v${JUST1KNODE_VERSION}                       │"
        echo "└─────────────────────────────────────────────────────────────┘"
        echo -e "${NC}"

        local status
        status="$(get_node_status)"

        if [[ "$status" == "unconfigured" ]]; then
            echo -e "  Статус текущего сервера: ${BOLD}${YELLOW}⚪ НЕ НАСТРОЕН${NC}\n"
            echo -e "  ${BOLD}[1]${NC} 🌐 Установить Origin узел (Белый Интернет — Входной шлюз в РФ)"
            echo -e "  ${BOLD}[2]${NC} 🛡️  Установить Relay узел (Белый Интернет — Зарубежный выход VLESS REALITY)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-2]: " choice

            case "$choice" in
                1) install_xray_origin_node; read -rp "Нажмите Enter для продолжения...";;
                2) install_xray_relay_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac

        elif [[ "$status" == "origin" ]]; then
            local domain cdn_domain
            domain="$(get_state_val "domain" "-")"
            cdn_domain="$(get_state_val "cdn_domain" "-")"

            echo -e "  Статус текущего сервера: ${BOLD}${GREEN}🇷🇺 ORIGIN (Шлюз РФ)${NC}"
            echo -e "  Origin Домен: ${CYAN}${domain}${NC}  |  CDN Домен: ${CYAN}${cdn_domain}${NC}\n"

            echo -e "  ${BOLD}[1]${NC} 🔄 Управление Relay-узлами на Origin (Добавить / Удалить / Список)"
            echo -e "  ${BOLD}[2]${NC} 📊 Статус узла и подключенные клиенты"
            echo -e "  ${BOLD}[3]${NC} 🩺 Комплексная самодиагностика (Doctor)"
            echo -e "  ${BOLD}[4]${NC} 🔑 Показать данные для Telegram-бота (/admin)"
            echo -e "  ${BOLD}[5]${NC} 🔄 Обновление ядра Xray-core"
            echo -e "  ${BOLD}[6]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-6]: " choice

            case "$choice" in
                1) manage_relays_menu; read -rp "Нажмите Enter для продолжения...";;
                2) show_status; read -rp "Нажмите Enter для продолжения...";;
                3) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                4) show_bot_credentials; read -rp "Нажмите Enter для продолжения...";;
                5) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                6) reset_node; read -rp "Нажмите Enter для продолжения...";;
                0) echo -e "\n${GREEN}До свидания!${NC}\n"; exit 0;;
                *) warn "Неверный выбор."; sleep 1;;
            esac

        elif [[ "$status" == "relay" ]]; then
            local r_port r_orig
            r_port="$(get_state_val "relay_port" "10443")"
            r_orig="$(get_state_val "origin_ip" "-")"

            echo -e "  Статус текущего сервера: ${BOLD}${GREEN}🛡️ RELAY (Зарубежный выход)${NC}"
            echo -e "  Порт: ${CYAN}${r_port}${NC} (REALITY)  |  Origin IP: ${CYAN}${r_orig}${NC}\n"

            echo -e "  ${BOLD}[1]${NC} 📋 Показать данные подключения (команда для Origin)"
            echo -e "  ${BOLD}[2]${NC} 📊 Статус туннеля и сетевой трафик"
            echo -e "  ${BOLD}[3]${NC} 🩺 Комплексная самодиагностика (Doctor)"
            echo -e "  ${BOLD}[4]${NC} 🔄 Обновление ядра Xray-core"
            echo -e "  ${BOLD}[5]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-5]: " choice

            case "$choice" in
                1) show_relay_credentials; read -rp "Нажмите Enter для продолжения...";;
                2) show_status; read -rp "Нажмите Enter для продолжения...";;
                3) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                4) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                5) reset_node; read -rp "Нажмите Enter для продолжения...";;
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
                    origin|xray-origin) install_xray_origin_node "${3:-}" "${4:-}" "${5:-}" "${6:-}" "${7:-}" "${8:-}" ;;
                    relay|xray-relay|exit|xray-exit) install_xray_relay_node "${3:-10443}" "${4:-}" "${5:-www.google.com}" ;;
                    amnezia) install_amnezia_node ;;
                    *) error "Неизвестный тип установки: $2. Доступно: origin, relay, amnezia" ;;
                esac
                ;;
            relay)
                case "${2:-}" in
                    add) add_relay_node "${3:-}" "${4:-}" "${5:-10443}" "${6:-}" "${7:-de}" "${8:-reality}" "${9:-}" "${10:-}" "${11:-www.google.com}" ;;
                    remove|del) remove_relay_node "${3:-}" ;;
                    list) list_relays ;;
                    *) manage_relays_menu ;;
                esac
                ;;
            status) show_status ;;
            doctor|test) run_doctor ;;
            update) update_xray_core ;;
            reset) reset_node ;;
            *) error "Неизвестная команда: $1. Запустите 'just1knode' без аргументов для входа в меню." ;;
        esac
    fi
fi
