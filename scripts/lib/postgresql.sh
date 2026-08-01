#!/bin/bash
# PostgreSQL helpers for Just1kBot deploy.
# This library never drops, recreates, resets, upgrades, or changes the port
# of an existing PostgreSQL cluster.

set -uo pipefail

: "${ENV_FILE:=/opt/just1kbot/.env}"
: "${PG_DATABASE:=just1kbot_bot}"
: "${PG_ROLE:=just1kbot}"
: "${PG_SOCKET_DIR:=/var/run/postgresql}"
: "${PG_START_TIMEOUT:=30}"

PG_VERSION=""
PG_CLUSTER=""
PG_PORT=""
PG_STATUS=""
PG_OWNER=""
PG_DATA_DIR=""
PG_UNIT=""

pg_log() {
    if declare -F log >/dev/null 2>&1; then
        log "$1"
    else
        printf '[postgres] %s\n' "$1"
    fi
}

pg_error() {
    if declare -F error >/dev/null 2>&1; then
        error "$1"
    else
        printf '[postgres] ERROR: %s\n' "$1" >&2
    fi
}

pg_require_commands() {
    local name
    for name in pg_lsclusters pg_ctlcluster pg_isready psql createdb runuser python3 systemctl; do
        command -v "$name" >/dev/null 2>&1 || {
            pg_error "Не найдена обязательная команда: $name"
            return 1
        }
    done
}

pg_cluster_rows() {
    pg_lsclusters --no-header 2>/dev/null |
        awk 'NF >= 6 {print $1 "\t" $2 "\t" $3 "\t" $4 "\t" $5 "\t" $6}'
}

pg_env_port() {
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || return 1

    ENV_FILE_PATH="$ENV_FILE" python3 - <<'PY'
import os
from pathlib import Path
from urllib.parse import urlsplit

values = []
for raw in Path(os.environ["ENV_FILE_PATH"]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != "DATABASE_URL":
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    values.append(value)

if len(values) != 1:
    raise SystemExit(1)

parsed = urlsplit(values[0])
if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
    raise SystemExit(1)
try:
    port = parsed.port
except ValueError:
    raise SystemExit(1)
if port is None:
    raise SystemExit(1)
print(port)
PY
}

pg_database_exists_on_port() {
    local port=$1
    local result

    pg_isready -q -h "$PG_SOCKET_DIR" -p "$port" -d postgres -t 2 || return 1

    result=$(
        runuser -u postgres -- \
            psql -X -A -t -q -v ON_ERROR_STOP=1 \
            -v database_name="$PG_DATABASE" \
            -h "$PG_SOCKET_DIR" -p "$port" -d postgres \
            2>/dev/null <<'SQL'
SELECT 1
FROM pg_database
WHERE datname = :'database_name';
SQL
    ) || return 1

    [[ "$result" == 1 ]]
}

pg_set_cluster() {
    PG_VERSION=$1
    PG_CLUSTER=$2
    PG_PORT=$3
    PG_STATUS=$4
    PG_OWNER=$5
    PG_DATA_DIR=$6
    PG_UNIT="postgresql@${PG_VERSION}-${PG_CLUSTER}.service"
}

pg_print_available_clusters() {
    local version cluster port status owner data_dir
    while IFS=$'\t' read -r version cluster port status owner data_dir; do
        [[ -n "$version" ]] || continue
        printf '  %s/%s port=%s status=%s owner=%s data=%s\n' \
            "$version" "$cluster" "$port" "$status" "$owner" "$data_dir" >&2
    done < <(pg_cluster_rows)
}

pg_select_cluster() {
    pg_require_commands || return 1

    local -a rows=()
    local -a matches=()
    local row version cluster port status owner data_dir
    local requested="${POSTGRES_CLUSTER:-}"
    local requested_version="" requested_cluster="" env_port=""

    mapfile -t rows < <(pg_cluster_rows)
    if (( ${#rows[@]} == 0 )); then
        pg_error "PostgreSQL-кластеры не найдены"
        return 1
    fi

    if [[ -n "$requested" ]]; then
        if [[ ! "$requested" =~ ^([0-9]+([.][0-9]+)?)/([A-Za-z0-9_-]+)$ ]]; then
            pg_error "POSTGRES_CLUSTER должен иметь формат 16/main"
            return 1
        fi
        requested_version=${BASH_REMATCH[1]}
        requested_cluster=${BASH_REMATCH[3]}

        for row in "${rows[@]}"; do
            IFS=$'\t' read -r version cluster port status owner data_dir <<<"$row"
            if [[ "$version" == "$requested_version" && "$cluster" == "$requested_cluster" ]]; then
                matches+=("$row")
            fi
        done
    elif (( ${#rows[@]} == 1 )); then
        matches=("${rows[0]}")
    else
        env_port=$(pg_env_port 2>/dev/null || true)
        if [[ "$env_port" =~ ^[1-9][0-9]{0,4}$ ]] && (( env_port <= 65535 )); then
            for row in "${rows[@]}"; do
                IFS=$'\t' read -r version cluster port status owner data_dir <<<"$row"
                [[ "$port" == "$env_port" ]] && matches+=("$row")
            done
        fi

        if (( ${#matches[@]} != 1 )); then
            matches=()
            for row in "${rows[@]}"; do
                IFS=$'\t' read -r version cluster port status owner data_dir <<<"$row"
                if [[ "$status" == online ]] && pg_database_exists_on_port "$port"; then
                    matches+=("$row")
                fi
            done
        fi
    fi

    if (( ${#matches[@]} != 1 )); then
        pg_error "Нельзя однозначно выбрать PostgreSQL-кластер"
        pg_error "Укажите его явно: POSTGRES_CLUSTER=16/main"
        pg_print_available_clusters
        return 1
    fi

    IFS=$'\t' read -r version cluster port status owner data_dir <<<"${matches[0]}"

    [[ "$owner" == postgres ]] || {
        pg_error "Неожиданный владелец PostgreSQL-кластера: $owner"
        return 1
    }
    [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || {
        pg_error "Некорректный порт PostgreSQL: $port"
        return 1
    }
    [[ -d "$data_dir" && ! -L "$data_dir" ]] || {
        pg_error "Data directory отсутствует или является symlink: $data_dir"
        return 1
    }
    [[ -f "$data_dir/PG_VERSION" && ! -L "$data_dir/PG_VERSION" ]] || {
        pg_error "В data directory отсутствует безопасный PG_VERSION"
        return 1
    }
    [[ "$(<"$data_dir/PG_VERSION")" == "$version" ]] || {
        pg_error "PG_VERSION не совпадает с выбранным кластером"
        return 1
    }

    pg_set_cluster "$version" "$cluster" "$port" "$status" "$owner" "$data_dir"
    pg_log "Выбран PostgreSQL-кластер ${PG_VERSION}/${PG_CLUSTER}, порт ${PG_PORT}"
}

pg_refresh_cluster() {
    local version cluster port status owner data_dir
    while IFS=$'\t' read -r version cluster port status owner data_dir; do
        if [[ "$version" == "$PG_VERSION" && "$cluster" == "$PG_CLUSTER" ]]; then
            pg_set_cluster "$version" "$cluster" "$port" "$status" "$owner" "$data_dir"
            return 0
        fi
    done < <(pg_cluster_rows)

    pg_error "Выбранный PostgreSQL-кластер исчез из pg_lsclusters"
    return 1
}

pg_start_cluster() {
    [[ -n "$PG_VERSION" ]] || {
        pg_error "Сначала вызовите pg_select_cluster"
        return 1
    }

    if [[ "$PG_STATUS" != online ]]; then
        pg_log "Кластер status=$PG_STATUS; выполняется безопасный start без изменения данных"
        systemctl reset-failed "$PG_UNIT" >/dev/null 2>&1 || true

        if ! pg_ctlcluster "$PG_VERSION" "$PG_CLUSTER" start; then
            systemctl status "$PG_UNIT" --no-pager --lines=80 >&2 2>/dev/null || true
            journalctl -u "$PG_UNIT" -n 120 --no-pager >&2 2>/dev/null || true
            pg_error "Не удалось запустить существующий PostgreSQL-кластер"
            return 1
        fi
    fi

    local deadline=$(( $(date +%s) + PG_START_TIMEOUT ))
    while (( $(date +%s) <= deadline )); do
        pg_isready -q -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres -t 2 && break
        sleep 1
    done

    pg_isready -q -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres -t 2 || {
        pg_error "PostgreSQL не принимает соединения на порту $PG_PORT"
        return 1
    }

    pg_refresh_cluster || return 1
    [[ "$PG_STATUS" == online ]] || {
        pg_error "pg_lsclusters не подтверждает status=online"
        return 1
    }
}

pg_role_exists() {
    local result
    result=$(
        runuser -u postgres -- \
            psql -X -A -t -q -v ON_ERROR_STOP=1 \
            -v role_name="$PG_ROLE" \
            -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres <<'SQL'
SELECT 1
FROM pg_roles
WHERE rolname = :'role_name';
SQL
    ) || return 1
    [[ "$result" == 1 ]]
}

pg_database_exists() {
    pg_database_exists_on_port "$PG_PORT"
}

pg_assert_existing_database() {
    pg_database_exists || {
        pg_error "Database $PG_DATABASE не найдена"
        pg_error "Update не создаёт и не пересоздаёт production database"
        return 1
    }
    pg_role_exists || {
        pg_error "Role $PG_ROLE не найдена"
        pg_error "Update не создаёт и не пересоздаёт production role"
        return 1
    }
    pg_log "Существующая database $PG_DATABASE найдена; пересоздание запрещено"
}

pg_prepare_initial_database() {
    [[ -n "${DB_PASSWORD:-}" ]] || {
        pg_error "DB_PASSWORD не задан"
        return 1
    }
    [[ "$DB_PASSWORD" =~ ^[A-Za-z0-9_@%*+=-]{8,}$ ]] || {
        pg_error "DB_PASSWORD имеет неверный формат"
        return 1
    }

    if pg_role_exists; then
        runuser -u postgres -- \
            psql -X -q -v ON_ERROR_STOP=1 \
            -v role_name="$PG_ROLE" -v role_password="$DB_PASSWORD" \
            -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres \
            >/dev/null <<'SQL'
ALTER ROLE :"role_name" WITH LOGIN PASSWORD :'role_password';
SQL
    else
        runuser -u postgres -- \
            psql -X -q -v ON_ERROR_STOP=1 \
            -v role_name="$PG_ROLE" -v role_password="$DB_PASSWORD" \
            -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres \
            >/dev/null <<'SQL'
CREATE ROLE :"role_name" WITH LOGIN PASSWORD :'role_password';
SQL
    fi

    if pg_database_exists; then
        pg_log "Database $PG_DATABASE уже существует; пересоздание не выполняется"
    else
        runuser -u postgres -- \
            createdb -h "$PG_SOCKET_DIR" -p "$PG_PORT" \
            --owner="$PG_ROLE" "$PG_DATABASE"
    fi

    pg_assert_existing_database
}

pg_repair_env_port() {
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
        pg_error "Production .env отсутствует или является symlink"
        return 1
    }

    local result
    result=$(
        ENV_FILE_PATH="$ENV_FILE" \
        EXPECTED_PORT="$PG_PORT" \
        EXPECTED_DATABASE="$PG_DATABASE" \
        EXPECTED_ROLE="$PG_ROLE" \
        python3 - <<'PY'
import os
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

path = Path(os.environ["ENV_FILE_PATH"])
expected_port = int(os.environ["EXPECTED_PORT"])
expected_database = os.environ["EXPECTED_DATABASE"]
expected_role = os.environ["EXPECTED_ROLE"]
metadata = path.stat()

if not stat.S_ISREG(metadata.st_mode):
    raise SystemExit("env is not a regular file")

lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
matches = []
for index, raw in enumerate(lines):
    stripped = raw.lstrip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, raw_value = stripped.split("=", 1)
    if key.strip() == "DATABASE_URL":
        matches.append((index, raw, raw_value))

if len(matches) != 1:
    raise SystemExit(f"expected exactly one DATABASE_URL, found {len(matches)}")

index, raw_line, raw_value = matches[0]
if raw_value.endswith("\r\n"):
    newline, body = "\r\n", raw_value[:-2]
elif raw_value.endswith("\n"):
    newline, body = "\n", raw_value[:-1]
else:
    newline, body = "", raw_value

leading = body[: len(body) - len(body.lstrip())]
trailing = body[len(body.rstrip()):]
token = body.strip()
quote = ""
if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
    quote, value = token[0], token[1:-1]
else:
    value = token

parsed = urlsplit(value)
if parsed.scheme != "postgresql+asyncpg":
    raise SystemExit("unexpected DATABASE_URL scheme")
if parsed.hostname not in {"127.0.0.1", "localhost"}:
    raise SystemExit("DATABASE_URL host must be local")
if parsed.username != expected_role:
    raise SystemExit("DATABASE_URL role mismatch")
if parsed.path.lstrip("/") != expected_database:
    raise SystemExit("DATABASE_URL database mismatch")
try:
    old_port = parsed.port
except ValueError as exc:
    raise SystemExit("invalid DATABASE_URL port") from exc
if old_port is None or "@" not in parsed.netloc:
    raise SystemExit("DATABASE_URL port or credentials missing")

if old_port == expected_port:
    print(f"unchanged:{old_port}")
    raise SystemExit(0)

host_prefix, port_text = parsed.netloc.rsplit(":", 1)
if port_text != str(old_port):
    raise SystemExit("unexpected port representation")

old_prefix = f"{parsed.scheme}://{parsed.netloc}"
new_prefix = f"{parsed.scheme}://{host_prefix}:{expected_port}"
if not value.startswith(old_prefix):
    raise SystemExit("DATABASE_URL cannot be changed safely")

updated = value.replace(old_prefix, new_prefix, 1)
before_equals = raw_line.split("=", 1)[0]
lines[index] = (
    f"{before_equals}={leading}{quote}{updated}{quote}{trailing}{newline}"
)

fd, temporary_name = tempfile.mkstemp(
    prefix=".env.pg-port.", dir=path.parent, text=True
)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as temporary:
        temporary.writelines(lines)
        temporary.flush()
        os.fsync(temporary.fileno())
    os.chmod(temporary_name, stat.S_IMODE(metadata.st_mode))
    os.chown(temporary_name, metadata.st_uid, metadata.st_gid)
    os.replace(temporary_name, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise

print(f"changed:{old_port}:{expected_port}")
PY
    ) || {
        pg_error "Не удалось атомарно изменить только порт DATABASE_URL"
        return 1
    }

    case "$result" in
        unchanged:*)
            pg_log "DATABASE_URL уже использует порт $PG_PORT"
            ;;
        changed:*)
            local old_port=${result#changed:}
            old_port=${old_port%%:*}
            pg_log "В DATABASE_URL изменён только порт: $old_port -> $PG_PORT"
            ;;
        *)
            pg_error "Неожиданный результат изменения DATABASE_URL"
            return 1
            ;;
    esac
}

pg_prepare() {
    local mode=${1:-}
    [[ "$mode" == initial || "$mode" == update ]] || {
        pg_error "pg_prepare ожидает initial или update"
        return 64
    }

    pg_select_cluster || return 1
    pg_start_cluster || return 1

    if [[ "$mode" == initial ]]; then
        pg_prepare_initial_database
    else
        pg_assert_existing_database
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'Этот файл является библиотекой и не запускается напрямую.\n' >&2
    exit 64
fi
