#!/bin/bash
# Just1kBot root control plane.
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPTS_DIR="$ROOT_DIR/scripts"

# For deploy operations (no management flags), redirect to the safe installer
# which uses isolated Redis on port 6380 instead of modifying global Redis.
# Management commands (--status, --logs, --restart, --backup, --restore, --help)
# continue to use the legacy control plane.
case "${1:-}" in
    --status|--logs|--restart|--backup|--restore|--help|-h|--yes|-y|--force|--dry-run)
        # These use the legacy control plane for compatibility
        ;;
    *)
        # Deploy/update operation - use safe installer
        SAFE_INSTALLER="$SCRIPTS_DIR/install_safe.sh"
        if [[ -f "$SAFE_INSTALLER" && ! -L "$SAFE_INSTALLER" ]]; then
            exec bash "$SAFE_INSTALLER" "$@"
        else
            printf 'Ошибка: безопасный installer не найден: %s\n' "$SAFE_INSTALLER" >&2
            exit 1
        fi
        ;;
esac

require_safe_script(){
    local path=$1 real scripts mode target
    # Allow symlinks if target points to $PROJECT_DIR/deploy.sh or inside $SCRIPTS_DIR.
    # This enables symlink-based deploy strategies (e.g. /usr/local/sbin/just1kbot -> /opt/just1kbot/deploy.sh)
    # while maintaining security: target must resolve to a valid path, permissions are still checked.
    if [[ -L "$path" ]]; then
        target=$(readlink -f "$path" 2>/dev/null || true)
        if [[ -z "$target" || ! -f "$target" ]]; then
            printf 'Ошибка: symlink target недоступен: %s\n' "$path" >&2
            exit 1
        fi
        real=$(realpath -e "$target")
    elif [[ -f "$path" ]]; then
        real=$(realpath -e "$path")
    else
        printf 'Ошибка: script не найден: %s\n' "$path" >&2
        exit 1
    fi
    scripts=$(realpath -e "$SCRIPTS_DIR")
    # Allow: (a) scripts inside $SCRIPTS_DIR, (b) the primary deploy.sh at /opt/just1kbot/deploy.sh
    if [[ "$real" == "$scripts/"* || "$real" == "/opt/just1kbot/deploy.sh" ]]; then
        : # Valid target
    else
        printf 'Ошибка: script вне scripts/ и не является основным deploy.sh: %s\n' "$path" >&2
        exit 1
    fi
    mode=$(stat -c %a "$real")
    if (( (8#$mode & 8#022) != 0 )) && (( ${EUID:-$(id -u)} == 0 )); then
        chmod go-w "$real" 2>/dev/null || true
        mode=$(stat -c %a "$real")
    fi
    (( (8#$mode & 8#022)==0 )) || { printf 'Ошибка: script writable group/other: %s\n' "$path" >&2; exit 1; }
}

if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" == 1 ]]; then library="$SCRIPTS_DIR/deploy_full.sh"; require_safe_script "$library"; source "$library"; return 0 2>/dev/null || exit 0; fi

diagnostics="$SCRIPTS_DIR/lib/installer_diagnostics.sh"; require_safe_script "$diagnostics"; source "$diagnostics"; installer_set_operation control-plane; installer_set_step initialization 'Проверка control-plane modules.'; installer_set_log_file /var/log/just1kbot-deploy.log; installer_enable_diagnostics
module="$SCRIPTS_DIR/lib/control_plane.sh"; require_safe_script "$module"; source "$module"
completion="$SCRIPTS_DIR/lib/control_plane_completion.sh"; require_safe_script "$completion"; source "$completion"
final="$SCRIPTS_DIR/lib/control_plane_final.sh"; require_safe_script "$final"; source "$final"
ui="$SCRIPTS_DIR/lib/control_plane_ui.sh"; require_safe_script "$ui"; CONTROL_PLANE_UI_SOURCE_ONLY=1 source "$ui"; unset CONTROL_PLANE_UI_SOURCE_ONLY
dispatch "$@"