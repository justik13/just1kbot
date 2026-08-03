#!/bin/bash
# Safe transition from a legacy/shared Redis endpoint to the dedicated service.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

record_legacy_redis_transition() {
    [[ "$INITIAL_INSTALL" == false ]] || return 0

    local old_url endpoint
    old_url=$(read_env_value REDIS_URL)
    [[ -n "$old_url" ]] || {
        error 'Legacy REDIS_URL отсутствует; dedicated Redis transition невозможен'
        return 1
    }

    endpoint=$(REDIS_URL_VALUE="$old_url" python3 - <<'PY'
import os
from urllib.parse import urlsplit

parsed = urlsplit(os.environ["REDIS_URL_VALUE"])
if parsed.scheme != "redis":
    raise SystemExit("legacy Redis scheme must be redis")
if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("legacy Redis host must be local")
port = parsed.port or 6379
database = parsed.path.lstrip("/") or "0"
if database != "0":
    raise SystemExit("legacy Redis database must be 0")
print(f"{parsed.hostname}:{port}/{database}")
PY
) || {
        error 'Legacy REDIS_URL не прошёл local endpoint validation'
        return 1
    }

    if [[ "$endpoint" == "127.0.0.1:${REDIS_PORT}/0" ||
          "$endpoint" == "localhost:${REDIS_PORT}/0" ||
          "$endpoint" == "::1:${REDIS_PORT}/0" ]]; then
        foundation_manifest_set_metadata legacy_redis_transition already-dedicated
        return 0
    fi

    # Aiogram's default FSM namespace is only "fsm:" and does not include a
    # bot ID. Copying those keys from a shared Redis could therefore import
    # another bot's state. The safe policy is to leave the old Redis entirely
    # untouched and begin with empty ephemeral FSM state in the dedicated
    # instance after the application has been stopped by the transaction.
    foundation_warn \
        "Legacy Redis $endpoint не будет читаться, очищаться или копироваться: FSM state ephemeral и namespace не доказывает ownership."
    foundation_manifest_set_metadata \
        legacy_redis_transition ephemeral-fsm-not-copied
    foundation_manifest_set_metadata legacy_redis_source_endpoint "$endpoint"
    foundation_manifest_set_metadata legacy_redis_source_modified false
}

if [[ "${INSTALL_SAFE_REDIS_TRANSITION_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_redis_transition.sh is source-only\n' >&2
    exit 64
fi
