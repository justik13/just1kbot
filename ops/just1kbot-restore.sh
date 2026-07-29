#!/bin/bash
set -euo pipefail
echo 'Production database cutover is not supported. Running isolated restore rehearsal only.' >&2
exec /usr/local/bin/restore_rehearsal.sh "$@"
