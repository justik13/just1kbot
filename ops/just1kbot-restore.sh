#!/bin/bash
set -euo pipefail
if [[ ${1:-} == --production ]]; then
    shift
    exec /usr/local/bin/restore_production.sh "$@"
fi
echo 'Production restore requires --production and --confirm-production-restore; running isolated rehearsal.' >&2
exec /usr/local/bin/restore_rehearsal.sh "$@"
