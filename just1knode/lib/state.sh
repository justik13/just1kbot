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
    chmod 750 "$STATE_DIR"

    if [[ ! -f "$STATE_FILE" ]]; then
        echo "{}" > "$STATE_FILE"
        chmod 640 "$STATE_FILE"
    fi
    if [[ ! -f "$CLIENTS_FILE" ]]; then
        echo "{}" > "$CLIENTS_FILE"
        chmod 660 "$CLIENTS_FILE"
    fi
    if [[ ! -f "$RELAYS_FILE" ]]; then
        echo "[]" > "$RELAYS_FILE"
        chmod 640 "$RELAYS_FILE"
    fi
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

f, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
lock_file = f + '.lock'
lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
if fcntl:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
try:
    data = {}
    if os.path.exists(f):
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
        except Exception:
            data = {}
    data[k] = v
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(f), suffix='.tmp')
    with os.fdopen(tmp_fd, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, indent=2)
        fp.flush()
    os.replace(tmp_path, f)
    try:
        import shutil
        shutil.chown(f, user='root', group='xrayapi')
        os.chmod(f, 0o640)
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
f, k, d = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(f, 'r', encoding='utf-8') as fp:
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
        *)
            echo "unconfigured"
            ;;
    esac
}

# Транзакционный манифест
manifest_begin() {
    local extra_targets=("$@")
    local txn_id="txn_$$"
    TXN_DIR="/tmp/just1knode_${txn_id}"
    rm -rf "$TXN_DIR"
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
        done < <(find "${NGINX_RELAYS_DIR:-/etc/nginx/just1k_relays.d}" -type f -name "*.conf" -print0 2>/dev/null)
    fi

    for extra in "${extra_targets[@]}"; do
        if [[ -n "$extra" ]]; then
            targets+=("$extra")
        fi
    done

    # Дедупликация и регистрация
    local seen_targets=()
    for target in "${targets[@]}"; do
        if [[ -z "$target" ]]; then
            continue
        fi
        local already_seen=0
        for s in "${seen_targets[@]}"; do
            if [[ "$s" == "$target" ]]; then
                already_seen=1
                break
            fi
        done
        if [[ $already_seen -eq 1 ]]; then
            continue
        fi
        seen_targets+=("$target")

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
                cp "$backup_path" "$target"
                log "Восстановлен исходный файл: $target"
            fi
        elif [[ "$status" == "absent" ]]; then
            rm -f "$target" 2>/dev/null || true
            log "Удален файл, созданный во время транзакции: $target"
        fi
    done < "$MANIFEST_LOG"

    rm -rf "${TXN_DIR:-}"
    MANIFEST_LOG=""

    # Восстановление рабочего состояния сервисов
    if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
        systemctl reload nginx 2>/dev/null || true
    fi
    if [[ -n "${XRAY_CONFIG:-}" && -f "${XRAY_CONFIG:-}" && -n "${XRAY_BIN:-}" && -x "${XRAY_BIN:-}" ]]; then
        if "$XRAY_BIN" run -test -config "$XRAY_CONFIG" >/dev/null 2>&1; then
            systemctl restart xray 2>/dev/null || true
        fi
    fi

    log "Откат транзакции завершен."
}
