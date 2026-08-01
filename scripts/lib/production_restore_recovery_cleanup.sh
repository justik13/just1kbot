# Recovery-specific EXIT behavior loaded after all restore overrides.
# An ambiguous recovery must preserve the durable journal and every database
# exactly as observed so an operator can inspect and retry safely.

cleanup_on_exit() {
    local rc=$?
    set +e

    if [[ "$RECOVERY_ACTION" == true ]]; then
        if [[ "$RUNTIME_PAUSED" == true && "$RUNTIME_RESTORED" != true ]]; then
            if [[ "$SERVICE_WAS_ACTIVE" == true ]] && database_exists "$LIVE_DATABASE"; then
                systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
            fi
            restore_timer_states || true
            RUNTIME_RESTORED=true
        fi
        [[ -z "$POSTGRES_WORK_DIR" ]] || rm -rf -- "$POSTGRES_WORK_DIR"
        [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"
        exit "$rc"
    fi

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
