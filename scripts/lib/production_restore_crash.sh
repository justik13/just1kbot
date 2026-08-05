# Bounded-validation and crash-consistency overrides loaded after the base
# restore runtime/actions.

validate_root_owned_regular_file() {
    local path=$1 label=$2 owner mode
    [[ -f "$path" && ! -L "$path" ]] || fail "$label is missing, not regular, or is a symlink: $path"
    owner=$(stat -c '%u' "$path") || return 1
    mode=$(stat -c '%a' "$path") || return 1
    if (( (8#$mode & 8#022) != 0 )) && (( ${EUID:-$(id -u)} == 0 )); then
        chmod go-w "$path" 2>/dev/null || true
        mode=$(stat -c '%a' "$path") || return 1
    fi
    [[ "$owner" == 0 ]] || fail "$label is not root-owned: $path"
    (( (8#$mode & 8#022) == 0 )) || (( ${EUID:-$(id -u)} == 0 )) || fail "$label is writable by group/other: $path mode=$mode"
}

validate_root_owned_directory() {
    local path=$1 label=$2 owner mode
    [[ -d "$path" && ! -L "$path" ]] || fail "$label is missing, not a directory, or is a symlink: $path"
    owner=$(stat -c '%u' "$path") || return 1
    mode=$(stat -c '%a' "$path") || return 1
    if (( (8#$mode & 8#022) != 0 )) && (( ${EUID:-$(id -u)} == 0 )); then
        chmod go-w "$path" 2>/dev/null || true
        mode=$(stat -c '%a' "$path") || return 1
    fi
    [[ "$owner" == 0 ]] || fail "$label is not root-owned: $path"
    (( (8#$mode & 8#022) == 0 )) || (( ${EUID:-$(id -u)} == 0 )) || fail "$label is writable by group/other: $path mode=$mode"
}

validate_runtime_paths() {
    validate_root_owned_regular_file "$ENV_FILE" 'production env'
    validate_root_owned_directory "$PROJECT_DIR" 'project directory'
    validate_root_owned_regular_file "$POSTGRES_LIBRARY" 'PostgreSQL library'
    validate_root_owned_regular_file "$VERIFY_BACKUP" 'backup verifier'
    validate_root_owned_regular_file "$HEALTHCHECK_COMMAND" 'healthcheck'
    validate_root_owned_regular_file "$VENV_DIR/bin/alembic" 'Alembic executable'
    [[ -x "$VENV_DIR/bin/alembic" ]] || fail "Alembic executable is not executable: $VENV_DIR/bin/alembic"
    validate_root_owned_regular_file "$PROJECT_DIR/alembic.ini" 'alembic.ini'
}

sync_file_and_parent() {
    local path=$1
    SYNC_PATH="$path" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ['SYNC_PATH'])
with path.open('rb') as handle:
    os.fsync(handle.fileno())
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

sync_parent_directory() {
    local path=$1
    SYNC_PATH="$path" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ['SYNC_PATH'])
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

write_active_state() {
    local status=$1 failed_db=${2:-}
    local temp="$ACTIVE_STATE.tmp.$$"
    cat > "$temp" <<EOF_STATE
format_version=1
status=$status
transaction_id=$TRANSACTION_ID
production_database=$LIVE_DATABASE
rollback_database=$ROLLBACK_DB
failed_database=$failed_db
artifact_name=$(basename -- "$ARTIFACT")
artifact_sha256=$ARTIFACT_SHA256
backup_created_at_utc=$BACKUP_CREATED_AT
cutover_at_utc=$(date -u +%FT%TZ)
pre_cutover_backup=$PRE_CUTOVER_BACKUP
EOF_STATE
    chmod 0600 "$temp"
    chown root:root "$temp"
    sync_file_and_parent "$temp"
    mv -f -- "$temp" "$ACTIVE_STATE"
    sync_file_and_parent "$ACTIVE_STATE"
}

archive_active_state() {
    local final_status=$1
    local archived="$STATE_DIR/${STATE_TRANSACTION_ID}.${final_status}.env"
    [[ ! -e "$archived" && ! -L "$archived" ]] || fail "restore archive already exists: $archived"
    local temp="$STATE_DIR/.archive.$$"
    sed "s/^status=.*/status=$final_status/" "$ACTIVE_STATE" > "$temp"
    chmod 0600 "$temp"
    chown root:root "$temp"
    sync_file_and_parent "$temp"
    mv -- "$temp" "$archived"
    sync_file_and_parent "$archived"
    rm -f -- "$ACTIVE_STATE"
    sync_parent_directory "$ACTIVE_STATE"
}

pause_runtime() {
    SERVICE_WAS_ACTIVE=false
    BACKUP_TIMER_WAS_ACTIVE=false
    HEALTH_TIMER_WAS_ACTIVE=false
    unit_active "$SERVICE_NAME" && SERVICE_WAS_ACTIVE=true
    unit_active "$BACKUP_TIMER" && BACKUP_TIMER_WAS_ACTIVE=true
    unit_active "$HEALTH_TIMER" && HEALTH_TIMER_WAS_ACTIVE=true
    RUNTIME_PAUSED=true

    systemctl stop "$BACKUP_TIMER" >/dev/null 2>&1 || true
    systemctl stop "$HEALTH_TIMER" >/dev/null 2>&1 || true
    ! unit_active "$BACKUP_TIMER" || fail "backup timer did not stop: $BACKUP_TIMER"
    ! unit_active "$HEALTH_TIMER" || fail "health timer did not stop: $HEALTH_TIMER"
    wait_for_unit_inactive "$BACKUP_SERVICE" 180 || fail 'an existing backup service did not finish in 180 seconds'

    systemctl stop "$SERVICE_NAME"
    local deadline=$(( $(date +%s) + 60 ))
    while unit_active "$SERVICE_NAME" && (( $(date +%s) <= deadline )); do
        sleep 1
    done
    ! unit_active "$SERVICE_NAME" || fail 'application service did not stop'
}

restore_timer_states() {
    local rc=0
    if [[ "$BACKUP_TIMER_WAS_ACTIVE" == true ]]; then
        systemctl start "$BACKUP_TIMER" >/dev/null 2>&1 || rc=1
        unit_active "$BACKUP_TIMER" || rc=1
    else
        systemctl stop "$BACKUP_TIMER" >/dev/null 2>&1 || true
        ! unit_active "$BACKUP_TIMER" || rc=1
    fi

    if [[ "$HEALTH_TIMER_WAS_ACTIVE" == true ]]; then
        systemctl start "$HEALTH_TIMER" >/dev/null 2>&1 || rc=1
        unit_active "$HEALTH_TIMER" || rc=1
    else
        systemctl stop "$HEALTH_TIMER" >/dev/null 2>&1 || true
        ! unit_active "$HEALTH_TIMER" || rc=1
    fi
    return "$rc"
}

write_cutover_journal() {
    local operation=$1 phase=$2
    local temp="$JOURNAL_STATE.tmp.$$" artifact_name
    artifact_name=$(basename -- "$ARTIFACT")

    [[ "$operation" =~ ^(production|rollback_previous|manual_return)$ ]] || fail 'invalid restore journal operation'
    [[ "$phase" =~ ^[a-z][a-z0-9_]*$ ]] || fail 'invalid restore journal phase'
    is_safe_database_name "$LIVE_DATABASE" || fail 'invalid live database for restore journal'
    [[ -z "$STAGING_DB" ]] || is_safe_database_name "$STAGING_DB" || fail 'invalid staging database for restore journal'
    is_safe_database_name "$ROLLBACK_DB" || fail 'invalid rollback database for restore journal'
    [[ -z "$FAILED_DB" ]] || is_safe_database_name "$FAILED_DB" || fail 'invalid failed database for restore journal'
    [[ "$TRANSACTION_ID" =~ ^[0-9]{14}_[0-9]+$ ]] || fail 'invalid transaction id for restore journal'
    [[ "$artifact_name" =~ ^just1kbot-pg-v1-[0-9]{8}T[0-9]{6}Z[.]tar[.]age$ ]] || fail 'invalid artifact name for restore journal'
    is_sha256 "$ARTIFACT_SHA256" || fail 'invalid artifact checksum for restore journal'
    [[ "$BACKUP_CREATED_AT" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || fail 'invalid backup timestamp for restore journal'
    [[ "$PRE_CUTOVER_BACKUP" =~ ^just1kbot-pg-v1-[0-9]{8}T[0-9]{6}Z[.]tar[.]age$ ]] || fail 'invalid safety backup for restore journal'

    cat > "$temp" <<EOF_JOURNAL
format_version=1
operation=$operation
phase=$phase
transaction_id=$TRANSACTION_ID
live_database=$LIVE_DATABASE
staging_database=$STAGING_DB
rollback_database=$ROLLBACK_DB
failed_database=$FAILED_DB
artifact_name=$artifact_name
artifact_sha256=$ARTIFACT_SHA256
backup_created_at_utc=$BACKUP_CREATED_AT
pre_cutover_backup=$PRE_CUTOVER_BACKUP
EOF_JOURNAL
    chmod 0600 "$temp"
    chown root:root "$temp"
    sync_file_and_parent "$temp"
    mv -f -- "$temp" "$JOURNAL_STATE"
    sync_file_and_parent "$JOURNAL_STATE"
    JOURNAL_OPERATION=$operation
    JOURNAL_PHASE=$phase
}

begin_cutover_journal() {
    local operation=$1 phase=$2
    [[ ! -e "$JOURNAL_STATE" && ! -L "$JOURNAL_STATE" ]] || fail 'an interrupted restore journal already exists; run recover first'
    write_cutover_journal "$operation" "$phase"
}

update_cutover_journal() {
    local phase=$1
    [[ -n "$JOURNAL_OPERATION" ]] || fail 'restore journal operation is not initialized'
    write_cutover_journal "$JOURNAL_OPERATION" "$phase"
}

clear_cutover_journal() {
    rm -f -- "$JOURNAL_STATE"
    sync_parent_directory "$JOURNAL_STATE"
    JOURNAL_OPERATION=""
    JOURNAL_PHASE=""
}

load_cutover_journal() {
    [[ -f "$JOURNAL_STATE" && ! -L "$JOURNAL_STATE" ]] || fail 'no interrupted restore journal'
    [[ $(stat -c '%u' "$JOURNAL_STATE") == 0 ]] || fail 'restore journal is not root-owned'
    local mode
    mode=$(stat -c '%a' "$JOURNAL_STATE")
    (( (8#$mode & 8#077) == 0 )) || fail 'restore journal permissions are too broad'

    mapfile -d '' -t JOURNAL_PARTS < <(
        JOURNAL_PATH="$JOURNAL_STATE" python3 - <<'PY'
import os
import re
from pathlib import Path

allowed = {
    'format_version', 'operation', 'phase', 'transaction_id', 'live_database',
    'staging_database', 'rollback_database', 'failed_database', 'artifact_name',
    'artifact_sha256', 'backup_created_at_utc', 'pre_cutover_backup',
}
values = {}
for raw in Path(os.environ['JOURNAL_PATH']).read_text(encoding='utf-8').splitlines():
    if not raw or '=' not in raw:
        raise SystemExit('invalid restore journal line')
    key, value = raw.split('=', 1)
    if key not in allowed or key in values or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SystemExit('invalid restore journal schema')
    values[key] = value
if set(values) != allowed or values['format_version'] != '1':
    raise SystemExit('unsupported restore journal')
if values['operation'] not in {'production', 'rollback_previous', 'manual_return'}:
    raise SystemExit('invalid restore journal operation')
if not re.fullmatch(r'[a-z][a-z0-9_]*', values['phase']):
    raise SystemExit('invalid restore journal phase')
if not re.fullmatch(r'\d{14}_\d+', values['transaction_id']):
    raise SystemExit('invalid restore journal transaction id')
for key in ('live_database', 'rollback_database'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values[key]):
        raise SystemExit('invalid restore journal database')
for key in ('staging_database', 'failed_database'):
    if values[key] and not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values[key]):
        raise SystemExit('invalid optional restore journal database')
if not re.fullmatch(r'just1kbot-pg-v1-\d{8}T\d{6}Z[.]tar[.]age', values['artifact_name']):
    raise SystemExit('invalid restore journal artifact')
if not re.fullmatch(r'[0-9a-f]{64}', values['artifact_sha256']):
    raise SystemExit('invalid restore journal checksum')
if not re.fullmatch(r'\d{8}T\d{6}Z', values['backup_created_at_utc']):
    raise SystemExit('invalid restore journal backup timestamp')
if not re.fullmatch(r'just1kbot-pg-v1-\d{8}T\d{6}Z[.]tar[.]age', values['pre_cutover_backup']):
    raise SystemExit('invalid restore journal safety backup')
for key in (
    'operation', 'phase', 'transaction_id', 'live_database', 'staging_database',
    'rollback_database', 'failed_database', 'artifact_name', 'artifact_sha256',
    'backup_created_at_utc', 'pre_cutover_backup',
):
    print(values[key], end='\0')
PY
    )
    (( ${#JOURNAL_PARTS[@]} == 11 )) || fail 'could not parse restore journal'
    JOURNAL_OPERATION=${JOURNAL_PARTS[0]}
    JOURNAL_PHASE=${JOURNAL_PARTS[1]}
    JOURNAL_TRANSACTION_ID=${JOURNAL_PARTS[2]}
    JOURNAL_LIVE_DB=${JOURNAL_PARTS[3]}
    JOURNAL_STAGING_DB=${JOURNAL_PARTS[4]}
    JOURNAL_ROLLBACK_DB=${JOURNAL_PARTS[5]}
    JOURNAL_FAILED_DB=${JOURNAL_PARTS[6]}
    JOURNAL_ARTIFACT_NAME=${JOURNAL_PARTS[7]}
    JOURNAL_ARTIFACT_SHA256=${JOURNAL_PARTS[8]}
    JOURNAL_BACKUP_CREATED_AT=${JOURNAL_PARTS[9]}
    JOURNAL_PRE_CUTOVER_BACKUP=${JOURNAL_PARTS[10]}
}

database_presence() {
    local name=$1 rc
    if database_exists "$name"; then
        printf 'true\n'
        return 0
    fi
    rc=$?
    if (( rc == 1 )); then
        printf 'false\n'
        return 0
    fi
    return "$rc"
}

restore_staging_database() {
    log "creating staging database $STAGING_DB"
    admin_createdb "$STAGING_DB"
    timeout --foreground "$RESTORE_TIMEOUT" runuser -u postgres -- \
        pg_restore --exit-on-error --no-owner --no-acl --role="$LIVE_ROLE" \
        -h "$PG_SOCKET_DIR" -p "$PG_PORT" --dbname="$STAGING_DB" \
        "$POSTGRES_WORK_DIR/dump.custom" >/dev/null

    local staging_url
    staging_url=$(make_database_url "$STAGING_DB")
    timeout --foreground "$RESTORE_TIMEOUT" runuser -u "$BOT_USER" -- \
        env DATABASE_URL="$staging_url" PYTHONPATH="$PROJECT_DIR" PYTHONDONTWRITEBYTECODE=1 \
        bash -c "cd '$PROJECT_DIR' && '$VENV_DIR/bin/alembic' upgrade head" >/dev/null

    mapfile -t code_heads < <(
        runuser -u "$BOT_USER" -- env PYTHONPATH="$PROJECT_DIR" PYTHONDONTWRITEBYTECODE=1 \
            bash -c "cd '$PROJECT_DIR' && '$VENV_DIR/bin/alembic' heads" \
            | awk 'NF {print $1}'
    )
    (( ${#code_heads[@]} == 1 )) || fail 'application migration graph must have exactly one head'
    CODE_HEAD_REVISION=${code_heads[0]}

    STAGING_DB_NAME="$STAGING_DB" TABLES="$CRITICAL_TABLES" EXPECTED_HEAD="$CODE_HEAD_REVISION" \
        PGHOST_VALUE="$PG_SOCKET_DIR" PGPORT_VALUE="$PG_PORT" python3 - <<'PY'
import os
import re
import subprocess

name = os.environ['STAGING_DB_NAME']
tables = [value for value in os.environ['TABLES'].split(',') if value]
if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name):
    raise SystemExit('unsafe staging database name')

def sql(query):
    return subprocess.check_output(
        [
            'runuser', '-u', 'postgres', '--', 'psql', '-XAt',
            '-v', 'ON_ERROR_STOP=1',
            '-h', os.environ['PGHOST_VALUE'],
            '-p', os.environ['PGPORT_VALUE'],
            '-d', name,
            '-c', query,
        ],
        text=True,
        timeout=35,
    ).strip()

if sql("SELECT to_regclass('public.alembic_version') IS NOT NULL") != 't':
    raise SystemExit('missing alembic_version')
if sql('SELECT count(*) FROM alembic_version') != '1':
    raise SystemExit('unexpected Alembic revision row count')
if sql('SELECT version_num FROM alembic_version') != os.environ['EXPECTED_HEAD']:
    raise SystemExit('staging database is not at application Alembic head')
for table in tables:
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', table):
        raise SystemExit('invalid critical table name')
    if sql(f"SELECT to_regclass('public.{table}') IS NOT NULL") != 't':
        raise SystemExit(f'missing critical table: {table}')
    sql(f'SELECT 1 FROM "{table}" LIMIT 1')
sql('SELECT 1')
PY
    log "staging database validated at Alembic head $CODE_HEAD_REVISION"
}

database_cutover() {
    begin_cutover_journal production before_old_rename
    database_allow_connections "$LIVE_DATABASE" false
    terminate_database_connections "$LIVE_DATABASE"
    database_allow_connections "$STAGING_DB" false
    terminate_database_connections "$STAGING_DB"

    rename_database "$LIVE_DATABASE" "$ROLLBACK_DB"
    CUTOVER_PHASE="old_renamed"
    update_cutover_journal old_renamed
    if ! rename_database "$STAGING_DB" "$LIVE_DATABASE"; then
        if rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" &&
           database_allow_connections "$LIVE_DATABASE" true; then
            CUTOVER_PHASE="recovered_before_cutover"
            clear_cutover_journal
        fi
        return 1
    fi
    CUTOVER_PHASE="new_renamed"
    update_cutover_journal new_renamed
    set_database_owner "$LIVE_DATABASE"
    database_allow_connections "$LIVE_DATABASE" true
    CUTOVER_PHASE="complete"
    update_cutover_journal cutover_complete
}

database_rollback_to_previous() {
    local current_failed_name=$1
    if [[ ! -e "$JOURNAL_STATE" && ! -L "$JOURNAL_STATE" ]]; then
        begin_cutover_journal rollback_previous before_live_rename
    else
        JOURNAL_OPERATION=rollback_previous
        update_cutover_journal before_live_rename
    fi
    database_allow_connections "$LIVE_DATABASE" false || return 1
    terminate_database_connections "$LIVE_DATABASE" || return 1
    database_allow_connections "$ROLLBACK_DB" false || return 1
    terminate_database_connections "$ROLLBACK_DB" || return 1
    rename_database "$LIVE_DATABASE" "$current_failed_name" || return 1
    CUTOVER_PHASE="rollback_live_renamed"
    update_cutover_journal rollback_live_renamed
    if ! rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"; then
        if rename_database "$current_failed_name" "$LIVE_DATABASE" &&
           database_allow_connections "$LIVE_DATABASE" true; then
            CUTOVER_PHASE="rollback_reverted"
            clear_cutover_journal
        fi
        return 1
    fi
    CUTOVER_PHASE="rollback_promoted"
    update_cutover_journal rollback_promoted
    if set_database_owner "$LIVE_DATABASE" && database_allow_connections "$LIVE_DATABASE" true; then
        update_cutover_journal rollback_complete
        return 0
    fi

    database_allow_connections "$LIVE_DATABASE" false || true
    terminate_database_connections "$LIVE_DATABASE" || true
    if rename_database "$LIVE_DATABASE" "$ROLLBACK_DB" &&
       rename_database "$current_failed_name" "$LIVE_DATABASE"; then
        set_database_owner "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        CUTOVER_PHASE="rollback_reverted"
        clear_cutover_journal
    fi
    return 1
}

return_restored_database_after_manual_rollback() {
    JOURNAL_OPERATION=manual_return
    if [[ ! -e "$JOURNAL_STATE" && ! -L "$JOURNAL_STATE" ]]; then
        begin_cutover_journal manual_return before_previous_rename
    else
        update_cutover_journal before_previous_rename
    fi
    database_allow_connections "$LIVE_DATABASE" false || return 1
    terminate_database_connections "$LIVE_DATABASE" || return 1
    database_allow_connections "$FAILED_DB" false || return 1
    terminate_database_connections "$FAILED_DB" || return 1
    rename_database "$LIVE_DATABASE" "$ROLLBACK_DB" || return 1
    CUTOVER_PHASE="manual_previous_renamed"
    update_cutover_journal manual_previous_renamed
    if ! rename_database "$FAILED_DB" "$LIVE_DATABASE"; then
        if rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" &&
           database_allow_connections "$LIVE_DATABASE" true; then
            CUTOVER_PHASE="manual_return_reverted"
            clear_cutover_journal
        fi
        return 1
    fi
    CUTOVER_PHASE="manual_restored_returned"
    update_cutover_journal manual_restored_promoted
    set_database_owner "$LIVE_DATABASE" || return 1
    database_allow_connections "$LIVE_DATABASE" true || return 1
    update_cutover_journal manual_return_complete
}

recover_interrupted_cutover() {
    load_cutover_journal

    TRANSACTION_ID=$JOURNAL_TRANSACTION_ID
    LIVE_DATABASE=$JOURNAL_LIVE_DB
    STAGING_DB=$JOURNAL_STAGING_DB
    ROLLBACK_DB=$JOURNAL_ROLLBACK_DB
    FAILED_DB=$JOURNAL_FAILED_DB
    ARTIFACT=$JOURNAL_ARTIFACT_NAME
    ARTIFACT_SHA256=$JOURNAL_ARTIFACT_SHA256
    BACKUP_CREATED_AT=$JOURNAL_BACKUP_CREATED_AT
    PRE_CUTOVER_BACKUP=$JOURNAL_PRE_CUTOVER_BACKUP

    local live_exists rollback_exists staging_exists=false failed_exists=false
    live_exists=$(database_presence "$LIVE_DATABASE") || fail 'could not inspect production database during recovery'
    rollback_exists=$(database_presence "$ROLLBACK_DB") || fail 'could not inspect rollback database during recovery'
    if [[ -n "$STAGING_DB" ]]; then
        staging_exists=$(database_presence "$STAGING_DB") || fail 'could not inspect staging database during recovery'
    fi
    if [[ -n "$FAILED_DB" ]]; then
        failed_exists=$(database_presence "$FAILED_DB") || fail 'could not inspect failed database during recovery'
    fi

    pause_runtime
    case "$JOURNAL_OPERATION:$live_exists:$rollback_exists:$staging_exists:$failed_exists" in
        production:true:false:true:false)
            admin_dropdb "$STAGING_DB"
            clear_cutover_journal
            log 'recovery found no production rename; staging database removed'
            ;;
        production:false:true:true:false)
            rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            admin_dropdb "$STAGING_DB"
            clear_cutover_journal
            log 'recovery restored the previous production database after the first rename'
            ;;
        production:true:true:false:false)
            JOURNAL_OPERATION=rollback_previous
            update_cutover_journal recovery_before_live_rename
            database_rollback_to_previous "$FAILED_DB"
            write_active_state rolled_back "$FAILED_DB"
            clear_cutover_journal
            log "recovery returned the previous database; interrupted restored database preserved as $FAILED_DB"
            ;;
        rollback_previous:true:true:false:false)
            clear_cutover_journal
            log 'recovery found rollback had not started; current transaction remains active'
            ;;
        rollback_previous:false:true:false:true)
            rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            write_active_state rolled_back "$FAILED_DB"
            clear_cutover_journal
            log 'recovery completed promotion of the previous database'
            ;;
        rollback_previous:true:false:false:true)
            write_active_state rolled_back "$FAILED_DB"
            clear_cutover_journal
            log 'recovery finalized an already completed rollback swap'
            ;;
        manual_return:true:false:false:true)
            clear_cutover_journal
            log 'recovery found restored-database return had not started; rolled-back database remains active'
            ;;
        manual_return:false:true:false:true)
            rename_database "$FAILED_DB" "$LIVE_DATABASE"
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            write_active_state active ""
            clear_cutover_journal
            log 'recovery completed return of the restored database'
            ;;
        manual_return:true:true:false:false)
            write_active_state active ""
            clear_cutover_journal
            log 'recovery finalized an already completed restored-database return'
            ;;
        *)
            fail "ambiguous restore recovery state: operation=$JOURNAL_OPERATION live=$live_exists rollback=$rollback_exists staging=$staging_exists failed=$failed_exists"
            return 1
            ;;
    esac

    if ! wait_for_application_health; then
        restore_timer_states || true
        RUNTIME_RESTORED=true
        fail 'database recovery completed but application healthcheck failed'
        return 1
    fi
    restore_timer_states || {
        RUNTIME_RESTORED=true
        fail 'database recovery completed but timer state could not be restored'
        return 1
    }
    RUNTIME_RESTORED=true
}

show_status() {
    prepare_state_dir
    if [[ -e "$JOURNAL_STATE" || -L "$JOURNAL_STATE" ]]; then
        printf 'Interrupted production restore journal: present\n'
        if [[ -f "$JOURNAL_STATE" && ! -L "$JOURNAL_STATE" ]]; then
            load_cutover_journal
            printf 'Journal operation: %s\n' "$JOURNAL_OPERATION"
            printf 'Journal phase: %s\n' "$JOURNAL_PHASE"
            printf 'Journal transaction ID: %s\n' "$JOURNAL_TRANSACTION_ID"
        fi
        printf 'Required action: sudo production_restore.sh recover\n\n'
    fi

    if [[ ! -e "$ACTIVE_STATE" && ! -L "$ACTIVE_STATE" ]]; then
        printf 'Production restore transaction: none\n'
        return 0
    fi
    load_active_state
    printf 'Production restore transaction: %s\n' "$STATE_STATUS"
    printf 'Transaction ID: %s\n' "$STATE_TRANSACTION_ID"
    printf 'Production database: %s\n' "$STATE_PRODUCTION_DB"
    printf 'Rollback database: %s\n' "$STATE_ROLLBACK_DB"
    printf 'Failed/restored preserved database: %s\n' "${STATE_FAILED_DB:-none}"
    printf 'Artifact: %s\n' "$STATE_ARTIFACT_NAME"
    printf 'Artifact SHA-256: %s\n' "$STATE_ARTIFACT_SHA256"
    printf 'Backup created UTC: %s\n' "$STATE_BACKUP_CREATED_AT"
    printf 'Cutover UTC: %s\n' "$STATE_CUTOVER_AT"
    printf 'Pre-cutover safety backup: %s\n' "$STATE_PRE_CUTOVER_BACKUP"
}

cleanup_on_exit() {
    local rc=$?
    set +e
    if (( rc != 0 )) && [[ "$MUTATING_ACTION" == true ]]; then
        if [[ "$CUTOVER_PHASE" == old_renamed ]]; then
            if database_exists "$ROLLBACK_DB" && ! database_exists "$LIVE_DATABASE"; then
                if rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" &&
                   database_allow_connections "$LIVE_DATABASE" true; then
                    [[ -z "$STAGING_DB" ]] || admin_dropdb "$STAGING_DB" >/dev/null 2>&1 || true
                    clear_cutover_journal || true
                fi
            fi
        elif [[ "$CUTOVER_PHASE" == new_renamed || "$CUTOVER_PHASE" == complete ]]; then
            systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
            if database_exists "$LIVE_DATABASE" && database_exists "$ROLLBACK_DB"; then
                JOURNAL_OPERATION=rollback_previous
                update_cutover_journal cleanup_before_rollback || true
                if database_rollback_to_previous "$FAILED_DB"; then
                    CUTOVER_PHASE="rolled_back"
                    if write_active_state rolled_back "$FAILED_DB"; then
                        clear_cutover_journal || true
                    fi
                fi
            fi
        elif [[ "$CUTOVER_PHASE" == manual_rollback_swapped || "$CUTOVER_PHASE" == rollback_promoted ]]; then
            if write_active_state rolled_back "$FAILED_DB"; then
                CUTOVER_PHASE="manual_rollback_state_written"
                clear_cutover_journal || true
            elif return_restored_database_after_manual_rollback; then
                CUTOVER_PHASE="complete"
                if write_active_state active ""; then
                    clear_cutover_journal || true
                fi
            fi
        elif [[ "$CUTOVER_PHASE" == manual_restored_returned ]]; then
            if write_active_state active ""; then
                clear_cutover_journal || true
            fi
        fi
        if [[ "$RUNTIME_PAUSED" == true && "$RUNTIME_RESTORED" != true ]]; then
            if [[ "$SERVICE_WAS_ACTIVE" == true ]] && database_exists "$LIVE_DATABASE"; then
                systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
            fi
            restore_runtime_without_start || true
        fi
        if [[ -n "$STAGING_DB" && ( "$CUTOVER_PHASE" == none || "$CUTOVER_PHASE" == recovered_before_cutover ) ]] && database_exists "$STAGING_DB"; then
            admin_dropdb "$STAGING_DB" >/dev/null 2>&1 || true
        fi
    fi
    [[ -z "$POSTGRES_WORK_DIR" ]] || rm -rf -- "$POSTGRES_WORK_DIR"
    [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"
    exit "$rc"
}
