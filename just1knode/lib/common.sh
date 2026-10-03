#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека базовых утилит и функций окружения (lib/common.sh)
# =============================================================================

# Цвета терминала
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Принудительная UTF-8 локаль и режим UTF-8 в Python (PEP 540)
export LC_ALL="${LC_ALL:-C.UTF-8}"
export LANG="${LANG:-C.UTF-8}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8


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

JUST1KNODE_LOCK_FD=200
acquire_just1knode_lock() {
    local lock_dir="/run/lock/just1knode"
    mkdir -p "$lock_dir" 2>/dev/null || lock_dir="/tmp/just1knode_locks"
    mkdir -p "$lock_dir" 2>/dev/null || true
    local lock_file="${lock_dir}/just1knode.lock"
    eval "exec ${JUST1KNODE_LOCK_FD}>\"${lock_file}\""
    if command -v flock >/dev/null 2>&1; then
        if ! flock -n "$JUST1KNODE_LOCK_FD"; then
            warn "Другой процесс just1knode уже выполняется. Ожидание снятия блокировки..."
            flock "$JUST1KNODE_LOCK_FD"
        fi
    fi
}

release_just1knode_lock() {
    if command -v flock >/dev/null 2>&1; then
        flock -u "$JUST1KNODE_LOCK_FD" 2>/dev/null || true
    fi
    eval "exec ${JUST1KNODE_LOCK_FD}>&-" 2>/dev/null || true
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

install_base_deps() {
    log "Проверка и установка системных пакетов..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl wget unzip jq python3 python3-pip python3-venv ufw openssl ca-certificates
}

install_nginx_if_missing() {
    if ! command -v nginx >/dev/null 2>&1; then
        log "Установка Nginx и Certbot..."
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq nginx certbot python3-certbot-nginx ca-certificates
        # Отключаем дефолтный сайт сразу после установки пакета для предотвращения конфликта портов
        local def_site="${NGINX_CONF_DIR:-/etc/nginx}/sites-enabled/default"
        if [[ -f "$def_site" || -L "$def_site" ]]; then
            cp -L "$def_site" "${NGINX_CONF_DIR:-/etc/nginx}/sites-available/default.user.bak" 2>/dev/null || true
            rm -f "$def_site" 2>/dev/null || true
        fi
        systemctl enable nginx 2>/dev/null || true
        systemctl restart nginx 2>/dev/null || true
    fi
}

# Обнаружение Docker-контейнера, слушающего хостовый TCP-порт 80
detect_host_port80_container() {
    command -v docker >/dev/null 2>&1 || return 0
    local matched
    matched="$(docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -E '(^|[[:space:],])([0-9\.:]+|\[::\]|:::):80->[0-9]+/tcp' | head -n 1 || true)"
    if [[ -n "$matched" ]]; then
        echo "$matched" | awk -F'\t' '{print $1}'
    fi
}

# Безопасная настройка UFW с детекцией SSH
configure_safe_ufw() {
    local ports=("$@")
    if ! command -v ufw >/dev/null 2>&1; then
        apt-get install -y -qq ufw
    fi

    # Детектируем порт SSH (Zero-Lockout гарантия: живые сокеты ядра ss -> sshd -T -> sshd_config)
    local ssh_port=22
    local detected
    if command -v ss >/dev/null 2>&1; then
        detected="$(ss -tlnp 2>/dev/null | grep -E 'users:.*"sshd"' | awk '{print $4}' | rev | cut -d: -f1 | rev | grep -E '^[0-9]+$' | head -n 1 || true)"
    fi
    if [[ -z "$detected" ]] && command -v sshd >/dev/null 2>&1; then
        detected="$(sshd -T 2>/dev/null | grep -i "^port " | awk '{print $2}' | head -n 1 || true)"
    fi
    if [[ -z "$detected" ]]; then
        detected="$(grep -E -h "^Port " /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | awk '{print $2}' | head -n 1 || true)"
    fi
    if [[ -n "$detected" ]]; then
        ssh_port="$detected"
    fi

    if ! ufw allow "$ssh_port/tcp" >/dev/null 2>&1; then
        error "КРИТИЧЕСКАЯ ОШИБКА: Не удалось открыть SSH-порт $ssh_port/tcp в UFW! Активация фаервола отменена во избежание потери доступа."
        return 1
    fi

    # Дополнительно гарантируем открытие всех портов, на которых слушает sshd
    if command -v ss >/dev/null 2>&1; then
        while read -r extra_p; do
            if [[ -n "$extra_p" && "$extra_p" =~ ^[0-9]+$ && "$extra_p" != "$ssh_port" ]]; then
                ufw allow "$extra_p/tcp" >/dev/null 2>&1 || true
            fi
        done < <(ss -tlnp 2>/dev/null | grep -E 'users:.*"sshd"' | awk '{print $4}' | rev | cut -d: -f1 | rev | sort -u)
    fi

    for p in "${ports[@]}"; do
        ufw allow "$p" >/dev/null 2>&1 || true
    done

    # Автодетекция веб-портов 80 и 443 (защита Caddy бота и вебхуков эквайринга при ко-локации)
    local has_port80=0
    local has_port443=0
    if [[ -n "$(detect_host_port80_container 2>/dev/null || true)" ]] || (command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "just1kbot_caddy"); then
        has_port80=1
    fi
    if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Ports}} {{.Names}}' 2>/dev/null | grep -qE '(:443->|just1kbot_caddy)'; then
        has_port443=1
    fi

    if [[ $has_port80 -eq 1 ]]; then
        if ! ufw status 2>/dev/null | grep -qE "(^|[[:space:]])80/tcp[[:space:]]+ALLOW"; then
            if ufw allow 80/tcp comment "http web service" >/dev/null 2>&1; then
                log "Фаервол UFW: автоматически разрешен порт 80/tcp для активного веб-сервиса (Caddy)."
            else
                warn "Предупреждение: Не удалось добавить правило UFW для порта 80/tcp."
            fi
        fi
    fi
    if [[ $has_port443 -eq 1 ]]; then
        if ! ufw status 2>/dev/null | grep -qE "(^|[[:space:]])443/tcp[[:space:]]+ALLOW"; then
            if ufw allow 443/tcp comment "https web service" >/dev/null 2>&1; then
                log "Фаервол UFW: автоматически разрешен порт 443/tcp для активного веб-сервиса (Caddy)."
            else
                warn "Предупреждение: Не удалось добавить правило UFW для порта 443/tcp."
            fi
        fi
    fi

    # Включаем UFW, если он отключен
    if ! ufw status | grep -q "Status: active"; then
        if echo "y" | ufw enable >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
            log "Фаервол UFW успешно активирован (SSH порт ${ssh_port} защищен от блокировки)."
        else
            warn "Внимание: не удалось активировать фаервол UFW."
        fi
    fi
}

# Обнаружение активных пользовательских сайтов в Nginx (для предотвращения случайного даунтайма)
detect_existing_nginx_sites() {
    local base_dir="${1:-/etc/nginx}"
    local sites_found=()
    local conf_dirs=("$base_dir/sites-enabled" "$base_dir/conf.d")

    for cdir in "${conf_dirs[@]}"; do
        [[ ! -d "$cdir" ]] && continue
        while IFS= read -r -d '' f; do
            local fname
            fname="$(basename "$f")"
            [[ "$fname" =~ ^(just1k|sub-wl|xhttp).* ]] && continue
            [[ "$fname" =~ .*\.(bak|old|tmp|disabled)$ ]] && continue

            if [[ "$fname" == "default" ]]; then
                if grep -Eq '(^|[[:space:]])server_name[[:space:]]+[^_;]' "$f" 2>/dev/null; then
                    local sname
                    sname="$(grep -E '(^|[[:space:]])server_name[[:space:]]+' "$f" 2>/dev/null | head -n1 | sed -E 's/.*server_name[[:space:]]+//; s/;.*//')"
                    sites_found+=("$fname ($sname)")
                fi
                continue
            fi

            if grep -Eq '(server_name|listen|proxy_pass)[[:space:]]+' "$f" 2>/dev/null; then
                local sname
                sname="$(grep -E '(^|[[:space:]])server_name[[:space:]]+' "$f" 2>/dev/null | head -n1 | sed -E 's/.*server_name[[:space:]]+//; s/;.*//' || echo "")"
                if [[ -n "$sname" && "$sname" != "_" ]]; then
                    sites_found+=("$fname ($sname)")
                else
                    sites_found+=("$fname")
                fi
            fi
        done < <(find "$cdir" -maxdepth 1 \( -type f -o -type l \) -print0 2>/dev/null)
    done

    if [[ ${#sites_found[@]} -gt 0 ]]; then
        printf '%s\n' "${sites_found[@]}"
        return 0
    fi
    return 1
}

validate_ipv4() {
    local ip="${1:-}"
    [[ -z "$ip" ]] && return 1
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import ipaddress, sys; ip = sys.argv[1]; addr = ipaddress.IPv4Address(ip); sys.exit(0 if not addr.is_multicast and not addr.is_unspecified and not addr.is_reserved else 1)" "$ip" 2>/dev/null
    else
        [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
        for oct in "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"; do
            (( oct < 0 || oct > 255 )) && return 1
            [[ ${#oct} -gt 1 && "$oct" =~ ^0 ]] && return 1
        done
        [[ "$ip" == "0.0.0.0" || "$ip" == "255.255.255.255" ]] && return 1
        return 0
    fi
}

validate_ip() {
    local ip="${1:-}"
    [[ -z "$ip" ]] && return 1
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import ipaddress, sys; ip = sys.argv[1]; addr = ipaddress.ip_address(ip); sys.exit(0 if not addr.is_multicast and not addr.is_unspecified and not addr.is_reserved else 1)" "$ip" 2>/dev/null
    else
        validate_ipv4 "$ip"
    fi
}

ensure_xray_api_healthy() {
    # Функция вызывается на узлах, где установлен агент xray-api (Origin / Dual).
    if [[ ! -f /etc/systemd/system/xray-api.service && ! -f /lib/systemd/system/xray-api.service ]]; then
        return 0
    fi

    # На узлах Relay, AWG или Dual служба xray-api не используется и не должна запускаться
    local role
    role="$(get_state_val "role" "")"
    if [[ "$role" != "origin" ]]; then
        return 0
    fi

    # 1. Если служба уже активна (systemd автоматически перезапустил её через PartOf=xray.service),
    # повторный restart категорически не вызываем, чтобы не провоцировать start-limit-hit.
    if systemctl is-active --quiet xray-api 2>/dev/null; then
        return 0
    fi

    # 2. Если служба в процессе запуска (activating), даем до 2.5 секунд на завершение
    local attempts=5
    while [[ $attempts -gt 0 ]]; do
        if systemctl is-active --quiet xray-api 2>/dev/null; then
            return 0
        fi
        local state
        state="$(systemctl is-active xray-api 2>/dev/null || true)"
        if [[ "$state" != "activating" ]]; then
            break
        fi
        sleep 0.5
        attempts=$((attempts - 1))
    done

    # 3. Если служба в статусе failed (например start-limit-hit) — сбрасываем лимит ошибок
    if systemctl is-failed --quiet xray-api 2>/dev/null; then
        systemctl reset-failed xray-api 2>/dev/null || true
    fi

    # 4. Выполняем контролируемый старт службы
    systemctl start xray-api 2>/dev/null || true
    sleep 0.5

    # 5. Проверяем финальный статус: функция возвращает 0 только при реальной активности службы
    if systemctl is-active --quiet xray-api 2>/dev/null; then
        return 0
    fi
    return 1
}

apply_node_sysctl_hardening() {
    local conf_path="${JUST1KNODE_SYSCTL_IPV6_CONF:-/etc/sysctl.d/99-disable-ipv6.conf}"
    mkdir -p "$(dirname "$conf_path")" 2>/dev/null || true
    cat > "$conf_path" <<EOF 2>/dev/null || true
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1
net.ipv4.icmp_echo_ignore_all = 1
EOF
    chmod 644 "$conf_path" 2>/dev/null || true

    local ufw_conf="${JUST1KNODE_UFW_SYSCTL_CONF:-/etc/ufw/sysctl.conf}"
    if [[ -f "$ufw_conf" ]]; then
        if grep -Eq '^[#[:space:]]*net/ipv4/icmp_echo_ignore_all[[:space:]]*=' "$ufw_conf" 2>/dev/null; then
            sed -i -E '/^[#[:space:]]*net\/ipv4\/icmp_echo_ignore_all[[:space:]]*=/d' "$ufw_conf" 2>/dev/null || true
        fi
        echo "net/ipv4/icmp_echo_ignore_all=1" >> "$ufw_conf" 2>/dev/null || true
    fi

    if command -v sysctl >/dev/null 2>&1; then
        sysctl -p "$conf_path" >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.lo.disable_ipv6=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.icmp_echo_ignore_all=1 >/dev/null 2>&1 || true
    fi
    local ipv6_curr
    ipv6_curr="$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null || echo "0")"
    if [[ "$ipv6_curr" != "1" ]]; then
        warn "Параметр net.ipv6.conf.all.disable_ipv6 не применился в ядре ноды (проверьте права или ограничения контейнера)."
    fi
    local icmp_curr
    icmp_curr="$(cat /proc/sys/net/ipv4/icmp_echo_ignore_all 2>/dev/null || echo "0")"
    if [[ "$icmp_curr" != "1" ]]; then
        warn "Параметр net.ipv4.icmp_echo_ignore_all не применился в ядре ноды (проверьте права или ограничения контейнера)."
    fi
}

# Автоматическое приведение периметра UFW и системных настроек к эталонному закрытому состоянию (Stealth)
heal_node_firewall_and_stealth() {
    # 1. Применяем системное скрытие (IPv6 leak protection + ICMP stealth)
    apply_node_sysctl_hardening

    if ! command -v ufw >/dev/null 2>&1 || ! ufw status 2>/dev/null | grep -qi "Status: active"; then
        return 0
    fi

    local role
    role="$(get_state_val "role" "")"

    # 2. Гарантия защиты SSH (Zero-Lockout стандарт: подтверждаем все активные сокеты SSH, правила SSH никогда не удаляются)
    local ssh_port=22
    local detected_ssh
    if command -v ss >/dev/null 2>&1; then
        detected_ssh="$(ss -tlnp 2>/dev/null | grep -E 'users:.*"sshd"' | awk '{print $4}' | rev | cut -d: -f1 | rev | grep -E '^[0-9]+$' | head -n 1 || true)"
    fi
    if [[ -z "$detected_ssh" ]] && command -v sshd >/dev/null 2>&1; then
        detected_ssh="$(sshd -T 2>/dev/null | grep -i "^port " | awk '{print $2}' | head -n 1 || true)"
    fi
    if [[ -z "$detected_ssh" ]]; then
        detected_ssh="$(grep -E -h "^Port " /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | awk '{print $2}' | head -n 1 || true)"
    fi
    [[ -n "$detected_ssh" ]] && ssh_port="$detected_ssh"
    ufw allow "$ssh_port/tcp" >/dev/null 2>&1 || true

    if command -v ss >/dev/null 2>&1; then
        while read -r extra_ssh; do
            if [[ -n "$extra_ssh" && "$extra_ssh" =~ ^[0-9]+$ && "$extra_ssh" != "$ssh_port" ]]; then
                ufw allow "$extra_ssh/tcp" >/dev/null 2>&1 || true
            fi
        done < <(ss -tlnp 2>/dev/null | grep -E 'users:.*"sshd"' | awk '{print $4}' | rev | cut -d: -f1 | rev | sort -u)
    fi

    # 5. AmneziaWG API (порты для ролей awg, dual, либо при наличии конфига amnezia)
    local is_awg_node=0
    if [[ "$role" == "awg" || "$role" == "dual" || -f "/etc/nginx/sites-available/just1k-amnezia.conf" ]]; then
        is_awg_node=1
    fi

    if [[ $is_awg_node -eq 1 ]]; then
        local awg_port
        awg_port="$(get_state_val "awg_port" "8443")"
        [[ -z "$awg_port" || "$awg_port" == "-" ]] && awg_port="8443"

        # Устраняем уязвимость: удаляем публичный доступ к порту Amnezia API
        if ufw status 2>/dev/null | grep -E "${awg_port}(/tcp)?[[:space:]]+ALLOW[[:space:]]+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
            ufw delete allow "${awg_port}/tcp" >/dev/null 2>&1 || true
            ufw delete allow "${awg_port}" >/dev/null 2>&1 || true
            warn "Фаервол UFW: устранена уязвимость — удалено публичное правило для порта API AmneziaWG (${awg_port})."
        fi

        # Динамический поиск IP Telegram-бота
        local bot_ip
        bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"
        if [[ -z "$bot_ip" || "$bot_ip" == "any" || "$bot_ip" == "0.0.0.0/0" || "$bot_ip" == "-" ]] || ! validate_ipv4 "$bot_ip"; then
            local detected_ip=""
            if command -v ss >/dev/null 2>&1; then
                detected_ip="$(ss -tn "( sport = :${awg_port} )" 2>/dev/null | awk 'NR>1 {print $4}' | rev | cut -d: -f2- | rev | tr -d '[]' | grep -vE '^(127\.|0\.|::)' | head -n1 || true)"
            fi
            if [[ -z "$detected_ip" ]] && [[ -f "/var/log/nginx/access.log" ]]; then
                detected_ip="$(awk '$9 ~ /^2/ {print $1}' /var/log/nginx/access.log 2>/dev/null | tail -n 20 | sort | uniq -c | sort -nr | awk '{print $2}' | head -n1 || true)"
            fi
            if [[ -n "$detected_ip" ]] && validate_ipv4 "$detected_ip"; then
                bot_ip="$detected_ip"
                set_state_val "bot_ip" "$bot_ip"
                log "Фаервол UFW: динамически обнаружен IP бота (${bot_ip}) из активных сетевых соединений."
            fi
        fi

        # Если валидный IP бота известен — гарантируем наличие точечного правила в UFW
        if [[ -n "$bot_ip" && "$bot_ip" != "any" && "$bot_ip" != "0.0.0.0/0" && "$bot_ip" != "-" ]] && validate_ipv4 "$bot_ip"; then
            if ! ufw status 2>/dev/null | grep -F "$bot_ip" | grep -q "$awg_port"; then
                if ufw allow from "$bot_ip" to any port "$awg_port" proto tcp comment "just1knode amnezia api" >/dev/null 2>&1; then
                    log "Фаервол UFW: подтвержден доступ к порту ${awg_port} строго для BOT_IP (${bot_ip})"
                fi
            fi
        fi
    fi

    # 6. Origin API (роль origin)
    if [[ "$role" == "origin" ]]; then
        local origin_bot_ip
        origin_bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"

        # Удаление глобальных уязвимых правил (ALLOW Anywhere на 8444)
        if ufw status 2>/dev/null | grep -E "8444(/tcp)?[[:space:]]+ALLOW[[:space:]]+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
            ufw delete allow 8444/tcp >/dev/null 2>&1 || true
            ufw delete allow 8444 >/dev/null 2>&1 || true
            warn "Фаервол UFW: устранена уязвимость — удалено глобальное правило на порт 8444."
        fi
        # Удаление устаревших правил на порт 8443
        if ufw status 2>/dev/null | grep -E "8443(/tcp)?[[:space:]]+ALLOW" -q; then
            ufw delete allow 8443/tcp >/dev/null 2>&1 || true
            ufw delete allow 8443 >/dev/null 2>&1 || true
            warn "Фаервол UFW: устранена уязвимость — удалено устаревшее правило на порт 8443."
        fi

        # Если bot_ip не задан, пробуем динамический детект
        if [[ -z "$origin_bot_ip" || "$origin_bot_ip" == "any" || "$origin_bot_ip" == "-" ]] || ! validate_ipv4 "$origin_bot_ip"; then
            local detected_orig_ip=""
            if command -v ss >/dev/null 2>&1; then
                detected_orig_ip="$(ss -tn "( sport = :8444 )" 2>/dev/null | awk 'NR>1 {print $4}' | rev | cut -d: -f2- | rev | tr -d '[]' | grep -vE '^(127\.|0\.|::)' | head -n1 || true)"
            fi
            if [[ -n "$detected_orig_ip" ]] && validate_ipv4 "$detected_orig_ip"; then
                origin_bot_ip="$detected_orig_ip"
                set_state_val "bot_ip" "$origin_bot_ip"
                log "Фаервол UFW: динамически обнаружен IP бота (${origin_bot_ip}) из активных сетевых соединений."
            fi
        fi

        if [[ -n "$origin_bot_ip" && "$origin_bot_ip" != "any" && "$origin_bot_ip" != "-" ]] && validate_ipv4 "$origin_bot_ip"; then
            if ! ufw status 2>/dev/null | grep -F "$origin_bot_ip" | grep -q "8444"; then
                if ufw allow from "$origin_bot_ip" to any port 8444 proto tcp >/dev/null 2>&1; then
                    log "Фаервол UFW: подтвержден доступ к порту 8444 для BOT_IP (${origin_bot_ip})"
                fi
            fi
        fi
    fi
}



