#!/bin/bash
# Just1kBot root control plane.
set -Eeuo pipefail
IFS=$'\n\t'
umask 027
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPTS_DIR="$ROOT_DIR/scripts"
require_safe_script(){ local path=$1 real scripts mode; [[ -f "$path" && ! -L "$path" ]] || { printf 'Ошибка: unsafe script %s\n' "$path" >&2; exit 1; }; real=$(realpath -e "$path"); scripts=$(realpath -e "$SCRIPTS_DIR"); [[ "$real" == "$scripts/"* ]] || { printf 'Ошибка: script вне scripts/: %s\n' "$path" >&2; exit 1; }; mode=$(stat -c %a "$real"); (( (8#$mode & 8#022)==0 )) || { printf 'Ошибка: script writable group/other: %s\n' "$path" >&2; exit 1; }; }
if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" == 1 ]]; then library="$SCRIPTS_DIR/deploy_full.sh"; require_safe_script "$library"; source "$library"; return 0 2>/dev/null || exit 0; fi
diagnostics="$SCRIPTS_DIR/lib/installer_diagnostics.sh"; require_safe_script "$diagnostics"; source "$diagnostics"; installer_set_operation control-plane; installer_set_step initialization 'Проверка control-plane modules.'; installer_set_log_file /var/log/just1kbot-deploy.log; installer_enable_diagnostics
module="$SCRIPTS_DIR/lib/control_plane.sh"; require_safe_script "$module"; source "$module"
completion="$SCRIPTS_DIR/lib/control_plane_completion.sh"; require_safe_script "$completion"; source "$completion"
dispatch "$@"
