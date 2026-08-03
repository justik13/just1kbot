#!/bin/bash
# Manifest-bounded repair for operational drift. Never adopts foreign resources.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
FOUNDATION="$ROOT_DIR/scripts/lib/installer_foundation.sh"
COMPAT="$ROOT_DIR/scripts/lib/installer_foundation_compat.sh"
DOCTOR="$SCRIPT_DIR/doctor.sh"
MODE=check
LOCK=/run/lock/just1kbot-deploy.lock
PROJECT_DIR=/opt/just1kbot
ENV_FILE="$PROJECT_DIR/.env"
BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
CLI_PATH=/usr/local/sbin/just1kbot
ISSUES=0
CHANGES=0

usage() {
    cat <<'EOF'
Usage:
  sudo just1kbot repair --check
  sudo just1kbot repair --apply

--check is read-only. --apply can repair only manifest-owned permissions,
service shell, CLI launcher, daemon-reload, autostart and inactive Just1kBot
units. It never rewrites Nginx, TLS, PostgreSQL data, firewall or foreign files.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --check) MODE=check ;;
        --apply) MODE=apply ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown repair argument: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

(( EUID == 0 )) || { printf 'repair must run as root\n' >&2; exit 1; }
for file in "$FOUNDATION" "$COMPAT" "$DOCTOR"; do
    [[ -f "$file" && ! -L "$file" ]] || {
        printf 'Unsafe or missing repair dependency: %s\n' "$file" >&2
        exit 1
    }
done
INSTALLER_FOUNDATION_SOURCE_ONLY=1
# shellcheck source=../lib/installer_foundation.sh
source "$FOUNDATION"
unset INSTALLER_FOUNDATION_SOURCE_ONLY
INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
# shellcheck source=../lib/installer_foundation_compat.sh
source "$COMPAT"
unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

install -d -o root -g root -m 0755 "$(dirname "$LOCK")"
exec 201>"$LOCK"
if [[ "$MODE" == apply ]]; then
    flock -n 201 || { printf 'operation lock is busy\n' >&2; exit 75; }
else
    flock -s -w 5 201 || { printf 'operation lock is busy\n' >&2; exit 75; }
fi

foundation_manifest_require
if foundation_path_exists "$INSTALL_JOURNAL"; then
    printf 'Repair blocked: unfinished transaction exists: %s\n' "$INSTALL_JOURNAL" >&2
    printf 'Use install-recover or install-rollback first.\n' >&2
    exit 1
fi

report_issue() {
    ISSUES=$((ISSUES + 1))
    printf '[NEEDS-REPAIR] %s\n' "$1"
}

report_ok() { printf '[OK] %s\n' "$1"; }
report_change() { CHANGES=$((CHANGES + 1)); printf '[REPAIRED] %s\n' "$1"; }

require_manifest_resource() {
    local resource=$1
    foundation_manifest_has "$resource" || {
        printf 'Repair refused: ownership proof missing: %s\n' "$resource" >&2
        exit 1
    }
}

check_service_account() {
    require_manifest_resource "service-user:$BOT_USER"
    local account home shell target=/usr/sbin/nologin
    [[ -x "$target" ]] || target=/sbin/nologin
    account=$(getent passwd "$BOT_USER" 2>/dev/null || true)
    if [[ -z "$account" ]]; then
        report_issue "service account $BOT_USER отсутствует; automatic recreation запрещена"
        return
    fi
    home=$(cut -d: -f6 <<<"$account")
    shell=$(cut -d: -f7 <<<"$account")
    [[ "$home" == "$BOT_HOME" ]] || {
        report_issue "service account home mismatch: $home"
        return
    }
    case "$shell" in
        /usr/sbin/nologin|/sbin/nologin) report_ok "service shell: $shell" ;;
        /bin/bash)
            report_issue 'legacy interactive service shell /bin/bash'
            if [[ "$MODE" == apply ]]; then
                usermod -s "$target" "$BOT_USER"
                report_change "service shell changed to $target"
            fi
            ;;
        *) report_issue "unexpected service shell: $shell" ;;
    esac
}

check_project_permissions() {
    require_manifest_resource "path:$PROJECT_DIR"
    [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || {
        report_issue "$PROJECT_DIR missing or unsafe; automatic recreation prohibited"
        return
    }
    if find "$PROJECT_DIR" -xdev -type l -not -path "$PROJECT_DIR/venv/*" -print -quit | grep -q .; then
        report_issue 'symlink outside virtualenv; permission repair blocked'
        return
    fi
    local project_state env_state
    project_state=$(stat -c '%U:%G %a' "$PROJECT_DIR")
    if [[ "$project_state" == 'root:just1kbot 750' ]]; then
        report_ok 'project directory permissions'
    else
        report_issue "project directory permissions: $project_state"
        if [[ "$MODE" == apply ]]; then
            chown root:"$BOT_USER" "$PROJECT_DIR"
            chmod 0750 "$PROJECT_DIR"
            report_change 'project directory owner/mode'
        fi
    fi
    if [[ ! -f "$ENV_FILE" || -L "$ENV_FILE" ]]; then
        report_issue '.env missing or unsafe; automatic recreation prohibited'
        return
    fi
    env_state=$(stat -c '%U:%G %a' "$ENV_FILE")
    if [[ "$env_state" == 'root:just1kbot 640' ]]; then
        report_ok '.env permissions'
    else
        report_issue ".env permissions: $env_state"
        if [[ "$MODE" == apply ]]; then
            chown root:"$BOT_USER" "$ENV_FILE"
            chmod 0640 "$ENV_FILE"
            report_change '.env owner/mode'
        fi
    fi
}

write_cli() {
    local temporary
    temporary=$(mktemp /usr/local/sbin/.just1kbot-repair.XXXXXX)
    cat >"$temporary" <<'EOF_CLI'
#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
CONTROL=/opt/just1kbot/deploy.sh
[[ -f "$CONTROL" && ! -L "$CONTROL" ]] || {
    printf 'Just1kBot control plane missing or unsafe: %s\n' "$CONTROL" >&2
    exit 1
}
exec /bin/bash "$CONTROL" "$@"
EOF_CLI
    chown root:root "$temporary"
    chmod 0750 "$temporary"
    mv -f -- "$temporary" "$CLI_PATH"
}

check_cli() {
    require_manifest_resource "path:$CLI_PATH"
    local valid=false
    if [[ -f "$CLI_PATH" && ! -L "$CLI_PATH" ]] &&
       [[ "$(stat -c '%U:%G %a' "$CLI_PATH")" == 'root:root 750' ]] &&
       grep -Fq 'CONTROL=/opt/just1kbot/deploy.sh' "$CLI_PATH"; then
        valid=true
    fi
    if [[ "$valid" == true ]]; then
        report_ok 'global CLI launcher'
    else
        report_issue 'global CLI launcher missing or drifted'
        if [[ "$MODE" == apply ]]; then
            write_cli
            report_change 'global CLI launcher'
        fi
    fi
}

unit_owned_and_safe() {
    local unit=$1 path=$2 marker=$3
    require_manifest_resource "$marker"
    [[ -f "$path" && ! -L "$path" ]] || return 1
    [[ "$(stat -c '%U:%G' "$path")" == root:root ]] || return 1
    grep -Fq 'Just1kBot' "$path" || return 1
    [[ -z "$unit" ]] || systemctl cat "$unit" >/dev/null 2>&1
}

check_unit() {
    local unit=$1 path=$2 marker=$3 should_start=$4
    if ! unit_owned_and_safe "$unit" "$path" "$marker"; then
        report_issue "$unit missing, unsafe or marker mismatch; automatic rewrite prohibited"
        return
    fi
    local enabled active
    enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
    active=$(systemctl is-active "$unit" 2>/dev/null || true)
    if [[ "$enabled" == enabled ]]; then
        report_ok "$unit enabled"
    else
        report_issue "$unit enabled state: ${enabled:-unknown}"
        if [[ "$MODE" == apply ]]; then
            systemctl enable "$unit" >/dev/null
            report_change "$unit enabled"
        fi
    fi
    if [[ "$should_start" == true ]]; then
        if [[ "$active" == active ]]; then
            report_ok "$unit active"
        else
            report_issue "$unit active state: ${active:-unknown}"
            if [[ "$MODE" == apply ]]; then
                systemctl start "$unit"
                report_change "$unit started"
            fi
        fi
    fi
}

check_service_account
check_project_permissions
check_cli
if [[ "$MODE" == apply ]]; then
    systemctl daemon-reload
fi
check_unit just1kbot-redis.service /etc/systemd/system/just1kbot-redis.service systemd:just1kbot-redis.service true
check_unit just1kbot.service /etc/systemd/system/just1kbot.service systemd:just1kbot.service true
for timer in just1kbot-healthcheck.timer just1kbot-backup.timer; do
    check_unit "$timer" "/etc/systemd/system/$timer" "path:/etc/systemd/system/$timer" true
done

printf '\nRepair result: mode=%s issues=%s changes=%s\n' "$MODE" "$ISSUES" "$CHANGES"
if [[ "$MODE" == apply ]]; then
    bash "$DOCTOR"
else
    (( ISSUES == 0 ))
fi
