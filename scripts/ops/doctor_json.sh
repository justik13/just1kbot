#!/bin/bash
# JSON adapter for the complete read-only Just1kBot doctor.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
DOCTOR="$SCRIPT_DIR/doctor_complete.sh"
[[ -f "$DOCTOR" && ! -L "$DOCTOR" ]] || {
    printf '{"error":"complete doctor script missing or unsafe"}\n' >&2
    exit 1
}

arguments=()
while (( $# > 0 )); do
    case "$1" in
        --smoke) arguments+=(--smoke) ;;
        -h|--help)
            printf 'Usage: doctor_json.sh [--smoke]\n'
            exit 0
            ;;
        *)
            printf '{"error":"unknown doctor argument"}\n' >&2
            exit 2
            ;;
    esac
    shift
done

temporary=$(mktemp /run/just1kbot-doctor-json.XXXXXX)
trap 'rm -f -- "$temporary"' EXIT INT TERM

set +e
bash "$DOCTOR" "${arguments[@]}" >"$temporary" 2>&1
rc=$?
set -e

DOCTOR_OUTPUT="$temporary" DOCTOR_RC="$rc" python3 - <<'PY'
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["DOCTOR_OUTPUT"])
checks = []
summaries = []
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    match = re.match(r"^\[(OK|WARN|FAIL)\]\s+(.*)$", line)
    if match:
        checks.append({"status": match.group(1).lower(), "message": match.group(2)})
    elif line.startswith("Doctor result:") or line.startswith("Complete doctor result:"):
        summaries.append(line)
payload = {
    "schema_version": 1,
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "exit_code": int(os.environ["DOCTOR_RC"]),
    "healthy": int(os.environ["DOCTOR_RC"]) == 0,
    "failures": sum(item["status"] == "fail" for item in checks),
    "warnings": sum(item["status"] == "warn" for item in checks),
    "checks": checks,
    "summaries": summaries,
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY

exit "$rc"
