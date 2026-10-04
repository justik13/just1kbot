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
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.core.somaxconn = 65535
net.ipv4.ip_local_port_range = 32768 65535
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
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
        modprobe tcp_bbr 2>/dev/null || true
        modprobe sch_fq 2>/dev/null || true
        sysctl -p "$conf_path" >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv6.conf.lo.disable_ipv6=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.icmp_echo_ignore_all=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.tcp_tw_reuse=1 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.tcp_fin_timeout=15 >/dev/null 2>&1 || true
        sysctl -w net.core.somaxconn=65535 >/dev/null 2>&1 || true
        sysctl -w net.ipv4.ip_local_port_range="32768 65535" >/dev/null 2>&1 || true
        sysctl -w net.core.default_qdisc=fq >/dev/null 2>&1 || true
        sysctl -w net.ipv4.tcp_congestion_control=bbr >/dev/null 2>&1 || true
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
    local bbr_curr
    bbr_curr="$(cat /proc/sys/net/ipv4/tcp_congestion_control 2>/dev/null || echo "")"
    if [[ -n "$bbr_curr" && "$bbr_curr" != "bbr" ]]; then
        warn "Контроль перегрузки BBR не активирован в ядре (текущий: $bbr_curr). Проверьте поддержку BBR хостинг-провайдером."
    fi
}

# Определение всех активных портов демона SSH (Zero-Lockout стандарт)
# Игнорирует сокеты, слушающие исключительно loopback (127.0.0.1 или ::1)
detect_active_sshd_ports() {
    local ports=()
    local p
    if command -v ss >/dev/null 2>&1; then
        while read -r line; do
            # Строка вида: 0.0.0.0:22 или [::]:22
            local laddr lport
            laddr="$(echo "$line" | awk '{print $4}')"
            lport="$(echo "$laddr" | rev | cut -d: -f1 | rev)"
            if [[ -n "$lport" && "$lport" =~ ^[0-9]+$ ]]; then
                # Пропускаем loopback-only сокеты (127.x.x.x или [::1])
                if [[ "$laddr" =~ ^127\. || "$laddr" =~ ^\[::1\] || "$laddr" =~ ^::1 ]]; then
                    continue
                fi
                ports+=("$lport")
            fi
        done < <(ss -tlnp 2>/dev/null | grep -E 'users:.*"sshd"')
    fi

    if [[ ${#ports[@]} -eq 0 ]] && command -v sshd >/dev/null 2>&1; then
        p="$(sshd -T 2>/dev/null | grep -i "^port " | awk '{print $2}' | head -n 1 || true)"
        [[ -n "$p" && "$p" =~ ^[0-9]+$ ]] && ports+=("$p")
    fi

    if [[ ${#ports[@]} -eq 0 ]]; then
        p="$(grep -E -h "^Port " /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | awk '{print $2}' | head -n 1 || true)"
        [[ -n "$p" && "$p" =~ ^[0-9]+$ ]] && ports+=("$p")
    fi

    [[ ${#ports[@]} -eq 0 ]] && ports+=("22")

    printf "%s\n" "${ports[@]}" | sort -u
}

# Проверка: слушает ли демон SSH на указанном порту
is_ssh_port() {
    local check_p="$1"
    [[ -z "$check_p" ]] && return 1
    while read -r active_sp; do
        [[ "$active_sp" == "$check_p" ]] && return 0
    done < <(detect_active_sshd_ports 2>/dev/null || true)
    return 1
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

    # Гарантия базовой политики фаервола: запрет входящих по умолчанию
    if ! ufw status verbose 2>/dev/null | grep -qi "Default: deny (incoming)"; then
        ufw default deny incoming >/dev/null 2>&1 || true
    fi
    ufw default allow outgoing >/dev/null 2>&1 || true

    # 2. Гарантия защиты SSH (Zero-Lockout стандарт: подтверждаем все активные публичные сокеты SSH)
    # Важно: не расширяем уже существующие restricted-правила (с привязкой к IP) до Anywhere!
    local ssh_live_ports=()
    while read -r sp; do
        [[ -n "$sp" ]] && ssh_live_ports+=("$sp")
    done < <(detect_active_sshd_ports)

    for sp in "${ssh_live_ports[@]}"; do
        # Если порт SSH уже разрешён в UFW (хоть с конкретного IP, хоть отовсюду) — НЕ создаём дублирующее Anywhere-правило!
        if ! ufw status 2>/dev/null | grep -E "(^|[[:space:]])${sp}(/tcp)?[[:space:]]+ALLOW" -q; then
            if ! ufw allow "$sp/tcp" comment "just1knode ssh access" >/dev/null 2>&1; then
                error "КРИТИЧЕСКАЯ ОШИБКА: Не удалось открыть SSH-порт $sp/tcp в UFW! Зачистка фаервола отменена во избежание потери доступа."
                return 1
            fi
            log "Фаервол UFW: гарантирован доступ к порту SSH ($sp/tcp)."
        fi
    done

    # 3. AmneziaWG API (порты для ролей awg, dual, либо при наличии активного конфига amnezia)
    local is_awg_node=0
    if [[ "$role" == "awg" || "$role" == "dual" || -f "/etc/nginx/sites-enabled/just1k-amnezia.conf" ]]; then
        is_awg_node=1
    fi

    if [[ $is_awg_node -eq 1 ]]; then
        local awg_port
        awg_port="$(get_state_val "awg_port" "8443")"
        [[ -z "$awg_port" || "$awg_port" == "-" ]] && awg_port="8443"

        # Zero-Lockout: никогда не удаляем порт, если на нем слушает SSH
        if ! is_ssh_port "$awg_port"; then
            # Устраняем уязвимость: удаляем публичный доступ к порту Amnezia API
            if ufw status 2>/dev/null | grep -E "${awg_port}(/tcp)?[[:space:]]+ALLOW[[:space:]]+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                ufw delete allow "${awg_port}/tcp" >/dev/null 2>&1 || true
                ufw delete allow "${awg_port}" >/dev/null 2>&1 || true
                warn "Фаервол UFW: устранена уязвимость — удалено публичное правило для порта API AmneziaWG (${awg_port})."
            fi
        fi

        # Проверка доверенного BOT_IP из state.json (Fail-Closed, без эвристического угадывания)
        local bot_ip
        bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"
        if [[ -n "$bot_ip" && "$bot_ip" != "any" && "$bot_ip" != "0.0.0.0/0" && "$bot_ip" != "-" ]] && validate_ipv4 "$bot_ip"; then
            if ! ufw status 2>/dev/null | grep -F "$bot_ip" | grep -q "$awg_port"; then
                if ufw allow from "$bot_ip" to any port "$awg_port" proto tcp comment "just1knode amnezia api" >/dev/null 2>&1; then
                    log "Фаервол UFW: подтвержден доступ к порту ${awg_port} строго для BOT_IP (${bot_ip})"
                fi
            fi
        else
            warn "BOT_IP не задан в state.json. Порт Amnezia API (${awg_port}) закрыт от мира (Fail-Closed)."
        fi
    fi

    # 4. Origin API (роль origin)
    if [[ "$role" == "origin" ]]; then
        local origin_bot_ip
        origin_bot_ip="$(get_state_val "bot_ip" 2>/dev/null || true)"

        # Zero-Lockout: не удаляем 8444 или 8443, если на них слушает SSH
        if ! is_ssh_port "8444"; then
            if ufw status 2>/dev/null | grep -E "8444(/tcp)?[[:space:]]+ALLOW[[:space:]]+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                ufw delete allow 8444/tcp >/dev/null 2>&1 || true
                ufw delete allow 8444 >/dev/null 2>&1 || true
                warn "Фаервол UFW: устранена уязвимость — удалено глобальное правило на порт 8444."
            fi
        fi
        if ! is_ssh_port "8443"; then
            if ufw status 2>/dev/null | grep -E "8443(/tcp)?[[:space:]]+ALLOW" -q; then
                ufw delete allow 8443/tcp >/dev/null 2>&1 || true
                ufw delete allow 8443 >/dev/null 2>&1 || true
                warn "Фаервол UFW: устранена уязвимость — удалено устаревшее правило на порт 8443."
            fi
        fi

        # Проверка доверенного BOT_IP из state.json (Fail-Closed)
        if [[ -n "$origin_bot_ip" && "$origin_bot_ip" != "any" && "$origin_bot_ip" != "-" ]] && validate_ipv4 "$origin_bot_ip"; then
            if ! ufw status 2>/dev/null | grep -F "$origin_bot_ip" | grep -q "8444"; then
                if ufw allow from "$origin_bot_ip" to any port 8444 proto tcp comment "just1knode origin api" >/dev/null 2>&1; then
                    log "Фаервол UFW: подтвержден доступ к порту 8444 для BOT_IP (${origin_bot_ip})"
                fi
            fi
        else
            warn "BOT_IP не задан в state.json. Порт Origin API (8444) закрыт от мира (Fail-Closed)."
        fi
    fi

    # 5. Relay туннель (роль relay или dual)
    if [[ "$role" == "relay" || "$role" == "dual" ]]; then
        local relay_port origin_ip
        relay_port="$(get_state_val "relay_port" 2>/dev/null || true)"
        origin_ip="$(get_state_val "origin_ip" 2>/dev/null || true)"

        if [[ -n "$relay_port" && "$relay_port" =~ ^[0-9]+$ ]]; then
            # Zero-Lockout: не удаляем правило, если на нем слушает SSH
            if ! is_ssh_port "$relay_port"; then
                if ufw status 2>/dev/null | grep -E "${relay_port}(/tcp)?[[:space:]]+ALLOW[[:space:]]+(Anywhere|0\.0\.0\.0/0|::/0)" -q; then
                    ufw delete allow "${relay_port}/tcp" >/dev/null 2>&1 || true
                    ufw delete allow "${relay_port}" >/dev/null 2>&1 || true
                    warn "Фаервол UFW: устранена уязвимость — удалено публичное правило на порт Relay туннеля (${relay_port})."
                fi
            fi

            if [[ -n "$origin_ip" ]] && validate_ipv4 "$origin_ip"; then
                if ! ufw status 2>/dev/null | grep -F "$origin_ip" | grep -q "$relay_port"; then
                    if ufw allow from "$origin_ip" to any port "$relay_port" proto tcp comment "just1knode relay tunnel" >/dev/null 2>&1; then
                        log "Фаервол UFW: подтвержден доступ к Relay порту ${relay_port} строго для ORIGIN_IP (${origin_ip})"
                    fi
                fi
            fi
        fi
    fi
}

sync_xhttp_upstreams_conf() {
    local conf_d="${NGINX_CONF_DIR:-/etc/nginx}/conf.d"
    local upstreams_file="${conf_d}/just1k-xhttp-upstreams.conf"
    local cfg_file="${XRAY_CONFIG:-/usr/local/etc/xray/config.json}"
    local relays_file="${RELAYS_FILE:-${STATE_DIR:-/etc/just1knode}/relays.json}"

    mkdir -p "$conf_d" 2>/dev/null || true
    manifest_track_file "$upstreams_file" 2>/dev/null || true
    create_backup "$upstreams_file" 2>/dev/null || true

    if ! python3 -c "
import json, os, tempfile, sys

upstreams_file = sys.argv[1]
cfg_file = sys.argv[2]
relays_file = sys.argv[3]

xray_inbound_ports = {}
if os.path.exists(cfg_file):
    try:
        with open(cfg_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            for ib in cfg.get('inbounds', []):
                t = ib.get('tag')
                p = ib.get('port')
                if t and p:
                    xray_inbound_ports[t] = int(p)
    except Exception:
        pass

relays = []
if os.path.exists(relays_file):
    try:
        with open(relays_file, 'r', encoding='utf-8') as f:
            relays = json.load(f)
            if not isinstance(relays, list):
                relays = []
    except Exception:
        relays = []

def_port = xray_inbound_ports.get('just1k-wl-default', 8003)
lines = [
    '# =============================================================================',
    '# Persistent Keepalive Upstream Pools for Xray XHTTP (Zero TIME_WAIT)',
    '# =============================================================================',
    'upstream xray_xhttp_default {',
    f'    server 127.0.0.1:{def_port};',
    '    keepalive 128;',
    '    keepalive_requests 100000;',
    '    keepalive_timeout 300s;',
    '}',
    ''
]

seen_codes = set()
for r in relays:
    if not isinstance(r, dict):
        continue
    code = r.get('code')
    if not code:
        continue
    code_lower = str(code).strip().lower()
    if not code_lower or code_lower in seen_codes:
        continue
    seen_codes.add(code_lower)
    in_tag = r.get('inbound_tag') or f'just1k-wl-inbound-{code_lower}'
    port = xray_inbound_ports.get(in_tag)
    if not port:
        for t, p in xray_inbound_ports.items():
            if t.lower() == f'just1k-wl-inbound-{code_lower}':
                port = p
                break
    # Источник истины — секция inbounds в config.json (Desired-State).
    # Релеи, отсутствующие в Xray, не получают upstream (защита от устаревших записей в relays.json).
    if not port and not xray_inbound_ports:
        port = r.get('inbound_port')
    if port:
        try:
            port_num = int(port)
            lines.append(f'upstream xray_xhttp_relay_{code_lower} {{')
            lines.append(f'    server 127.0.0.1:{port_num};')
            lines.append('    keepalive 128;')
            lines.append('    keepalive_requests 100000;')
            lines.append('    keepalive_timeout 300s;')
            lines.append('}')
            lines.append('')
        except Exception:
            continue

# Защита в глубину: если релеи есть в Xray inbounds, но relays.json временно пуст
for t, p in xray_inbound_ports.items():
    if t.startswith('just1k-wl-inbound-'):
        c_tag = t[len('just1k-wl-inbound-'):].strip().lower()
        if c_tag and c_tag not in seen_codes:
            try:
                port_num = int(p)
                seen_codes.add(c_tag)
                lines.append(f'upstream xray_xhttp_relay_{c_tag} {{')
                lines.append(f'    server 127.0.0.1:{port_num};')
                lines.append('    keepalive 128;')
                lines.append('    keepalive_requests 100000;')
                lines.append('    keepalive_timeout 300s;')
                lines.append('}')
                lines.append('')
            except Exception:
                continue

content = '\\n'.join(lines) + '\\n'
d = os.path.dirname(os.path.abspath(upstreams_file))
os.makedirs(d, exist_ok=True)
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8') as f:
    f.write(content)
    f.flush()
    os.fsync(f.fileno())
os.replace(t_path, upstreams_file)
try:
    os.chmod(upstreams_file, 0o644)
except Exception:
    pass
" "$upstreams_file" "$cfg_file" "$relays_file"; then
        warn "Не удалось сгенерировать Nginx upstreams для XHTTP ($upstreams_file)"
        return 1
    fi
    return 0
}



