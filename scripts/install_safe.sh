#!/bin/bash
# Shared-server-safe Just1kBot installer. Ubuntu 24.04 only.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
BASE="$SCRIPT_DIR/deploy_full.sh"
PG_LIB="$SCRIPT_DIR/lib/postgresql.sh"
OPS_LIB="$SCRIPT_DIR/lib/operational_transaction.sh"
FOUNDATION="$SCRIPT_DIR/lib/installer_foundation.sh"
FOUNDATION_COMPAT="$SCRIPT_DIR/lib/installer_foundation_compat.sh"
DIAGNOSTICS="$SCRIPT_DIR/lib/installer_diagnostics.sh"
for file in "$BASE" "$PG_LIB" "$OPS_LIB" "$FOUNDATION" "$FOUNDATION_COMPAT" "$DIAGNOSTICS"; do
    [[ -f "$file" && ! -L "$file" ]] || {
        printf 'ОШИБКА: отсутствует безопасный library %s\n' "$file" >&2
        exit 1
    }
done
# shellcheck source=lib/installer_diagnostics.sh
source "$DIAGNOSTICS"
installer_set_operation installer
installer_set_step initialization 'Загрузка installer libraries.'
installer_set_log_file /var/log/just1kbot-deploy.log
installer_enable_diagnostics
DEPLOY_FUNCTIONS_ONLY=1
# shellcheck source=deploy_full.sh
source "$BASE"
unset DEPLOY_FUNCTIONS_ONLY
# shellcheck source=lib/postgresql.sh
source "$PG_LIB"
# shellcheck source=lib/operational_transaction.sh
source "$OPS_LIB"
INSTALLER_FOUNDATION_SOURCE_ONLY=1
# shellcheck source=lib/installer_foundation.sh
source "$FOUNDATION"
unset INSTALLER_FOUNDATION_SOURCE_ONLY
INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
# shellcheck source=lib/installer_foundation_compat.sh
source "$FOUNDATION_COMPAT"
unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

SOURCE_DIR="$ROOT_DIR"
RUNTIME_DIR=/run/just1kbot
HEARTBEAT_FILE="$RUNTIME_DIR/heartbeat"
REDIS_SERVICE=just1kbot-redis.service
REDIS_PORT=6380
REQUIREMENTS_LOCK="$ROOT_DIR/requirements.lock"

for module in \
    "$SCRIPT_DIR/lib/install_safe_platform.sh" \
    "$SCRIPT_DIR/lib/install_safe_legacy.sh" \
    "$SCRIPT_DIR/lib/install_safe_redis_transition.sh" \
    "$SCRIPT_DIR/lib/install_safe_runtime.sh" \
    "$SCRIPT_DIR/lib/install_safe_dispatch.sh"; do
    [[ -f "$module" && ! -L "$module" ]] || {
        printf 'ОШИБКА: installer module отсутствует или небезопасен: %s\n' "$module" >&2
        exit 1
    }
    # shellcheck source=/dev/null
    source "$module"
done

case ${1:-} in
    --recover)
        shift
        (( $# == 0 )) || exit 2
        recover_install
        ;;
    --rollback-incomplete)
        shift
        (( $# == 0 )) || exit 2
        rollback_incomplete
        ;;
    *)
        main "$@"
        ;;
esac
