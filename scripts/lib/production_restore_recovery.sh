# Persistent write-ahead journal for PostgreSQL database-name cutovers.
# Loaded last by production_restore.sh so these definitions override the base engine.

JOURNAL_FILE=${RESTORE_JOURNAL_FILE:-$STATE_DIR/cutover-journal.env}
RESTORE_FUNCTIONS_ONLY_MODE=${RESTORE_FUNCTIONS_ONLY:-0}

_base_show_status_definition=$(declare -f show_status)
_base_show_status_definition=${_base_show_status_definition/#"show_status ()"/"base_show_status ()"}
eval "$_base_show_status_definition"
unset _base_show_status_definition

fsync_restore_path() {
    python3 - "$1" <<'PY'
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
flags = os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0)
fd = os.open(path, flags)
try:
    os.fsync(fd)
finally:
    os.close(fd)
parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(parent_fd)
finally:
    os.close(parent_fd)
PY
}

journal_value() {
    local primary=$1 fallback=${2:-}
    [[ -n "$primary" ]] && printf '%s' "$primary" || printf '%s' "$fallback"
}

journal_enabled() {
    [[ "${RESTORE_DISABLE_JOURNAL:-0}" != 1 ]] &&
        [[ "$RESTORE_FUNCTIONS_ONLY_MODE" != 1 || "${RESTORE_TEST_JOURNAL:-0}" == 1 ]]
}

write_cutover_journal() {
    journal_enabled || return 0
    prepare_state_dir
    local action=$1 phase=$2
    local artifact_name artifact_sha backup_created pre_cutover
    artifact_name=$(journal_value "${ARTIFACT:+$(basename -- "$ARTIFACT")}" "${STATE_ARTIFACT_NAME:-unknown}")
    artifact_sha=$(journal_value "${ARTIFACT_SHA256:-}" "${STATE_ARTIFACT_SHA256:-}")
    backup_created=$(journal_value "${BACKUP_CREATED_AT:-}" "${STATE_BACKUP_CREATED_AT:-}")
    pre_cutover=$(journal_value "${PRE_CUTOVER_BACKUP:-}" "${STATE_PRE_CUTOVER_BACKUP:-}")
    local temp="${JOURNAL_FILE}.tmp.$$"

    [[ "$action" =~ ^(production|automatic_rollback|manual_rollback|manual_return)$ ]] || return 64
    [[ "$phase" =~ ^[a-z0-9_]+$ ]] || return 64
    is_safe_database_name "$LIVE_DATABASE" || return 64
    [[ -z "$STAGING_DB" ]] || is_safe_database_name "$STAGING_DB" || return 64
    [[ -z "$ROLLBACK_DB" ]] || is_safe_database_name "$ROLLBACK_DB" || return 64
    [[ -z "$FAILED_DB" ]] || is_safe_database_name "$FAILED_DB" || return 64

    cat > "$temp" <<EOF
format_version=1
action=$action
phase=$phase
transaction_id=$TRANSACTION_ID
live_database=$LIVE_DATABASE
staging_database=$STAGING_DB
rollback_database=$ROLLBACK_DB
failed_database=$FAILED_DB
artifact_name=$artifact_name
artifact_sha256=$artifact_sha
backup_created_at_utc=$backup_created
pre_cutover_backup=$pre_cutover
EOF
    chmod 0600 "$temp"
    chown root:root "$temp"
    mv -f -- "$temp" "$JOURNAL_FILE"
    fsync_restore_path "$JOURNAL_FILE"
}

clear_cutover_journal() {
    journal_enabled || return 0
    if [[ -e "$JOURNAL_FILE" || -L "$JOURNAL_FILE" ]]; then
        [[ -f "$JOURNAL_FILE" && ! -L "$JOURNAL_FILE" ]] || return 1
        rm -f -- "$JOURNAL_FILE"
        fsync_restore_path "$STATE_DIR"
    fi
}

load_cutover_journal() {
    [[ -f "$JOURNAL_FILE" && ! -L "$JOURNAL_FILE" ]] || fail 'no interrupted cutover journal'
    [[ $(stat -c '%u' "$JOURNAL_FILE") == 0 ]] || fail 'cutover journal is not root-owned'
    local mode
    mode=$(stat -c '%a' "$JOURNAL_FILE")
    (( (8#$mode & 8#077) == 0 )) || fail 'cutover journal permissions are too broad'

    mapfile -d '' -t JOURNAL_PARTS < <(
        JOURNAL_PATH="$JOURNAL_FILE" python3 - <<'PY'
import os
import re
from pathlib import Path

allowed = {
    'format_version', 'action', 'phase', 'transaction_id', 'live_database',
    'staging_database', 'rollback_database', 'failed_database', 'artifact_name',
    'artifact_sha256', 'backup_created_at_utc', 'pre_cutover_backup',
}
values = {}
for raw in Path(os.environ['JOURNAL_PATH']).read_text(encoding='utf-8').splitlines():
    if not raw or '=' not in raw:
        raise SystemExit('invalid cutover journal line')
    key, value = raw.split('=', 1)
    if key not in allowed or key in values or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SystemExit('invalid cutover journal schema')
    values[key] = value
if set(values) != allowed or values['format_version'] != '1':
    raise SystemExit('unsupported cutover journal')
if values['action'] not in {'production', 'automatic_rollback', 'manual_rollback', 'manual_return'}:
    raise SystemExit('invalid cutover action')
if not re.fullmatch(r'[a-z0-9_]+', values['phase']):
    raise SystemExit('invalid cutover phase')
if not re.fullmatch(r'\d{14}_\d+', values['transaction_id']):
    raise SystemExit('invalid transaction id')
for key in ('live_database', 'rollback_database'):
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values[key]):
        raise SystemExit('invalid database name')
for key in ('staging_database', 'failed_database'):
    if values[key] and not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', values[key]):
        raise SystemExit('invalid optional database name')
for key in (
    'action', 'phase', 'transaction_id', 'live_database', 'staging_database',
    'rollback_database', 'failed_database', 'artifact_name', 'artifact_sha256',
    'backup_created_at_utc', 'pre_cutover_backup',
):
    print(values[key], end='\0')
PY
    )
    (( ${#JOURNAL_PARTS[@]} == 11 )) || fail 'could not parse cutover journal'
    JOURNAL_ACTION=${JOURNAL_PARTS[0]}
    JOURNAL_PHASE=${JOURNAL_PARTS[1]}
    TRANSACTION_ID=${JOURNAL_PARTS[2]}
    LIVE_DATABASE=${JOURNAL_PARTS[3]}
    STAGING_DB=${JOURNAL_PARTS[4]}
    ROLLBACK_DB=${JOURNAL_PARTS[5]}
    FAILED_DB=${JOURNAL_PARTS[6]}
    ARTIFACT=${JOURNAL_PARTS[7]}
    ARTIFACT_SHA256=${JOURNAL_PARTS[8]}
    BACKUP_CREATED_AT=${JOURNAL_PARTS[9]}
    PRE_CUTOVER_BACKUP=${JOURNAL_PARTS[10]}
}

write_active_state() {
    local status=$1 failed_db=${2:-}
    local temp="$ACTIVE_STATE.tmp.$$"
    cat > "$temp" <<EOF
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
EOF
    chmod 0600 "$temp"
    chown root:root "$temp"
    mv -f -- "$temp" "$ACTIVE_STATE"
    fsync_restore_path "$ACTIVE_STATE"
    clear_cutover_journal
}

archive_active_state() {
    local final_status=$1
    local archived="$STATE_DIR/${STATE_TRANSACTION_ID}.${final_status}.env"
    [[ ! -e "$archived" && ! -L "$archived" ]] || fail "restore archive already exists: $archived"
    local temp="$STATE_DIR/.archive.$$"
    sed "s/^status=.*/status=$final_status/" "$ACTIVE_STATE" > "$temp"
    chmod 0600 "$temp"
    chown root:root "$temp"
    mv -- "$temp" "$archived"
    fsync_restore_path "$archived"
    rm -f -- "$ACTIVE_STATE"
    fsync_restore_path "$STATE_DIR"
}

pause_runtime() {
    SERVICE_WAS_ACTIVE=false
    BACKUP_TIMER_WAS_ACTIVE=false
    HEALTH_TIMER_WAS_ACTIVE=false
    unit_active "$SERVICE_NAME" && SERVICE_WAS_ACTIVE=true
    unit_active "$BACKUP_TIMER" && BACKUP_TIMER_WAS_ACTIVE=true
    unit_active "$HEALTH_TIMER" && HEALTH_TIMER_WAS_ACTIVE=true
    RUNTIME_PAUSED=true

    systemctl stop "$BACKUP_TIMER" "$HEALTH_TIMER" >/dev/null ||
        fail 'could not stop backup/health timers'
    wait_for_unit_inactive "$BACKUP_TIMER" 30 || fail 'backup timer did not stop'
    wait_for_unit_inactive "$HEALTH_TIMER" 30 || fail 'health timer did not stop'
    wait_for_unit_inactive "$BACKUP_SERVICE" 180 ||
        fail 'an existing backup service did not finish in 180 seconds'
    systemctl stop "$SERVICE_NAME"
    wait_for_unit_inactive "$SERVICE_NAME" 60 ||
        fail 'application service did not stop'
}

restore_timer_states() {
    local rc=0
    if [[ "$BACKUP_TIMER_WAS_ACTIVE" == true ]]; then
        systemctl start "$BACKUP_TIMER" >/dev/null || rc=1
    fi
    if [[ "$HEALTH_TIMER_WAS_ACTIVE" == true ]]; then
        systemctl start "$HEALTH_TIMER" >/dev/null || rc=1
    fi
    return "$rc"
}

database_cutover() {
    write_cutover_journal production before_connection_block
    database_allow_connections "$LIVE_DATABASE" false
    terminate_database_connections "$LIVE_DATABASE"
    database_allow_connections "$STAGING_DB" false
    terminate_database_connections "$STAGING_DB"

    write_cutover_journal production before_old_rename
    rename_database "$LIVE_DATABASE" "$ROLLBACK_DB"
    CUTOVER_PHASE=old_renamed
    write_cutover_journal production old_renamed
    if ! rename_database "$STAGING_DB" "$LIVE_DATABASE"; then
        rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        CUTOVER_PHASE=recovered_before_cutover
        write_cutover_journal production recovered_before_cutover
        return 1
    fi
    CUTOVER_PHASE=new_renamed
    write_cutover_journal production new_renamed
    set_database_owner "$LIVE_DATABASE"
    database_allow_connections "$LIVE_DATABASE" true
    CUTOVER_PHASE=complete
    write_cutover_journal production complete
}

database_rollback_to_previous() {
    local current_failed_name=$1
    FAILED_DB=$current_failed_name
    local action=${RESTORE_SWAP_ACTION:-automatic_rollback}
    write_cutover_journal "$action" before_connection_block
    database_allow_connections "$LIVE_DATABASE" false || return 1
    terminate_database_connections "$LIVE_DATABASE" || return 1
    database_allow_connections "$ROLLBACK_DB" false || return 1
    terminate_database_connections "$ROLLBACK_DB" || return 1
    write_cutover_journal "$action" before_live_to_failed
    rename_database "$LIVE_DATABASE" "$current_failed_name" || return 1
    write_cutover_journal "$action" live_renamed_to_failed
    if ! rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"; then
        rename_database "$current_failed_name" "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        write_cutover_journal "$action" recovered_before_rollback
        return 1
    fi
    write_cutover_journal "$action" previous_renamed_to_live
    if set_database_owner "$LIVE_DATABASE" && database_allow_connections "$LIVE_DATABASE" true; then
        write_cutover_journal "$action" complete
        return 0
    fi

    database_allow_connections "$LIVE_DATABASE" false || true
    terminate_database_connections "$LIVE_DATABASE" || true
    if rename_database "$LIVE_DATABASE" "$ROLLBACK_DB" &&
       rename_database "$current_failed_name" "$LIVE_DATABASE"; then
        set_database_owner "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
    fi
    write_cutover_journal "$action" recovery_failed
    return 1
}

return_restored_database_after_manual_rollback() {
    write_cutover_journal manual_return before_connection_block
    database_allow_connections "$LIVE_DATABASE" false || return 1
    terminate_database_connections "$LIVE_DATABASE" || return 1
    database_allow_connections "$FAILED_DB" false || return 1
    terminate_database_connections "$FAILED_DB" || return 1
    write_cutover_journal manual_return before_live_to_rollback
    rename_database "$LIVE_DATABASE" "$ROLLBACK_DB" || return 1
    write_cutover_journal manual_return previous_renamed_to_rollback
    if ! rename_database "$FAILED_DB" "$LIVE_DATABASE"; then
        rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        return 1
    fi
    write_cutover_journal manual_return restored_renamed_to_live
    set_database_owner "$LIVE_DATABASE" || return 1
    database_allow_connections "$LIVE_DATABASE" true || return 1
    write_cutover_journal manual_return complete
}

database_exists_bool() {
    if database_exists "$1"; then
        printf true
        return 0
    fi
    local rc=$?
    (( rc == 1 )) || return 1
    printf false
}

recover_restore() {
    (( $# == 0 )) || {
        printf 'recover accepts no arguments\n' >&2
        exit 2
    }
    require_root
    for command in flock stat python3 runuser psql dropdb systemctl; do
        require_command "$command"
    done
    validate_runtime_paths
    acquire_operation_lock
    prepare_state_dir
    read_env_contract
    load_postgresql_library
    load_cutover_journal

    [[ "$LIVE_DATABASE" == just1kbot_bot ]] ||
        fail 'journal production database does not match contract'

    local live rollback staging failed
    live=$(database_exists_bool "$LIVE_DATABASE") || fail 'could not inspect live database'
    rollback=$(database_exists_bool "$ROLLBACK_DB") || fail 'could not inspect rollback database'
    staging=false
    failed=false
    [[ -z "$STAGING_DB" ]] || staging=$(database_exists_bool "$STAGING_DB") ||
        fail 'could not inspect staging database'
    [[ -z "$FAILED_DB" ]] || failed=$(database_exists_bool "$FAILED_DB") ||
        fail 'could not inspect failed database'

    log "recovery journal action=$JOURNAL_ACTION phase=$JOURNAL_PHASE live=$live rollback=$rollback staging=$staging failed=$failed"

    case "$JOURNAL_ACTION:$live:$rollback:$failed" in
        production:true:false:false)
            [[ "$staging" != true ]] || admin_dropdb "$STAGING_DB"
            clear_cutover_journal
            log 'interrupted restore aborted before cutover; original production remains active'
            ;;
        production:false:true:false)
            rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            [[ "$staging" != true ]] || admin_dropdb "$STAGING_DB"
            clear_cutover_journal
            log 'original production database restored after interrupted first rename'
            ;;
        production:true:true:false)
            FAILED_DB="just1kbot_fail_${TRANSACTION_ID}"
            assert_database_absent "$FAILED_DB"
            database_allow_connections "$LIVE_DATABASE" false
            terminate_database_connections "$LIVE_DATABASE"
            database_allow_connections "$ROLLBACK_DB" false
            terminate_database_connections "$ROLLBACK_DB"
            rename_database "$LIVE_DATABASE" "$FAILED_DB"
            rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            write_active_state rolled_back "$FAILED_DB"
            log "previous production restored; interrupted restored database preserved as $FAILED_DB"
            ;;
        manual_rollback:true:true:false)
            clear_cutover_journal
            log 'manual rollback had not swapped database names; existing active transaction preserved'
            ;;
        manual_rollback:false:true:true)
            rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            write_active_state rolled_back "$FAILED_DB"
            log 'manual rollback completed from interrupted first rename'
            ;;
        manual_rollback:true:false:true)
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            write_active_state rolled_back "$FAILED_DB"
            log 'manual rollback state reconstructed'
            ;;
        automatic_rollback:false:true:true)
            rename_database "$ROLLBACK_DB" "$LIVE_DATABASE"
            set_database_owner "$LIVE_DATABASE"
            database_allow_connections "$LIVE_DATABASE" true
            write_active_state rolled_back "$FAILED_DB"
            log 'automatic rollback completed from journal'
            ;;
        automatic_rollback:true:false:true)
            write_active_state rolled_back "$FAILED_DB"
            log 'automatic rollback state reconstructed'
            ;;
        manual_return:true:true:false)
            clear_cutover_journal
            log 'restored database is active; prior rolled-back database remains preserved'
            ;;
        manual_return:true:false:false)
            write_active_state active ""
            log 'restored database return state reconstructed'
            ;;
        *)
            fail 'database layout is ambiguous; journal retained for manual recovery'
            ;;
    esac

    systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
}

show_status() {
    base_show_status
    if [[ -e "$JOURNAL_FILE" || -L "$JOURNAL_FILE" ]]; then
        printf 'Interrupted cutover journal: %s\n' "$JOURNAL_FILE"
    fi
}

usage() {
    cat <<'EOF'
Just1kBot production PostgreSQL restore/cutover

Production restore/cutover:
  sudo AGE_IDENTITY_FILE=/secure/key production_restore.sh production ARTIFACT
  sudo AGE_IDENTITY_FILE=/secure/key production_restore.sh production \
    --yes --expected-sha256 SHA256 ARTIFACT

Transaction management:
  sudo production_restore.sh status
  sudo production_restore.sh recover
  sudo production_restore.sh rollback
  sudo production_restore.sh rollback --yes --transaction-id ID
  sudo production_restore.sh finalize
  sudo production_restore.sh finalize --yes --transaction-id ID

Production restore never overwrites .env and never drops the previous database.
EOF
}

_base_production_restore_definition=$(declare -f production_restore)
_base_production_restore_definition=${_base_production_restore_definition/#"production_restore ()"/"base_production_restore ()"}
eval "$_base_production_restore_definition"
unset _base_production_restore_definition

_base_manual_rollback_definition=$(declare -f manual_rollback)
_base_manual_rollback_definition=${_base_manual_rollback_definition/#"manual_rollback ()"/"base_manual_rollback ()"}
eval "$_base_manual_rollback_definition"
unset _base_manual_rollback_definition

_base_finalize_restore_definition=$(declare -f finalize_restore)
_base_finalize_restore_definition=${_base_finalize_restore_definition/#"finalize_restore ()"/"base_finalize_restore ()"}
eval "$_base_finalize_restore_definition"
unset _base_finalize_restore_definition

assert_no_cutover_journal() {
    journal_enabled || return 0
    [[ ! -e "$JOURNAL_FILE" && ! -L "$JOURNAL_FILE" ]] ||
        fail 'interrupted cutover exists; run recover first'
}

production_restore() {
    assert_no_cutover_journal
    base_production_restore "$@"
}

manual_rollback() {
    assert_no_cutover_journal
    RESTORE_SWAP_ACTION=manual_rollback
    base_manual_rollback "$@"
}

finalize_restore() {
    assert_no_cutover_journal
    base_finalize_restore "$@"
}

main() {
    case "$ACTION" in
        production)
            production_restore "$@"
            ;;
        status)
            (( $# == 0 )) || { printf 'status accepts no arguments\n' >&2; exit 2; }
            require_root
            show_status
            ;;
        recover)
            recover_restore "$@"
            ;;
        rollback)
            manual_rollback "$@"
            ;;
        finalize)
            finalize_restore "$@"
            ;;
        help|-h|--help)
            usage
            ;;
        "")
            usage >&2
            exit 2
            ;;
        *)
            printf 'unknown production restore action: %s\n' "$ACTION" >&2
            usage >&2
            exit 2
            ;;
    esac
}
