#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

while IFS= read -r -d '' script; do
    bash -n "$script"
done < <(find . -type f -name '*.sh' -print0)

help_output="$(bash just1kbot.sh --help)"
for command in install update status doctor backup restore uninstall; do
    grep -q "$command" <<< "$help_output" || fail "help does not mention $command"
done

required_patterns=(
    'readonly RELEASES_DIR="${APP_ROOT}/releases"'
    'readonly ENV_FILE="${CONFIG_DIR}/just1kbot.env"'
    'readonly BACKUP_KEY_FILE="${CONFIG_DIR}/backup.agekey"'
    'age --recipient'
    'certbot certonly'
    '/health'
    'redis-cli'
    'ProtectSystem=strict'
    'runuser -u'
    'pg_dump'
    'pg_restore'
    'flock -n'
    'ALLOW_UPDATE_WITHOUT_BACKUP'
)
for required_file in \
    installer/entrypoint.sh \
    installer/lib/core.sh \
    installer/lib/platform.sh \
    installer/lib/release.sh \
    installer/lib/commands.sh; do
    [[ -f "$required_file" ]] || fail "missing installer module: $required_file"
done

for pattern in "${required_patterns[@]}"; do
    grep -RFq -- "$pattern" just1kbot.sh installer \
        || fail "missing production invariant: $pattern"
done

for forbidden in 'SERVICE_USER' '/root/.config/just1kbot' 'rm -rf /etc/letsencrypt'; do
    if grep -RFq -- "$forbidden" just1kbot.sh installer; then
        fail "forbidden installer pattern found: $forbidden"
    fi
done

[[ ! -e fix_rollback_ctrlc.py ]] || fail 'legacy patch helper must not be committed'

python3 -m compileall -q bot config database services utils alembic
printf 'Installer checks passed.\n'
