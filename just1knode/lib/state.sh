#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека управления состоянием узла (lib/state.sh)
# =============================================================================

STATE_DIR="${STATE_DIR:-/etc/just1knode}"
STATE_FILE="${STATE_FILE:-${STATE_DIR}/state.json}"
CLIENTS_FILE="${CLIENTS_FILE:-${STATE_DIR}/clients.json}"
RELAYS_FILE="${RELAYS_FILE:-${STATE_DIR}/relays.json}"

init_state_dir() {
    mkdir -p "$STATE_DIR"
    chown root:xrayapi "$STATE_DIR" 2>/dev/null || true
    chmod 2770 "$STATE_DIR" 2>/dev/null || true

    for f in "$STATE_FILE" "$CLIENTS_FILE" "$RELAYS_FILE"; do
        if [[ -L "$f" ]]; then
            rm -f "$f"
        fi
    done

    if [[ ! -f "$STATE_FILE" ]]; then
        echo "{}" > "$STATE_FILE"
    fi
    if [[ ! -f "$CLIENTS_FILE" ]]; then
        echo "{}" > "$CLIENTS_FILE"
    fi
    if [[ ! -f "$RELAYS_FILE" ]]; then
        echo "[]" > "$RELAYS_FILE"
    fi

    chown -h root:xrayapi "$STATE_FILE" "$CLIENTS_FILE" "$RELAYS_FILE" 2>/dev/null || true
    chmod 660 "$STATE_FILE" "$CLIENTS_FILE" "$RELAYS_FILE" 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name "*.lock" -exec chown -h root:xrayapi {} + 2>/dev/null || true
    find "$STATE_DIR" -maxdepth 1 -name "*.lock" -exec chmod 660 {} + 2>/dev/null || true
}

set_state_val() {
    local key="$1"
    local val="$2"
    init_state_dir
    python3 -c "
import sys, json, os, tempfile
try:
    import fcntl
except ImportError:
    fcntl = None

os.umask(0o007)

def safe_arg(val):
    if not isinstance(val, str):
        return val
    try:
        return val.encode(sys.getfilesystemencoding(), 'surrogateescape').decode('utf-8', 'replace')
    except Exception:
        return val

f, k, v = sys.argv[1], safe_arg(sys.argv[2]), safe_arg(sys.argv[3])
lock_file = f + '.lock'
open_flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
lock_fd = os.open(lock_file, open_flags, 0o660)
try:
    if hasattr(os, 'fchmod'):
        os.fchmod(lock_fd, 0o660)
    else:
        os.chmod(lock_file, 0o660)
    try:
        import grp
        gid = grp.getgrnam('xrayapi').gr_gid
        if hasattr(os, 'fchown'):
            os.fchown(lock_fd, 0, gid)
    except Exception:
        import shutil
        shutil.chown(lock_file, user='root', group='xrayapi')
except Exception:
    pass
if fcntl:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    data = {}
    if os.path.exists(f):
        try:
            if os.path.getsize(f) == 0:
                raise ValueError('State file ' + str(f) + ' is empty (0 bytes)')
            with open(f, 'r', encoding='utf-8', errors='replace') as fp:
                data = json.load(fp)
                if not isinstance(data, dict):
                    raise ValueError('State file ' + str(f) + ' root must be a JSON object')
        except Exception as e:
            bak = f + '.corrupted.bak'
            bak_saved = False
            try:
                if os.path.islink(bak) or os.path.lexists(bak):
                    os.unlink(bak)
                open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
                bak_fd = os.open(bak, open_flags, 0o600)
                try:
                    with open(f, 'rb') as src_fp, os.fdopen(bak_fd, 'wb') as dst_fp:
                        dst_fp.write(src_fp.read())
                    bak_saved = True
                except Exception:
                    pass
            except Exception:
                pass
            if bak_saved:
                print('ОШИБКА: Поврежден файл состояния ' + str(f) + ' (' + str(e) + '). Резервная копия сохранена в ' + str(bak) + '. Запись прервана во избежание потери данных.', file=sys.stderr)
            else:
                print('ОШИБКА: Поврежден файл состояния ' + str(f) + ' (' + str(e) + '). Не удалось сохранить резервную копию. Запись прервана во избежание потери данных.', file=sys.stderr)
            sys.exit(1)
    data[k] = v
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(f), suffix='.tmp')
    with os.fdopen(tmp_fd, 'w', encoding='utf-8', errors='replace') as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)
        fp.flush()
    os.replace(tmp_path, f)
    try:
        import shutil
        shutil.chown(f, user='root', group='xrayapi')
        os.chmod(f, 0o660)
    except Exception:
        pass
finally:
    if fcntl:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
" "$STATE_FILE" "$key" "$val"
}

get_state_val() {
    local key="$1"
    local default_val="${2:-}"
    if [[ ! -f "$STATE_FILE" ]] || ! command -v python3 >/dev/null 2>&1; then
        echo "$default_val"
        return
    fi
    python3 -c "
import sys, json

def safe_arg(val):
    if not isinstance(val, str):
        return val
    try:
        return val.encode(sys.getfilesystemencoding(), 'surrogateescape').decode('utf-8', 'replace')
    except Exception:
        return val

f, k, d = sys.argv[1], safe_arg(sys.argv[2]), safe_arg(sys.argv[3])
try:
    with open(f, 'r', encoding='utf-8', errors='replace') as fp:
        data = json.load(fp)
    print(data.get(k, d))
except Exception:
    print(d)
" "$STATE_FILE" "$key" "$default_val"
}

# Определение фактического статуса сервера
get_node_status() {
    if [[ ! -f "$STATE_FILE" ]]; then
        echo "unconfigured"
        return
    fi

    local role
    role="$(get_state_val "role" "unconfigured")"

    case "$role" in
        origin)
            echo "origin"
            ;;
        relay)
            echo "relay"
            ;;
        awg)
            echo "awg"
            ;;
        dual)
            echo "dual"
            ;;
        *)
            echo "unconfigured"
            ;;
    esac
}

# Транзакционный манифест
manifest_begin() {
    local extra_targets=("$@")
    TXN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/just1knode_txn_XXXXXX")"
    mkdir -p "$TXN_DIR/files"
    MANIFEST_LOG="$TXN_DIR/manifest.tsv"
    : > "$MANIFEST_LOG"

    local targets=(
        "$RELAYS_FILE"
        "${XRAY_CONFIG:-/usr/local/etc/xray/config.json}"
        "${XRAY_API_CONFIG_ENV:-/etc/xray-api/config.env}"
    )

    if [[ -d "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" ]]; then
        while IFS= read -r -d '' conf_file; do
            targets+=("$conf_file")
            echo "$conf_file" >> "$TXN_DIR/initial_nginx_relays.txt"
        done < <(find "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" -type f -name "*.conf" -print0 2>/dev/null)
    fi

    for extra in "${extra_targets[@]}"; do
        if [[ -n "$extra" ]]; then
            targets+=("$extra")
        fi
    done

    # Дедупликация и регистрация
    local seen_targets=()
    local tgt
    for tgt in "${targets[@]}"; do
        if [[ -z "$tgt" ]]; then
            continue
        fi
        local already_seen=0
        for s in "${seen_targets[@]}"; do
            if [[ "$s" == "$tgt" ]]; then
                already_seen=1
                break
            fi
        done
        if [[ $already_seen -eq 1 ]]; then
            continue
        fi
        seen_targets+=("$tgt")

        if [[ -f "$tgt" ]]; then
            local hash_orig
            hash_orig="$(sha256sum "$tgt" | awk '{print $1}')"
            local backup_path
            backup_path="$TXN_DIR/files/$(basename "$tgt")_$$_${RANDOM}"
            cp -p "$tgt" "$backup_path" 2>/dev/null || cp "$tgt" "$backup_path"
            echo -e "${tgt}\tpresent\t${hash_orig}\t${backup_path}" >> "$MANIFEST_LOG"
        else
            echo -e "${tgt}\tabsent\t-\t-" >> "$MANIFEST_LOG"
        fi
    done
}

manifest_track_file() {
    local target="$1"
    if [[ -z "${MANIFEST_LOG:-}" || ! -f "${MANIFEST_LOG:-}" ]]; then
        return
    fi
    if grep -q "^${target}\t" "$MANIFEST_LOG" 2>/dev/null; then
        return
    fi
    if [[ -f "$target" ]]; then
        local hash_orig
        hash_orig="$(sha256sum "$target" | awk '{print $1}')"
        local backup_path
        backup_path="$TXN_DIR/files/$(basename "$target")_$$_${RANDOM}"
        cp "$target" "$backup_path"
        echo -e "${target}\tpresent\t${hash_orig}\t${backup_path}" >> "$MANIFEST_LOG"
    else
        echo -e "${target}\tabsent\t-\t-" >> "$MANIFEST_LOG"
    fi
}

manifest_commit() {
    if [[ -n "${TXN_DIR:-}" && -d "${TXN_DIR:-}" ]]; then
        rm -rf "$TXN_DIR"
    fi
    MANIFEST_LOG=""
}

manifest_rollback() {
    warn "Инициирован откат изменений транзакции..."
    if [[ -z "${MANIFEST_LOG:-}" || ! -f "${MANIFEST_LOG:-}" ]]; then
        warn "Манифест транзакции не найден. Откат невозможен."
        return
    fi

    while IFS=$'\t' read -r target status _orig_hash backup_path; do
        if [[ "$status" == "present" ]]; then
            if [[ -f "$backup_path" ]]; then
                cp -p "$backup_path" "$target" 2>/dev/null || cp "$backup_path" "$target"
                log "Восстановлен исходный файл: $target"
            fi
        elif [[ "$status" == "absent" ]]; then
            rm -f "$target" 2>/dev/null || true
            log "Удален файл, созданный во время транзакции: $target"
        fi
    done < "$MANIFEST_LOG"

    # Удаление любых новых файлов в NGINX_RELAYS_DIR, созданных во время транзакции
    if [[ -d "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" ]]; then
        while IFS= read -r -d '' cur_conf; do
            if [[ ! -f "$TXN_DIR/initial_nginx_relays.txt" ]] || ! grep -Fxq "$cur_conf" "$TXN_DIR/initial_nginx_relays.txt" 2>/dev/null; then
                rm -f "$cur_conf" 2>/dev/null || true
                log "Удален новый Nginx конфиг, созданный во время транзакции: $cur_conf"
            fi
        done < <(find "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" -type f -name "*.conf" -print0 2>/dev/null)
    fi

    rm -rf "${TXN_DIR:-}"
    MANIFEST_LOG=""

    ensure_xray_config_permissions "${XRAY_CONFIG:-/usr/local/etc/xray/config.json}"

    # Восстановление рабочего состояния сервисов
    if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || true
    fi
    if [[ -n "${XRAY_CONFIG:-}" && -f "${XRAY_CONFIG:-}" && -n "${XRAY_BIN:-}" && -x "${XRAY_BIN:-}" ]]; then
        if [[ ! -f "${STATE_DIR:-/etc/just1knode}/traffic_cutoff.active" ]]; then
            if "$XRAY_BIN" run -test -config "$XRAY_CONFIG" >/dev/null 2>&1; then
                systemctl restart xray 2>/dev/null || true
            fi
        else
            systemctl stop xray 2>/dev/null || true
        fi
    fi

    log "Откат транзакции завершен."
}

ensure_xray_config_permissions() {
    local cfg="${1:-${XRAY_CONFIG:-/usr/local/etc/xray/config.json}}"
    local cfg_dir
    cfg_dir="$(dirname "$cfg")"
    chmod 755 "$cfg_dir" 2>/dev/null || true
    if [[ -f "$cfg" ]]; then
        chown root:xrayapi "$cfg" 2>/dev/null || true
        chmod 640 "$cfg" 2>/dev/null || true
    fi
}
