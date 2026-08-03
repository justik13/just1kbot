#!/bin/bash
# State-aware operator commands layered over the audited control plane.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

clone_control_function() {
    local source=$1 target=$2 definition
    definition=$(declare -f "$source") || return 1
    definition=${definition/#"$source ()"/"$target ()"}
    eval "$definition"
}

clone_control_function usage completion_base_usage
clone_control_function dispatch completion_base_dispatch
clone_control_function smoke completion_base_smoke

usage() {
    completion_base_usage
    cat <<'EOF'

Additional safe operations:
  sudo just1kbot doctor --json
  sudo just1kbot repair --check|--apply
  sudo just1kbot support-bundle [--output DIR]
  sudo just1kbot proxy-config
  sudo just1kbot deploy --external-proxy [--yes|--dry-run]

Without arguments the global CLI renders a menu based on the detected install
state. Foreign collisions and corrupted state expose diagnostics only.
EOF
}

smoke() {
    if ! call_script ops/doctor_complete.sh --smoke; then
        die \
            'итоговая complete diagnostics не пройдена' \
            'production health/ownership checks вернули ошибку; поздний smoke сам не выполняет rollback' \
            'Запустите doctor, support-bundle и documented recovery.'
    fi
}

read_install_state() {
    local output state_value
    set +e
    output=$(bash "$SCRIPTS_DIR/inspect_install_state.sh" --json 2>/dev/null)
    set -e
    state_value=$(STATE_JSON="$output" python3 - <<'PY' 2>/dev/null || true
import json
import os
try:
    value = json.loads(os.environ.get("STATE_JSON", ""))
except Exception:
    print("unknown")
else:
    print(value.get("state", "unknown"))
PY
)
    printf '%s\n' "${state_value:-unknown}"
}

show_proxy_config() {
    local manifest=/var/lib/just1kbot/install-state/manifest.json
    local snippet=/var/lib/just1kbot/install-state/external-proxy.nginx.conf
    [[ -f "$manifest" && ! -L "$manifest" && -f "$snippet" && ! -L "$snippet" ]] ||
        die 'external proxy contract отсутствует' \
            'installation не использует external proxy или файл небезопасен' \
            'Запустите state/doctor и не подключайте неизвестный snippet.'
    MANIFEST="$manifest" python3 - <<'PY' >/dev/null ||
        die 'manifest не подтверждает external proxy mode'
import json
import os
from pathlib import Path
x = json.loads(Path(os.environ["MANIFEST"]).read_text(encoding="utf-8"))
if x.get("metadata", {}).get("proxy_mode") != "external":
    raise SystemExit(1)
if "path:/var/lib/just1kbot/install-state/external-proxy.nginx.conf" not in x.get("managed_resources", []):
    raise SystemExit(1)
PY
    cat "$snippet"
}

dispatch() {
    local command=${1:-}
    shift || true
    case "$command" in
        status)
            call_script install_safe.sh --status "$@"
            call_script ops/doctor_complete.sh
            ;;
        doctor)
            if [[ "${1:-}" == --json ]]; then
                shift
                (( $# == 0 )) || die 'doctor --json не принимает дополнительные аргументы'
                call_script ops/doctor_json.sh
            else
                call_script ops/doctor_complete.sh "$@"
            fi
            ;;
        repair)
            if (( $# == 0 )); then
                set -- --check
            fi
            call_script ops/repair.sh "$@"
            ;;
        support-bundle)
            call_script ops/support_bundle.sh "$@"
            ;;
        proxy-config)
            (( $# == 0 )) || die 'proxy-config не принимает аргументы'
            show_proxy_config
            ;;
        deploy)
            local proxy_mode='' argument
            local -a forwarded=()
            for argument in "$@"; do
                case "$argument" in
                    --external-proxy)
                        [[ -z "$proxy_mode" ]] || die 'proxy mode указан несколько раз'
                        proxy_mode=external
                        ;;
                    --managed-proxy)
                        [[ -z "$proxy_mode" ]] || die 'proxy mode указан несколько раз'
                        proxy_mode=managed
                        ;;
                    *) forwarded+=("$argument") ;;
                esac
            done
            if [[ -n "$proxy_mode" ]]; then
                (
                    export JUST1KBOT_PROXY_MODE=$proxy_mode
                    completion_base_dispatch deploy "${forwarded[@]}"
                )
            else
                completion_base_dispatch deploy "${forwarded[@]}"
            fi
            ;;
        *) completion_base_dispatch "$command" "$@" ;;
    esac
}

menu_clean() {
    local choice
    cat <<'EOF'
1. Read-only installation dry-run
2. Install with managed Nginx/TLS
3. Install in external-proxy mode
4. State details
5. Create support bundle
0. Exit
EOF
    read -rp 'Choose: ' choice
    case "$choice" in
        1) dispatch deploy --dry-run ;;
        2) dispatch deploy ;;
        3) dispatch deploy --external-proxy ;;
        4) dispatch state ;;
        5) dispatch support-bundle ;;
        0) return 10 ;;
        *) printf 'Unknown choice.\n' >&2 ;;
    esac
}

menu_installed() {
    local choice file mode
    cat <<'EOF'
1. Status
2. Doctor
3. Doctor JSON
4. Check GitHub update
5. Install exact reviewed update
6. Logs
7. Restart
8. Create backup
9. Verify backup
10. Restore rehearsal
11. Repair check
12. Apply safe repair
13. Create support bundle
14. Show external proxy config
15. Uninstall
16. State details
0. Exit
EOF
    read -rp 'Choose: ' choice
    case "$choice" in
        1) dispatch status ;;
        2) dispatch doctor ;;
        3) dispatch doctor --json ;;
        4) dispatch update --check ;;
        5) dispatch update ;;
        6) dispatch logs ;;
        7) dispatch restart ;;
        8) dispatch backup ;;
        9) read -rp 'Backup path: ' file; dispatch verify-backup "$file" ;;
        10) read -rp 'Backup path: ' file; dispatch restore-test "$file" ;;
        11) dispatch repair --check ;;
        12) dispatch repair --apply ;;
        13) dispatch support-bundle ;;
        14) dispatch proxy-config ;;
        15) read -rp 'Mode (--keep-data or --purge-data): ' mode; dispatch uninstall "$mode" ;;
        16) dispatch state ;;
        0) return 10 ;;
        *) printf 'Unknown choice.\n' >&2 ;;
    esac
}

menu_partial() {
    local choice
    cat <<'EOF'
1. State details
2. Installation recovery status
3. Roll back incomplete first install
4. Doctor
5. Create support bundle
0. Exit
EOF
    read -rp 'Choose: ' choice
    case "$choice" in
        1) dispatch state ;;
        2) dispatch install-recover ;;
        3) dispatch install-rollback ;;
        4) dispatch doctor ;;
        5) dispatch support-bundle ;;
        0) return 10 ;;
        *) printf 'Unknown choice.\n' >&2 ;;
    esac
}

menu_legacy_or_residual() {
    local choice mode
    cat <<'EOF'
1. State details
2. Run strict legacy migration/deploy
3. Repair check
4. Create support bundle
5. Safe uninstall
0. Exit
EOF
    read -rp 'Choose: ' choice
    case "$choice" in
        1) dispatch state ;;
        2) dispatch deploy ;;
        3) dispatch repair --check ;;
        4) dispatch support-bundle ;;
        5) read -rp 'Mode (--keep-data or --purge-data): ' mode; dispatch uninstall "$mode" ;;
        0) return 10 ;;
        *) printf 'Unknown choice.\n' >&2 ;;
    esac
}

menu_blocked() {
    local choice
    cat <<'EOF'
1. State details
2. Doctor
3. Doctor JSON
4. Create support bundle
0. Exit

Mutating actions are hidden because ownership is not proven.
EOF
    read -rp 'Choose: ' choice
    case "$choice" in
        1) dispatch state ;;
        2) dispatch doctor ;;
        3) dispatch doctor --json ;;
        4) dispatch support-bundle ;;
        0) return 10 ;;
        *) printf 'Unknown choice.\n' >&2 ;;
    esac
}

menu() {
    local state_value rc
    while true; do
        state_value=$(read_install_state)
        printf '\nJust1kBot — state: %s\n' "$state_value"
        rc=0
        case "$state_value" in
            clean) menu_clean || rc=$? ;;
            installed_managed) menu_installed || rc=$? ;;
            partial_install) menu_partial || rc=$? ;;
            legacy_managed|residual_managed) menu_legacy_or_residual || rc=$? ;;
            foreign_collision|corrupted_state|unknown) menu_blocked || rc=$? ;;
            *) menu_blocked || rc=$? ;;
        esac
        (( rc != 10 )) || return 0
    done
}

if [[ "${CONTROL_PLANE_COMPLETION_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'control_plane_completion.sh is source-only\n' >&2
    exit 64
fi
