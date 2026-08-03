#!/bin/bash
# Activation ordering for resources that must be covered by operational rollback.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

activate_release_bundle() {
    foundation_journal_update dedicated-redis
    foundation_setup_dedicated_redis "$REDIS_PASSWORD"
    setup_firewall_initial

    install_backup_tooling
    install_healthcheck
    setup_logrotate
    if [[ "$INITIAL_INSTALL" == true ]]; then
        setup_nginx_initial
    else
        refresh_existing_nginx
    fi
    setup_systemd
    foundation_install_cli
}

if [[ "${INSTALL_SAFE_ACTIVATION_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_activation_policy.sh is source-only\n' >&2
    exit 64
fi
