#!/bin/bash
# Source-only loader for the retained deploy function implementation.
# The implementation is stored as a non-entrypoint include under scripts/lib.

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CORE_INCLUDE="$SCRIPT_DIR/lib/deploy_core.inc"

if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" != 1 ]]; then
    printf 'direct execution is forbidden; use repository deploy.sh\n' >&2
    exit 64
fi

[[ -f "$CORE_INCLUDE" && ! -L "$CORE_INCLUDE" ]] || {
    printf 'missing safe deploy implementation include: %s\n' "$CORE_INCLUDE" >&2
    return 1 2>/dev/null || exit 1
}
owner=$(stat -c '%u' "$CORE_INCLUDE")
mode=$(stat -c '%a' "$CORE_INCLUDE")
if (( (8#$mode & 8#022) != 0 )) && (( ${EUID:-$(id -u)} == 0 )); then
    chmod go-w "$CORE_INCLUDE" 2>/dev/null || true
    mode=$(stat -c '%a' "$CORE_INCLUDE")
fi
(( (8#$mode & 8#022) == 0 )) || (( ${EUID:-$(id -u)} == 0 )) || {
    printf 'unsafe deploy implementation permissions: %s mode=%s\n' "$CORE_INCLUDE" "$mode" >&2
    return 1 2>/dev/null || exit 1
}
if (( EUID == 0 )) && [[ "$owner" != 0 ]]; then
    printf 'deploy implementation is not root-owned: %s\n' "$CORE_INCLUDE" >&2
    return 1 2>/dev/null || exit 1
fi

# shellcheck source=lib/deploy_core.inc
source "$CORE_INCLUDE"
