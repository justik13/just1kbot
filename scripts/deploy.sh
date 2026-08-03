#!/bin/bash
# Compatibility wrapper. The former direct deploy adapter is intentionally
# unreachable: all mutations must pass through the root safe control plane.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
CONTROL="$ROOT_DIR/deploy.sh"

[[ -f "$CONTROL" && ! -L "$CONTROL" ]] || {
    printf 'ОШИБКА: safe root control plane отсутствует или небезопасен: %s\n' \
        "$CONTROL" >&2
    exit 1
}
mode=$(stat -c '%a' "$CONTROL")
(( (8#$mode & 8#022) == 0 )) || {
    printf 'ОШИБКА: safe root control plane writable для group/other: %s mode=%s\n' \
        "$CONTROL" "$mode" >&2
    exit 1
}

exec /bin/bash "$CONTROL" deploy "$@"
