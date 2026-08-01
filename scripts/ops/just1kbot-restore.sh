#!/bin/bash
# Compatibility entrypoint: artifact-only invocation is always rehearsal;
# production actions are delegated to the separate root-only cutover engine.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}

resolve_safe_script() {
    local candidate=$1 mode
    [[ -f "$candidate" && ! -L "$candidate" ]] || return 1
    mode=$(stat -c '%a' "$candidate") || return 1
    (( (8#$mode & 8#022) == 0 )) || return 1
    realpath -e -- "$candidate"
}

resolve_engine() {
    local candidate resolved
    for candidate in \
        "$SCRIPT_DIR/production_restore.sh" \
        "$PROJECT_DIR/scripts/ops/production_restore.sh"; do
        resolved=$(resolve_safe_script "$candidate" 2>/dev/null || true)
        [[ -n "$resolved" ]] && { printf '%s\n' "$resolved"; return 0; }
    done
    return 1
}

resolve_rehearsal() {
    local candidate resolved
    for candidate in \
        "$SCRIPT_DIR/restore_rehearsal.sh" \
        /usr/local/bin/restore_rehearsal.sh \
        "$PROJECT_DIR/scripts/ops/restore_rehearsal.sh"; do
        resolved=$(resolve_safe_script "$candidate" 2>/dev/null || true)
        [[ -n "$resolved" ]] && { printf '%s\n' "$resolved"; return 0; }
    done
    return 1
}

case ${1:-} in
    production|status|rollback|finalize)
        engine=$(resolve_engine) || {
            printf 'production restore engine is unavailable\n' >&2
            exit 1
        }
        exec bash "$engine" "$@"
        ;;
    help|-h|--help)
        cat <<'EOF_USAGE'
Just1kBot restore entrypoint

Isolated rehearsal:
  AGE_IDENTITY_FILE=/secure/key just1kbot-restore.sh ARTIFACT
  AGE_IDENTITY_FILE=/secure/key just1kbot-restore.sh rehearsal ARTIFACT

Production lifecycle:
  just1kbot-restore.sh production ARTIFACT
  just1kbot-restore.sh status
  just1kbot-restore.sh rollback
  just1kbot-restore.sh finalize
EOF_USAGE
        ;;
    rehearsal)
        shift
        rehearsal=$(resolve_rehearsal) || {
            printf 'restore rehearsal tool is unavailable\n' >&2
            exit 1
        }
        exec bash "$rehearsal" "$@"
        ;;
    "")
        printf 'restore artifact or action is required\n' >&2
        exit 2
        ;;
    *)
        rehearsal=$(resolve_rehearsal) || {
            printf 'restore rehearsal tool is unavailable\n' >&2
            exit 1
        }
        exec bash "$rehearsal" "$@"
        ;;
esac
