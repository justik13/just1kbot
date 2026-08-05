#!/bin/bash
# Compatibility wrapper. Destructive removal is implemented only by the
# manifest-driven uninstaller.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
TARGET="$SCRIPT_DIR/uninstall_foundation.sh"

[[ -f "$TARGET" && ! -L "$TARGET" ]] || {
    printf 'ОШИБКА: manifest-driven uninstall отсутствует или небезопасен: %s\n' \
        "$TARGET" >&2
    exit 1
}
mode=$(stat -c '%a' "$TARGET")
if (( (8#$mode & 8#022) != 0 )) && (( ${EUID:-$(id -u)} == 0 )); then
    chmod go-w "$TARGET" 2>/dev/null || true
    mode=$(stat -c '%a' "$TARGET")
fi
(( (8#$mode & 8#022) == 0 )) || {
    printf 'ОШИБКА: uninstall writable для group/other: %s mode=%s\n' \
        "$TARGET" "$mode" >&2
    exit 1
}

exec /bin/bash "$TARGET" "$@"
