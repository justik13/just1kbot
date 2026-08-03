#!/bin/bash
# DEPRECATED: Legacy migration module removed.
# Fresh installations do not require nginx/certificate/service-account adoption.
# This file is retained only as a placeholder for backward compatibility.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

if [[ "${INSTALL_SAFE_LEGACY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_legacy.sh is source-only\n' >&2
    exit 64
fi
