#!/bin/bash
# Activation ordering for resources covered by operational rollback.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

activate_release_bundle() {
    foundation_journal_update dedicated-redis
    foundation_setup_dedicated_redis "$REDIS_PASSWORD"
    setup_firewall_initial
    installer_failpoint after-dedicated-redis || return $?

    install_backup_tooling
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
    installer_failpoint after-cli || return $?
}

if [[ "${INSTALL_SAFE_ACTIVATION_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_activation_policy.sh is source-only\n' >&2
    exit 64
fi
