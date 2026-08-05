#!/bin/bash
# Compatibility entrypoint for the complete exact-SHA GitHub updater.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
TARGET="$SCRIPT_DIR/update_from_github_complete.sh"
[[ -f "$TARGET" && ! -L "$TARGET" ]] || {
    printf 'Complete GitHub updater missing or unsafe: %s\n' "$TARGET" >&2
    exit 1
}
mode=$(stat -c '%a' "$TARGET")
if (( (8#$mode & 8#022) != 0 )) && (( ${EUID:-$(id -u)} == 0 )); then
    chmod go-w "$TARGET" 2>/dev/null || true
    mode=$(stat -c '%a' "$TARGET")
fi
(( (8#$mode & 8#022) == 0 )) || (( ${EUID:-$(id -u)} == 0 )) || {
    printf 'Complete GitHub updater writable by group/other: %s\n' "$TARGET" >&2
    exit 1
}
exec /bin/bash "$TARGET" "$@"
