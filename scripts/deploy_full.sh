#!/bin/bash
# Source-only compatibility wrapper for the legacy deploy function library.
# Production operators must use repository deploy.sh or scripts/deploy.sh so
# PostgreSQL discovery, root-owned releases, and operational rollback overrides
# are always installed.

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
LIBRARY="$SCRIPT_DIR/deploy_full_library.sh"

if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" != 1 ]]; then
    printf 'direct execution is forbidden; use repository deploy.sh\n' >&2
    exit 64
fi

[[ -f "$LIBRARY" && ! -L "$LIBRARY" ]] || {
    printf 'missing safe deploy function library: %s\n' "$LIBRARY" >&2
    return 1 2>/dev/null || exit 1
}
mode=$(stat -c '%a' "$LIBRARY")
(( (8#$mode & 8#022) == 0 )) || {
    printf 'unsafe deploy function library permissions: %s mode=%s\n' "$LIBRARY" "$mode" >&2
    return 1 2>/dev/null || exit 1
}

# shellcheck source=deploy_full_library.sh
source "$LIBRARY"

# The retained legacy library used one cleanup function for EXIT and signals.
# Preserve its cleanup ownership while restoring conventional signal statuses.
trap cleanup_temp_files EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
