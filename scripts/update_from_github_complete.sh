#!/bin/bash
# Fetch fixed GitHub main into a root-only release and deploy one verified SHA.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly REPOSITORY_URL='https://github.com/justik13/projectx.git'
readonly REPOSITORY_REF='refs/heads/main'
readonly RELEASE_ROOT='/var/lib/just1kbot/source-releases'
readonly LIVE_DIR='/opt/just1kbot'
readonly RELEASE_METADATA='.release-version'
readonly RELEASE_RETENTION=3
readonly GIT_TIMEOUT_SECONDS=120
readonly UPDATE_LOCK='/run/lock/just1kbot-update.lock'

ASSUME_YES=false
CHECK_ONLY=false
DRY_RUN=false
EXPECTED_SHA=''
TEMP_RELEASE=''
PUBLISHED_RELEASE=''
TARGET_SHA=''
CURRENT_SHA='unknown'

log() { printf '[github-update] %s\n' "$*"; }

fail() {
    printf '\nОШИБКА JUST1KBOT GITHUB UPDATE\n' >&2
    printf 'Проблема: %s\n' "$1" >&2
    printf 'Причина: %s\n' "${2:-$1}" >&2
    printf 'Что сделать: %s\n' "${3:-Исправьте причину и повторите update.}" >&2
    exit 1
}

usage() {
    cat <<'EOF_USAGE'
Just1kBot — exact-SHA update from fixed GitHub main
Repository: https://github.com/justik13/projectx

Usage:
  sudo bash deploy.sh update --check
  sudo bash deploy.sh update
  sudo bash deploy.sh update --sha <40-hex-commit> --yes
  sudo bash deploy.sh update --sha <40-hex-commit> --dry-run

--sha SHA  expected commit; fetched main must match exactly.
--yes      unattended mode; allowed only with --sha.
--check    read-only installed/fetched commit comparison.
--dry-run  verify release and execute non-mutating deploy plan.
EOF_USAGE
}

cleanup() {
    local rc=$?
    if [[ -n "$TEMP_RELEASE" && -d "$TEMP_RELEASE" && ! -L "$TEMP_RELEASE" ]]; then
        rm -rf -- "$TEMP_RELEASE"
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

is_valid_sha() { [[ ${1:-} =~ ^[0-9a-f]{40}$ ]]; }
require_root() { (( EUID == 0 )) || fail 'команда запущена не от root' 'EUID != 0' 'Повторите через sudo.'; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "не найдена команда: $1"; }

parse_args() {
    while (( $# > 0 )); do
        case "$1" in
            --sha)
                shift
                (( $# > 0 )) || { printf '%s\n' '--sha требует значение' >&2; exit 2; }
                EXPECTED_SHA=${1,,}
                is_valid_sha "$EXPECTED_SHA" || { printf '%s\n' '--sha должен быть полным 40-hex commit SHA' >&2; exit 2; }
                ;;
            --yes) ASSUME_YES=true ;;
            --check) CHECK_ONLY=true ;;
            --dry-run) DRY_RUN=true ;;
            -h|--help) usage; exit 0 ;;
            *) printf 'Неизвестный аргумент update: %s\n' "$1" >&2; exit 2 ;;
        esac
        shift
    done
    [[ "$CHECK_ONLY" == false || "$DRY_RUN" == false ]] || { printf '%s\n' '--check и --dry-run нельзя объединять' >&2; exit 2; }
    [[ "$ASSUME_YES" == false || -n "$EXPECTED_SHA" ]] || { printf '%s\n' '--yes requires --sha' >&2; exit 2; }
}

acquire_update_lock() {
    install -d -o root -g root -m 0755 "$(dirname "$UPDATE_LOCK")"
    exec 199>"$UPDATE_LOCK"
    flock -n 199 || fail 'GitHub update lock занят' 'другой update уже выполняется'
}

validate_secure_directory() {
    local directory=$1 owner mode real
    [[ -d "$directory" && ! -L "$directory" ]] || return 1
    real=$(realpath -e -- "$directory") || return 1
    [[ "$real" == "$directory" ]] || return 1
    owner=$(stat -c '%u' "$directory") || return 1
    mode=$(stat -c '%a' "$directory") || return 1
    [[ "$owner" == 0 ]] || return 1
    (( (8#$mode & 8#022) == 0 ))
}

prepare_release_root() {
    [[ ! -L /var/lib/just1kbot && ! -L "$RELEASE_ROOT" ]] || fail 'release root содержит symlink'
    install -d -o root -g root -m 0711 /var/lib/just1kbot
    install -d -o root -g root -m 0700 "$RELEASE_ROOT"
    validate_secure_directory /var/lib/just1kbot || fail '/var/lib/just1kbot имеет unsafe owner/mode'
    validate_secure_directory "$RELEASE_ROOT" || fail "$RELEASE_ROOT имеет unsafe owner/mode"
}

read_current_sha() {
    local metadata="$LIVE_DIR/$RELEASE_METADATA" value count owner mode
    CURRENT_SHA=unknown
    [[ -f "$metadata" && ! -L "$metadata" ]] || return 0
    owner=$(stat -c '%u' "$metadata" 2>/dev/null || true)
    mode=$(stat -c '%a' "$metadata" 2>/dev/null || true)
    [[ "$owner" == 0 && "$mode" =~ ^[0-7]{3,4}$ ]] || return 0
    (( (8#$mode & 8#022) == 0 )) || return 0
    grep -Fxq "source_repository=$REPOSITORY_URL" "$metadata" || return 0
    grep -Fxq "source_ref=$REPOSITORY_REF" "$metadata" || return 0
    count=$(grep -c '^source_commit=' "$metadata" 2>/dev/null || true)
    [[ "$count" == 1 ]] || return 0
    value=$(sed -n 's/^source_commit=//p' "$metadata")
    is_valid_sha "$value" && CURRENT_SHA=${value,,}
}

run_git() {
    timeout --foreground "$GIT_TIMEOUT_SECONDS" \
        env -i PATH="$PATH" HOME=/root LC_ALL=C \
        GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
        git -C "$TEMP_RELEASE" \
        -c core.hooksPath=/dev/null \
        -c protocol.file.allow=never \
        -c protocol.ext.allow=never \
        -c protocol.version=2 "$@"
}

validate_tracked_entries() {
    run_git ls-files -s -z | python3 -c '
import sys
for raw in sys.stdin.buffer.read().split(b"\0"):
    if not raw:
        continue
    metadata, path = raw.split(b"\t", 1)
    mode = metadata.split(b" ", 1)[0]
    if mode in {b"120000", b"160000"}:
        raise SystemExit(f"symlink or submodule is forbidden: {path!r}")
    if any(byte < 32 or byte == 127 for byte in path):
        raise SystemExit(f"control character in tracked path: {path!r}")
'
}

required_release_files() {
    cat <<'EOF_REQUIRED'
deploy.sh
requirements.txt
requirements.lock
alembic.ini
bot/main.py
scripts/install_safe.sh
scripts/update_from_github.sh
scripts/update_from_github_complete.sh
scripts/inspect_install_state.sh
scripts/preflight_install_state.sh
scripts/uninstall_entrypoint.sh
scripts/uninstall_foundation.sh
scripts/verify_uninstall_state.sh
scripts/lib/control_plane.sh
scripts/lib/control_plane_completion.sh
scripts/lib/control_plane_final.sh
scripts/lib/installer_diagnostics.sh
scripts/lib/installer_foundation.sh
scripts/lib/installer_foundation_compat.sh
scripts/lib/install_safe_platform.sh
scripts/lib/install_safe_platform_support.sh
scripts/lib/install_safe_release_contract.sh
scripts/lib/install_safe_lock_policy.sh
scripts/lib/install_safe_runtime.sh
scripts/lib/install_safe_tls_policy.sh
scripts/lib/install_safe_postgres_ownership.sh
scripts/lib/install_safe_proxy_mode.sh
scripts/lib/install_safe_package_policy.sh
scripts/lib/install_safe_activation_policy.sh
scripts/lib/install_safe_failure_injection.sh
scripts/lib/install_safe_dispatch.sh
scripts/lib/postgresql.sh
scripts/lib/operational_transaction.sh
scripts/lib/uninstall_safe_core.sh
scripts/lib/uninstall_safe_actions.sh
scripts/lib/uninstall_safe_ownership.sh
scripts/ops/deploy_application.sh
scripts/ops/backup_postgres.sh
scripts/ops/verify_backup.sh
scripts/ops/restore_rehearsal.sh
scripts/ops/just1kbot-restore.sh
scripts/ops/doctor.sh
scripts/ops/doctor_complete.sh
scripts/ops/doctor_json.sh
scripts/ops/repair.sh
scripts/ops/repair_complete.sh
scripts/ops/support_bundle.sh
EOF_REQUIRED
}

validate_checkout() {
    [[ "$(run_git rev-parse --verify 'HEAD^{commit}')" == "$TARGET_SHA" ]] || fail 'checkout HEAD не совпадает с fetched commit'
    [[ "$(run_git config --get remote.origin.url)" == "$REPOSITORY_URL" ]] || fail 'remote origin неожиданно изменён'
    run_git fsck --strict --no-dangling >/dev/null
    run_git diff --quiet --ignore-submodules=none
    [[ -z "$(run_git status --porcelain=v1 --untracked-files=no)" ]] || fail 'checkout не является чистым'
    [[ ! -e "$TEMP_RELEASE/.gitmodules" && ! -L "$TEMP_RELEASE/.gitmodules" ]] || fail 'submodules запрещены production updater'
    validate_tracked_entries || fail 'repository содержит запрещённый tracked object'
    if find "$TEMP_RELEASE" -path "$TEMP_RELEASE/.git" -prune -o -type l -print -quit | grep -q .; then
        fail 'repository содержит symlink'
    fi

    local required
    while IFS= read -r required; do
        [[ -n "$required" ]] || continue
        [[ -f "$TEMP_RELEASE/$required" && ! -L "$TEMP_RELEASE/$required" ]] ||
            fail "release не содержит required safety file: $required"
    done < <(required_release_files)

    bash -n "$TEMP_RELEASE/deploy.sh"
    find "$TEMP_RELEASE/scripts" -type f -name '*.sh' -print0 |
        xargs -0 -r bash -n
}

fetch_release() {
    local fetched
    TEMP_RELEASE=$(mktemp -d "$RELEASE_ROOT/.incoming.XXXXXX")
    run_git init -q
    run_git remote add origin "$REPOSITORY_URL"
    run_git fetch --quiet --depth=1 --no-tags origin "$REPOSITORY_REF"
    fetched=$(run_git rev-parse --verify 'FETCH_HEAD^{commit}')
    fetched=${fetched,,}
    is_valid_sha "$fetched" || fail 'GitHub вернул invalid commit SHA'
    TARGET_SHA=$fetched
    if [[ -n "$EXPECTED_SHA" && "$TARGET_SHA" != "$EXPECTED_SHA" ]]; then
        fail 'fetched main не совпадает с ожидаемым SHA' \
            "expected=$EXPECTED_SHA fetched=$TARGET_SHA" \
            'Повторите --check, изучите новый commit и передайте полный SHA явно.'
    fi
    run_git checkout --quiet --detach --force "$TARGET_SHA"
    validate_checkout
}

write_release_metadata() {
    local metadata="$TEMP_RELEASE/$RELEASE_METADATA" temporary="$metadata.tmp"
    cat > "$temporary" <<EOF_METADATA
format_version=1
source_repository=$REPOSITORY_URL
source_ref=$REPOSITORY_REF
source_commit=$TARGET_SHA
fetched_at_utc=$(date -u +%FT%TZ)
EOF_METADATA
    chmod 0600 "$temporary"
    mv -- "$temporary" "$metadata"
}

harden_and_publish_release() {
    local stamp final
    write_release_metadata
    chown -R root:root "$TEMP_RELEASE"
    find "$TEMP_RELEASE" -xdev -type d -exec chmod 0700 {} +
    find "$TEMP_RELEASE" -xdev -type f -perm /111 -exec chmod 0700 {} +
    find "$TEMP_RELEASE" -xdev -type f ! -perm /111 -exec chmod 0600 {} +
    find "$TEMP_RELEASE" -xdev -perm /022 -print -quit | grep -q . && fail 'release writable для group/other'
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    final="$RELEASE_ROOT/release-${stamp}-${TARGET_SHA:0:12}-$$"
    [[ ! -e "$final" && ! -L "$final" ]] || fail 'release directory collision'
    mv -- "$TEMP_RELEASE" "$final"
    TEMP_RELEASE=''
    PUBLISHED_RELEASE=$final
}

confirm_update() {
    [[ "$ASSUME_YES" == true ]] && return 0
    [[ -t 0 ]] || fail 'interactive confirmation requires TTY' 'stdin is not a terminal' 'Use --sha <full-commit> --yes.'
    printf '\nFetched main commit: %s\n' "$TARGET_SHA"
    printf 'Enter the complete SHA to install it.\n'
    local answer
    read -r -p 'SHA: ' answer
    [[ "${answer,,}" == "$TARGET_SHA" ]] || { log 'update cancelled'; exit 0; }
}

cleanup_old_releases() {
    local item count=0
    while IFS= read -r item; do
        [[ -n "$item" ]] || continue
        count=$((count + 1))
        (( count <= RELEASE_RETENTION )) || rm -rf -- "$item" || log "warning: cannot remove old release $item"
    done < <(find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'release-*' -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
}

run_transactional_deploy() {
    local rc=0
    local -a arguments=(deploy)
    [[ "$ASSUME_YES" == true ]] && arguments+=(--yes)
    [[ "$DRY_RUN" == true ]] && arguments+=(--dry-run)
    log "transactional deploy from $PUBLISHED_RELEASE"
    set +e
    env \
        JUST1KBOT_SOURCE_COMMIT="$TARGET_SHA" \
        JUST1KBOT_SOURCE_REPOSITORY="$REPOSITORY_URL" \
        JUST1KBOT_SOURCE_REF="$REPOSITORY_REF" \
        bash "$PUBLISHED_RELEASE/deploy.sh" "${arguments[@]}"
    rc=$?
    set -e
    if (( rc == 0 )); then
        cleanup_old_releases
        log "update completed: $TARGET_SHA"
    else
        printf 'Update failed code=%s. Verified source release preserved: %s\n' "$rc" "$PUBLISHED_RELEASE" >&2
    fi
    return "$rc"
}

main() {
    parse_args "$@"
    require_root
    local command
    for command in git python3 realpath stat find grep sed sort cut mktemp install date timeout flock xargs; do
        require_command "$command"
    done
    acquire_update_lock
    prepare_release_root
    read_current_sha
    fetch_release
    printf 'Installed commit: %s\n' "$CURRENT_SHA"
    printf 'Fetched main:     %s\n' "$TARGET_SHA"
    if [[ "$CHECK_ONLY" == true ]]; then
        [[ "$CURRENT_SHA" == "$TARGET_SHA" ]] && log 'update not required' || log 'update available'
        return 0
    fi
    if [[ "$CURRENT_SHA" == "$TARGET_SHA" && "$DRY_RUN" == false ]]; then
        log 'this exact version is already installed'
        return 0
    fi
    confirm_update
    harden_and_publish_release
    run_transactional_deploy
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
