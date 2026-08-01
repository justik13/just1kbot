# Crash-consistency overrides loaded after the base restore runtime/actions.

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
