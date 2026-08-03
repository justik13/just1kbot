#!/bin/bash
# Final command routing overrides loaded after control_plane_completion.sh.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

final_definition=$(declare -f dispatch)
final_definition=${final_definition/#"dispatch ()"/"final_base_dispatch ()"}
eval "$final_definition"
unset final_definition

dispatch() {
    local command=${1:-}
    shift || true
    case "$command" in
        repair)
            if (( $# == 0 )); then
                set -- --check
            fi
            call_script ops/repair_complete.sh "$@"
            ;;
        *) final_base_dispatch "$command" "$@" ;;
    esac
}

if [[ "${CONTROL_PLANE_FINAL_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'control_plane_final.sh is source-only\n' >&2
    exit 64
fi
