preflight_before_packages(){
    foundation_preflight_static_resources
    foundation_preflight_domain "$DOMAIN" "$YOOKASSA_WEBHOOK_PORT"
    if command -v pg_lsclusters >/dev/null && [[ -n "$(pg_lsclusters --no-header 2>/dev/null||true)" ]]; then pg_select_cluster; [[ "$PG_STATUS" == online ]] || { error 'Существующий PostgreSQL cluster должен быть запущен вручную для read-only ownership check.'; return 1; }; preflight_postgres_names_absent; fi
}
run_management_action(){
    require_root; init_logging
    case "$ACTION" in status) pg_select_cluster||true; show_status;; logs) exec journalctl -u "$SERVICE_NAME" -f -n100;; restart) acquire_deploy_lock; pg_prepare update; ensure_env_permissions; systemctl is-active --quiet "$REDIS_SERVICE"||{ error "$REDIS_SERVICE не запущен"; return 1; }; systemctl restart "$SERVICE_NAME"; wait_for_application_health||return 1; show_status;; backup) pg_prepare update; ensure_env_permissions; run_manual_backup;; restore) pg_prepare update; run_restore_rehearsal "$ACTION_ARG";; esac
}
print_dry_run(){ cat <<'EOF'
DRY RUN: до apt выполняются ownership/path/port/PostgreSQL/Nginx checks. Создаётся dedicated Redis 127.0.0.1:6380. UFW, nftables, iptables, global redis.conf, Nginx default site, Docker и VPN не изменяются. Dependencies устанавливаются из requirements.lock с hashes. Все этапы записываются в durable journal.
EOF
}
run_deploy(){
    require_root; init_logging; acquire_deploy_lock; check_os; determine_install_kind; validate_source_tree
    [[ "$DRY_RUN" == false ]] || { print_dry_run; return; }
    if [[ "$INITIAL_INSTALL" == true ]]; then collect_initial_input; validate_initial_input; preflight_before_packages; install_dependencies; fi
    validate_runtime_commands; ensure_manifest
    if foundation_exists "$INSTALL_JOURNAL"; then error 'Найдена незавершённая installer transaction. Используйте install-recover или install-rollback.'; return 1; fi
    foundation_journal_begin "$([[ "$INITIAL_INSTALL" == true ]]&&echo install||echo update)" preflight
    setup_user_and_dirs
    if [[ "$INITIAL_INSTALL" == true ]]; then pg_select_cluster; pg_start_cluster; setup_postgresql_initial; else pg_prepare update; record_existing_postgres; DOMAIN=$(read_env_value DOMAIN); SSL_EMAIL=$(read_env_value SSL_EMAIL); YOOKASSA_WEBHOOK_PORT=$(read_env_value YOOKASSA_WEBHOOK_PORT); REDIS_PASSWORD=$(read_env_value REDIS_PASSWORD); foundation_preflight_static_resources; foundation_preflight_domain "$DOMAIN" "$YOOKASSA_WEBHOOK_PORT"; fi
    foundation_journal_update dedicated-redis; foundation_setup_dedicated_redis "$REDIS_PASSWORD"; setup_firewall_initial
    configure_operational_transaction
    # shellcheck source=ops/deploy_application.sh
    source "$SCRIPT_DIR/ops/deploy_application.sh"
    install_operational_transaction_overrides; install_rollback_override
    SOURCE_DIR="$ROOT_DIR"; PREPARE_COMMAND=(prepare_release_runtime); MIGRATION_COMMAND=(init_database); ACTIVATION_COMMAND=(activate_release_bundle)
    if [[ "$INITIAL_INSTALL" == true ]]; then BACKUP_COMMAND=(pause_operational_timers); else BACKUP_COMMAND=(pause_and_backup); fi
    foundation_journal_update application-transaction
    if run_application_transaction; then resume_operational_timers; foundation_manifest_update_source "${JUST1KBOT_SOURCE_REPOSITORY:-local-checkout}" "${JUST1KBOT_SOURCE_REF:-local}" "${JUST1KBOT_SOURCE_COMMIT:-unknown}"; foundation_journal_update completed; foundation_journal_clear; show_status; print_result; else local rc=$?; foundation_journal_update failed "application transaction rc=$rc"||true; error "Deploy failed rc=$rc; journal сохранён. Используйте install-recover."; return "$rc"; fi
}
recover_install(){ require_root; init_logging; foundation_recover_status; if foundation_journal_validate && systemctl is-active --quiet just1kbot.service && systemctl is-active --quiet "$REDIS_SERVICE" && "$SCRIPT_DIR/ops/doctor.sh" --smoke; then foundation_journal_update completed; foundation_journal_clear; echo 'Healthy installation подтверждена; journal удалён.'; fi; }
rollback_incomplete(){ require_root; init_logging; foundation_journal_validate||{ echo 'Journal отсутствует.'; return 0; }; [[ "$(foundation_journal_operation)" == install ]]||{ error 'Automatic installer rollback разрешён только для первичной установки.'; return 1; }; exec bash "$SCRIPT_DIR/uninstall_foundation.sh" --purge-data --yes --incomplete-install; }
