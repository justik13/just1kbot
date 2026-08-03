#!/bin/bash
# Complete source-tree contract for local and release deployments.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

release_contract_definition=$(declare -f validate_source_tree)
release_contract_definition=${release_contract_definition/#"validate_source_tree ()"/"release_contract_base_validate_source_tree ()"}
eval "$release_contract_definition"
unset release_contract_definition

required_installer_files() {
    cat <<'EOF_REQUIRED'
deploy.sh
requirements.txt
requirements.lock
alembic.ini
bot/main.py
scripts/install_safe.sh
scripts/update_from_github.sh
scripts/update_from_github_complete.sh
scripts/inspect_install_state.sh
scripts/preflight_install_state.sh
scripts/uninstall_entrypoint.sh
scripts/uninstall_foundation.sh
scripts/verify_uninstall_state.sh
scripts/lib/control_plane.sh
scripts/lib/control_plane_completion.sh
scripts/lib/control_plane_final.sh
scripts/lib/installer_diagnostics.sh
scripts/lib/installer_foundation.sh
scripts/lib/installer_foundation_compat.sh
scripts/lib/install_safe_platform.sh
scripts/lib/install_safe_release_contract.sh
scripts/lib/install_safe_lock_policy.sh
scripts/lib/install_safe_legacy.sh
scripts/lib/install_safe_redis_transition.sh
scripts/lib/install_safe_runtime.sh
scripts/lib/install_safe_tls_policy.sh
scripts/lib/install_safe_postgres_ownership.sh
scripts/lib/install_safe_proxy_mode.sh
scripts/lib/install_safe_activation_policy.sh
scripts/lib/install_safe_failure_injection.sh
scripts/lib/install_safe_dispatch.sh
scripts/lib/postgresql.sh
scripts/lib/operational_transaction.sh
scripts/lib/uninstall_safe_core.sh
scripts/lib/uninstall_safe_actions.sh
scripts/lib/uninstall_safe_ownership.sh
scripts/ops/deploy_application.sh
scripts/ops/backup_postgres.sh
scripts/ops/verify_backup.sh
scripts/ops/restore_rehearsal.sh
scripts/ops/just1kbot-restore.sh
scripts/ops/doctor.sh
scripts/ops/doctor_complete.sh
scripts/ops/doctor_json.sh
scripts/ops/repair.sh
scripts/ops/repair_complete.sh
scripts/ops/support_bundle.sh
EOF_REQUIRED
}

validate_source_tree() {
    release_contract_base_validate_source_tree
    local required state
    while IFS= read -r required; do
        [[ -n "$required" ]] || continue
        [[ -f "$ROOT_DIR/$required" && ! -L "$ROOT_DIR/$required" ]] || {
            error "Source tree не содержит complete safety file: $required"
            return 1
        }
        state=$(stat -c '%U:%G %a' "$ROOT_DIR/$required" 2>/dev/null || true)
        [[ -n "$state" ]] || {
            error "Не удалось прочитать owner/mode: $required"
            return 1
        }
        mode=${state##* }
        (( (8#$mode & 8#022) == 0 )) || {
            error "Safety file writable group/other: $required mode=$mode"
            return 1
        }
    done < <(required_installer_files)
}

if [[ "${INSTALL_SAFE_RELEASE_CONTRACT_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_release_contract.sh is source-only\n' >&2
    exit 64
fi
