production_restore() {
    MUTATING_ACTION=true
    parse_mutating_args true "$@"
    [[ -n "$ARTIFACT" ]] || { printf 'production restore requires an artifact\n' >&2; exit 2; }
    require_root
    [[ "$RESTORE_TIMEOUT" =~ ^[1-9][0-9]*$ && "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ && "$MIN_FREE_MARGIN_BYTES" =~ ^[0-9]+$ ]] || fail 'restore timeout/free-space settings are invalid'
    for command in flock realpath stat sha256sum python3 timeout runuser psql createdb dropdb pg_restore systemctl find sort cut df awk sed; do
        require_command "$command"
    done
    validate_runtime_paths
    acquire_operation_lock
    prepare_state_dir
    [[ ! -e "$ACTIVE_STATE" && ! -L "$ACTIVE_STATE" ]] || fail 'a previous restore transaction must be finalized before another restore'
    read_env_contract
    load_postgresql_library
    extract_and_verify_backup
    check_free_space
    new_transaction_names
    restore_staging_database
    confirm_production_cutover

    pause_runtime
    create_final_pre_cutover_backup
    database_cutover
    write_active_state active ""

    if wait_for_application_health; then
        restore_timer_states
        RUNTIME_RESTORED=true
        log "production restore succeeded; previous database retained as $ROLLBACK_DB"
        log "run 'just1kbot-restore.sh status', then choose rollback or finalize"
        return 0
    fi

    warn 'restored production failed readiness; returning previous database automatically'
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    database_rollback_to_previous "$FAILED_DB" || {
        write_active_state active "$FAILED_DB" || true
        fail 'automatic database rollback failed; manual recovery is required'
    }
    CUTOVER_PHASE="rolled_back"
    write_active_state rolled_back "$FAILED_DB"
    if ! wait_for_application_health; then
        restore_timer_states
        RUNTIME_RESTORED=true
        fail 'previous database was restored but application health is still failing'
    fi
    restore_timer_states
    RUNTIME_RESTORED=true
    fail "restored database failed readiness; previous database is active and failed database is preserved as $FAILED_DB"
}

manual_rollback() {
    MUTATING_ACTION=true
    parse_mutating_args false "$@"
    require_root
    for command in flock stat python3 runuser psql dropdb systemctl; do require_command "$command"; done
    validate_runtime_paths
    acquire_operation_lock
    prepare_state_dir
    read_env_contract
    load_postgresql_library
    load_active_state
    [[ "$STATE_STATUS" == active ]] || fail 'restore transaction is already rolled back; use finalize to remove the preserved failed database'
    [[ "$STATE_PRODUCTION_DB" == "$LIVE_DATABASE" ]] || fail 'restore state production database does not match .env'
    database_exists "$LIVE_DATABASE" || fail 'current production database is missing'
    database_exists "$STATE_ROLLBACK_DB" || fail 'rollback database is missing'
    confirm_transaction_action rollback

    TRANSACTION_ID=$STATE_TRANSACTION_ID
    ROLLBACK_DB=$STATE_ROLLBACK_DB
    FAILED_DB="just1kbot_fail_${TRANSACTION_ID}"
    is_safe_database_name "$FAILED_DB" || fail 'generated failed database name is unsafe'
    assert_database_absent "$FAILED_DB"

    pause_runtime
    create_final_pre_cutover_backup
    database_rollback_to_previous "$FAILED_DB"
    CUTOVER_PHASE="manual_rollback_swapped"
    ARTIFACT=$STATE_ARTIFACT_NAME
    ARTIFACT_SHA256=$STATE_ARTIFACT_SHA256
    BACKUP_CREATED_AT=$STATE_BACKUP_CREATED_AT
    write_active_state rolled_back "$FAILED_DB"
    CUTOVER_PHASE="manual_rollback_state_written"
    if wait_for_application_health; then
        restore_timer_states
        RUNTIME_RESTORED=true
        log "manual rollback succeeded; restored database preserved as $FAILED_DB"
        return 0
    fi

    warn 'previous database failed readiness; attempting to return restored database'
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
    return_restored_database_after_manual_rollback || fail 'could not return restored production database after failed manual rollback'
    CUTOVER_PHASE="manual_restored_returned"
    write_active_state active ""
    CUTOVER_PHASE="complete"
    if wait_for_application_health; then
        restore_timer_states
        RUNTIME_RESTORED=true
        fail 'manual rollback was rejected because the previous database did not pass readiness; restored database is active again'
    fi
    restore_timer_states
    RUNTIME_RESTORED=true
    fail 'neither database passed application readiness; manual intervention is required'
}

finalize_restore() {
    parse_mutating_args false "$@"
    require_root
    for command in flock stat python3 runuser psql dropdb systemctl timeout; do require_command "$command"; done
    validate_runtime_paths
    acquire_operation_lock
    prepare_state_dir
    read_env_contract
    load_postgresql_library
    load_active_state
    confirm_transaction_action finalize

    timeout --foreground 35 "$HEALTHCHECK_COMMAND" >/dev/null 2>&1 || fail 'production healthcheck must pass before finalize'
    local preserved final_status
    if [[ "$STATE_STATUS" == active ]]; then
        preserved=$STATE_ROLLBACK_DB
        final_status=finalized
    else
        preserved=$STATE_FAILED_DB
        final_status=rollback_finalized
    fi
    [[ -n "$preserved" ]] || fail 'restore state does not identify a preserved database'
    [[ "$preserved" != "$LIVE_DATABASE" ]] || fail 'refusing to drop the production database'
    local exists_rc
    if database_exists "$preserved"; then
        admin_dropdb "$preserved"
    else
        exists_rc=$?
        (( exists_rc == 1 )) || fail "could not determine preserved database state: $preserved"
        warn "preserved database is already absent; completing interrupted finalize: $preserved"
    fi
    if database_exists "$preserved"; then
        fail "preserved database still exists after finalize drop: $preserved"
    else
        exists_rc=$?
        (( exists_rc == 1 )) || fail "could not verify preserved database removal: $preserved"
    fi
    archive_active_state "$final_status"
    log "restore transaction finalized; removed preserved database $preserved"
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
