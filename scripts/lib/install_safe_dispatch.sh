preflight_before_packages() {
    foundation_preflight_static_resources
    foundation_preflight_domain "$DOMAIN" "$YOOKASSA_WEBHOOK_PORT"

    if command -v pg_lsclusters >/dev/null 2>&1 &&
       [[ -n "$(pg_lsclusters --no-header 2>/dev/null || true)" ]]; then
        pg_select_cluster
        [[ "$PG_STATUS" == online ]] || {
            error 'Существующий PostgreSQL cluster должен быть запущен вручную для read-only ownership check.'
            return 1
        }
        preflight_postgres_names_absent
    fi
}

run_management_action() {
    require_root
    init_logging
    case "$ACTION" in
        status)
            pg_select_cluster || true
            show_status
            ;;
        logs)
            exec journalctl -u "$SERVICE_NAME" -f -n100
            ;;
        restart)
            acquire_deploy_lock
            pg_prepare update
            ensure_env_permissions
            systemctl is-active --quiet "$REDIS_SERVICE" || {
                error "$REDIS_SERVICE не запущен"
                return 1
            }
            systemctl restart "$SERVICE_NAME"
            wait_for_application_health || return 1
            show_status
            ;;
        backup)
            pg_prepare update
            ensure_env_permissions
            run_manual_backup
            ;;
        restore)
            pg_prepare update
            run_restore_rehearsal "$ACTION_ARG"
            ;;
    esac
}

print_dry_run() {
    resolve_proxy_mode
    cat <<EOF_DRY
DRY RUN:
- exact Ubuntu 24.04, source tree, ownership, path, port, PostgreSQL and proxy checks completed before apt;
- proxy mode: ${PROXY_MODE};
- dedicated Redis is 127.0.0.1:6380 with separate config/data/unit;
- UFW, nftables, iptables, /etc/redis/redis.conf, Nginx default site, Docker and VPN are not changed;
- dependencies install only from requirements.lock with exact versions and SHA-256 hashes;
- every mutating phase is recorded in a durable root-only journal;
- failed first install runs manifest-driven rollback automatically.
No server state was changed by this dry run.
EOF_DRY
}

begin_installer_transaction() {
    local operation
    operation=$([[ "$INITIAL_INSTALL" == true ]] && printf install || printf update)

    if foundation_exists "$INSTALL_JOURNAL"; then
        error 'Найдена незавершённая installer transaction. Используйте install-recover или install-rollback.'
        return 1
    fi
    foundation_journal_begin "$operation" preflight
}

run_initial_read_only_preflight() {
    collect_initial_input
    validate_initial_input
    preflight_before_packages
}

run_existing_read_only_preflight() {
    DOMAIN=$(read_env_value DOMAIN)
    SSL_EMAIL=$(read_env_value SSL_EMAIL)
    YOOKASSA_WEBHOOK_PORT=$(read_env_value YOOKASSA_WEBHOOK_PORT)
    REDIS_PASSWORD=$(read_env_value REDIS_PASSWORD)
    [[ -n "$DOMAIN" && -n "$SSL_EMAIL" && -n "$YOOKASSA_WEBHOOK_PORT" ]] || {
        error 'Существующий production .env не содержит обязательные DOMAIN/SSL_EMAIL/YOOKASSA_WEBHOOK_PORT.'
        return 1
    }

    if foundation_manifest_validate; then
        foundation_preflight_static_resources
        foundation_preflight_domain "$DOMAIN" "$YOOKASSA_WEBHOOK_PORT"
    else
        legacy_read_only_preflight
    fi
}

perform_deploy_mutations() {
    begin_installer_transaction
    installer_failpoint after-journal

    if [[ "$INITIAL_INSTALL" == true ]]; then
        foundation_journal_update package-install
        install_dependencies
        installer_failpoint after-packages
        validate_runtime_commands
        # Close the apt TOCTOU window before the first managed resource is
        # created. Input values remain unchanged; all checks are read-only.
        preflight_before_packages
    else
        validate_runtime_commands
    fi

    foundation_journal_update ownership-manifest
    ensure_manifest
    installer_failpoint after-manifest

    setup_user_and_dirs
    installer_failpoint after-service-user

    if [[ "$INITIAL_INSTALL" == true ]]; then
        pg_select_cluster
        pg_start_cluster
        setup_postgresql_initial
    else
        pg_prepare update
        record_existing_postgres
        record_legacy_redis_transition
    fi
    installer_failpoint after-postgresql

    configure_operational_transaction
    # shellcheck source=ops/deploy_application.sh
    source "$SCRIPT_DIR/ops/deploy_application.sh"
    install_operational_transaction_overrides
    install_rollback_override

    SOURCE_DIR="$ROOT_DIR"
    PREPARE_COMMAND=(prepare_release_runtime)
    MIGRATION_COMMAND=(init_database)
    ACTIVATION_COMMAND=(activate_release_bundle)
    if [[ "$INITIAL_INSTALL" == true ]]; then
        BACKUP_COMMAND=(pause_operational_timers)
    else
        BACKUP_COMMAND=(pause_and_backup)
    fi

    foundation_journal_update application-transaction
    installer_failpoint before-application-transaction
    run_application_transaction
    installer_failpoint after-application-transaction

    resume_operational_timers
    foundation_manifest_update_source \
        "${JUST1KBOT_SOURCE_REPOSITORY:-local-checkout}" \
        "${JUST1KBOT_SOURCE_REF:-local}" \
        "${JUST1KBOT_SOURCE_COMMIT:-unknown}"
    foundation_journal_update completed
    foundation_journal_clear
}

automatic_initial_rollback() {
    local original_rc=$1 rollback_rc=0
    printf 'Первичная установка завершилась ошибкой rc=%s; выполняется автоматический manifest-driven rollback.\n' \
        "$original_rc" >&2
    flock -u 200 2>/dev/null || true
    set +e
    bash "$SCRIPT_DIR/uninstall_foundation.sh" \
        --purge-data --yes --incomplete-install
    rollback_rc=$?
    set -e
    if (( rollback_rc == 0 )); then
        printf 'Автоматический rollback первичной установки завершён.\n' >&2
        return 0
    fi
    printf 'ОШИБКА: automatic rollback failed rc=%s. Journal/manifest сохранены для install-recover.\n' \
        "$rollback_rc" >&2
    return "$rollback_rc"
}

run_deploy() {
    require_root
    init_logging
    acquire_deploy_lock
    check_os
    determine_install_kind
    validate_source_tree

    if [[ "$INITIAL_INSTALL" == true ]]; then
        run_initial_read_only_preflight
    else
        run_existing_read_only_preflight
    fi

    if [[ "$DRY_RUN" == true ]]; then
        print_dry_run
        return 0
    fi

    local rc
    set +e
    (
        set -Eeuo pipefail
        perform_deploy_mutations
    )
    rc=$?
    set -e

    if (( rc == 0 )); then
        show_status
        print_result
        return 0
    fi

    if foundation_journal_validate >/dev/null 2>&1; then
        foundation_journal_update failed "deploy mutation rc=$rc" || true
    fi
    if [[ "$INITIAL_INSTALL" == true ]]; then
        automatic_initial_rollback "$rc" || true
    else
        error "Update failed rc=$rc; application rollback выполнен, journal сохранён для install-recover."
    fi
    return "$rc"
}

recover_install() {
    require_root
    init_logging
    foundation_recover_status
    if foundation_journal_validate &&
       foundation_manifest_validate &&
       systemctl is-active --quiet just1kbot.service &&
       systemctl is-active --quiet "$REDIS_SERVICE" &&
       "$SCRIPT_DIR/ops/doctor.sh" --smoke; then
        foundation_journal_update completed
        foundation_journal_clear
        printf 'Healthy installation подтверждена; journal удалён.\n'
        return 0
    fi
    error 'Installation ещё не healthy; journal сохранён.'
    error 'Запустите doctor/support-bundle и устраните указанную первичную причину.'
    return 1
}

rollback_empty_pre_manifest_journal() {
    foundation_journal_validate || return 1
    foundation_exists "$INSTALL_MANIFEST" && return 1
    local created
    created=$(foundation_journal_created)
    [[ -z "$created" ]] || return 1
    foundation_journal_clear
    rmdir "$INSTALL_STATE_DIR" 2>/dev/null || true
    rmdir "$STATE_ROOT" 2>/dev/null || true
    printf 'Пустая transaction до создания manifest безопасно удалена.\n'
}

rollback_incomplete() {
    require_root
    init_logging
    if ! foundation_journal_validate; then
        printf 'Journal отсутствует.\n'
        return 0
    fi
    [[ "$(foundation_journal_operation)" == install ]] || {
        error 'Automatic installer rollback разрешён только для первичной установки.'
        return 1
    }
    if rollback_empty_pre_manifest_journal; then
        return 0
    fi
    foundation_manifest_validate || {
        error 'Manifest отсутствует или повреждён, а journal содержит resources; automatic deletion запрещён.'
        return 1
    }
    flock -u 200 2>/dev/null || true
    exec bash "$SCRIPT_DIR/uninstall_foundation.sh" \
        --purge-data --yes --incomplete-install
}
