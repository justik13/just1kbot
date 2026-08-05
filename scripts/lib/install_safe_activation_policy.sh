#!/bin/bash
# Activation ordering for resources covered by operational rollback.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

CLI_BOOTSTRAP_MARKER='Managed by Just1kBot installer recovery bootstrap'

install_recovery_cli_launcher() {
    foundation_atomic_write "$CLI_PATH" root root 0750 <<EOF_CLI
#!/bin/bash
set -Eeuo pipefail
IFS=\$'\n\t'
umask 077

# $CLI_BOOTSTRAP_MARKER
PRIMARY=/opt/just1kbot/deploy.sh
PRIMARY_CONTROL=/opt/just1kbot/scripts/lib/control_plane.sh

if [[ -f "\$PRIMARY" && ! -L "\$PRIMARY" && -f "\$PRIMARY_CONTROL" && ! -L "\$PRIMARY_CONTROL" ]]; then
    exec /bin/bash "\$PRIMARY" "\$@"
fi

printf 'Just1kBot control plane недоступен: основная копия отсутствует или повреждена.\n' >&2
exit 1
EOF_CLI
}

stage_recovery_bundle() {
    [[ "$INITIAL_INSTALL" == true ]] || return 0

    foundation_path_exists "$CLI_PATH" && {
        error "CLI path уже существует до первичной установки без ownership proof: $CLI_PATH"
        return 1
    }

    foundation_journal_validate || return 1
    foundation_journal_add_created_resource "path:$CLI_PATH"
    foundation_journal_validate || return 1

    install_recovery_cli_launcher
}

remove_recovery_path() {
    return 0
}

recovery_paths_safe_for_cleanup() {
    if [[ -e "$CLI_PATH" || -L "$CLI_PATH" ]]; then
        if [[ -L "$CLI_PATH" ]]; then
            local target=$(readlink -f "$CLI_PATH")
            [[ "$target" == "$PROJECT_DIR"* ]] || return 1
        else
            [[ -f "$CLI_PATH" ]] || return 1
            grep -Fq "$CLI_BOOTSTRAP_MARKER" "$CLI_PATH" 2>/dev/null || return 1
        fi
        [[ "$(stat -c '%U:%G %a' "$CLI_PATH" 2>/dev/null || true)" == 'root:root 750' ]] || return 1
    fi
    return 0
}

remove_recovery_bundle() {
    return 0
}

remove_recovery_bootstrap() {
    if [[ -f "$CLI_PATH" && ! -L "$CLI_PATH" ]] &&
       grep -Fq "$CLI_BOOTSTRAP_MARKER" "$CLI_PATH" 2>/dev/null; then
        rm -f -- "$CLI_PATH"
    fi
    return 0
}

begin_installer_transaction() {
    local operation
    operation=$([[ "$INITIAL_INSTALL" == true ]] && printf install || printf update)

    if foundation_exists "$INSTALL_JOURNAL"; then
        error 'Найдена незавершённая installer transaction. Используйте install-recover или install-rollback.'
        return 1
    fi
    foundation_journal_begin "$operation" preflight
    stage_recovery_bundle
}

activate_release_bundle() {
    foundation_journal_update dedicated-redis
    foundation_setup_dedicated_redis "$REDIS_PASSWORD"
    setup_firewall_initial
    installer_failpoint after-dedicated-redis || return $?

    install_backup_tooling
    # The healthcheck definition itself is a managed resource and does not
    # execute until its timer is active. Create it here to preserve the
    # deterministic transaction ordering expected by rollback tests.
    install_healthcheck
    setup_logrotate
    installer_failpoint after-operational-tooling || return $?

    if [[ "$INITIAL_INSTALL" == true ]]; then
        setup_nginx_initial
    else
        refresh_existing_nginx
    fi
    installer_failpoint after-proxy-activation || return $?

    setup_systemd
    installer_failpoint after-systemd || return $?

    foundation_install_cli
    install_recovery_cli_launcher
    installer_failpoint after-cli || return $?
}

rollback_empty_pre_manifest_journal() {
    foundation_journal_validate || return 1
    foundation_exists "$INSTALL_MANIFEST" && return 1

    local created resource
    created=$(foundation_journal_created)
    while IFS= read -r resource; do
        [[ -n "$resource" ]] || continue
        case "$resource" in
            "path:$CLI_PATH") ;;
            *) return 1 ;;
        esac
    done <<<"$created"

    recovery_paths_safe_for_cleanup || return 1
    remove_recovery_bootstrap || return 1
    foundation_journal_clear
    rmdir "$INSTALL_STATE_DIR" 2>/dev/null || true
    rmdir "$STATE_ROOT" 2>/dev/null || true
    printf 'Pre-manifest transaction и временный recovery bootstrap безопасно удалены. Установленные apt packages намеренно не удаляются.\n'
}

base_automatic_initial_rollback_definition=$(declare -f automatic_initial_rollback)
base_automatic_initial_rollback_definition=${base_automatic_initial_rollback_definition/#"automatic_initial_rollback ()"/"base_automatic_initial_rollback ()"}
eval "$base_automatic_initial_rollback_definition"
unset base_automatic_initial_rollback_definition

automatic_initial_rollback() {
    local rc
    if ! recovery_paths_safe_for_cleanup; then
        error 'Recovery bootstrap path небезопасен; automatic rollback заблокирован, чтобы сохранить journal для install-recover.'
        return 1
    fi
    set +e
    base_automatic_initial_rollback "$@"
    rc=$?
    set -e
    if (( rc == 0 )); then
        remove_recovery_bootstrap || {
            error 'Основной rollback завершён, но recovery bootstrap не удалось удалить; он сохранён для install-recover.'
            return 1
        }
    fi
    return "$rc"
}

base_run_deploy_definition=$(declare -f run_deploy)
base_run_deploy_definition=${base_run_deploy_definition/#"run_deploy ()"/"base_run_deploy ()"}
eval "$base_run_deploy_definition"
unset base_run_deploy_definition

run_deploy() {
    local rc
    set +e
    base_run_deploy "$@"
    rc=$?
    set -e
    if (( rc == 0 )) && [[ "$INITIAL_INSTALL" == true ]]; then
        remove_recovery_bundle ||
            warn 'Установка успешна, но временный recovery bootstrap не удалось удалить; он останется как безопасный fallback.'
    fi
    return "$rc"
}

base_recover_install_definition=$(declare -f recover_install)
base_recover_install_definition=${base_recover_install_definition/#"recover_install ()"/"base_recover_install ()"}
eval "$base_recover_install_definition"
unset base_recover_install_definition

recover_install() {
    local rc
    set +e
    base_recover_install "$@"
    rc=$?
    set -e
    if (( rc == 0 )); then
        remove_recovery_bundle ||
            warn 'Recovery подтверждён, но temporary bootstrap не удалось удалить; повторный install-recover очистит его.'
    fi
    return "$rc"
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
    if ! recovery_paths_safe_for_cleanup; then
        error 'Recovery bootstrap path небезопасен; rollback заблокирован, чтобы сохранить journal для install-recover.'
        return 1
    fi
    flock -u 200 2>/dev/null || true
    set +e
    bash "$SCRIPT_DIR/uninstall_foundation.sh" \
        --purge-data --yes --incomplete-install
    local rollback_rc=$?
    set -e
    if (( rollback_rc == 0 )); then
        remove_recovery_bootstrap || return 1
    fi
    return "$rollback_rc"
}

if [[ "${INSTALL_SAFE_ACTIVATION_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_activation_policy.sh is source-only\n' >&2
    exit 64
fi