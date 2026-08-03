#!/bin/bash
# Strict validation for the production dependency lock.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

validate_lock() {
    [[ -f "$REQUIREMENTS_LOCK" && ! -L "$REQUIREMENTS_LOCK" ]] || {
        error 'requirements.lock отсутствует или небезопасен'
        return 1
    }

    LOCK="$REQUIREMENTS_LOCK" python3 - <<'PY_LOCK' || {
        error 'requirements.lock должен содержать только exact versions, SHA-256 hashes и безопасные markers'
        return 1
    }
import os
import re
from pathlib import Path

path = Path(os.environ["LOCK"])
text = path.read_text(encoding="utf-8")

forbidden = (
    "--index-url",
    "--extra-index-url",
    "--trusted-host",
    "--find-links",
    "--no-index",
    "--pre",
    "--editable",
    "-e ",
    "git+",
    "hg+",
    "svn+",
    "bzr+",
    "file:",
    "http://",
    "https://",
    " @ ",
)
for token in forbidden:
    if token.lower() in text.lower():
        raise SystemExit(f"forbidden lock directive/reference: {token}")

logical = []
current = ""
for raw in text.splitlines():
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        continue
    current = f"{current} {stripped}".strip()
    if current.endswith("\\"):
        current = current[:-1].rstrip()
        continue
    logical.append(current)
    current = ""
if current:
    logical.append(current)

name_and_pin = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?=="
    r"[A-Za-z0-9][A-Za-z0-9.!+_~-]*"
)
hash_pattern = re.compile(r"(?:^|\s)--hash=sha256:[0-9a-f]{64}(?:\s|$)")
marker_pattern = re.compile(r"\s;\s[A-Za-z0-9_ .<>=!'\"()&|+-]+$")

if not logical:
    raise SystemExit("empty requirements.lock")

for line in logical:
    requirement = line
    marker_match = marker_pattern.search(line)
    if marker_match:
        requirement = line[: marker_match.start()]
    first = requirement.split()[0]
    if name_and_pin.match(first) is None:
        raise SystemExit(f"not an exact pinned requirement: {line}")
    if hash_pattern.search(requirement) is None:
        raise SystemExit(f"missing SHA-256 hash: {line}")
    for token in requirement.split():
        if token.startswith("--") and not token.startswith("--hash=sha256:"):
            raise SystemExit(f"unsupported per-requirement option: {token}")
PY_LOCK
}

if [[ "${INSTALL_SAFE_LOCK_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_lock_policy.sh is source-only\n' >&2
    exit 64
fi
