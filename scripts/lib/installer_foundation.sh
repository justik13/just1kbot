#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

# Shared-server-safe installer foundation for Just1kBot.
# This library is source-only and never changes global Redis or firewall state.

: "${BOT_USER:=just1kbot}"
: "${BOT_HOME:=/home/just1kbot}"
: "${PROJECT_DIR:=/opt/just1kbot}"
: "${ENV_FILE:=$PROJECT_DIR/.env}"
: "${STATE_ROOT:=/var/lib/just1kbot}"
: "${INSTALL_STATE_DIR:=$STATE_ROOT/install-state}"
: "${INSTALL_MANIFEST:=$INSTALL_STATE_DIR/manifest.json}"
: "${INSTALL_JOURNAL:=$INSTALL_STATE_DIR/transaction.json}"
: "${REDIS_SERVICE:=just1kbot-redis.service}"
: "${REDIS_PORT:=6380}"
: "${REDIS_CONFIG:=/etc/just1kbot/redis.conf}"
: "${REDIS_DATA_DIR:=$STATE_ROOT/redis}"
: "${REDIS_UNIT:=/etc/systemd/system/$REDIS_SERVICE}"
: "${CLI_PATH:=/usr/local/sbin/just1kbot}"
: "${NGINX_AVAILABLE_DIR:=/etc/nginx/sites-available}"
: "${NGINX_ENABLED_DIR:=/etc/nginx/sites-enabled}"
: "${LETSENCRYPT_LIVE_DIR:=/etc/letsencrypt/live}"
: "${LETSENCRYPT_RENEWAL_DIR:=/etc/letsencrypt/renewal}"
: "${ACME_ROOT:=/var/lib/just1kbot/acme}"
: "${INSTALLATION_SCHEMA_VERSION:=1}"

FOUNDATION_OPERATION=${FOUNDATION_OPERATION:-install}
FOUNDATION_STEP=${FOUNDATION_STEP:-initialization}
FOUNDATION_CREATED_RESOURCES=()
FOUNDATION_JOURNAL_PHASE=none

foundation_log() {
    if declare -F log >/dev/null 2>&1; then
        log "$1"
    else
        printf '[installer] %s\n' "$1"
    fi
}

foundation_warn() {
    if declare -F warn >/dev/null 2>&1; then
        warn "$1"
    else
        printf '[installer] WARNING: %s\n' "$1" >&2
    fi
}

foundation_error() {
    if declare -F error >/dev/null 2>&1; then
        error "$1"
    else
        printf '[installer] ERROR: %s\n' "$1" >&2
    fi
}

foundation_fail() {
    local code=${1:-INSTALLER_ERROR}
    local problem=${2:-'операция не выполнена'}
    local reason=${3:-$problem}
    local action=${4:-'Исправьте указанную причину и повторите операцию.'}

    if declare -F installer_fail >/dev/null 2>&1; then
        installer_fail "$code" "$problem" "$reason" "$action"
    fi

    printf '\nОШИБКА JUST1KBOT [%s]\n' "$code" >&2
    printf 'Операция: %s\n' "$FOUNDATION_OPERATION" >&2
    printf 'Этап: %s\n' "$FOUNDATION_STEP" >&2
    printf 'Проблема: %s\n' "$problem" >&2
    printf 'Причина: %s\n' "$reason" >&2
    printf 'Что сделать: %s\n' "$action" >&2
    return 1
}

foundation_set_operation() {
    FOUNDATION_OPERATION=$1
}

foundation_set_step() {
    FOUNDATION_STEP=$1
    foundation_log "$1"
}

foundation_path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

foundation_assert_regular_file() {
    local path=$1
    [[ -f "$path" && ! -L "$path" ]] || foundation_fail \
        UNSAFE_FILE \
        'ожидался обычный файл' \
        "$path отсутствует, является symlink или имеет другой тип" \
        'Проверьте объект вручную. Installer не будет следовать по symlink или перезаписывать неизвестный тип.'
}

foundation_assert_directory() {
    local path=$1
    [[ -d "$path" && ! -L "$path" ]] || foundation_fail \
        UNSAFE_DIRECTORY \
        'ожидался безопасный каталог' \
        "$path отсутствует, является symlink или имеет другой тип" \
        'Проверьте объект вручную; автоматическая перезапись запрещена.'
}

foundation_secure_parent_chain() {
    local path=$1 current mode owner
    current=$(dirname "$path")
    while [[ "$current" != / ]]; do
        [[ ! -L "$current" ]] || foundation_fail \
            SYMLINK_PARENT \
            'parent directory является symlink' \
            "$current входит в путь $path" \
            'Освободите зарезервированный путь или проверьте его вручную.'

        # Check ownership and permissions to prevent TOCTOU attacks
        mode=$(stat -c '%a' "$current" 2>/dev/null || printf '000')
        owner=$(stat -c '%U' "$current" 2>/dev/null || printf 'unknown')

        # Parent must not be group/other writable (mode & 022 must be 0)
        if (( (8#$mode & 8#022) != 0 )); then
            foundation_fail \
                INSECURE_PARENT_PERMISSIONS \
                'parent directory имеет небезопасные permissions' \
                "$current имеет mode $mode (group/other writable)" \
                "Исправьте permissions: chmod go-w $current"
        fi

        # Parent must be owned by root or bot user
        if [[ "$owner" != root && "$owner" != "$BOT_USER" && "$owner" != "unknown" ]]; then
            foundation_fail \
                INSECURE_PARENT_OWNER \
                'parent directory имеет неожиданного владельца' \
                "$current принадлежит $owner" \
                "Исправьте ownership: chown root:root $current"
        fi

        current=$(dirname "$current")
    done
}

foundation_require_commands() {
    local command
    for command in "$@"; do
        command -v "$command" >/dev/null 2>&1 || foundation_fail \
            MISSING_COMMAND \
            "не найдена обязательная команда: $command" \
            "PATH=${PATH:-empty}" \
            'Установите пакет, предоставлящий команду, и повторите операцию.'
    done
}

command_required() {
    foundation_require_commands "$1"
}

foundation_exact_ubuntu_2404() {
    [[ -f /etc/os-release && ! -L /etc/os-release ]] || foundation_fail \
        UNSUPPORTED_OS 'не удалось определить ОС' '/etc/os-release отсутствует или небезопасен' \
        'Используйте чистую Ubuntu 24.04 LTS.'
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || foundation_fail \
        UNSUPPORTED_OS \
        'поддерживается только Ubuntu 24.04 LTS' \
        "обнаружено: ID=${ID:-unknown} VERSION_ID=${VERSION_ID:-unknown}" \
        'Разверните Ubuntu 24.04 LTS или используйте ручную установку без этого installer.'
}

foundation_atomic_write() {
    local target=$1 owner=$2 group=$3 mode=$4
    local parent temporary
    parent=$(dirname "$target")
    foundation_secure_parent_chain "$target"
    install -d -o "$owner" -g "$group" -m 0750 "$parent"
    temporary=$(mktemp "$parent/.just1kbot-write.XXXXXX")
    cat > "$temporary"

    # CRITICAL: fsync file data before rename to prevent data loss on crash
    # mv -f is atomic at metadata level, but file data may still be in page cache.
    # Without fsync, a crash 1ms after mv could leave corrupted/empty file on disk.
    sync -f "$temporary" 2>/dev/null || true

    chown "$owner:$group" "$temporary"
    chmod "$mode" "$temporary"
    mv -f -- "$temporary" "$target"

    # CRITICAL: fsync parent directory to ensure rename is persisted
    # Without this, directory metadata may be lost on crash.
    sync -f "$parent" 2>/dev/null || true
}

foundation_manifest_validate() {
    [[ -f "$INSTALL_MANIFEST" && ! -L "$INSTALL_MANIFEST" ]] || return 1
    local state
    state=$(stat -c '%U:%G %a' "$INSTALL_MANIFEST" 2>/dev/null || true)
    [[ "$state" == 'root:root 600' ]] || return 1

    MANIFEST_PATH="$INSTALL_MANIFEST" \
    EXPECTED_SCHEMA="$INSTALLATION_SCHEMA_VERSION" \
    EXPECTED_PROJECT_DIR="$PROJECT_DIR" \
    python3 - <<'PY' >/dev/null
import json
import os
import re
from pathlib import Path

path = Path(os.environ["MANIFEST_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("schema_version") != int(os.environ["EXPECTED_SCHEMA"]):
    raise SystemExit(1)
if data.get("project_dir") != os.environ["EXPECTED_PROJECT_DIR"]:
    raise SystemExit(1)
installation_id = data.get("installation_id")
if not isinstance(installation_id, str) or re.fullmatch(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    installation_id,
    re.IGNORECASE,
) is None:
    raise SystemExit(1)
resources = data.get("managed_resources")
if not isinstance(resources, list) or not all(isinstance(item, str) and item for item in resources):
    raise SystemExit(1)
if len(resources) != len(set(resources)):
    raise SystemExit(1)
metadata = data.get("metadata")
if not isinstance(metadata, dict):
    raise SystemExit(1)
PY
}

foundation_manifest_create() {
    foundation_set_step 'Создание ownership manifest'
    foundation_path_exists "$INSTALL_MANIFEST" && foundation_fail \
        MANIFEST_COLLISION \
        'ownership manifest уже существует' \
        "$INSTALL_MANIFEST нельзя автоматически заменить" \
        'Запустите state/doctor. Повреждённый manifest требует ручной проверки.'

    install -d -o root -g root -m 0700 "$INSTALL_STATE_DIR"
    local installation_id
    installation_id=$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)
    MANIFEST_PATH="$INSTALL_MANIFEST" \
    INSTALLATION_ID="$installation_id" \
    PROJECT_DIR_VALUE="$PROJECT_DIR" \
    SCHEMA_VALUE="$INSTALLATION_SCHEMA_VERSION" \
    python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

payload = {
    "schema_version": int(os.environ["SCHEMA_VALUE"]),
    "installation_id": os.environ["INSTALLATION_ID"],
    "project_dir": os.environ["PROJECT_DIR_VALUE"],
    "created_at_utc": datetime.now(timezone.utc).isoformat(),
    "managed_resources": [],
    "metadata": {
        "firewall_managed": False,
        "redis_mode": "dedicated-service",
        "redis_port": 6380,
        "platform": "ubuntu-24.04",
    },
}
path = Path(os.environ["MANIFEST_PATH"])
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_MANIFEST"
    chmod 0600 "$INSTALL_MANIFEST"
    foundation_manifest_validate || foundation_fail \
        MANIFEST_CREATE_FAILED 'созданный manifest не прошёл self-check' "$INSTALL_MANIFEST" \
        'Сохраните журнал и удалите только новый повреждённый manifest после ручной проверки.'
}

foundation_manifest_require() {
    foundation_manifest_validate || foundation_fail \
        MANIFEST_INVALID \
        'ownership manifest отсутствует или повреждён' \
        "$INSTALL_MANIFEST не прошёл schema/owner/mode validation" \
        'Запустите sudo bash deploy.sh state и не выполняйте destructive operation вслепую.'
}

foundation_manifest_add_resource() {
    local resource=$1
    foundation_manifest_require
    MANIFEST_PATH="$INSTALL_MANIFEST" RESOURCE_VALUE="$resource" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MANIFEST_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
resources = data["managed_resources"]
value = os.environ["RESOURCE_VALUE"]
if value not in resources:
    resources.append(value)
    resources.sort()
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_MANIFEST"
    chmod 0600 "$INSTALL_MANIFEST"
}

foundation_manifest_remove_resource() {
    local resource=$1
    foundation_manifest_require
    MANIFEST_PATH="$INSTALL_MANIFEST" RESOURCE_VALUE="$resource" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MANIFEST_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
value = os.environ["RESOURCE_VALUE"]
data["managed_resources"] = [item for item in data["managed_resources"] if item != value]
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_MANIFEST"
    chmod 0600 "$INSTALL_MANIFEST"
}

foundation_manifest_has_resource() {
    local resource=$1
    foundation_manifest_validate || return 1
    MANIFEST_PATH="$INSTALL_MANIFEST" RESOURCE_VALUE="$resource" python3 - <<'PY' >/dev/null
import json
import os
from pathlib import Path

data = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))
raise SystemExit(0 if os.environ["RESOURCE_VALUE"] in data["managed_resources"] else 1)
PY
}

foundation_manifest_set_metadata() {
    local key=$1 value=$2
    foundation_manifest_require
    MANIFEST_PATH="$INSTALL_MANIFEST" META_KEY="$key" META_VALUE="$value" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["MANIFEST_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
value = os.environ["META_VALUE"]
if value == "true":
    converted = True
elif value == "false":
    converted = False
elif value.isdigit():
    converted = int(value)
else:
    converted = value
data["metadata"][os.environ["META_KEY"]] = converted
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_MANIFEST"
    chmod 0600 "$INSTALL_MANIFEST"
}

foundation_journal_validate() {
    [[ -f "$INSTALL_JOURNAL" && ! -L "$INSTALL_JOURNAL" ]] || return 1
    [[ "$(stat -c '%U:%G %a' "$INSTALL_JOURNAL" 2>/dev/null || true)" == 'root:root 600' ]] || return 1
    JOURNAL_PATH="$INSTALL_JOURNAL" python3 - <<'PY' >/dev/null
import json
import os
from pathlib import Path

data = json.loads(Path(os.environ["JOURNAL_PATH"]).read_text(encoding="utf-8"))
if not isinstance(data, dict) or data.get("schema_version") != 1:
    raise SystemExit(1)
if data.get("operation") not in {"install", "update", "uninstall"}:
    raise SystemExit(1)
if not isinstance(data.get("phase"), str):
    raise SystemExit(1)
if not isinstance(data.get("created_resources"), list):
    raise SystemExit(1)
PY
}

foundation_journal_begin() {
    local operation=$1 phase=${2:-preflight}
    foundation_path_exists "$INSTALL_JOURNAL" && foundation_fail \
        UNFINISHED_TRANSACTION \
        'найден journal незавершённой операции' \
        "$INSTALL_JOURNAL уже существует" \
        'Запустите sudo bash deploy.sh install-recover или install-rollback.'
    install -d -o root -g root -m 0700 "$INSTALL_STATE_DIR"
    JOURNAL_PATH="$INSTALL_JOURNAL" OPERATION_VALUE="$operation" PHASE_VALUE="$phase" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

payload = {
    "schema_version": 1,
    "operation": os.environ["OPERATION_VALUE"],
    "phase": os.environ["PHASE_VALUE"],
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    "created_resources": [],
    "notes": [],
}
path = Path(os.environ["JOURNAL_PATH"])
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_JOURNAL"
    chmod 0600 "$INSTALL_JOURNAL"
}

foundation_journal_update_phase() {
    local phase=$1
    foundation_journal_validate || foundation_fail \
        JOURNAL_INVALID 'transaction journal повреждён' "$INSTALL_JOURNAL" \
        'Не продолжайте автоматически; сохраните journal и выполните ручную проверку.'
    JOURNAL_PATH="$INSTALL_JOURNAL" PHASE_VALUE="$phase" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["JOURNAL_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
data["phase"] = os.environ["PHASE_VALUE"]
data["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_JOURNAL"
    chmod 0600 "$INSTALL_JOURNAL"
    FOUNDATION_JOURNAL_PHASE=$phase
}

foundation_journal_add_created_resource() {
    local resource=$1
    foundation_journal_validate || foundation_fail JOURNAL_INVALID "не удалось валидировать journal"
    JOURNAL_PATH="$INSTALL_JOURNAL" RESOURCE_VALUE="$resource" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["JOURNAL_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
value = os.environ["RESOURCE_VALUE"]
if value not in data["created_resources"]:
    data["created_resources"].append(value)
data["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
    chown root:root "$INSTALL_JOURNAL"
    chmod 0600 "$INSTALL_JOURNAL"
}

foundation_journal_finish() {
    foundation_path_exists "$INSTALL_JOURNAL" || return 0
    foundation_journal_validate || foundation_fail \
        JOURNAL_INVALID 'нельзя завершить повреждённый journal' "$INSTALL_JOURNAL" \
        'Сохраните journal и выполните ручную проверку.'
    rm -f -- "$INSTALL_JOURNAL"
    sync -f "$INSTALL_STATE_DIR" 2>/dev/null || true
}

foundation_journal_created_resources() {
    foundation_journal_validate || return 1
    JOURNAL_PATH="$INSTALL_JOURNAL" python3 - <<'PY'
import json
import os
from pathlib import Path

for item in reversed(json.loads(Path(os.environ["JOURNAL_PATH"]).read_text(encoding="utf-8"))["created_resources"]):
    print(item)
PY
}

foundation_register_resource() {
    local resource=$1 created=${2:-false}
    foundation_manifest_add_resource "$resource"
    if [[ "$created" == true ]]; then
        foundation_journal_add_created_resource "$resource"
    fi
}

foundation_preflight_path_absent_or_owned() {
    local path=$1 marker=$2 description=$3
    foundation_path_exists "$path" || return 0
    if foundation_manifest_has_resource "$marker"; then
        return 0
    fi
    foundation_fail \
        FOREIGN_COLLISION \
        "$description уже существует без ownership proof" \
        "$path найден, но resource '$marker' отсутствует в manifest" \
        'Перенесите чужой объект или выполните документированную legacy migration; installer не будет его перезаписывать.'
}

foundation_port_in_use() {
    local port=$1
    if command -v ss >/dev/null 2>&1; then
        ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\])${port}$"
        return
    fi
    PORT_HEX=$(printf '%04X' "$port") python3 - <<'PY'
import os
from pathlib import Path

needle = os.environ["PORT_HEX"].upper()
for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
    path = Path(filename)
    if not path.exists():
        continue
    for line in path.read_text(encoding="ascii").splitlines()[1:]:
        parts = line.split()
        if len(parts) > 3 and parts[1].split(":")[-1].upper() == needle and parts[3] == "0A":
            raise SystemExit(0)
raise SystemExit(1)
PY
}

foundation_preflight_port() {
    local port=$1 unit=$2
    if ! foundation_port_in_use "$port"; then
        return 0
    fi
    if foundation_manifest_has_resource "systemd:$unit" && systemctl is-active --quiet "$unit" 2>/dev/null; then
        return 0
    fi
    foundation_fail \
        PORT_COLLISION \
        "порт $port уже занят" \
        "listener не доказан как принадлежащий $unit" \
        "Освободите порт или задайте отдельный безопасный порт перед повторной установкой."
}

foundation_validate_domain() {
    DOMAIN_VALUE=${1:-} python3 - <<'PY' >/dev/null
import os
import re

value = os.environ["DOMAIN_VALUE"].strip().lower().rstrip(".")
labels = value.split(".")
pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if not value or len(value) > 253 or len(labels) < 2 or any(pattern.fullmatch(label) is None for label in labels):
    raise SystemExit(1)
PY
}

foundation_preflight_domain() {
    local domain=$1 port=${2:-8080}
    foundation_validate_domain "$domain" || foundation_fail \
        INVALID_DOMAIN 'DOMAIN имеет небезопасный формат' "$domain" \
        'Используйте корректное DNS-имя без wildcard, URL-схемы и path.'

    local available="$NGINX_AVAILABLE_DIR/$domain"
    local enabled="$NGINX_ENABLED_DIR/$domain"
    foundation_preflight_path_absent_or_owned "$available" "nginx-site:$domain" 'Nginx site'
    foundation_preflight_path_absent_or_owned "$enabled" "nginx-enabled:$domain" 'Nginx enabled link'

    if command -v nginx >/dev/null 2>&1; then
        local rendered
        rendered=$(nginx -T 2>&1) || foundation_fail \
            NGINX_INVALID 'существующая Nginx configuration невалидна' "$rendered" \
            'Исправьте nginx -t до установки Just1kBot.'
        if grep -Eq "^[[:space:]]*server_name[[:space:]]+([^;[:space:]]+[[:space:]]+)*${domain}([[:space:]]+[^;]*)?;" <<<"$rendered"; then
            foundation_manifest_has_resource "nginx-site:$domain" || foundation_fail \
                NGINX_DOMAIN_COLLISION \
                "server_name $domain уже объявлен" \
                'объявление найдено в существующей Nginx configuration без ownership proof' \
                'Выберите другой домен или удалите/перенесите чужую конфигурацию вручную.'
        fi
    fi

    foundation_preflight_port "$port" just1kbot.service
}

foundation_preflight_static_resources() {
    foundation_set_step 'Read-only проверка зарезервированных ресурсов'
    foundation_preflight_path_absent_or_owned "$PROJECT_DIR" "path:$PROJECT_DIR" 'Production directory'
    foundation_preflight_path_absent_or_owned "$BOT_HOME" "path:$BOT_HOME" 'Service home'
    foundation_preflight_path_absent_or_owned "$CLI_PATH" "path:$CLI_PATH" 'CLI path'
    foundation_preflight_path_absent_or_owned "$REDIS_CONFIG" "path:$REDIS_CONFIG" 'Dedicated Redis config'
    foundation_preflight_path_absent_or_owned "$REDIS_DATA_DIR" "path:$REDIS_DATA_DIR" 'Dedicated Redis data directory'
    foundation_preflight_path_absent_or_owned "$REDIS_UNIT" "systemd:$REDIS_SERVICE" 'Dedicated Redis unit'
    foundation_preflight_path_absent_or_owned /etc/systemd/system/just1kbot.service 'systemd:just1kbot.service' 'Application unit'
    foundation_preflight_port "$REDIS_PORT" "$REDIS_SERVICE"
}

foundation_setup_state_dirs() {
    foundation_secure_parent_chain "$STATE_ROOT"
    if foundation_path_exists "$STATE_ROOT"; then
        foundation_assert_directory "$STATE_ROOT"
        # fix perms if they were 0700
        chmod 0711 "$STATE_ROOT"
    else
        install -d -o root -g root -m 0711 "$STATE_ROOT"
        foundation_journal_add_created_resource "path:$STATE_ROOT"
    fi
    install -d -o root -g root -m 0700 "$INSTALL_STATE_DIR"
}

foundation_write_dedicated_redis_config() {
    local password=$1
    [[ "$password" =~ ^[A-Za-z0-9_@%*+=-]{8,}$ ]] || foundation_fail \
        INVALID_REDIS_PASSWORD 'REDIS_PASSWORD имеет неверный формат' \
        'минимум 8 символов; разрешены A-Z, a-z, 0-9, _ @ % * + = -' \
        'Сгенерируйте новый уникальный пароль и повторите установку.'

    foundation_set_step 'Настройка изолированного Redis'
    exec 9>"/run/lock/just1kbot-port-${REDIS_PORT}.lock"
    flock -n 9 || foundation_fail PORT_LOCKED "порт $REDIS_PORT заблокирован другой установкой" "не удалось получить lock на порт $REDIS_PORT"
    foundation_preflight_port "$REDIS_PORT" "$REDIS_SERVICE"

    local config_created=false data_created=false unit_created=false
    foundation_path_exists "$REDIS_CONFIG" || config_created=true
    foundation_path_exists "$REDIS_DATA_DIR" || data_created=true
    foundation_path_exists "$REDIS_UNIT" || unit_created=true

    install -d -o root -g redis -m 0750 "$(dirname "$REDIS_CONFIG")"
    install -d -o redis -g redis -m 0700 "$REDIS_DATA_DIR"

    foundation_atomic_write "$REDIS_CONFIG" root redis 0640 <<EOF_REDIS
bind 127.0.0.1 ::1
protected-mode yes
port ${REDIS_PORT}
tcp-backlog 128
timeout 0
tcp-keepalive 300
supervised systemd
daemonize no
pidfile /run/just1kbot-redis/redis.pid
loglevel notice
logfile ""
databases 1
always-show-logo no
set-proc-title yes
proc-title-template "{title} {listen-addr}"
dir ${REDIS_DATA_DIR}
dbfilename dump.rdb
appendonly yes
appenddirname appendonlydir
appendfilename appendonly.aof
appendfsync everysec
no-appendfsync-on-rewrite no
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
aof-load-truncated yes
stop-writes-on-bgsave-error yes
rdbcompression yes
rdbchecksum yes
maxmemory 256mb
maxmemory-policy noeviction
requirepass ${password}
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command CONFIG ""
EOF_REDIS

    foundation_atomic_write "$REDIS_UNIT" root root 0644 <<EOF_UNIT
[Unit]
Description=Just1kBot dedicated Redis
After=network.target
Before=just1kbot.service

[Service]
Type=notify
User=redis
Group=redis
RuntimeDirectory=just1kbot-redis
RuntimeDirectoryMode=0750
ExecStart=/usr/bin/redis-server ${REDIS_CONFIG}
ExecStop=/bin/kill -s TERM \$MAINPID
Restart=on-failure
RestartSec=3s
TimeoutStartSec=30s
TimeoutStopSec=30s
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=${REDIS_DATA_DIR} /run/just1kbot-redis

[Install]
WantedBy=multi-user.target
EOF_UNIT

    foundation_register_resource "path:$REDIS_CONFIG" "$config_created"
    foundation_register_resource "path:$REDIS_DATA_DIR" "$data_created"
    foundation_register_resource "systemd:$REDIS_SERVICE" "$unit_created"

    systemctl daemon-reload
    systemctl enable "$REDIS_SERVICE" >/dev/null

    # Pre-flight path check
    sudo -u redis test -d "$REDIS_DATA_DIR" || {
        foundation_fail \
            REDIS_DATA_UNREACHABLE \
            'Redis не может получить доступ к своему каталогу данных' \
            "Пользователь redis не может прочитать $REDIS_DATA_DIR" \
            "Проверьте права доступа на родительские каталоги (например, $STATE_ROOT)."
    }

    systemctl restart "$REDIS_SERVICE"

    # Exponential backoff with retry for Redis PING
    local max_retries=16
    local base_delay=1
    local retries=0
    local success=false

    while (( retries < max_retries )); do
        if REDISCLI_AUTH="$password" redis-cli -h 127.0.0.1 -p "$REDIS_PORT" -n 0 PING 2>/dev/null | grep -qx PONG; then
            success=true
            break
        fi
        sleep "$base_delay"
        base_delay=$(( base_delay * 2 > 10 ? 10 : base_delay * 2 ))
        retries=$(( retries + 1 ))
    done

    if [[ "$success" == false ]]; then
        journalctl -u "$REDIS_SERVICE" -n 80 --no-pager >&2 2>/dev/null || true
        # Attempt to parse stderr for readable permission error
        local redis_logs
        redis_logs=$(journalctl -u "$REDIS_SERVICE" -n 20 --no-pager 2>/dev/null)
        if echo "$redis_logs" | grep -qi "Permission denied"; then
             printf "\n>>> Redis (uid=$(id -u redis)) не может открыть $REDIS_DATA_DIR: Permission denied на родительском каталоге <<<\n" >&2
        fi

        foundation_fail \
            REDIS_START_FAILED \
            'dedicated Redis не прошёл PING' \
            "$REDIS_SERVICE не отвечает на 127.0.0.1:$REDIS_PORT" \
            "Проверьте journalctl -u $REDIS_SERVICE и свободное место в $REDIS_DATA_DIR."
    fi
}

foundation_update_redis_env() {
    local password=$1
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || return 0
    ENV_FILE_PATH="$ENV_FILE" REDIS_PASSWORD_VALUE="$password" REDIS_PORT_VALUE="$REDIS_PORT" python3 - <<'PY'
import os
import stat
import tempfile
from pathlib import Path
from urllib.parse import quote

path = Path(os.environ["ENV_FILE_PATH"])
metadata = path.stat()
if not stat.S_ISREG(metadata.st_mode):
    raise SystemExit("env is not regular")
password = quote(os.environ["REDIS_PASSWORD_VALUE"], safe="")
url = f"redis://:{password}@127.0.0.1:{int(os.environ['REDIS_PORT_VALUE'])}/0"
lines = path.read_text(encoding="utf-8").splitlines()
output = []
seen_url = seen_password = False
for raw in lines:
    stripped = raw.lstrip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key == "REDIS_URL":
            if seen_url:
                raise SystemExit("duplicate REDIS_URL")
            output.append(f'REDIS_URL="{url}"')
            seen_url = True
            continue
        if key == "REDIS_PASSWORD":
            if seen_password:
                raise SystemExit("duplicate REDIS_PASSWORD")
            escaped = os.environ["REDIS_PASSWORD_VALUE"].replace("\\", "\\\\").replace('"', '\\"')
            output.append(f'REDIS_PASSWORD="{escaped}"')
            seen_password = True
            continue
    output.append(raw)
if not seen_url:
    output.append(f'REDIS_URL="{url}"')
if not seen_password:
    escaped = os.environ["REDIS_PASSWORD_VALUE"].replace("\\", "\\\\").replace('"', '\\"')
    output.append(f'REDIS_PASSWORD="{escaped}"')
fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
try:
    os.write(fd, ("\n".join(output) + "\n").encode())
finally:
    os.close(fd)
os.chown(temporary, metadata.st_uid, metadata.st_gid)
os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
os.replace(temporary, path)
PY
}

foundation_install_cli() {
    local created=false
    foundation_path_exists "$CLI_PATH" || created=true
    foundation_atomic_write "$CLI_PATH" root root 0750 <<'EOF_CLI'
#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

CONTROL=/opt/just1kbot/deploy.sh
[[ -f "$CONTROL" && ! -L "$CONTROL" ]] || {
    printf 'Just1kBot control plane отсутствует или небезопасен: %s\n' "$CONTROL" >&2
    exit 1
}
exec /bin/bash "$CONTROL" "$@"
EOF_CLI
    foundation_register_resource "path:$CLI_PATH" "$created"
}

foundation_nginx_site_content() {
    local domain=$1 port=$2 tls=$3
    if [[ "$tls" == true ]]; then
        cat <<EOF
# Managed by Just1kBot ownership manifest. Do not adopt by filename alone.
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    location ^~ /.well-known/acme-challenge/ { root ${ACME_ROOT}; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${domain};
    ssl_certificate ${LETSENCRYPT_LIVE_DIR}/${domain}/fullchain.pem;
    ssl_certificate_key ${LETSENCRYPT_LIVE_DIR}/${domain}/privkey.pem;
    client_max_body_size 64k;
    location = /health {
        proxy_pass http://127.0.0.1:${port}/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
    location = /webhook/yookassa {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:${port}/webhook/yookassa;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
    location / { return 404; }
}
EOF
    else
        cat <<EOF
# Managed by Just1kBot ownership manifest. Do not adopt by filename alone.
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    location ^~ /.well-known/acme-challenge/ { root ${ACME_ROOT}; }
    location = /health {
        proxy_pass http://127.0.0.1:${port}/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location = /webhook/yookassa {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:${port}/webhook/yookassa;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location / { return 404; }
}
EOF
    fi
}

foundation_write_nginx_site() {
    local domain=$1 port=$2 tls=$3
    foundation_preflight_domain "$domain" "$port"
    local available="$NGINX_AVAILABLE_DIR/$domain"
    local enabled="$NGINX_ENABLED_DIR/$domain"
    local available_created=false enabled_created=false stash
    foundation_path_exists "$available" || available_created=true
    foundation_path_exists "$enabled" || enabled_created=true

    stash=$(mktemp -d /run/just1kbot-nginx.XXXXXX)
    if [[ -f "$available" && ! -L "$available" ]]; then
        cp -a -- "$available" "$stash/previous-site"
    fi
    if [[ -L "$enabled" ]]; then
        readlink -- "$enabled" > "$stash/previous-enabled"
    fi

    foundation_nginx_site_content "$domain" "$port" "$tls" | foundation_atomic_write "$available" root root 0644
    if foundation_path_exists "$enabled"; then
        [[ -L "$enabled" && "$(readlink -f -- "$enabled")" == "$(realpath -m -- "$available")" ]] || {
            [[ -f "$stash/previous-site" ]] && cp -a -- "$stash/previous-site" "$available"
            rm -rf -- "$stash"
            foundation_fail NGINX_LINK_COLLISION 'enabled Nginx path не принадлежит ожидаемому site' "$enabled" \
                'Освободите путь вручную; чужой объект не будет заменён.'
            return 1
        }
    else
        ln -s -- "$available" "$enabled"
    fi

    if ! nginx -t; then
        rm -f -- "$enabled"
        [[ -f "$stash/previous-enabled" ]] && ln -s -- "$(cat "$stash/previous-enabled")" "$enabled"
        if [[ -f "$stash/previous-site" ]]; then
            cp -a -- "$stash/previous-site" "$available"
        elif [[ "$available_created" == true ]]; then
            rm -f -- "$available"
        fi
        if ! nginx -t >/dev/null 2>&1; then
            foundation_warn "Nginx rollback failed: предыдущая конфигурация тоже невалидна"
        fi
        rm -rf -- "$stash"
        foundation_fail NGINX_GENERATED_INVALID 'сгенерированная Nginx configuration не прошла nginx -t' "$available" \
            'Предыдущее состояние восстановлено. Проверьте полный вывод nginx -t.'
        return 1
    fi

    foundation_register_resource "nginx-site:$domain" "$available_created"
    foundation_register_resource "nginx-enabled:$domain" "$enabled_created"
    rm -rf -- "$stash"
}

foundation_setup_nginx_tls() {
    local domain=$1 email=$2 port=$3
    foundation_set_step "Настройка manifest-owned Nginx/TLS для $domain"
    foundation_require_commands nginx certbot
    install -d -o root -g root -m 0755 "$ACME_ROOT/.well-known/acme-challenge"
    foundation_register_resource "path:$ACME_ROOT" false

    foundation_write_nginx_site "$domain" "$port" false
    systemctl enable --now nginx >/dev/null
    systemctl reload nginx

    local cert_created=false
    if [[ ! -f "$LETSENCRYPT_LIVE_DIR/$domain/fullchain.pem" || ! -f "$LETSENCRYPT_LIVE_DIR/$domain/privkey.pem" ]]; then
        cert_created=true
        certbot certonly --webroot \
            --webroot-path "$ACME_ROOT" \
            --non-interactive --agree-tos --no-eff-email \
            --email "$email" -d "$domain"
    elif ! foundation_manifest_has_resource "certbot:$domain"; then
        local renewal="$LETSENCRYPT_RENEWAL_DIR/$domain.conf"
        [[ -f "$renewal" && ! -L "$renewal" ]] || foundation_fail \
            FOREIGN_CERTIFICATE \
            "TLS certificate $domain уже существует без ownership proof" \
            "$renewal отсутствует или небезопасен" \
            'Используйте другой домен или явно мигрируйте сертификат после проверки certbot renewal configuration.'
        grep -Fq "archive_dir = /etc/letsencrypt/archive/$domain" "$renewal" || foundation_fail \
            FOREIGN_CERTIFICATE \
            "существующий TLS certificate не прошёл legacy ownership check" \
            "$renewal не указывает на ожидаемый archive/$domain" \
            'Не удаляйте сертификат вслепую; выполните ручную проверку.'
    fi

    foundation_register_resource "certbot:$domain" "$cert_created"
    foundation_write_nginx_site "$domain" "$port" true
    systemctl reload nginx
}

foundation_firewall_noop() {
    foundation_log 'Firewall не изменяется: UFW defaults/rules/enabled state сохранены как есть.'
    foundation_manifest_set_metadata firewall_managed false
}

foundation_recover_status() {
    if ! foundation_path_exists "$INSTALL_JOURNAL"; then
        printf 'Незавершённый installer journal не найден.\n'
        return 0
    fi
    foundation_journal_validate || foundation_fail \
        JOURNAL_INVALID 'installer journal повреждён' "$INSTALL_JOURNAL" \
        'Сохраните файл и выполните ручной аудит перед любым удалением.'
    cat "$INSTALL_JOURNAL"
}

foundation_rollback_created_resources() {
    foundation_journal_validate || foundation_fail \
        JOURNAL_INVALID 'невозможно выполнить rollback повреждённого journal' "$INSTALL_JOURNAL" \
        'Сохраните файл и выполните ручной аудит.'

    local resource value
    while IFS= read -r resource; do
        [[ -n "$resource" ]] || continue
        case "$resource" in
            systemd:just1kbot-redis.service)
                systemctl disable --now just1kbot-redis.service >/dev/null 2>&1 || true
                rm -f -- "$REDIS_UNIT"
                systemctl daemon-reload
                ;;
            systemd:just1kbot.service)
                systemctl disable --now just1kbot.service >/dev/null 2>&1 || true
                rm -f -- /etc/systemd/system/just1kbot.service
                systemctl daemon-reload
                ;;
            path:/etc/just1kbot/redis.conf) rm -f -- "$REDIS_CONFIG" ;;
            path:/var/lib/just1kbot/redis) rm -rf --one-file-system -- "$REDIS_DATA_DIR" ;;
            path:/usr/local/sbin/just1kbot) rm -f -- "$CLI_PATH" ;;
            nginx-enabled:*)
                value=${resource#nginx-enabled:}
                rm -f -- "$NGINX_ENABLED_DIR/$value"
                nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
                ;;
            nginx-site:*)
                value=${resource#nginx-site:}
                rm -f -- "$NGINX_AVAILABLE_DIR/$value"
                nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
                ;;
            certbot:*)
                foundation_warn "rollback не удаляет автоматически certificate: ${resource#certbot:}"
                ;;
            path:/opt/just1kbot) rm -rf --one-file-system -- "$PROJECT_DIR" ;;
            path:/home/just1kbot)
                id "$BOT_USER" >/dev/null 2>&1 && userdel "$BOT_USER" >/dev/null 2>&1 || true
                [[ ! -L "$BOT_HOME" ]] && rm -rf --one-file-system -- "$BOT_HOME" || true
                ;;
            path:/var/lib/just1kbot)
                # State root contains the journal itself and is removed last only
                # after the journal has been copied to memory by the caller.
                ;;
            *) foundation_warn "rollback пропускает неизвестный resource: $resource" ;;
        esac
    done < <(foundation_journal_created_resources)

    foundation_journal_finish
    if foundation_manifest_validate; then
        rm -f -- "$INSTALL_MANIFEST"
    fi
    if [[ -d "$INSTALL_STATE_DIR" && ! -L "$INSTALL_STATE_DIR" ]]; then
        rmdir "$INSTALL_STATE_DIR" 2>/dev/null || true
    fi
    rmdir "$STATE_ROOT" 2>/dev/null || true
}

if [[ "${INSTALLER_FOUNDATION_SOURCE_ONLY:-0}" != 1 && "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'installer_foundation.sh is source-only\n' >&2
    exit 64
fi