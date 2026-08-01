# Bounded-validation and crash-consistency overrides loaded after the base
# restore runtime/actions.

validate_root_owned_regular_file() {
    local path=$1 label=$2 owner mode
    [[ -f "$path" && ! -L "$path" ]] || fail "$label is missing, not regular, or is a symlink: $path"
    owner=$(stat -c '%u' "$path") || return 1
    mode=$(stat -c '%a' "$path") || return 1
    [[ "$owner" == 0 ]] || fail "$label is not root-owned: $path"
    (( (8#$mode & 8#022) == 0 )) || fail "$label is writable by group/other: $path mode=$mode"
}

validate_root_owned_directory() {
    local path=$1 label=$2 owner mode
    [[ -d "$path" && ! -L "$path" ]] || fail "$label is missing, not a directory, or is a symlink: $path"
    owner=$(stat -c '%u' "$path") || return 1
    mode=$(stat -c '%a' "$path") || return 1
    [[ "$owner" == 0 ]] || fail "$label is not root-owned: $path"
    (( (8#$mode & 8#022) == 0 )) || fail "$label is writable by group/other: $path mode=$mode"
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

return_restored_database_after_manual_rollback() {
    database_allow_connections "$LIVE_DATABASE" false || return 1
    terminate_database_connections "$LIVE_DATABASE" || return 1
    database_allow_connections "$FAILED_DB" false || return 1
    terminate_database_connections "$FAILED_DB" || return 1
    rename_database "$LIVE_DATABASE" "$ROLLBACK_DB" || return 1
    if ! rename_database "$FAILED_DB" "$LIVE_DATABASE"; then
        rename_database "$ROLLBACK_DB" "$LIVE_DATABASE" || true
        database_allow_connections "$LIVE_DATABASE" true || true
        return 1
    fi
    set_database_owner "$LIVE_DATABASE" || return 1
    database_allow_connections "$LIVE_DATABASE" true || return 1
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
                if database_rollback_to_previous "$FAILED_DB"; then
                    CUTOVER_PHASE="rolled_back"
                    write_active_state rolled_back "$FAILED_DB" || true
                fi
            fi
        elif [[ "$CUTOVER_PHASE" == manual_rollback_swapped ]]; then
            if write_active_state rolled_back "$FAILED_DB"; then
                CUTOVER_PHASE="manual_rollback_state_written"
            elif return_restored_database_after_manual_rollback; then
                CUTOVER_PHASE="complete"
                write_active_state active "" || true
            fi
        elif [[ "$CUTOVER_PHASE" == manual_restored_returned ]]; then
            write_active_state active "" || true
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
