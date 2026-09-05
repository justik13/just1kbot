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
    INSTALL_DIR="${INSTALL_DIR:-/opt/just1knode}"
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
        log "8. Проверка Nginx-проксирования подписок (/sub/wl)..."
        local check_target="${domain:-localhost}"
        local sub_code
        local sub_err=0
        sub_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 --resolve "${check_target}:443:127.0.0.1" "https://${check_target}/sub/wl/ping" 2>/dev/null)" || sub_err=$?
        if [[ "$sub_code" == "200" && $sub_err -eq 0 ]]; then
            echo -e "  ${GREEN}✔${NC} Nginx прокси подписок (/sub/wl/ping) отвечает 200 OK (TLS валиден)"
        elif [[ $sub_err -eq 60 ]]; then
            echo -e "  ${RED}✗${NC} ОШИБКА TLS: Сертификат для https://${check_target} недействителен или просрочен (curl error 60)!"
            failed=$((failed + 1))
        elif [[ "$sub_code" == "502" ]]; then
            echo -e "  ${RED}✗${NC} ОШИБКА 502 Bad Gateway: Nginx не может связаться с ботом (проверьте SSL/upstream в sub-wl.conf)!"
            failed=$((failed + 1))
        elif [[ "$sub_code" == "404" ]]; then
            echo -e "  ${RED}✗${NC} ОШИБКА 404 Not Found: Nginx прокси отвечает 404 (эндпоинт /sub/wl/ping не найден на боте)!"
            failed=$((failed + 1))
        elif [[ "$sub_code" == "000" || $sub_err -ne 0 ]]; then
            local insecure_code
            insecure_code="$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 5 --resolve "${check_target}:443:127.0.0.1" "https://${check_target}/sub/wl/ping" 2>/dev/null || echo "000")"
            if [[ "$insecure_code" == "200" ]]; then
                echo -e "  ${RED}✗${NC} TLS ОШИБКА: Nginx отвечает 200 OK только без проверки сертификата (curl -k). Проверьте Let's Encrypt / CA!"
                failed=$((failed + 1))
            else
                echo -e "  ${RED}✗${NC} Не удалось выполнить запрос к https://${check_target}/sub/wl/ping (Nginx недоступен, код: $sub_code, err: $sub_err)"
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
            cdn_code="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "https://${cdn_domain}/sub/wl/ping" 2>/dev/null)" || cdn_err=$?
            if [[ "$cdn_code" == "200" && $cdn_err -eq 0 ]]; then
                echo -e "  ${GREEN}✔${NC} Публичный CDN прокси (/sub/wl/ping) отвечает 200 OK (TLS валиден)"
            elif [[ $cdn_err -eq 60 ]]; then
                echo -e "  ${RED}✗${NC} ОШИБКА TLS CDN: Сертификат для https://${cdn_domain} не прошел валидацию (curl error 60)!"
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "502" ]]; then
                echo -e "  ${RED}✗${NC} CDN вернул 502 Bad Gateway (проверьте Origin и CDN кэш)!"
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "404" ]]; then
                echo -e "  ${RED}✗${NC} CDN вернул 404 Not Found (эндпоинт /sub/wl/ping не найден на CDN/Origin)!"
                failed=$((failed + 1))
            elif [[ "$cdn_code" == "000" || $cdn_err -ne 0 ]]; then
                local cdn_insecure
                cdn_insecure="$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 5 "https://${cdn_domain}/sub/wl/ping" 2>/dev/null || echo "000")"
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
    systemctl stop xray xray-api 2>/dev/null || true
    systemctl disable xray xray-api 2>/dev/null || true
    rm -f "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/xray.service" "${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}/xray-api.service" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
    systemctl reset-failed xray xray-api 2>/dev/null || true

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

    info "5/11. Удаление агента Xray-API и виртуального окружения..."
    rm -rf "${XRAY_API_DIR:-/opt/xray-api}" 2>/dev/null || true
    rm -rf "${XRAY_API_ETC:-/etc/xray-api}" 2>/dev/null || true
    rm -rf "${XRAY_API_LIB:-/var/lib/xray-api}" 2>/dev/null || true

    info "6/11. Очистка конфигурации Nginx..."
    local nginx_conf_dir="${NGINX_CONF_DIR:-/etc/nginx}"
    rm -f "${nginx_conf_dir}/sites-enabled/just1k-origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/sites-available/just1k-origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/just1k-origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/origin.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/just1k-bootstrap.conf" 2>/dev/null || true
    rm -f "${nginx_conf_dir}/conf.d/xhttp-map.conf" 2>/dev/null || true
    rm -rf "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" 2>/dev/null || true

    if [[ -f "${nginx_conf_dir}/sites-available/default.user.bak" ]]; then
        info "Восстановление исходного default сайта в Nginx..."
        if cp -a "${nginx_conf_dir}/sites-available/default.user.bak" "${nginx_conf_dir}/sites-available/default" 2>/dev/null; then
            ln -sf "${nginx_conf_dir}/sites-available/default" "${nginx_conf_dir}/sites-enabled/default" 2>/dev/null || true
            rm -f "${nginx_conf_dir}/sites-available/default.user.bak" 2>/dev/null || true
        else
            node_cleanup_errors+=("Не удалось восстановить default.user.bak в Nginx")
        fi
    fi

    if command -v nginx >/dev/null 2>&1; then
        if systemctl is-active --quiet nginx 2>/dev/null; then
            if nginx -t >/dev/null 2>&1; then
                systemctl reload nginx 2>/dev/null || true
            fi
        fi
    fi

    info "7/11. Удаление камуфляжного сайта и хуков Let's Encrypt..."
    local www_index="${WWW_HTML_DIR:-/var/www/html}/index.html"
    if [[ -f "$www_index" ]] && grep -q "Cloud Ingress Network Node" "$www_index" 2>/dev/null; then
        rm -f "$www_index" 2>/dev/null || true
    fi
    local certbot_dir="${CERTBOT_DIR:-/var/www/certbot}"
    if [[ -d "$certbot_dir" ]] && [[ -z "$(ls -A "$certbot_dir" 2>/dev/null)" ]]; then
        rmdir "$certbot_dir" 2>/dev/null || true
    fi
    rm -f "${LETSENCRYPT_DIR:-/etc/letsencrypt}/renewal-hooks/deploy/restart-xray-nginx.sh" 2>/dev/null || true

    info "8/11. Удаление конфигурации ядра sysctl и восстановление IPv6..."
    local sysctl_ipv6_conf="${JUST1KNODE_SYSCTL_IPV6_CONF:-/etc/sysctl.d/99-disable-ipv6.conf}"
    if [[ -f "$sysctl_ipv6_conf" ]]; then
        rm -f "$sysctl_ipv6_conf" 2>/dev/null || true
        if command -v sysctl >/dev/null 2>&1; then
            sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
            sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null 2>&1 || true
            sysctl -w net.ipv6.conf.lo.disable_ipv6=0 >/dev/null 2>&1 || true
            sysctl --system >/dev/null 2>&1 || true
        fi
    fi

    info "9/11. Очистка правил фаервола (UFW)..."
    if command -v ufw >/dev/null 2>&1; then
        local st_relay_port st_origin_ip st_bot_ip
        st_relay_port="$(get_state_val "relay_port" 2>/dev/null || true)"
        st_origin_ip="$(get_state_val "origin_ip" 2>/dev/null || true)"
        st_bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"

        if [[ -n "$st_relay_port" ]]; then
            if [[ -n "$st_origin_ip" ]]; then
                ufw delete allow from "$st_origin_ip" to any port "$st_relay_port" proto tcp 2>/dev/null || true
            fi
            ufw delete allow "$st_relay_port"/tcp 2>/dev/null || true
            ufw delete allow "$st_relay_port" 2>/dev/null || true
        fi
        if [[ -n "$st_bot_ip" ]]; then
            ufw delete allow from "$st_bot_ip" to any port 8444 proto tcp 2>/dev/null || true
        fi
        ufw delete allow 8444/tcp 2>/dev/null || true
        ufw delete allow 8444 2>/dev/null || true
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
            echo -e "  ${BOLD}[3]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-3]: " choice

            case "$choice" in
                1) install_xray_origin_node; read -rp "Нажмите Enter для продолжения...";;
                2) install_xray_relay_node; read -rp "Нажмите Enter для продолжения...";;
                3) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
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
            echo -e "  ${BOLD}[5]${NC} 🔄 Обновить утилиту и конфигурацию узла (Auto-Heal & Update)"
            echo -e "  ${BOLD}[6]${NC} ⚡ Обновить ядро Xray-core"
            echo -e "  ${BOLD}[7]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[8]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-8]: " choice

            case "$choice" in
                1) manage_relays_menu; read -rp "Нажмите Enter для продолжения...";;
                2) show_status; read -rp "Нажмите Enter для продолжения...";;
                3) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                4) show_bot_credentials; read -rp "Нажмите Enter для продолжения...";;
                5) update_node; read -rp "Нажмите Enter для продолжения...";;
                6) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                7) reset_node; read -rp "Нажмите Enter для продолжения...";;
                8) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
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
            echo -e "  ${BOLD}[4]${NC} 🔄 Обновить утилиту и конфигурацию узла (Auto-Heal & Update)"
            echo -e "  ${BOLD}[5]${NC} ⚡ Обновить ядро Xray-core"
            echo -e "  ${BOLD}[6]${NC} ⚠️ Сбросить / переустановить узел"
            echo -e "  ${BOLD}[7]${NC} 🗑️  Полное удаление (Uninstall just1knode с сервера)"
            echo -e "  ${BOLD}[0]${NC} ❌ Выход"
            echo ""
            read -rp "Выберите действие [0-7]: " choice

            case "$choice" in
                1) show_relay_credentials; read -rp "Нажмите Enter для продолжения...";;
                2) show_status; read -rp "Нажмите Enter для продолжения...";;
                3) run_doctor; read -rp "Нажмите Enter для продолжения...";;
                4) update_node; read -rp "Нажмите Enter для продолжения...";;
                5) update_xray_core; read -rp "Нажмите Enter для продолжения...";;
                6) reset_node; read -rp "Нажмите Enter для продолжения...";;
                7) uninstall_node; read -rp "Нажмите Enter для продолжения...";;
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
                    relay|xray-relay|exit|xray-exit) install_xray_relay_node "${3:-10443}" "${4:-}" "${5:-www.google.com}" ;;
                    amnezia) install_amnezia_node ;;
                    *) error "Неизвестный тип установки: $2. Доступно: origin, relay, amnezia" ;;
                esac
                ;;
            relay)
                case "${2:-}" in
                    add) add_relay_node "${3:-}" "${4:-}" "${5:-10443}" "${6:-}" "${7:-de}" "${8:-reality}" "${9:-}" "${10:-}" "${11:-www.google.com}" ;;
                    remove|del) remove_relay_node "${3:-}" ;;
                    rename) rename_relay_node "${3:-}" "${4:-}" ;;
                    list) list_relays ;;
                    *) manage_relays_menu ;;
                esac
                ;;
            status) show_status ;;
            doctor|test) run_doctor ;;
            update)
                case "${2:-}" in
                    core|xray) update_xray_core ;;
                    config|heal)
                        role="$(get_state_val "role")"
                        if [[ "$role" == "origin" ]]; then
                            heal_and_update_origin_config
                        elif [[ "$role" == "relay" ]]; then
                            heal_and_update_relay_config
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
