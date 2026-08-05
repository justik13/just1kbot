#!/bin/bash
# Build a root-only diagnostic archive without .env, dumps, keys or backups.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
OUTPUT_DIR=/root/just1kbot-support-bundles
DEPLOY_LOCK=/run/lock/just1kbot-deploy.lock
LIMIT=300

usage() {
    cat <<'EOF'
Usage: sudo just1kbot support-bundle [--output DIR]

Creates a root-only diagnostic tar.gz outside managed installation state.
It never includes .env, database dumps, backup archives, API keys, bot tokens
or age identities. The archive is an explicit operator artifact and is not
removed automatically by uninstall.
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --output)
            shift
            (( $# > 0 )) || { printf '%s\n' '--output requires DIR' >&2; exit 2; }
            OUTPUT_DIR=$1
            ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown support-bundle argument: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

(( EUID == 0 )) || { printf 'support-bundle must run as root\n' >&2; exit 1; }
[[ "$OUTPUT_DIR" == /* && "$OUTPUT_DIR" != /var/lib/just1kbot* && ! -L "$OUTPUT_DIR" ]] || {
    printf 'Unsafe output directory: %s\n' "$OUTPUT_DIR" >&2
    printf 'Choose an absolute root-owned directory outside /var/lib/just1kbot.\n' >&2
    exit 1
}
install -d -o root -g root -m 0700 "$OUTPUT_DIR"
[[ "$(stat -c '%U:%G %a' "$OUTPUT_DIR")" == 'root:root 700' ]] || {
    printf 'Output directory must be root:root 0700\n' >&2
    exit 1
}

install -d -o root -g root -m 0755 "$(dirname "$DEPLOY_LOCK")"
exec 201>"$DEPLOY_LOCK"
flock -s -w 10 201 || { printf 'operation lock is busy\n' >&2; exit 75; }

work=$(mktemp -d /run/just1kbot-support.XXXXXX)
trap 'rm -rf -- "$work"' EXIT INT TERM
install -d -o root -g root -m 0700 "$work/raw" "$work/bundle"

capture() {
    local name=$1
    shift
    set +e
    "$@" >"$work/raw/$name" 2>&1
    printf '%s\n' "$?" >"$work/raw/$name.exit-code"
    set -e
}

capture state.json bash "$ROOT_DIR/scripts/inspect_install_state.sh" --json
capture doctor.txt bash "$SCRIPT_DIR/doctor_complete.sh"
capture os-release.txt cat /etc/os-release
capture disk.txt df -hT
capture memory.txt free -h
capture listeners.txt ss -H -ltnp
capture nginx-test.txt nginx -t
capture manifest-stat.txt stat -c '%n %U:%G %a %s bytes %y' /var/lib/just1kbot/install-state/manifest.json
capture transaction-stat.txt stat -c '%n %U:%G %a %s bytes %y' /var/lib/just1kbot/install-state/transaction.json

for unit in \
    just1kbot.service \
    just1kbot-redis.service \
    just1kbot-healthcheck.service \
    just1kbot-healthcheck.timer \
    just1kbot-backup.service \
    just1kbot-backup.timer; do
    safe=${unit//[^A-Za-z0-9_.-]/_}
    capture "systemctl-${safe}.txt" systemctl show "$unit" \
        --property=Id,LoadState,ActiveState,SubState,UnitFileState,MainPID,ExecMainStatus,NRestarts,FragmentPath
    capture "journal-${safe}.txt" journalctl -u "$unit" -n "$LIMIT" --no-pager --output=short-iso
    capture "unit-${safe}.txt" systemctl cat "$unit"
done

if [[ -f /var/lib/just1kbot/install-state/manifest.json &&
      ! -L /var/lib/just1kbot/install-state/manifest.json ]]; then
    cp -- /var/lib/just1kbot/install-state/manifest.json "$work/raw/manifest.json"
fi
if [[ -f /var/lib/just1kbot/install-state/transaction.json &&
      ! -L /var/lib/just1kbot/install-state/transaction.json ]]; then
    cp -- /var/lib/just1kbot/install-state/transaction.json "$work/raw/transaction.json"
fi
if [[ -f /opt/just1kbot/.release-version && ! -L /opt/just1kbot/.release-version ]]; then
    cp -- /opt/just1kbot/.release-version "$work/raw/release-version.txt"
fi

RAW_DIR="$work/raw" OUTPUT_DIR_VALUE="$work/bundle" python3 - <<'PY'
import json
import os
import re
from pathlib import Path

raw_dir = Path(os.environ["RAW_DIR"])
out_dir = Path(os.environ["OUTPUT_DIR_VALUE"])
secret_key = re.compile(
    r"(?i)(BOT_TOKEN|PASSWORD|SECRET|API_KEY|DB_ENCRYPTION_KEY|DATABASE_URL|REDIS_URL|AGE_IDENTITY|PRIVATE_KEY)"
)
telegram_token = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{15,}\b")
url_credentials = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.I)
age_secret = re.compile(r"AGE-SECRET-KEY-[A-Z0-9]+", re.I)
assignment = re.compile(
    r"(?i)\b(BOT_TOKEN|PASSWORD|SECRET|API_KEY|DB_ENCRYPTION_KEY|DATABASE_URL|REDIS_URL)\s*[=:]\s*([^\s,;]+)"
)

def redact_text(text: str) -> str:
    text = telegram_token.sub("<redacted-telegram-token>", text)
    text = url_credentials.sub(lambda m: m.group("scheme") + "<redacted>@", text)
    text = age_secret.sub("<redacted-age-secret>", text)
    text = assignment.sub(lambda m: m.group(1) + "=<redacted>", text)
    return text

def sanitize_json(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if secret_key.search(str(key)):
                result[key] = "<redacted>"
            else:
                result[key] = sanitize_json(child)
        return result
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

for source in sorted(raw_dir.iterdir()):
    if source.is_symlink() or not source.is_file():
        continue
    destination = out_dir / source.name
    data = source.read_text(encoding="utf-8", errors="replace")
    if source.suffix == ".json":
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            destination.write_text(redact_text(data), encoding="utf-8")
        else:
            destination.write_text(
                json.dumps(sanitize_json(parsed), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    else:
        destination.write_text(redact_text(data), encoding="utf-8")
    destination.chmod(0o600)
PY

cat >"$work/bundle/README.txt" <<'EOF'
This archive intentionally excludes:
- /opt/just1kbot/.env
- PostgreSQL dumps and backup archives
- age identities and private keys
- bot tokens, API keys, passwords and credential-bearing URLs
- foreign Nginx configuration contents
EOF
chmod 0600 "$work/bundle/README.txt"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="$OUTPUT_DIR/just1kbot-support-${stamp}.tar.gz"
temporary="$archive.tmp.$$"
tar --numeric-owner --owner=0 --group=0 --mode='u=rw,go=' \
    -C "$work/bundle" -czf "$temporary" .
chown root:root "$temporary"
chmod 0600 "$temporary"
mv -- "$temporary" "$archive"
printf 'Support bundle created: %s\n' "$archive"
