#!/bin/bash
# Strictly read-only preflight. Repair/resume belongs to install_safe.sh.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_DIR=/opt/just1kbot
ENV_FILE="$PROJECT_DIR/.env"
UNIT_FILE=/etc/systemd/system/just1kbot.service
BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
STATE_ROOT=/var/lib/just1kbot
MANIFEST=$STATE_ROOT/install-state/manifest.json
TRANSACTION=$STATE_ROOT/install-state/transaction.json
POSTGRES_LIBRARY="$SCRIPT_DIR/lib/postgresql.sh"
FOUNDATION_LIBRARY="$SCRIPT_DIR/lib/installer_foundation.sh"
FOUNDATION_COMPAT="$SCRIPT_DIR/lib/installer_foundation_compat.sh"
PREFLIGHT_STEP=initialization

fail() {
    printf '\nОШИБКА JUST1KBOT PREFLIGHT\n' >&2
    printf 'Этап: %s\n' "$PREFLIGHT_STEP" >&2
    printf 'Проблема: %s\n' "$1" >&2
    printf 'Причина: %s\n' "${2:-$1}" >&2
    printf 'Что сделать: %s\n' "${3:-Исправьте причину и повторите команду.}" >&2
    exit 1
}

step() {
    PREFLIGHT_STEP=$1
    printf '[preflight] %s\n' "$1"
}

[[ ${EUID:-$(id -u)} -eq 0 ]] ||
    fail 'команда запущена не от root' 'EUID != 0' 'Повторите через sudo.'
for library in "$FOUNDATION_LIBRARY" "$FOUNDATION_COMPAT"; do
    [[ -f "$library" && ! -L "$library" ]] ||
        fail 'installer library отсутствует или небезопасна' "$library"
done
INSTALLER_FOUNDATION_SOURCE_ONLY=1
# shellcheck source=lib/installer_foundation.sh
source "$FOUNDATION_LIBRARY"
unset INSTALLER_FOUNDATION_SOURCE_ONLY
INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
# shellcheck source=lib/installer_foundation_compat.sh
source "$FOUNDATION_COMPAT"
unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

step 'Проверка Ubuntu 24.04'
foundation_assert_ubuntu_2404 || exit 1

for argument in "$@"; do
    case "$argument" in
        --check|--dry-run|--yes|--sha) ;;
        [0-9a-fA-F][0-9a-fA-F]*) ;;
        *) fail 'неизвестный preflight argument' "$argument" ;;
    esac
done

if [[ ! -e "$ENV_FILE" && ! -L "$ENV_FILE" ]]; then
    [[ ! -e "$UNIT_FILE" && ! -L "$UNIT_FILE" ]] || fail \
        'systemd unit существует без production .env' "$UNIT_FILE" \
        'Проверьте foreign collision или незавершённую installation.'
    foundation_preflight_static_resources || exit 1
    printf '[preflight] clean host: production .env/unit отсутствуют; сервер не изменён.\n'
    exit 0
fi

step 'Проверка production .env'
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] ||
    fail 'production .env имеет unsafe type' "$ENV_FILE"
state=$(stat -c '%U %G %a' "$ENV_FILE")
case "$state" in
    "root ${BOT_USER} 640") ;;
    "${BOT_USER} ${BOT_USER} 600")
        printf '[preflight] legacy env permissions обнаружены: %s; migration выполняется только внутри journal.\n' "$state"
        ;;
    *)
        fail 'production .env имеет неожиданные owner/mode' "$state" \
            'Не выполняйте chown вслепую; подтвердите ownership через state/manifest.'
        ;;
esac

ENV_FILE_PATH="$ENV_FILE" python3 - <<'PY' || fail \
    'production .env не прошёл schema validation' "$ENV_FILE" \
    'Исправьте duplicate/missing/unsafe values; secrets в вывод не печатаются.'
from pathlib import Path
from urllib.parse import urlsplit
import os

path = Path(os.environ["ENV_FILE_PATH"])
values = {}
counts = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    counts[key] = counts.get(key, 0) + 1
    values[key] = value
required = {
    "BOT_TOKEN", "ADMIN_IDS", "SUPPORT_USERNAME", "DATABASE_URL",
    "DB_ENCRYPTION_KEY", "REDIS_URL", "REDIS_PASSWORD",
    "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY", "YOOKASSA_RETURN_URL",
    "YOOKASSA_WEBHOOK_PORT", "DOMAIN", "SSL_EMAIL",
}
missing = sorted(key for key in required if counts.get(key) != 1 or not values.get(key))
if missing:
    raise SystemExit("missing/duplicate required settings: " + ",".join(missing))
for removed in ("AMNEZIA_API_URL", "AMNEZIA_API_KEY", "WEBHOOK_URL"):
    if counts.get(removed):
        raise SystemExit(f"removed setting present: {removed}")
database = urlsplit(values["DATABASE_URL"])
if database.scheme != "postgresql+asyncpg" or database.hostname not in {"127.0.0.1", "localhost"}:
    raise SystemExit("unsafe DATABASE_URL")
redis = urlsplit(values["REDIS_URL"])
if redis.scheme != "redis" or redis.hostname not in {"127.0.0.1", "localhost"}:
    raise SystemExit("unsafe REDIS_URL")
if (redis.port or 6379) not in {6379, 6380} or (redis.path.lstrip("/") or "0") != "0":
    raise SystemExit("unexpected Redis endpoint")
PY

step 'Проверка service account без изменений'
if id "$BOT_USER" >/dev/null 2>&1; then
    account=$(getent passwd "$BOT_USER")
    home=$(cut -d: -f6 <<<"$account")
    shell=$(cut -d: -f7 <<<"$account")
    [[ "$home" == "$BOT_HOME" ]] || fail 'service account home mismatch' "$home"
    case "$shell" in
        /bin/bash|/usr/sbin/nologin|/sbin/nologin) ;;
        *) fail 'service account shell mismatch' "$shell" ;;
    esac
else
    [[ ! -f "$UNIT_FILE" ]] ||
        fail 'service account отсутствует при существующем unit' "$BOT_USER"
fi

step 'Проверка manifest/journal'
if [[ -e "$MANIFEST" || -L "$MANIFEST" ]]; then
    foundation_manifest_validate >/dev/null || fail 'ownership manifest повреждён' "$MANIFEST"
    if [[ -e "$TRANSACTION" || -L "$TRANSACTION" ]]; then
        foundation_journal_validate >/dev/null || fail 'durable journal повреждён' "$TRANSACTION"
        fail \
            'найдена незавершённая installer transaction' "$TRANSACTION" \
            'Запустите sudo bash deploy.sh install-recover или install-rollback.'
    fi
else
    printf '[preflight] legacy installation без manifest; разрешена только strict migration safe installer.\n'
fi

step 'Read-only PostgreSQL discovery'
if command -v pg_lsclusters >/dev/null 2>&1 &&
    [[ -f "$POSTGRES_LIBRARY" && ! -L "$POSTGRES_LIBRARY" ]]; then
    # shellcheck source=lib/postgresql.sh
    source "$POSTGRES_LIBRARY"
    pg_select_cluster || fail \
        'PostgreSQL cluster не выбран однозначно' \
        'Укажите POSTGRES_CLUSTER=16/main.'
    [[ "$PG_STATUS" == online ]] || fail \
        'выбранный PostgreSQL cluster не online' \
        "${PG_VERSION}/${PG_CLUSTER} status=$PG_STATUS" \
        'Запустите cluster вручную и повторите; preflight сам его не запускает.'
    pg_assert_existing_database || fail 'production PostgreSQL role/database отсутствуют'
else
    fail \
        'PostgreSQL tooling отсутствует у существующей installation' \
        'pg_lsclusters/postgresql library недоступны'
fi

step 'Read-only Redis connectivity'
command -v redis-cli >/dev/null 2>&1 || fail 'redis-cli отсутствует'
redis_data=$(ENV_FILE_PATH="$ENV_FILE" python3 - <<'PY'
from pathlib import Path
from urllib.parse import unquote, urlsplit
import os

value = None
for raw in Path(os.environ["ENV_FILE_PATH"]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line.startswith("REDIS_URL="):
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
if value is None:
    raise SystemExit(1)
parsed = urlsplit(value)
print(parsed.hostname or "")
print(parsed.port or 6379)
print(parsed.path.lstrip("/") or "0")
print(unquote(parsed.password or ""))
PY
) || fail 'REDIS_URL не разобран'
mapfile -t redis_values <<<"$redis_data"
if [[ -n "${redis_values[3]:-}" ]]; then
    REDISCLI_AUTH=${redis_values[3]} redis-cli --no-auth-warning \
        -h "${redis_values[0]}" -p "${redis_values[1]}" -n "${redis_values[2]}" PING \
        2>/dev/null | grep -Fxq PONG || fail \
        'Redis PING failed' "${redis_values[0]}:${redis_values[1]}/${redis_values[2]}"
else
    redis-cli -h "${redis_values[0]}" -p "${redis_values[1]}" \
        -n "${redis_values[2]}" PING 2>/dev/null | grep -Fxq PONG || fail \
        'Redis PING failed' "${redis_values[0]}:${redis_values[1]}/${redis_values[2]}"
fi

printf '[preflight] read-only checks пройдены; сервер не изменён.\n'
