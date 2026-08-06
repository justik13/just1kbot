#!/usr/bin/env bash

set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true
umask 027

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

readonly APP_NAME="just1kbot"
readonly SERVICE_NAME="just1kbot.service"
readonly BOT_USER="just1kbot"
readonly BOT_GROUP="just1kbot"
readonly APP_ROOT="/opt/just1kbot"
readonly RELEASES_DIR="${APP_ROOT}/releases"
readonly CURRENT_LINK="${APP_ROOT}/current"
readonly CONFIG_DIR="/etc/just1kbot"
readonly ENV_FILE="${CONFIG_DIR}/just1kbot.env"
readonly LEGACY_ENV_FILE="${APP_ROOT}/.env"
readonly BACKUP_KEY_FILE="${CONFIG_DIR}/backup.agekey"
readonly BACKUP_RECIPIENT_FILE="${CONFIG_DIR}/backup.agepub"
readonly STATE_DIR="${APP_ROOT}/.state"
readonly RELEASE_SHA_FILE="${STATE_DIR}/release_sha"
readonly REPO_BRANCH_FILE="${STATE_DIR}/repo_branch"
readonly SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
readonly SELF_SYMLINK="/usr/local/bin/just1kbot"
readonly INSTALL_LOG="/var/log/just1kbot-install.log"
readonly BACKUP_ROOT="/var/backups/just1kbot"
readonly NGINX_CONF="/etc/nginx/conf.d/just1kbot.conf"
readonly ACME_WEBROOT="/var/www/letsencrypt"
readonly LOCK_FILE="/run/lock/just1kbot-installer.lock"

readonly DEFAULT_REPO_OWNER="justik13"
readonly DEFAULT_REPO_NAME="just1kbot"
readonly DEFAULT_REPO_BRANCH="bot"

REPO_OWNER="${REPO_OWNER:-$DEFAULT_REPO_OWNER}"
REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"
REPO_BRANCH="${REPO_BRANCH:-$(cat "$REPO_BRANCH_FILE" 2>/dev/null || true)}"
REPO_BRANCH="${REPO_BRANCH:-$DEFAULT_REPO_BRANCH}"
INSTALL_TLS="${INSTALL_TLS:-1}"
NON_INTERACTIVE="${NON_INTERACTIVE:-0}"
ALLOW_UPDATE_WITHOUT_BACKUP="${ALLOW_UPDATE_WITHOUT_BACKUP:-0}"
PYTHON_BIN="${PYTHON_BIN:-}"

readonly REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"
readonly RAW_BASE_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"
readonly COMMIT_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/commits/${REPO_BRANCH}"

TMP_DIRS=()
LOCK_HELD=0

supports_color() { [[ -t 2 && "${TERM:-}" != "dumb" ]]; }
color() {
    local code="$1"; shift
    if supports_color; then printf '\033[%sm%s\033[0m' "$code" "$*"; else printf '%s' "$*"; fi
}

red() { color '1;31' "$*"; }
green() { color '1;32' "$*"; }
yellow() { color '1;33' "$*"; }
cyan() { color '1;36' "$*"; }

sanitize_log() {
    sed -E \
        -e 's#([A-Za-z][A-Za-z0-9+.-]*://[^:/[:space:]]+:)[^@[:space:]]+@#\1***@#g' \
        -e 's#(BOT_TOKEN|YOOKASSA_SECRET_KEY|REDIS_PASSWORD|DB_ENCRYPTION_KEY)=[^[:space:]]+#\1=***REDACTED***#g' \
        -e 's#[0-9]{6,}:[A-Za-z0-9_-]{20,}#***TELEGRAM_TOKEN_REDACTED***#g'
}

log_to_file() {
    local level="$1"; shift
    mkdir -p "$(dirname "$INSTALL_LOG")" 2>/dev/null || true
    printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$*" \
        | sanitize_log >> "$INSTALL_LOG" 2>/dev/null || true
}

info() { printf '[*] %s\n' "$*" >&2; log_to_file INFO "$*"; }
ok() { printf '[+] %s\n' "$*" >&2; log_to_file OK "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; log_to_file WARN "$*"; }
error() { printf '[ERROR] %s\n' "$*" >&2; log_to_file ERROR "$*"; }
die() { error "$*"; exit 1; }
line() { printf '%s\n' '------------------------------------------------------------' >&2; }

register_tmp_dir() { TMP_DIRS+=("$1"); }
cleanup() {
    local path
    for path in "${TMP_DIRS[@]}"; do
        if [[ -n "$path" && -d "$path" ]]; then
            rm -rf -- "$path"
        fi
    done
    return 0
}
on_error() {
    local rc="$1" line_no="$2" command_text="$3"
    [[ "$rc" -eq 141 ]] && return 0
    error "Команда завершилась с кодом ${rc} на строке ${line_no}: ${command_text}"
    error "Подробности: ${INSTALL_LOG}"
}
on_signal() {
    warn "Операция прервана пользователем."
    exit 130
}
trap cleanup EXIT
trap 'on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
trap on_signal INT TERM HUP

require_root() {
    [[ "${EUID}" -eq 0 ]] || die "Запустите команду от root: sudo bash just1kbot.sh $*"
}

acquire_lock() {
    [[ "$LOCK_HELD" -eq 1 ]] && return 0
    mkdir -p "$(dirname "$LOCK_FILE")"
    if command -v flock >/dev/null 2>&1; then
        exec 9>"$LOCK_FILE"
        flock -n 9 || die "Уже выполняется другая операция установки/обновления."
    else
        mkdir "${LOCK_FILE}.d" 2>/dev/null || die "Уже выполняется другая операция установки/обновления."
        TMP_DIRS+=("${LOCK_FILE}.d")
    fi
    LOCK_HELD=1
}

is_interactive() {
    [[ "$NON_INTERACTIVE" != "1" && -c /dev/tty ]]
}

read_tty() {
    local prompt="$1" result_var="$2" secret="${3:-0}" value=""
    is_interactive || return 1
    if [[ "$secret" == "1" ]]; then
        printf '%s' "$prompt" >/dev/tty
        IFS= read -r -s value </dev/tty || return 1
        printf '\n' >/dev/tty
    else
        printf '%s' "$prompt" >/dev/tty
        IFS= read -r value </dev/tty || return 1
    fi
    printf -v "$result_var" '%s' "$value"
}

confirm() {
    local prompt="$1" default="${2:-n}" value=""
    if ! is_interactive; then
        [[ "$default" == "y" ]]
        return
    fi
    while true; do
        read_tty "$prompt [y/n]: " value || return 1
        case "${value,,}" in
            y|yes|д|да) return 0 ;;
            n|no|н|нет|'') [[ "$default" == "y" ]] && return 0 || return 1 ;;
            *) warn "Введите y или n." ;;
        esac
    done
}

make_temp_dir() {
    local dir
    dir="$(mktemp -d -t just1kbot.XXXXXX)"
    register_tmp_dir "$dir"
    printf '%s' "$dir"
}

select_python() {
    local candidate resolved
    if [[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]]; then
        "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 && return 0
    fi
    for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
        resolved="$(command -v "$candidate" 2>/dev/null || true)"
        [[ -n "$resolved" ]] || continue
        if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON_BIN="$resolved"
            return 0
        fi
    done
    return 1
}

check_python_version() {
    select_python || die "Требуется Python 3.10 или новее."
}

get_env_value() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || return 0
    "$PYTHON_BIN" - "$ENV_FILE" "$key" <<'PY'
import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() != key:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        try:
            value = ast.literal_eval(value)
        except Exception:
            value = value[1:-1]
    print(value, end="")
    break
PY
}

set_env_value() {
    local key="$1" value="$2"
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "Некорректное имя переменной: $key"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "Переменная $key не может содержать перевод строки."
    mkdir -p "$CONFIG_DIR"
    touch "$ENV_FILE"
    JUST1KBOT_ENV_VALUE="$value" "$PYTHON_BIN" - "$ENV_FILE" "$key" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
value = os.environ.pop("JUST1KBOT_ENV_VALUE")
encoded = '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out = []
replaced = False
for line in lines:
    stripped = line.lstrip()
    if not stripped.startswith("#") and "=" in line and line.split("=", 1)[0].strip() == key:
        if not replaced:
            out.append(f"{key}={encoded}")
            replaced = True
        continue
    out.append(line)
if not replaced:
    if out and out[-1] != "":
        out.append("")
    out.append(f"{key}={encoded}")
path.parent.mkdir(parents=True, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out).rstrip() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
    chown root:"$BOT_GROUP" "$ENV_FILE" 2>/dev/null || true
    chmod 640 "$ENV_FILE"
}

unset_env_value() {
    local key="$1"
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "Некорректное имя переменной: $key"
    [[ -f "$ENV_FILE" ]] || return 0
    "$PYTHON_BIN" - "$ENV_FILE" "$key" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
key = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
out = [
    line for line in lines
    if line.lstrip().startswith("#")
    or "=" not in line
    or line.split("=", 1)[0].strip() != key
]
fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out).rstrip() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
    chown root:"$BOT_GROUP" "$ENV_FILE" 2>/dev/null || true
    chmod 640 "$ENV_FILE"
}

is_placeholder_value() {
    local key="$1" value="$2" normalized="${2,,}"
    [[ -z "$value" || "$normalized" == *change_me* ]] && return 0
    case "$key" in
        ADMIN_IDS)
            [[ "$normalized" =~ ^\[[[:space:]]*(111111111|123456789)[[:space:]]*\]$ ]] && return 0
            ;;
        SUPPORT_USERNAME)
            [[ "$normalized" == "support" || "$normalized" == "my_support_bot" ]] && return 0
            ;;
        DATABASE_URL)
            [[ "$normalized" == *":just1kpass@"* || "$normalized" == *":testpass@"* ]] && return 0
            ;;
        REDIS_PASSWORD)
            [[ "$normalized" == "testpass" ]] && return 0
            ;;
        YOOKASSA_SHOP_ID)
            [[ "$normalized" == "123456" ]] && return 0
            ;;
        YOOKASSA_SECRET_KEY)
            [[ "$normalized" == "live_secret_key_123" ]] && return 0
            ;;
        DOMAIN)
            [[ "$normalized" == "vpn.mydomain.com" || "$normalized" == "example.com" || "$normalized" == *.example.com ]] && return 0
            ;;
        SSL_EMAIL)
            [[ "$normalized" == "admin@mydomain.com" || "$normalized" == *@example.com ]] && return 0
            ;;
    esac
    return 1
}

require_env_value() {
    local key="$1" prompt="$2" default="${3:-}" secret="${4:-0}"
    local current input
    current="$(get_env_value "$key")"
    if ! is_placeholder_value "$key" "$current"; then
        return 0
    fi
    if [[ -n "${!key:-}" ]] && ! is_placeholder_value "$key" "${!key}"; then
        set_env_value "$key" "${!key}"
        return 0
    fi
    if ! is_interactive; then
        die "Не задана обязательная переменная ${key}. Передайте её в окружении или заполните ${ENV_FILE}."
    fi
    while true; do
        local suffix=""
        [[ -n "$default" ]] && suffix=" [${default}]"
        read_tty "${prompt}${suffix}: " input "$secret" || die "Не удалось прочитать ${key}."
        input="${input:-$default}"
        if ! is_placeholder_value "$key" "$input"; then
            set_env_value "$key" "$input"
            return 0
        fi
        warn "Значение ${key} не может быть пустым или placeholder."
    done
}

generate_fernet_key() {
    "$PYTHON_BIN" - <<'PY'
import base64, secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
}

generate_secret() {
    "$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

urlencode() {
    JUST1KBOT_URLENCODE_VALUE="$1" "$PYTHON_BIN" - <<'PY'
import os
from urllib.parse import quote
print(quote(os.environ.pop("JUST1KBOT_URLENCODE_VALUE"), safe=""), end="")
PY
}

parse_database_url() {
    local url="$1" output
    output="$(JUST1KBOT_DATABASE_URL="$url" "$PYTHON_BIN" - <<'PY'
import base64
import os
from urllib.parse import urlparse, unquote

url = urlparse(os.environ.pop("JUST1KBOT_DATABASE_URL"))
if not url.scheme.startswith("postgresql"):
    raise SystemExit(2)
values = [
    unquote(url.username or ""),
    unquote(url.password or ""),
    url.hostname or "localhost",
    str(url.port or 5432),
    unquote((url.path or "").lstrip("/")),
]
if not values[0] or not values[4]:
    raise SystemExit(3)
for item in values:
    print(base64.b64encode(item.encode()).decode())
PY
)" || return 1
    mapfile -t _DB_PARTS <<< "$output"
    [[ "${#_DB_PARTS[@]}" -eq 5 ]] || return 1
    DB_USER="$(printf '%s' "${_DB_PARTS[0]}" | base64 -d)"
    DB_PASS="$(printf '%s' "${_DB_PARTS[1]}" | base64 -d)"
    DB_HOST="$(printf '%s' "${_DB_PARTS[2]}" | base64 -d)"
    DB_PORT="$(printf '%s' "${_DB_PARTS[3]}" | base64 -d)"
    DB_NAME="$(printf '%s' "${_DB_PARTS[4]}" | base64 -d)"
}

is_local_host() {
    case "$1" in localhost|127.0.0.1|::1|/var/run/postgresql|'') return 0 ;; *) return 1 ;; esac
}

parse_redis_url() {
    local url="$1" output
    output="$(JUST1KBOT_REDIS_URL="$url" "$PYTHON_BIN" - <<'PY'
import base64
import os
from urllib.parse import urlparse, unquote
url = urlparse(os.environ.pop("JUST1KBOT_REDIS_URL"))
if url.scheme not in {"redis", "rediss"}:
    raise SystemExit(2)
values = [url.scheme, url.hostname or "localhost", str(url.port or 6379), (url.path or "/0").lstrip("/") or "0", unquote(url.username or ""), unquote(url.password or "")]
for item in values:
    print(base64.b64encode(item.encode()).decode())
PY
)" || return 1
    mapfile -t _REDIS_PARTS <<< "$output"
    [[ "${#_REDIS_PARTS[@]}" -eq 6 ]] || return 1
    REDIS_SCHEME="$(printf '%s' "${_REDIS_PARTS[0]}" | base64 -d)"
    REDIS_HOST="$(printf '%s' "${_REDIS_PARTS[1]}" | base64 -d)"
    REDIS_PORT="$(printf '%s' "${_REDIS_PARTS[2]}" | base64 -d)"
    REDIS_DB="$(printf '%s' "${_REDIS_PARTS[3]}" | base64 -d)"
    REDIS_USER="$(printf '%s' "${_REDIS_PARTS[4]}" | base64 -d)"
    REDIS_URL_PASS="$(printf '%s' "${_REDIS_PARTS[5]}" | base64 -d)"
}

ensure_service_user() {
    if ! getent group "$BOT_GROUP" >/dev/null; then
        groupadd --system "$BOT_GROUP"
    fi
    if ! id "$BOT_USER" >/dev/null 2>&1; then
        useradd --system --gid "$BOT_GROUP" --home-dir "$APP_ROOT" --shell /usr/sbin/nologin "$BOT_USER"
    fi
    mkdir -p "$APP_ROOT" "$RELEASES_DIR" "$STATE_DIR" "$CONFIG_DIR" "$BACKUP_ROOT" /run/just1kbot
    chown root:"$BOT_GROUP" "$APP_ROOT" "$RELEASES_DIR" "$STATE_DIR" "$CONFIG_DIR"
    chmod 750 "$APP_ROOT" "$RELEASES_DIR" "$STATE_DIR" "$CONFIG_DIR"
    chown "$BOT_USER:$BOT_GROUP" /run/just1kbot
    chmod 700 /run/just1kbot
    chown root:root "$BACKUP_ROOT"
    chmod 700 "$BACKUP_ROOT"
}

