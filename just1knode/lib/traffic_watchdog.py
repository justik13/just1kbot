import datetime
import json
import os
import sys
import tempfile
try:
    import fcntl
except ImportError:
    fcntl = None

def main():
    if len(sys.argv) < 6:
        sys.stderr.write("Usage: traffic_watchdog.py <state_file> <limit_gb> <reset_day> <cutoff_active> <warn_pct>\n")
        sys.exit(1)

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
        proc_read_ok = False
        if os.path.exists(proc_net):
            try:
                with open(proc_net, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        if ':' not in line:
                            continue
                        name, stats = line.split(':', 1)
                        name = name.strip()
                        if name == 'lo' or name.startswith(('docker', 'veth', 'br-', 'wg', 'awg', 'tun', 'tap')):
                            continue
                        cols = stats.split()
                        if len(cols) >= 9:
                            tx_raw += int(cols[8])
                proc_read_ok = True
            except Exception:
                pass

        if not proc_read_ok:
            sys.stderr.write('WARNING: Failed to read /proc/net/dev. Falling back to state file.\n')

        # 2. Определение текущего биллингового периода
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.day >= reset_day:
            cycle_start = datetime.date(now.year, now.month, reset_day)
        else:
            first_this_month = datetime.date(now.year, now.month, 1)
            last_prev_month = first_this_month - datetime.timedelta(days=1)
            cycle_start = datetime.date(last_prev_month.year, last_prev_month.month, min(reset_day, last_prev_month.day))
        current_cycle = cycle_start.strftime('%Y-%m-%d')

        # 3. Чтение сохраненного состояния (Fail-Closed)
        data = {}
        state_corrupted = False
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8', errors='replace') as f:
                    c = json.load(f)
                    if isinstance(c, dict):
                        data = c
                    else:
                        state_corrupted = True
            except Exception:
                state_corrupted = True

        if state_corrupted:
            sys.stderr.write(f'CRITICAL: Failed to load corrupted traffic state file: {state_file}\n')
            lim_gb_str = f'{float(limit_gb):.2f}'
            if cutoff_active:
                print(f'ensure_stopped|0.00|{lim_gb_str}|100.0')
                sys.exit(0)
            else:
                print(f'none|0.00|{lim_gb_str}|0.0')
                sys.exit(0)

        saved_cycle = data.get('cycle', '')
        saved_reset_day = int(data.get('reset_day', 0))
        accumulated = int(data.get('accumulated_tx', 0))
        raw_tx_last = int(data.get('raw_tx_last', 0))
        warn_sent = bool(data.get('warn_sent', False))
        cutoff_sent = bool(data.get('cutoff_sent', False))

        if proc_read_ok:
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
            
            cycle_to_save = current_cycle
        else:
            # Не смогли прочитать /proc/net/dev. Не вычисляем delta и не обновляем raw_tx_last.
            if saved_cycle != current_cycle and saved_cycle:
                if saved_reset_day != 0 and saved_reset_day != reset_day:
                    # Смена дня сброса: без tx_raw оставляем накопленный трафик как есть.
                    pass
                else:
                    # Новый месяц: сброс накопленного счетчика, чтобы разблокировать доступ.
                    accumulated = 0
                    warn_sent = False
                    cutoff_sent = False
                cycle_to_save = current_cycle
            else:
                # Тот же цикл или первый запуск (saved_cycle == '').
                # При первом запуске не сохраняем текущий цикл, чтобы корректно инициализировать raw_tx_last при успешном чтении.
                cycle_to_save = saved_cycle

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
            'cycle': cycle_to_save,
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

if __name__ == '__main__':
    main()
