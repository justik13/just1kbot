usage() {
    cat <<'EOF_USAGE'
Just1kBot production PostgreSQL restore/cutover

Production restore/cutover:
  sudo AGE_IDENTITY_FILE=/secure/key production_restore.sh production ARTIFACT
  sudo AGE_IDENTITY_FILE=/secure/key production_restore.sh production \
    --yes --expected-sha256 SHA256 ARTIFACT

Transaction management:
  sudo production_restore.sh status
  sudo production_restore.sh rollback
  sudo production_restore.sh rollback --yes --transaction-id ID
  sudo production_restore.sh finalize
  sudo production_restore.sh finalize --yes --transaction-id ID

Production restore never overwrites .env and never drops the previous database.
After a successful cutover, run status, observe the bot, then explicitly choose
rollback or finalize.
EOF_USAGE
}

require_root() {
    (( EUID == 0 )) || { printf 'restore error: production actions require root\n' >&2; exit 1; }
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

is_safe_database_name() {
    [[ ${1:-} =~ ^[a-z][a-z0-9_]{0,62}$ ]]
}

is_sha256() {
    [[ ${1:-} =~ ^[0-9a-f]{64}$ ]]
}

parse_mutating_args() {
    local allow_artifact=$1
    shift
    while (( $# > 0 )); do
        case "$1" in
            --yes)
                ASSUME_YES=true
                ;;
            --expected-sha256)
                (( $# >= 2 )) || { printf 'missing value for --expected-sha256\n' >&2; exit 2; }
                EXPECTED_SHA256=${2,,}
                shift
                ;;
            --transaction-id)
                (( $# >= 2 )) || { printf 'missing value for --transaction-id\n' >&2; exit 2; }
                EXPECTED_TRANSACTION=$2
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            --*)
                printf 'unknown restore option: %s\n' "$1" >&2
                exit 2
                ;;
            *)
                [[ "$allow_artifact" == true && -z "$ARTIFACT" ]] || {
                    printf 'unexpected restore argument: %s\n' "$1" >&2
                    exit 2
                }
                ARTIFACT=$1
                ;;
        esac
        shift
    done
}

validate_runtime_paths() {
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "production env is missing or unsafe: $ENV_FILE"
    [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || fail "project directory is missing or unsafe: $PROJECT_DIR"
    [[ -f "$POSTGRES_LIBRARY" && ! -L "$POSTGRES_LIBRARY" ]] || fail "PostgreSQL library is missing or unsafe: $POSTGRES_LIBRARY"
    [[ -x "$VERIFY_BACKUP" && ! -L "$VERIFY_BACKUP" ]] || fail "backup verifier is missing or unsafe: $VERIFY_BACKUP"
    [[ -x "$HEALTHCHECK_COMMAND" && ! -L "$HEALTHCHECK_COMMAND" ]] || fail "healthcheck is missing or unsafe: $HEALTHCHECK_COMMAND"
    [[ -x "$VENV_DIR/bin/alembic" && ! -L "$VENV_DIR/bin/alembic" ]] || fail "Alembic executable is missing or unsafe"
    [[ -f "$PROJECT_DIR/alembic.ini" && ! -L "$PROJECT_DIR/alembic.ini" ]] || fail "alembic.ini is missing or unsafe"
}

acquire_operation_lock() {
    install -d -o root -g root -m 0755 "$(dirname "$LOCK_FILE")"
    exec 200>"$LOCK_FILE"
    flock -n 200 || fail 'another deploy/backup/restore/uninstall operation is running'
}

prepare_state_dir() {
    [[ ! -L "$STATE_DIR" ]] || fail "restore state directory is a symlink: $STATE_DIR"
    install -d -o root -g root -m 0700 "$STATE_DIR"
    [[ $(stat -c '%u' "$STATE_DIR") == 0 ]] || fail 'restore state directory is not root-owned'
    local mode
    mode=$(stat -c '%a' "$STATE_DIR")
    (( (8#$mode & 8#077) == 0 )) || fail 'restore state directory permissions are too broad'
}

read_env_contract() {
    mapfile -d '' -t LIVE_PARTS < <(
        ENV_FILE_PATH="$ENV_FILE" python3 - <<'PY'
import os
from pathlib import Path
from urllib.parse import urlsplit, unquote

path = Path(os.environ['ENV_FILE_PATH'])
values = {}
counts = {}
for raw in path.read_text(encoding='utf-8').splitlines():
    line = raw.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    counts[key] = counts.get(key, 0) + 1
    values[key] = value
for key in ('DATABASE_URL', 'DB_ENCRYPTION_KEY'):
    if counts.get(key) != 1 or not values.get(key):
        raise SystemExit(f'expected exactly one non-empty {key}')
url = values['DATABASE_URL']
parsed = urlsplit(url)
if parsed.scheme != 'postgresql+asyncpg':
    raise SystemExit('DATABASE_URL scheme mismatch')
if parsed.hostname not in {'127.0.0.1', 'localhost'}:
    raise SystemExit('DATABASE_URL must use a local host')
try:
    port = parsed.port
except ValueError as exc:
    raise SystemExit('invalid DATABASE_URL port') from exc
if port is None or not parsed.username or not parsed.password or not parsed.path.lstrip('/'):
    raise SystemExit('DATABASE_URL is incomplete')
for value in (
    url,
    parsed.hostname,
    str(port),
    unquote(parsed.username),
    parsed.path.lstrip('/'),
):
    print(value, end='\0')
PY
    )
    (( ${#LIVE_PARTS[@]} == 5 )) || fail 'could not parse production database configuration'
    LIVE_DATABASE_URL=${LIVE_PARTS[0]}
    LIVE_HOST=${LIVE_PARTS[1]}
    LIVE_PORT=${LIVE_PARTS[2]}
    LIVE_ROLE=${LIVE_PARTS[3]}
    LIVE_DATABASE=${LIVE_PARTS[4]}

    [[ "$LIVE_DATABASE" == just1kbot_bot ]] || fail "unexpected production database: $LIVE_DATABASE"
    [[ "$LIVE_ROLE" == just1kbot ]] || fail "unexpected production role: $LIVE_ROLE"
}

load_postgresql_library() {
    # shellcheck source=/opt/just1kbot/scripts/lib/postgresql.sh
    source "$POSTGRES_LIBRARY"
    PG_DATABASE=$LIVE_DATABASE
    PG_ROLE=$LIVE_ROLE
    pg_prepare update
    pg_repair_env_port
    # pg_repair_env_port may have atomically corrected an obsolete hard-coded port.
    # Re-read the live contract before comparing it with the selected cluster.
    read_env_contract
    [[ "$PG_PORT" == "$LIVE_PORT" ]] || fail "selected PostgreSQL port $PG_PORT does not match .env port $LIVE_PORT"
}

admin_psql() {
    runuser -u postgres -- psql -X -A -t -q -v ON_ERROR_STOP=1 \
        -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres "$@"
}

admin_createdb() {
    runuser -u postgres -- createdb -h "$PG_SOCKET_DIR" -p "$PG_PORT" --owner="$LIVE_ROLE" "$1"
}

admin_dropdb() {
    runuser -u postgres -- dropdb -h "$PG_SOCKET_DIR" -p "$PG_PORT" --force --if-exists --maintenance-db=postgres "$1"
}

database_exists() {
    local result
    result=$(admin_psql -v db_name="$1" <<'SQL'
SELECT 1
FROM pg_database
WHERE datname = :'db_name';
SQL
    ) || return 2
    [[ "$result" == 1 ]]
}

assert_database_absent() {
    local name=$1 rc
    if database_exists "$name"; then
        fail "database already exists: $name"
        return 1
    else
        rc=$?
    fi
    (( rc == 1 )) || { fail "could not verify database absence: $name"; return 1; }
}

database_allow_connections() {
    local db=$1 allowed=$2
    [[ "$allowed" == true || "$allowed" == false ]] || return 64
    admin_psql -v db_name="$db" -v allowed="$allowed" >/dev/null <<'SQL'
ALTER DATABASE :"db_name" WITH ALLOW_CONNECTIONS = :allowed;
SQL
}

terminate_database_connections() {
    admin_psql -v db_name="$1" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'db_name'
  AND pid <> pg_backend_pid();
SQL
}

rename_database() {
    local old=$1 new=$2
    is_safe_database_name "$old" && is_safe_database_name "$new" || return 64
    admin_psql -v old_name="$old" -v new_name="$new" >/dev/null <<'SQL'
ALTER DATABASE :"old_name" RENAME TO :"new_name";
SQL
}

set_database_owner() {
    admin_psql -v db_name="$1" -v role_name="$LIVE_ROLE" >/dev/null <<'SQL'
ALTER DATABASE :"db_name" OWNER TO :"role_name";
SQL
}

make_database_url() {
    LIVE_DATABASE_URL_VALUE="$LIVE_DATABASE_URL" TARGET_DATABASE="$1" python3 - <<'PY'
import os
from urllib.parse import urlsplit, urlunsplit
url = os.environ['LIVE_DATABASE_URL_VALUE']
target = os.environ['TARGET_DATABASE']
p = urlsplit(url)
print(urlunsplit((p.scheme, p.netloc, '/' + target, p.query, p.fragment)))
PY
}

extract_and_verify_backup() {
    [[ -f "$ARTIFACT" && ! -L "$ARTIFACT" ]] || fail "backup artifact is missing or unsafe: $ARTIFACT"
    [[ $(basename -- "$ARTIFACT") =~ ^just1kbot-pg-v1-[0-9]{8}T[0-9]{6}Z[.]tar[.]age$ ]] || fail 'backup artifact basename does not match the canonical format'
    [[ -f "$ARTIFACT.sha256" && ! -L "$ARTIFACT.sha256" ]] || fail 'matching .sha256 sidecar is missing or unsafe'
    [[ -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE ]] || fail 'AGE_IDENTITY_FILE is missing or unsafe'

    WORK_DIR=$(mktemp -d "$STATE_DIR/.restore-work.XXXXXX")
    chmod 0700 "$WORK_DIR"
    "$VERIFY_BACKUP" --extract-dir "$WORK_DIR/verified" "$ARTIFACT" >/dev/null

    [[ -d /var/lib/postgresql && ! -L /var/lib/postgresql ]] || fail 'PostgreSQL private workspace parent is missing or unsafe'
    POSTGRES_WORK_DIR=$(mktemp -d /var/lib/postgresql/just1kbot-production-restore.XXXXXX)
    chown postgres:postgres "$POSTGRES_WORK_DIR"
    chmod 0700 "$POSTGRES_WORK_DIR"
    install -o postgres -g postgres -m 0600 \
        "$WORK_DIR/verified/dump.custom" "$POSTGRES_WORK_DIR/dump.custom"

    ARTIFACT_SHA256=$(sha256sum "$ARTIFACT" | awk '{print $1}')
    is_sha256 "$ARTIFACT_SHA256" || fail 'artifact SHA-256 has an invalid format'

    mapfile -d '' -t MANIFEST_PARTS < <(
        MANIFEST_PATH="$WORK_DIR/verified/manifest.json" python3 - <<'PY'
import json, os, re
m = json.loads(open(os.environ['MANIFEST_PATH'], encoding='utf-8').read())
name = m.get('database_name')
created = m.get('created_at_utc')
revision = m.get('alembic_revision')
if name != 'just1kbot_bot':
    raise SystemExit('backup database_name is not just1kbot_bot')
if not isinstance(created, str) or not re.fullmatch(r'\d{8}T\d{6}Z', created):
    raise SystemExit('invalid backup created_at_utc')
if not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', revision):
    raise SystemExit('invalid backup Alembic revision')
for value in (name, created, revision):
    print(value, end='\0')
PY
    )
    (( ${#MANIFEST_PARTS[@]} == 3 )) || fail 'could not validate backup manifest'
    BACKUP_CREATED_AT=${MANIFEST_PARTS[1]}
    BACKUP_REVISION=${MANIFEST_PARTS[2]}

    CURRENT_ENV="$ENV_FILE" BACKUP_ENV="$WORK_DIR/verified/config.env" python3 - <<'PY'
import hmac, os
from pathlib import Path

def one(path, key):
    found=[]
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        line=raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k,v=line.split('=',1)
        if k.strip()!=key:
            continue
        v=v.strip()
        if len(v)>=2 and v[0]==v[-1] and v[0] in "'\"":
            v=v[1:-1]
        found.append(v)
    if len(found)!=1 or not found[0]:
        raise SystemExit(f'expected exactly one non-empty {key}')
    return found[0]
if not hmac.compare_digest(one(os.environ['CURRENT_ENV'],'DB_ENCRYPTION_KEY'), one(os.environ['BACKUP_ENV'],'DB_ENCRYPTION_KEY')):
    raise SystemExit('backup DB_ENCRYPTION_KEY does not match production')
PY
}

check_free_space() {
    local production_size dump_size free required
    production_size=$(admin_psql -v db_name="$LIVE_DATABASE" <<'SQL'
SELECT pg_database_size(:'db_name');
SQL
    )
    [[ "$production_size" =~ ^[0-9]+$ ]] || fail 'could not determine production database size'
    dump_size=$(stat -c '%s' "$WORK_DIR/verified/dump.custom")
    free=$(df -PB1 "$PG_DATA_DIR" | awk 'NR==2 {print $4}')
    [[ "$dump_size" =~ ^[0-9]+$ && "$free" =~ ^[0-9]+$ ]] || fail 'could not determine restore disk-space requirements'
    required=$(( production_size > dump_size * 6 ? production_size : dump_size * 6 ))
    required=$(( required + MIN_FREE_MARGIN_BYTES ))
    (( free >= required )) || fail "insufficient free space: free=$free required_at_least=$required"
    log "disk preflight passed: free=$free required_at_least=$required"
}

new_transaction_names() {
    local stamp
    stamp=$(date -u +%Y%m%d%H%M%S)
    TRANSACTION_ID="${stamp}_$$"
    STAGING_DB="just1kbot_stg_${TRANSACTION_ID}"
    ROLLBACK_DB="just1kbot_rb_${TRANSACTION_ID}"
    FAILED_DB="just1kbot_fail_${TRANSACTION_ID}"
    is_safe_database_name "$STAGING_DB" && is_safe_database_name "$ROLLBACK_DB" && is_safe_database_name "$FAILED_DB" || fail 'generated database name is unsafe'
    assert_database_absent "$STAGING_DB"
    assert_database_absent "$ROLLBACK_DB"
    assert_database_absent "$FAILED_DB"
    return 0
}

