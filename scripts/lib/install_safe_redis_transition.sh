#!/bin/bash
# DEPRECATED: Legacy Redis transition module removed.
# Fresh installations always start with dedicated Redis from the beginning.
# This file is retained only as a placeholder for backward compatibility.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

record_legacy_redis_transition() {
    # No-op for fresh installations: no legacy Redis to transition from.
    return 0
}

if [[ "${INSTALL_SAFE_REDIS_TRANSITION_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_redis_transition.sh is source-only\n' >&2
    exit 64
fi
