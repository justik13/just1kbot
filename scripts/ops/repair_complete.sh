#!/bin/bash
# Run manifest-bounded repair and verify the result with complete doctor.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPAIR="$SCRIPT_DIR/repair.sh"
DOCTOR="$SCRIPT_DIR/doctor_complete.sh"
for file in "$REPAIR" "$DOCTOR"; do
    [[ -f "$file" && ! -L "$file" ]] || {
        printf 'Repair dependency missing or unsafe: %s\n' "$file" >&2
        exit 1
    }
done

mode=${1:---check}
case "$mode" in
    --check)
        (( $# <= 1 )) || exit 2
        exec bash "$REPAIR" --check
        ;;
    --apply)
        (( $# <= 1 )) || exit 2
        bash "$REPAIR" --apply
        exec bash "$DOCTOR"
        ;;
    -h|--help)
        exec bash "$REPAIR" --help
        ;;
    *)
        printf 'Unknown repair mode: %s\n' "$mode" >&2
        exit 2
        ;;
esac
