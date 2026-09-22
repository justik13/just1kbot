#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль опционального контроля лимита трафика (lib/traffic_watchdog.sh)
# =============================================================================

TRAFFIC_STATE_FILE="${STATE_DIR:-/etc/just1knode}/traffic_monthly.json"
TRAFFIC_CUTOFF_FLAG="${STATE_DIR:-/etc/just1knode}/traffic_cutoff.active"

send_traffic_telegram_alert() {
    local message="$1"
    local bot_token
    bot_token="$(get_state_val "traffic_telegram_token" "")"
    local chat_id
    chat_id="$(get_state_val "traffic_telegram_chat_id" "")"

    if [[ -n "$bot_token" && -n "$chat_id" ]]; then
        local http_code
        http_code="$(curl -s -o /dev/null -w "%{http_code}" -X POST "https://api.telegram.org/bot${bot_token}/sendMessage" \
            --max-time 10 \
            -d "chat_id=${chat_id}" \
            -d "parse_mode=HTML" \
            --data-urlencode "text=${message}" 2>/dev/null || echo "000")"
        if [[ "$http_code" == "200" ]]; then
            return 0
        else
            warn "Не удалось отправить Telegram-уведомление (HTTP ${http_code})"
            return 1
        fi
    fi
    return 0
}

mark_traffic_alert_flag() {
    local flag_key="$1"
    python3 -c "
import json, os, sys, tempfile
try:
    import fcntl
except ImportError:
    fcntl = None

sf = sys.argv[1]
k = sys.argv[2]
if not os.path.exists(sf):
    sys.exit(0)

lock_file = sf + '.lock'
d = os.path.dirname(os.path.abspath(sf))
os.makedirs(d, exist_ok=True)
lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o660)
if fcntl:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    with open(sf, 'r', encoding='utf-8', errors='replace') as fp:
        data = json.load(fp)
    if isinstance(data, dict):
        data[k] = True
        t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
        with os.fdopen(t_fd, 'w', encoding='utf-8', errors='replace') as fp:
            json.dump(data, fp, indent=2)
            fp.flush()
        os.replace(t_path, sf)
        try:
            import shutil
            shutil.chown(sf, user='root', group='xrayapi')
            os.chmod(sf, 0o640)
        except Exception:
            pass
finally:
    if fcntl:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
" "$TRAFFIC_STATE_FILE" "$flag_key"
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

    local warn_pct
    warn_pct="$(get_state_val "traffic_warn_pct" "90")"

    local cutoff_active
    cutoff_active="$(get_state_val "traffic_cutoff_triggered" "false")"

    # Выполняем подсчет трафика, проверку цикла и определение действия за один атомарный запуск Python с блокировкой
    local result
    result="$(python3 -c "
import datetime, json, os, sys, tempfile
try:
    import fcntl
except ImportError:
    fcntl = None

state_file = sys.argv[1]
limit_gb = int(sys.argv[2])
reset_day = max(1, min(28, int(sys.argv[3])))
cutoff_active = (sys.argv[4].lower() == 'true')
try:
    warn_pct_cfg = float(sys.argv[5])
except Exception:
    warn_pct_cfg = 90.0

lock_file = state_file + '.lock'
d = os.path.dirname(os.path.abspath(state_file))
os.makedirs(d, exist_ok=True)
lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o660)
if fcntl:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)

try:
    # 1. Чтение сырых TX-байт ядра (исключая loopback и виртуальные интерфейсы)
    tx_raw = 0
    proc_net = '/proc/net/dev'
    if os.path.exists(proc_net):
        try:
            with open(proc_net, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    if ':' not in line:
                        continue
                    name, stats = line.split(':', 1)
                    name = name.strip()
                    if name == 'lo' or name.startswith(('docker', 'veth', 'br-', 'wg', 'tun', 'tap')):
                        continue
                    cols = stats.split()
                    if len(cols) >= 9:
                        tx_raw += int(cols[8])
        except Exception:
            pass

    # 2. Определение текущего биллингового периода
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.day >= reset_day:
        cycle_start = datetime.date(now.year, now.month, reset_day)
    else:
        first_this_month = datetime.date(now.year, now.month, 1)
        last_prev_month = first_this_month - datetime.timedelta(days=1)
        cycle_start = datetime.date(last_prev_month.year, last_prev_month.month, min(reset_day, last_prev_month.day))
    current_cycle = cycle_start.strftime('%Y-%m-%d')

    # 3. Чтение сохраненного состояния
    data = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8', errors='replace') as f:
                c = json.load(f)
                if isinstance(c, dict):
                    data = c
        except Exception:
            pass

    saved_cycle = data.get('cycle', '')
    saved_reset_day = int(data.get('reset_day', 0))
    accumulated = int(data.get('accumulated_tx', 0))
    raw_tx_last = int(data.get('raw_tx_last', 0))
    warn_sent = bool(data.get('warn_sent', False))
    cutoff_sent = bool(data.get('cutoff_sent', False))

    if saved_cycle != current_cycle:
        # Проверяем: это смена настроек reset_day администратором или новый календарный период хостера?
        if saved_reset_day != 0 and saved_reset_day != reset_day and saved_cycle:
            # Администратор перенастроил день сброса: сохраняем накопленный трафик периода
            if tx_raw >= raw_tx_last:
                delta = tx_raw - raw_tx_last
            else:
                delta = tx_raw
            accumulated += delta
            raw_tx_last = tx_raw
        else:
            # Новый расчетный месяц у хостера: сброс накопленного счетчика
            accumulated = 0
            raw_tx_last = tx_raw
            warn_sent = False
            cutoff_sent = False
    else:
        if tx_raw >= raw_tx_last:
            delta = tx_raw - raw_tx_last
        else:
            # Сервер перезагружался: счетчик ядра сбросился
            delta = tx_raw
        accumulated += delta
        raw_tx_last = tx_raw

    limit_bytes = limit_gb * (1024 ** 3)
    acc_gb_fmt = f'{float(accumulated) / (1024 ** 3):.2f}'
    lim_gb_fmt = f'{float(limit_bytes) / (1024 ** 3):.2f}'
    pct = (float(accumulated) / float(limit_bytes) * 100) if limit_bytes > 0 else 0.0

    action = 'none'
    if accumulated < limit_bytes:
        if cutoff_active or cutoff_sent:
            # Лимит восстановлен (новый расчетный период или увеличение лимита владельцем) -> перезапуск Xray
            action = 'resume'
            cutoff_sent = False
            if pct < warn_pct_cfg:
                warn_sent = False
        elif pct >= warn_pct_cfg and not warn_sent:
            action = 'warn'
        elif pct < warn_pct_cfg:
            warn_sent = False
    elif accumulated >= limit_bytes:
        # 100% лимита исчерпано -> отключение Xray (дедуплицировано)
        if not cutoff_sent:
            action = 'cutoff'
        else:
            action = 'ensure_stopped'

    # Атомарная запись через tempfile + os.replace (POSIX atomic rename)
    data = {
        'cycle': current_cycle,
        'reset_day': reset_day,
        'raw_tx_last': raw_tx_last,
        'accumulated_tx': accumulated,
        'warn_sent': warn_sent,
        'cutoff_sent': cutoff_sent,
    }
    t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
    with os.fdopen(t_fd, 'w', encoding='utf-8', errors='replace') as fp:
        json.dump(data, fp, indent=2)
        fp.flush()
    os.replace(t_path, state_file)

    try:
        import shutil
        shutil.chown(state_file, user='root', group='xrayapi')
        os.chmod(state_file, 0o640)
    except Exception:
        pass

    print(f'{action}|{acc_gb_fmt}|{lim_gb_fmt}|{pct:.1f}')
finally:
    if fcntl:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
" "$TRAFFIC_STATE_FILE" "$limit_gb" "$reset_day" "$cutoff_active" "$warn_pct")"

    IFS='|' read -r action acc_gb lim_gb pct <<< "$result"

    case "$action" in
        resume)
            rm -f "$TRAFFIC_CUTOFF_FLAG" 2>/dev/null || true
            set_state_val "traffic_cutoff_triggered" "false"
            warn "Лимит трафика восстановлен / начат новый биллинговый период. Запуск службы Xray..."
            systemctl start xray 2>/dev/null || true
            local resume_msg="✅ <b>Лимит трафика сброшен / обновлен</b>

Сервер: <code>$(hostname)</code>
Использовано: <b>${acc_gb} ГБ</b> из <b>${lim_gb} ГБ</b>.

Служба Xray автоматически запущена и принимает соединения."
            send_traffic_telegram_alert "$resume_msg" || true
            ;;
        cutoff)
            echo -e "\n${BOLD}${RED}🚨 КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ: Лимит трафика исчерпан! (${acc_gb} ГБ / ${lim_gb} ГБ)${NC}" >&2
            warn "Остановка службы Xray во избежание платного овердрафта у хостинг-провайдера..."
            touch "$TRAFFIC_CUTOFF_FLAG" 2>/dev/null || true
            systemctl stop xray 2>/dev/null || true
            set_state_val "traffic_cutoff_triggered" "true"

            local alert_msg="🚨 <b>ВНИМАНИЕ! Лимит трафика исчерпан!</b>

Сервер: <code>$(hostname)</code>
Использовано: <b>${acc_gb} ГБ</b> из <b>${lim_gb} ГБ</b>.

Служба Xray остановлена для защиты от платного перерасхода."
            if send_traffic_telegram_alert "$alert_msg"; then
                mark_traffic_alert_flag "cutoff_sent"
            fi
            ;;
        ensure_stopped)
            touch "$TRAFFIC_CUTOFF_FLAG" 2>/dev/null || true
            set_state_val "traffic_cutoff_triggered" "true"
            if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet xray 2>/dev/null; then
                warn "Лимит исчерпан, но Xray активен. Принудительная повторная остановка службы..."
                systemctl stop xray 2>/dev/null || true
            fi
            ;;
        warn)
            warn "Потребление трафика превысило порог (${pct}%): ${acc_gb} ГБ из ${lim_gb} ГБ."
            local warn_msg="⚠️ <b>Предупреждение по лимиту трафика (${pct}%):</b>

Сервер: <code>$(hostname)</code>
Использовано: <b>${acc_gb} ГБ</b> из <b>${lim_gb} ГБ</b>."
            if send_traffic_telegram_alert "$warn_msg"; then
                mark_traffic_alert_flag "warn_sent"
            fi
            ;;
        *)
            ;;
    esac

    return 0
}

deploy_traffic_watchdog_timer() {
    local systemd_dir="${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"
    mkdir -p "$systemd_dir"

    local bin_path="/usr/local/bin/just1knode"
    if [[ ! -x "$bin_path" ]]; then
        bin_path="${SCRIPT_DIR:-/opt/just1knode}/just1knode.sh"
    fi

    # Drop-in для xray.service, блокирующий запуск при активном cutoff (reboot / certbot / hooks)
    local xray_dropin_dir="${systemd_dir}/xray.service.d"
    mkdir -p "$xray_dropin_dir"
    cat > "${xray_dropin_dir}/traffic-cutoff.conf" <<EOF
[Unit]
ConditionPathExists=!${TRAFFIC_CUTOFF_FLAG}
EOF

    cat > "${systemd_dir}/just1knode-traffic.service" <<EOF
[Unit]
Description=Just1kNode Traffic Limit Watchdog Service
After=network.target

[Service]
Type=oneshot
ExecStart=${bin_path} limit check
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
        if ! systemctl is-active --quiet just1knode-traffic.timer 2>/dev/null; then
            warn "Не удалось подтвердить активность just1knode-traffic.timer через systemctl."
        fi
    fi
}

remove_traffic_watchdog_timer() {
    local systemd_dir="${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop just1knode-traffic.timer 2>/dev/null || true
        systemctl disable just1knode-traffic.timer 2>/dev/null || true
    fi
    rm -f "${systemd_dir}/just1knode-traffic.service" "${systemd_dir}/just1knode-traffic.timer" 2>/dev/null || true
    rm -f "${systemd_dir}/xray.service.d/traffic-cutoff.conf" 2>/dev/null || true
    rm -f "$TRAFFIC_CUTOFF_FLAG" 2>/dev/null || true
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
    deploy_traffic_watchdog_timer

    set_state_val "traffic_limit_status" "enabled"
    set_state_val "traffic_limit_gb" "$limit_gb"
    set_state_val "traffic_reset_day" "$reset_day"
    set_state_val "traffic_warn_pct" "90"

    if [[ -n "$tg_token" ]]; then
        set_state_val "traffic_telegram_token" "$tg_token"
    fi
    if [[ -n "$tg_chat" ]]; then
        set_state_val "traffic_telegram_chat_id" "$tg_chat"
    fi

    # Первичная фиксация точки отсчета
    check_traffic_limit >/dev/null 2>&1 || true

    local tb_fmt
    tb_fmt="$(python3 -c "print(f'{float($limit_gb)/1024:.2f}')")"
    log "Лимит трафика успешно активирован: ${BOLD}${limit_gb} ГБ (${tb_fmt} ТБ)${NC} (сброс: ${reset_day}-го числа каждого месяца)."
    log "Фоновый таймер (just1knode-traffic.timer) запущен с интервалом 15 минут."
}

disable_traffic_limit() {
    init_state_dir
    local cutoff_active
    cutoff_active="$(get_state_val "traffic_cutoff_triggered" "false")"
    rm -f "$TRAFFIC_CUTOFF_FLAG" 2>/dev/null || true
    set_state_val "traffic_limit_status" "disabled"
    set_state_val "traffic_cutoff_triggered" "false"
    remove_traffic_watchdog_timer
    if [[ "$cutoff_active" == "true" ]]; then
        systemctl start xray 2>/dev/null || true
        log "Служба Xray автоматически запущена после снятия лимита."
    fi
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
    local warn_pct
    warn_pct="$(get_state_val "traffic_warn_pct" "90")"
    local cutoff
    cutoff="$(get_state_val "traffic_cutoff_triggered" "false")"
    if [[ -f "$TRAFFIC_CUTOFF_FLAG" ]]; then
        cutoff="true"
    fi

    title "СТАТУС ЛИМИТА ТРАФИКА"

    if [[ "$status" != "enabled" || "$limit_gb" -le 0 ]]; then
        echo -e "  Статус контроля:   ${BOLD}${YELLOW}⚪ ОТКЛЮЧЕН (Безлимитный режим)${NC}"
        echo -e "  Чтобы включить:    ${CYAN}just1knode limit set <ГБ> [день_сброса] [token] [chat_id]${NC}"
        return
    fi

    local info
    info="$(python3 -c "
import datetime, json, os, sys

state_file = sys.argv[1]
limit_gb = int(sys.argv[2])
reset_day = max(1, min(28, int(sys.argv[3])))

now = datetime.datetime.now(datetime.timezone.utc)
if now.day >= reset_day:
    cycle_start = datetime.date(now.year, now.month, reset_day)
else:
    first_this_month = datetime.date(now.year, now.month, 1)
    last_prev_month = first_this_month - datetime.timedelta(days=1)
    cycle_start = datetime.date(last_prev_month.year, last_prev_month.month, min(reset_day, last_prev_month.day))
current_cycle = cycle_start.strftime('%Y-%m-%d')

accumulated = 0
if os.path.exists(state_file):
    try:
        with open(state_file, 'r', encoding='utf-8', errors='replace') as f:
            d = json.load(f)
            accumulated = int(d.get('accumulated_tx', 0))
    except Exception:
        pass

limit_bytes = limit_gb * (1024 ** 3)
acc_gb = f'{float(accumulated) / (1024 ** 3):.2f}'
acc_tb = f'{float(accumulated) / (1024 ** 4):.2f}'
lim_tb = f'{float(limit_bytes) / (1024 ** 4):.2f}'
pct = (float(accumulated) / float(limit_bytes) * 100) if limit_bytes > 0 else 0.0

print(f'{current_cycle}|{acc_gb}|{acc_tb}|{lim_tb}|{pct:.1f}')
" "$TRAFFIC_STATE_FILE" "$limit_gb" "$reset_day")"

    IFS='|' read -r current_cycle acc_gb acc_tb lim_tb pct <<< "$info"

    local status_color="$GREEN"
    if (( $(python3 -c "print(1 if float('$pct') >= float('$warn_pct') else 0)") )); then
        status_color="$RED"
    elif (( $(python3 -c "print(1 if float('$pct') >= (float('$warn_pct') * 0.8) else 0)") )); then
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
