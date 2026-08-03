#!/bin/bash
# Complete doctor: runtime checks plus ownership/proxy invariants.
set -Eeuo pipefail
IFS=$'\n\t'
umask 027
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
BASE_DOCTOR="$SCRIPT_DIR/doctor.sh"
FOUNDATION="$ROOT_DIR/scripts/lib/installer_foundation.sh"
COMPAT="$ROOT_DIR/scripts/lib/installer_foundation_compat.sh"
PG_LIB="$ROOT_DIR/scripts/lib/postgresql.sh"
MANIFEST=/var/lib/just1kbot/install-state/manifest.json
MODE=summary
PROXY_MODE=managed
EXTRA_FAILURES=0

while (( $# > 0 )); do
    case "$1" in
        --smoke) MODE=smoke ;;
        -h|--help)
            printf 'Usage: doctor_complete.sh [--smoke]\n'
            exit 0
            ;;
        *) printf 'Unknown doctor argument: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

(( EUID == 0 )) || { printf 'doctor must run as root\n' >&2; exit 2; }
for file in "$BASE_DOCTOR" "$FOUNDATION" "$COMPAT" "$PG_LIB"; do
    [[ -f "$file" && ! -L "$file" ]] || {
        printf '[FAIL] doctor dependency missing/unsafe: %s\n' "$file" >&2
        exit 1
    }
done

if [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]]; then
    PROXY_MODE=$(MANIFEST_PATH="$MANIFEST" python3 - <<'PY' 2>/dev/null || printf unknown
import json
import os
from pathlib import Path
x = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))
print(x.get("metadata", {}).get("proxy_mode", "managed"))
PY
)
fi

arguments=()
[[ "$MODE" == smoke ]] && arguments+=(--smoke)
base_output=$(mktemp /run/just1kbot-doctor-base.XXXXXX)
trap 'rm -f -- "$base_output"' EXIT INT TERM
set +e
bash "$BASE_DOCTOR" "${arguments[@]}" >"$base_output" 2>&1
base_rc=$?
set -e

if [[ "$PROXY_MODE" == external ]]; then
    filtered=$(mktemp /run/just1kbot-doctor-filtered.XXXXXX)
    trap 'rm -f -- "$base_output" "$filtered"' EXIT INT TERM
    BASE_OUTPUT="$base_output" FILTERED_OUTPUT="$filtered" python3 - <<'PY'
import os
from pathlib import Path
source = Path(os.environ["BASE_OUTPUT"])
target = Path(os.environ["FILTERED_OUTPUT"])
skip = {
    "[OK] Nginx configuration valid",
    "[FAIL] nginx binary отсутствует",
    "[FAIL] nginx -t failed",
}
lines = []
for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
    if raw in skip or raw.startswith("Doctor result:"):
        continue
    lines.append(raw)
target.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    cat "$filtered"
    remaining_base_failures=$(grep -c '^\[FAIL\]' "$filtered" 2>/dev/null || true)
    remaining_base_warnings=$(grep -c '^\[WARN\]' "$filtered" 2>/dev/null || true)
    printf '\nBase doctor result after external-proxy filtering: failures=%s warnings=%s mode=%s\n' \
        "$remaining_base_failures" "$remaining_base_warnings" "$MODE"
    (( remaining_base_failures == 0 )) && base_rc=0 || base_rc=1
else
    cat "$base_output"
fi

INSTALLER_FOUNDATION_SOURCE_ONLY=1
# shellcheck source=../lib/installer_foundation.sh
source "$FOUNDATION"
unset INSTALLER_FOUNDATION_SOURCE_ONLY
INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
# shellcheck source=../lib/installer_foundation_compat.sh
source "$COMPAT"
unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY
# shellcheck source=../lib/postgresql.sh
source "$PG_LIB"

extra_ok() { printf '[OK] %s\n' "$*"; }
extra_fail() { EXTRA_FAILURES=$((EXTRA_FAILURES + 1)); printf '[FAIL] %s\n' "$*" >&2; }

check_cli() {
    local cli=/usr/local/sbin/just1kbot state
    if [[ ! -f "$cli" || -L "$cli" ]]; then
        extra_fail "Global CLI missing/unsafe: $cli"
        return
    fi
    state=$(stat -c '%U:%G %a' "$cli" 2>/dev/null || true)
    [[ "$state" == 'root:root 750' ]] && extra_ok 'Global CLI permissions' || extra_fail "Global CLI permissions: $state"
    grep -Fq 'CONTROL=/opt/just1kbot/deploy.sh' "$cli" && extra_ok 'Global CLI target' || extra_fail 'Global CLI target mismatch'
    foundation_manifest_has "path:$cli" && extra_ok 'Global CLI ownership proof' || extra_fail 'Global CLI absent from manifest'
}

check_postgres_comments() {
    pg_select_cluster >/dev/null 2>&1 || {
        extra_fail 'PostgreSQL cluster selection failed for ownership comments'
        return
    }
    [[ "$PG_STATUS" == online ]] || {
        extra_fail "PostgreSQL cluster not online: ${PG_VERSION}/${PG_CLUSTER}"
        return
    }
    local expected database_comment role_comment
    expected="managed-by=just1kbot;installation-id=$(foundation_manifest_id)"
    database_comment=$(pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_database'), '')
FROM pg_database
WHERE datname = :'db';
SQL
) || {
        extra_fail 'Cannot read database ownership comment'
        return
    }
    role_comment=$(pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_authid'), '')
FROM pg_authid
WHERE rolname = :'role';
SQL
) || {
        extra_fail 'Cannot read role ownership comment'
        return
    }
    [[ "$database_comment" == "$expected" ]] && extra_ok 'PostgreSQL database ownership comment' || extra_fail 'PostgreSQL database ownership comment mismatch'
    [[ "$role_comment" == "$expected" ]] && extra_ok 'PostgreSQL role ownership comment' || extra_fail 'PostgreSQL role ownership comment mismatch'
}

check_proxy_mode() {
    local mode snippet port domain listener
    mode=$(foundation_manifest_metadata proxy_mode 2>/dev/null || true)
    case "$mode" in
        managed|'')
            extra_ok 'Proxy mode: managed'
            ;;
        external)
            snippet=/var/lib/just1kbot/install-state/external-proxy.nginx.conf
            if [[ ! -f "$snippet" || -L "$snippet" ]]; then
                extra_fail "External proxy contract missing/unsafe: $snippet"
                return
            fi
            [[ "$(stat -c '%U:%G %a' "$snippet")" == 'root:root 600' ]] && extra_ok 'External proxy contract permissions' || extra_fail 'External proxy contract permissions mismatch'
            foundation_manifest_has "path:$snippet" && extra_ok 'External proxy ownership proof' || extra_fail 'External proxy contract absent from manifest'
            port=$(foundation_manifest_metadata internal_webhook_port 2>/dev/null || true)
            domain=$(foundation_manifest_metadata external_proxy_domain 2>/dev/null || true)
            [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || {
                extra_fail 'External proxy internal port metadata invalid'
                return
            }
            foundation_validate_domain "$domain" && extra_ok "External proxy domain: $domain" || extra_fail 'External proxy domain metadata invalid'
            listener=$(ss -H -ltn "( sport = :$port )" 2>/dev/null || true)
            if [[ -n "$listener" ]] && grep -Eq '127[.]0[.]0[.]1|\[::1\]' <<<"$listener"; then
                extra_ok "Application loopback listener: $port"
            else
                extra_fail "Application is not listening on loopback port $port"
            fi
            ;;
        *) extra_fail "Unknown proxy mode: $mode" ;;
    esac
}

if foundation_manifest_validate >/dev/null 2>&1; then
    check_cli
    check_postgres_comments
    check_proxy_mode
else
    extra_fail 'Complete doctor cannot verify invalid ownership manifest'
fi

printf '\nComplete doctor result: base_rc=%s extra_failures=%s mode=%s proxy=%s\n' \
    "$base_rc" "$EXTRA_FAILURES" "$MODE" "$PROXY_MODE"
(( base_rc == 0 && EXTRA_FAILURES == 0 ))
