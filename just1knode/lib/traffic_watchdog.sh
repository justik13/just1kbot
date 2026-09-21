#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль опционального контроля лимита трафика (lib/traffic_watchdog.sh)
# =============================================================================

TRAFFIC_STATE_FILE="${STATE_DIR:-/etc/just1knode}/traffic_monthly.json"

get_current_raw_tx_bytes() {
    python3 -c "
import os, sys

tx_total = 0
proc_net = '/proc/net/dev'
if os.path.exists(proc_net):
    try:
        with open(proc_net, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if ':' not in line:
                    continue
                name, stats = line.split(':', 1)
                name = name.strip()
                # Исключаем локальную петлю и виртуальные интерфейсы
                if name == 'lo' or name.startswith(('docker', 'veth', 'br-')):
                    continue
                cols = stats.split()
                if len(cols) >= 9:
                    tx_total += int(cols[8])
    except Exception:
        pass

print(tx_total)
"
}

get_current_billing_cycle() {
    local reset_day="${1:-1}"
    python3 -c "
import datetime, sys
now = datetime.datetime.now(datetime.timezone.utc)
try:
    r_day = int(sys.argv[1])
except Exception:
    r_day = 1
r_day = max(1, min(28, r_day))

if now.day >= r_day:
    cycle_start = datetime.date(now.year, now.month, r_day)
else:
    # Предыдущий месяц
    first_this_month = datetime.date(now.year, now.month, 1)
    last_prev_month = first_this_month - datetime.timedelta(days=1)
    cycle_start = datetime.date(last_prev_month.year, last_prev_month.month, min(r_day, last_prev_month.day))

print(cycle_start.strftime('%Y-%m-%d'))
" "$reset_day"
}

update_accumulated_tx() {
    local reset_day="${1:-1}"
    local raw_tx
    raw_tx="$(get_current_raw_tx_bytes)"
    local current_cycle
    current_cycle="$(get_current_billing_cycle "$reset_day")"

    python3 -c "
import json, os, sys, tempfile

state_file = sys.argv[1]
raw_tx = int(sys.argv[2])
current_cycle = sys.argv[3]

data = {
    'cycle': current_cycle,
    'raw_tx_last': raw_tx,
    'accumulated_tx': 0,
    'warn_sent': False
}

if os.path.exists(state_file):
    try:
        with open(state_file, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
    except Exception:
        pass

saved_cycle = data.get('cycle', '')
accumulated = int(data.get('accumulated_tx', 0))
raw_tx_last = int(data.get('raw_tx_last', 0))
warn_sent = bool(data.get('warn_sent', False))

if saved_cycle != current_cycle:
    # Наступил новый биллинговый месяц у хостера
    accumulated = 0
    raw_tx_last = raw_tx
    warn_sent = False
else:
    if raw_tx >= raw_tx_last:
        delta = raw_tx - raw_tx_last
    else:
        # Сервер перезагрузился, счетчик ядра сбросился
        delta = raw_tx
    accumulated += delta
    raw_tx_last = raw_tx

data['cycle'] = current_cycle
data['accumulated_tx'] = accumulated
data['raw_tx_last'] = raw_tx_last
data['warn_sent'] = warn_sent

d = os.path.dirname(os.path.abspath(state_file))
os.makedirs(d, exist_ok=True)
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8', errors='replace') as fp:
    json.dump(data, fp, indent=2)
    fp.flush()
os.replace(t_path, state_file)
try:
    import shutil
    shutil.chown(state_file, user='root', group='xrayapi')
    os.chmod(state_file, 0o660)
except Exception:
    pass

print(accumulated)
" "$TRAFFIC_STATE_FILE" "$raw_tx" "$current_cycle"
}

send_traffic_telegram_alert() {
    local message="$1"
    local bot_token
    bot_token="$(get_state_val "traffic_telegram_token" "")"
    local chat_id
    chat_id="$(get_state_val "traffic_telegram_chat_id" "")"

    if [[ -n "$bot_token" && -n "$chat_id" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
            -d "chat_id=${chat_id}" \
            -d "text=${message}" \
            -d "parse_mode=HTML" >/dev/null 2>&1 || true
    fi
}

check_traffic_limit() {
    init_state_dir
    local status
    status="$(get_state_val "traffic_limit_status" "disabled")"
    if [[ "$status" != "enabled" ]]; then
        return 0
    fi

    local limit_gb
    limit_gb="$(get_state_val "traffic_limit_gb" "0")"
    if [[ "$limit_gb" -le 0 ]]; then
        return 0
    fi

    local reset_day
    reset_day="$(get_state_val "traffic_reset_day" "1")"

    local accumulated_bytes
    accumulated_bytes="$(update_accumulated_tx "$reset_day")"

    local limit_bytes=$(( limit_gb * 1024 * 1024 * 1024 ))
    local warn_threshold_pct
    warn_threshold_pct="$(get_state_val "traffic_warn_pct" "90")"
    local warn_bytes=$(( limit_bytes * warn_threshold_pct / 100 ))

    local acc_gb
    acc_gb="$(python3 -c "print(f'{float($accumulated_bytes)/(1024**3):.2f}')")"
    local lim_gb
    lim_gb="$(python3 -c "print(f'{float($limit_bytes)/(1024**3):.2f}')")"

    # 1. Проверка на превышение лимита 100%
    if (( accumulated_bytes >= limit_bytes )); then
        echo -e "\n${BOLD}${RED}🚨 КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: Лимит трафика исчерпан! (${acc_gb} ГБ / ${lim_gb} ГБ)${NC}" >&2
        warn "Остановка службы Xray во избежание платного овердрафта у хостинг-провайдера..."
        systemctl stop xray 2>/dev/null || true
        set_state_val "traffic_cutoff_triggered" "true"

        send_traffic_telegram_alert "🚨 <b>ВНИМАНИЕ! Лимит трафика исчерпан!</b>%0A%0AСервер: <code>$(hostname)</code>%0AИспользовано: <b>${acc_gb} ГБ</b> из <b>${lim_gb} ГБ</b>.%0A%0AСлужба Xray остановлена для защиты от платного перерасхода."
        return 1
    fi

    # 2. Проверка порога предупреждения (90%)
    if (( accumulated_bytes >= warn_bytes )); then
        local warn_sent
        warn_sent="$(python3 -c "
import json, os
if os.path.exists('$TRAFFIC_STATE_FILE'):
    try:
        with open('$TRAFFIC_STATE_FILE', 'r') as f:
            d = json.load(f)
        print('true' if d.get('warn_sent') else 'false')
    except:
        print('false')
else:
    print('false')
")"
        if [[ "$warn_sent" != "true" ]]; then
            warn "Потребление трафика превысило ${warn_threshold_pct}%: ${acc_gb} ГБ из ${lim_gb} ГБ."
            send_traffic_telegram_alert "⚠️ <b>Предупреждение по лимиту трафика (${warn_threshold_pct}%):</b>%0A%0AСервер: <code>$(hostname)</code>%0AИспользовано: <b>${acc_gb} ГБ</b> из <b>${lim_gb} ГБ</b>."
            python3 -c "
import json, os
if os.path.exists('$TRAFFIC_STATE_FILE'):
    try:
        with open('$TRAFFIC_STATE_FILE', 'r') as f:
            d = json.load(f)
        d['warn_sent'] = True
        with open('$TRAFFIC_STATE_FILE', 'w') as f:
            json.dump(d, f, indent=2)
    except: pass
" 2>/dev/null || true
        fi
    fi

    return 0
}

deploy_traffic_watchdog_timer() {
    local systemd_dir="${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"
    mkdir -p "$systemd_dir"

    cat > "${systemd_dir}/just1knode-traffic.service" <<EOF
[Unit]
Description=Just1kNode Traffic Limit Watchdog Service
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/just1knode limit check
StandardOutput=journal
StandardError=journal
EOF

    cat > "${systemd_dir}/just1knode-traffic.timer" <<EOF
[Unit]
Description=Just1kNode Traffic Limit Watchdog Timer (every 15 min)

[Timer]
OnBootSec=3min
OnUnitActiveSec=15min
Unit=just1knode-traffic.service

[Install]
WantedBy=timers.target
EOF

    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload 2>/dev/null || true
        systemctl enable --now just1knode-traffic.timer 2>/dev/null || true
    fi
}

remove_traffic_watchdog_timer() {
    local systemd_dir="${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop just1knode-traffic.timer 2>/dev/null || true
        systemctl disable just1knode-traffic.timer 2>/dev/null || true
    fi
    rm -f "${systemd_dir}/just1knode-traffic.service" "${systemd_dir}/just1knode-traffic.timer" 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload 2>/dev/null || true
    fi
}

set_traffic_limit() {
    local limit_gb="${1:-}"
    local reset_day="${2:-1}"
    local tg_token="${3:-}"
    local tg_chat="${4:-}"

    if [[ -z "$limit_gb" || ! "$limit_gb" =~ ^[1-9][0-9]*$ ]]; then
        error "Укажите корректный лимит трафика в ГБ (целое положительное число, например: 8000)."
    fi

    if [[ ! "$reset_day" =~ ^[1-9][0-9]*$ ]] || (( reset_day < 1 || reset_day > 28 )); then
        warn "Некорректный день сброса: '$reset_day'. Установлено значение по умолчанию: 1-е число."
        reset_day=1
    fi

    init_state_dir
    set_state_val "traffic_limit_status" "enabled"
    set_state_val "traffic_limit_gb" "$limit_gb"
    set_state_val "traffic_reset_day" "$reset_day"
    set_state_val "traffic_warn_pct" "90"
    set_state_val "traffic_cutoff_triggered" "false"

    if [[ -n "$tg_token" ]]; then
        set_state_val "traffic_telegram_token" "$tg_token"
    fi
    if [[ -n "$tg_chat" ]]; then
        set_state_val "traffic_telegram_chat_id" "$tg_chat"
    fi

    # Инициализация первого замера
    update_accumulated_tx "$reset_day" >/dev/null
    deploy_traffic_watchdog_timer

    local tb_fmt
    tb_fmt="$(python3 -c "print(f'{float($limit_gb)/1024:.2f}')")"
    log "Лимит трафика успешно активирован: ${BOLD}${limit_gb} ГБ (${tb_fmt} ТБ)${NC} (сброс: ${reset_day}-го числа каждого месяца)."
    log "Фоновый таймер (just1knode-traffic.timer) запущен с интервалом 15 минут."
}

disable_traffic_limit() {
    init_state_dir
    set_state_val "traffic_limit_status" "disabled"
    set_state_val "traffic_cutoff_triggered" "false"
    remove_traffic_watchdog_timer
    log "Контроль лимита трафика успешно отключен. Сервер переведен в безлимитный режим."
}

show_traffic_limit_status() {
    init_state_dir
    local status
    status="$(get_state_val "traffic_limit_status" "disabled")"
    local limit_gb
    limit_gb="$(get_state_val "traffic_limit_gb" "0")"
    local reset_day
    reset_day="$(get_state_val "traffic_reset_day" "1")"
    local cutoff
    cutoff="$(get_state_val "traffic_cutoff_triggered" "false")"

    title "СТАТУС ЛИМИТА ТРАФИКА"

    if [[ "$status" != "enabled" || "$limit_gb" -le 0 ]]; then
        echo -e "  Статус контроля:   ${BOLD}${YELLOW}⚪ ОТКЛЮЧЕН (Безлимитный режим)${NC}"
        echo -e "  Чтобы включить:    ${CYAN}just1knode limit set <ГБ> [день_сброса]${NC}"
        return
    fi

    local current_cycle
    current_cycle="$(get_current_billing_cycle "$reset_day")"
    local acc_bytes
    acc_bytes="$(update_accumulated_tx "$reset_day")"

    local limit_bytes=$(( limit_gb * 1024 * 1024 * 1024 ))
    local pct
    pct="$(python3 -c "print(f'{(float($acc_bytes)/float($limit_bytes))*100:.1f}')")"
    local acc_gb
    acc_gb="$(python3 -c "print(f'{float($acc_bytes)/(1024**3):.2f}')")"
    local acc_tb
    acc_tb="$(python3 -c "print(f'{float($acc_bytes)/(1024**4):.2f}')")"
    local lim_tb
    lim_tb="$(python3 -c "print(f'{float($limit_bytes)/(1024**4):.2f}')")"

    local status_color="$GREEN"
    if (( $(python3 -c "print(1 if float('$pct') >= 90.0 else 0)") )); then
        status_color="$RED"
    elif (( $(python3 -c "print(1 if float('$pct') >= 75.0 else 0)") )); then
        status_color="$YELLOW"
    fi

    echo -e "  Статус контроля:      ${BOLD}${GREEN}🟢 ВКЛЮЧЕН${NC}"
    echo -e "  Установленный лимит:  ${BOLD}${CYAN}${limit_gb} ГБ (${lim_tb} ТБ)${NC}"
    echo -e "  Биллинговый цикл:     ${BOLD}с ${current_cycle}${NC} (сброс: ${reset_day}-е число)"
    echo -e "  Израсходовано:        ${BOLD}${status_color}${acc_gb} ГБ (${acc_tb} ТБ, ${pct}%)${NC}"

    if [[ "$cutoff" == "true" ]]; then
        echo -e "  Защитный выключатель: ${BOLD}${RED}🚨 АКТИВИРОВАН (Служба Xray остановлена)${NC}"
    else
        echo -e "  Защитное действие:    ${BOLD}Остановка Xray при 100% лимита${NC}"
    fi

    local timer_active="нет"
    if systemctl is-active --quiet just1knode-traffic.timer 2>/dev/null; then
        timer_active="активен (15 мин)"
    fi
    echo -e "  Фоновый таймер:       ${timer_active}"
}
