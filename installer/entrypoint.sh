#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# The caller may be inside a directory removed by a previous uninstall.
# Python/pip call getcwd(), so always switch to a stable working directory.
cd /

# shellcheck source=installer/lib/core.sh
source "$SCRIPT_DIR/lib/core.sh"
# shellcheck source=installer/lib/platform.sh
source "$SCRIPT_DIR/lib/platform.sh"
# shellcheck source=installer/lib/release.sh
source "$SCRIPT_DIR/lib/release.sh"
# shellcheck source=installer/lib/compat.sh
source "$SCRIPT_DIR/lib/compat.sh"
# shellcheck source=installer/lib/redis_fix.sh
source "$SCRIPT_DIR/lib/redis_fix.sh"
# shellcheck source=installer/lib/commands.sh
source "$SCRIPT_DIR/lib/commands.sh"

main "$@"
