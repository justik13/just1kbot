restore_staging_database() {
    log "creating staging database $STAGING_DB"
    admin_createdb "$STAGING_DB"
    timeout --foreground "$RESTORE_TIMEOUT" runuser -u postgres -- \
        pg_restore --exit-on-error --no-owner --no-acl --role="$LIVE_ROLE" \
        -h "$PG_SOCKET_DIR" -p "$PG_PORT" --dbname="$STAGING_DB" \
        "$WORK_DIR/verified/dump.custom" >/dev/null

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
import os, re, subprocess
name=os.environ['STAGING_DB_NAME']
tables=[v for v in os.environ['TABLES'].split(',') if v]
if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name):
    raise SystemExit('unsafe staging database name')
def sql(query):
    return subprocess.check_output([
        'runuser','-u','postgres','--','psql','-XAt','-v','ON_ERROR_STOP=1',
        '-h',os.environ['PGHOST_VALUE'],'-p',os.environ['PGPORT_VALUE'],'-d',name,'-c',query
    ], text=True).strip()
if sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'")!='1':
    raise SystemExit('missing alembic_version')
if sql('SELECT count(*) FROM alembic_version')!='1':
    raise SystemExit('unexpected Alembic revision row count')
if sql('SELECT version_num FROM alembic_version')!=os.environ['EXPECTED_HEAD']:
    raise SystemExit('staging database is not at application Alembic head')
for table in tables:
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', table):
        raise SystemExit('invalid critical table name')
    if sql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='{table}'")!='1':
        raise SystemExit(f'missing critical table: {table}')
    sql(f'SELECT count(*) FROM "{table}"')
sql('SELECT 1')
PY
    log "staging database validated at Alembic head $CODE_HEAD_REVISION"
}

confirm_production_cutover() {
    printf '\nRecovery artifact: %s\n' "$(basename -- "$ARTIFACT")"
    printf 'Artifact SHA-256: %s\n' "$ARTIFACT_SHA256"
    printf 'Backup created UTC: %s\n' "$BACKUP_CREATED_AT"
    printf 'Backup revision: %s\n' "$BACKUP_REVISION"
    printf 'Migrated staging head: %s\n' "$CODE_HEAD_REVISION"
    printf 'Current production DB will be preserved as: %s\n\n' "$ROLLBACK_DB"

    if [[ "$ASSUME_YES" == true ]]; then
        is_sha256 "$EXPECTED_SHA256" || fail '--yes requires --expected-sha256 with 64 lowercase hex characters'
        [[ "$EXPECTED_SHA256" == "$ARTIFACT_SHA256" ]] || fail '--expected-sha256 does not match the selected artifact'
        return 0
    fi
    [[ -t 0 ]] || fail 'interactive confirmation requires a TTY; otherwise use --yes --expected-sha256'
    local phrase answer
    phrase="RESTORE ${ARTIFACT_SHA256:0:12}"
    read -r -p "Type exactly '$phrase' to start production cutover: " answer
    [[ "$answer" == "$phrase" ]] || fail 'production restore confirmation did not match'
}

unit_active() { systemctl is-active --quiet "$1"; }

wait_for_unit_inactive() {
    local unit=$1 timeout_seconds=$2
    local deadline=$(( $(date +%s) + timeout_seconds ))
    while unit_active "$unit" && (( $(date +%s) <= deadline )); do sleep 2; done
    ! unit_active "$unit"
}

pause_runtime() {
    SERVICE_WAS_ACTIVE=false; BACKUP_TIMER_WAS_ACTIVE=false; HEALTH_TIMER_WAS_ACTIVE=false
    unit_active "$SERVICE_NAME" && SERVICE_WAS_ACTIVE=true
    unit_active "$BACKUP_TIMER" && BACKUP_TIMER_WAS_ACTIVE=true
    unit_active "$HEALTH_TIMER" && HEALTH_TIMER_WAS_ACTIVE=true
    RUNTIME_PAUSED=true

    systemctl stop "$BACKUP_TIMER" "$HEALTH_TIMER" >/dev/null 2>&1 || true
    wait_for_unit_inactive "$BACKUP_SERVICE" 180 || fail 'an existing backup service did not finish in 180 seconds'
    systemctl stop "$SERVICE_NAME"
    local deadline=$(( $(date +%s) + 60 ))
    while unit_active "$SERVICE_NAME" && (( $(date +%s) <= deadline )); do sleep 1; done
    ! unit_active "$SERVICE_NAME" || fail 'application service did not stop'
}

restore_timer_states() {
    [[ "$BACKUP_TIMER_WAS_ACTIVE" == true ]] && systemctl start "$BACKUP_TIMER" >/dev/null || true
    [[ "$HEALTH_TIMER_WAS_ACTIVE" == true ]] && systemctl start "$HEALTH_TIMER" >/dev/null || true
}

restore_runtime_without_start() {
    restore_timer_states
    RUNTIME_RESTORED=true
}

create_final_pre_cutover_backup() {
    local started latest verify_identity=""
    systemctl cat "$BACKUP_SERVICE" >/dev/null 2>&1 || fail "$BACKUP_SERVICE is not installed"
    started=$(date +%s)
    systemctl start "$BACKUP_SERVICE"
    latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@ %p\n' \
        | sort -rn | head -1 | cut -d' ' -f2-)
    [[ -n "$latest" && -s "$latest" && -s "$latest.sha256" ]] || fail 'pre-cutover backup service produced no complete artifact'
    (( $(stat -c '%Y' "$latest") >= started )) || fail 'pre-cutover backup artifact is not new'

    if [[ -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE ]] &&
       AGE_IDENTITY_FILE="$AGE_IDENTITY_FILE" "$VERIFY_BACKUP" "$latest" >/dev/null 2>&1; then
        verify_identity=$AGE_IDENTITY_FILE
    elif [[ -f "$BACKUP_IDENTITY_FILE" && ! -L "$BACKUP_IDENTITY_FILE" ]] &&
         AGE_IDENTITY_FILE="$BACKUP_IDENTITY_FILE" "$VERIFY_BACKUP" "$latest" >/dev/null 2>&1; then
        verify_identity=$BACKUP_IDENTITY_FILE
    fi
    [[ -n "$verify_identity" ]] || fail 'fresh pre-cutover backup cannot be decrypted and verified with an available identity'

    PRE_CUTOVER_BACKUP=$(basename -- "$latest")
    log "fresh pre-cutover backup created and strictly verified: $PRE_CUTOVER_BACKUP"
}

database_cutover() {
    database_allow_connections "$LIVE_DATABASE" false
    terminate_database_connections "$LIVE_DATABASE"
    database_allow_connections "$STAGING_DB" false
    terminate_database_connections "$STAGING_DB"

    rename_database "$LIVE_DATABASE" "$ROLLBACK_DB"
    CUTOVER_PHASE="old_renamed"
    if ! rename_database "$STAGING_DB" "$LIVE_DATABASE"; then
        rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        CUTOVER_PHASE="recovered_before_cutover"
        return 1
    fi
    CUTOVER_PHASE="new_renamed"
    set_database_owner "$LIVE_DATABASE"
    database_allow_connections "$LIVE_DATABASE" true
    CUTOVER_PHASE="complete"
}

database_rollback_to_previous() {
    local current_failed_name=$1
    database_allow_connections "$LIVE_DATABASE" false || return 1
    terminate_database_connections "$LIVE_DATABASE" || return 1
    database_allow_connections "$ROLLBACK_DB" false || return 1
    terminate_database_connections "$ROLLBACK_DB" || return 1
    rename_database "$LIVE_DATABASE" "$current_failed_name" || return 1
    if ! rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"; then
        rename_database "$current_failed_name" "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        return 1
    fi
    if set_database_owner "$LIVE_DATABASE" && database_allow_connections "$LIVE_DATABASE" true; then
        return 0
    fi

    # The old database was renamed into production but could not be made ready.
    # Return the restored database to production rather than leaving a partial
    # rollback with ambiguous connection state.
    database_allow_connections "$LIVE_DATABASE" false || true
    terminate_database_connections "$LIVE_DATABASE" || true
    if rename_database "$LIVE_DATABASE" "$ROLLBACK_DB" &&
       rename_database "$current_failed_name" "$LIVE_DATABASE"; then
        set_database_owner "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
    fi
    return 1
}

wait_for_application_health() {
    systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl start "$SERVICE_NAME"
    local deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
    while (( $(date +%s) <= deadline )); do
        if unit_active "$SERVICE_NAME" && timeout --foreground 35 "$HEALTHCHECK_COMMAND" >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    journalctl -u "$SERVICE_NAME" -n 100 --no-pager >&2 2>/dev/null || true
    return 1
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
    mv -f -- "$temp" "$ACTIVE_STATE"
}

load_active_state() {
    [[ -f "$ACTIVE_STATE" && ! -L "$ACTIVE_STATE" ]] || fail 'no pending production restore transaction'
    [[ $(stat -c '%u' "$ACTIVE_STATE") == 0 ]] || fail 'restore state is not root-owned'
    local mode
    mode=$(stat -c '%a' "$ACTIVE_STATE")
    (( (8#$mode & 8#077) == 0 )) || fail 'restore state permissions are too broad'

    mapfile -d '' -t STATE_PARTS < <(
        STATE_PATH="$ACTIVE_STATE" python3 - <<'PY'
import os, re
from pathlib import Path
allowed={
 'format_version','status','transaction_id','production_database','rollback_database',
 'failed_database','artifact_name','artifact_sha256','backup_created_at_utc',
 'cutover_at_utc','pre_cutover_backup'
}
values={}
for raw in Path(os.environ['STATE_PATH']).read_text(encoding='utf-8').splitlines():
    if not raw or '=' not in raw:
        raise SystemExit('invalid restore state line')
    key,value=raw.split('=',1)
    if key not in allowed or key in values or any(ord(c)<32 or ord(c)==127 for c in value):
        raise SystemExit('invalid restore state schema')
    values[key]=value
if set(values)!=allowed or values['format_version']!='1':
    raise SystemExit('unsupported restore state')
if values['status'] not in {'active','rolled_back'}:
    raise SystemExit('invalid restore state status')
if not re.fullmatch(r'\d{14}_\d+', values['transaction_id']):
    raise SystemExit('invalid transaction id')
for key in ('production_database','rollback_database'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values[key]):
        raise SystemExit('invalid database name')
if values['failed_database'] and not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values['failed_database']):
    raise SystemExit('invalid failed database name')
if not re.fullmatch(r'[0-9a-f]{64}', values['artifact_sha256']):
    raise SystemExit('invalid artifact SHA-256')
if not re.fullmatch(r'just1kbot-pg-v1-\d{8}T\d{6}Z[.]tar[.]age', values['artifact_name']):
    raise SystemExit('invalid artifact name')
if not re.fullmatch(r'just1kbot-pg-v1-\d{8}T\d{6}Z[.]tar[.]age', values['pre_cutover_backup']):
    raise SystemExit('invalid pre-cutover backup name')
if not re.fullmatch(r'\d{8}T\d{6}Z', values['backup_created_at_utc']):
    raise SystemExit('invalid backup timestamp')
if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', values['cutover_at_utc']):
    raise SystemExit('invalid cutover timestamp')
for key in ('status','transaction_id','production_database','rollback_database','failed_database','artifact_name','artifact_sha256','backup_created_at_utc','cutover_at_utc','pre_cutover_backup'):
    print(values[key],end='\0')
PY
    )
    (( ${#STATE_PARTS[@]} == 10 )) || fail 'could not parse restore state'
    STATE_STATUS=${STATE_PARTS[0]}
    STATE_TRANSACTION_ID=${STATE_PARTS[1]}
    STATE_PRODUCTION_DB=${STATE_PARTS[2]}
    STATE_ROLLBACK_DB=${STATE_PARTS[3]}
    STATE_FAILED_DB=${STATE_PARTS[4]}
    STATE_ARTIFACT_NAME=${STATE_PARTS[5]}
    STATE_ARTIFACT_SHA256=${STATE_PARTS[6]}
    STATE_BACKUP_CREATED_AT=${STATE_PARTS[7]}
    STATE_CUTOVER_AT=${STATE_PARTS[8]}
    STATE_PRE_CUTOVER_BACKUP=${STATE_PARTS[9]}
}

archive_active_state() {
    local final_status=$1
    local archived="$STATE_DIR/${STATE_TRANSACTION_ID}.${final_status}.env"
    [[ ! -e "$archived" && ! -L "$archived" ]] || fail "restore archive already exists: $archived"
    local temp="$STATE_DIR/.archive.$$"
    sed "s/^status=.*/status=$final_status/" "$ACTIVE_STATE" > "$temp"
    chmod 0600 "$temp"; chown root:root "$temp"
    mv -- "$temp" "$archived"
    rm -f -- "$ACTIVE_STATE"
}

confirm_transaction_action() {
    local verb=$1
    if [[ "$ASSUME_YES" == true ]]; then
        [[ "$EXPECTED_TRANSACTION" == "$STATE_TRANSACTION_ID" ]] || fail "--yes requires matching --transaction-id $STATE_TRANSACTION_ID"
        return 0
    fi
    [[ -t 0 ]] || fail "interactive $verb requires a TTY; otherwise use --yes --transaction-id"
    local phrase answer
    phrase="${verb^^} $STATE_TRANSACTION_ID"
    read -r -p "Type exactly '$phrase': " answer
    [[ "$answer" == "$phrase" ]] || fail "$verb confirmation did not match"
}

show_status() {
    prepare_state_dir
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
                rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
                database_allow_connections "$LIVE_DATABASE" true
            fi
        elif [[ "$CUTOVER_PHASE" == new_renamed || "$CUTOVER_PHASE" == complete ]]; then
            systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
            if database_exists "$LIVE_DATABASE" && database_exists "$ROLLBACK_DB"; then
                database_rollback_to_previous "$FAILED_DB" || true
            fi
        fi
        if [[ "$RUNTIME_PAUSED" == true && "$RUNTIME_RESTORED" != true ]]; then
            if [[ "$SERVICE_WAS_ACTIVE" == true ]] && database_exists "$LIVE_DATABASE"; then
                systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
            fi
            restore_runtime_without_start || true
        fi
        if [[ -n "$STAGING_DB" && "$CUTOVER_PHASE" == none ]] && database_exists "$STAGING_DB"; then
            admin_dropdb "$STAGING_DB" >/dev/null 2>&1 || true
        fi
    fi
    [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"
    exit "$rc"
}

