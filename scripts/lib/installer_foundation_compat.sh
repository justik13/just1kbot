#!/bin/bash
# Compatibility API used by the safe installer/uninstaller.
# Source after installer_foundation.sh.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

foundation_exists() { foundation_path_exists "$1"; }
foundation_manifest_add() { foundation_manifest_add_resource "$1"; }
foundation_manifest_remove() { foundation_manifest_remove_resource "$1"; }
foundation_manifest_has() { foundation_manifest_has_resource "$1"; }

foundation_manifest_resources() {
    foundation_manifest_require
    MANIFEST_PATH="$INSTALL_MANIFEST" python3 - <<'PY'
import json
import os
from pathlib import Path
for value in json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))["managed_resources"]:
    print(value)
PY
}

foundation_manifest_metadata() {
    local key=$1
    foundation_manifest_require
    MANIFEST_PATH="$INSTALL_MANIFEST" META_KEY="$key" python3 - <<'PY'
import json
import os
from pathlib import Path
value = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))["metadata"].get(os.environ["META_KEY"], "")
print(str(value).lower() if isinstance(value, bool) else value)
PY
}

foundation_manifest_id() {
    foundation_manifest_require
    MANIFEST_PATH="$INSTALL_MANIFEST" python3 - <<'PY'
import json
import os
from pathlib import Path
print(json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))["installation_id"])
PY
}

foundation_manifest_update_source() {
    foundation_manifest_set_metadata source_repository "$1"
    foundation_manifest_set_metadata source_ref "$2"
    foundation_manifest_set_metadata source_commit "$3"
}

foundation_journal_update() {
    local phase=$1 note=${2:-}
    foundation_journal_update_phase "$phase"
    if [[ -n "$note" ]]; then
        JOURNAL_PATH="$INSTALL_JOURNAL" NOTE_VALUE="$note" python3 - <<'PY'
import json
import os
from pathlib import Path
path = Path(os.environ["JOURNAL_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
data.setdefault("notes", []).append(os.environ["NOTE_VALUE"])
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(path)
PY
        chown root:root "$INSTALL_JOURNAL"
        chmod 0600 "$INSTALL_JOURNAL"
    fi
}

foundation_journal_add_created() { foundation_journal_add_created_resource "$1"; }
foundation_journal_created() { foundation_journal_created_resources; }
foundation_journal_clear() { foundation_journal_finish; }

foundation_journal_operation() {
    foundation_journal_validate || return 1
    JOURNAL_PATH="$INSTALL_JOURNAL" python3 - <<'PY'
import json
import os
from pathlib import Path
print(json.loads(Path(os.environ["JOURNAL_PATH"]).read_text(encoding="utf-8"))["operation"])
PY
}

# A resource created by the current durable transaction is already owned by
# this installer operation even before its final manifest entry is written.
# This is required for multi-phase activation where recovery bootstrap files
# are journaled first and the normal manifest is populated later.
foundation_journal_has_created_resource() {
    local resource=$1
    foundation_journal_validate || return 1
    JOURNAL_PATH="$INSTALL_JOURNAL" RESOURCE_VALUE="$resource" python3 - <<'PY' >/dev/null
import json
import os
from pathlib import Path

value = os.environ["RESOURCE_VALUE"]
data = json.loads(Path(os.environ["JOURNAL_PATH"]).read_text(encoding="utf-8"))
raise SystemExit(0 if value in data.get("created_resources", []) else 1)
PY
}

foundation_resource_owned_by_current_operation() {
    local resource=$1
    foundation_manifest_has_resource "$resource" && return 0
    foundation_journal_has_created_resource "$resource" && return 0
    return 1
}

# Override the foundation preflight hooks after foundation.sh has been sourced.
# Existing/foreign resources remain blocked; only resources proven by the
# current manifest or durable journal are accepted.
foundation_preflight_path_absent_or_owned() {
    local path=$1 marker=$2 description=$3
    foundation_path_exists "$path" || return 0
    foundation_resource_owned_by_current_operation "$marker" && return 0
    foundation_fail \
        FOREIGN_COLLISION \
        "$description уже существует без ownership proof" \
        "$path найден, но resource '$marker' отсутствует в manifest и текущем journal" \
        'Перенесите чужой объект или выполните документированную legacy migration; installer не будет его перезаписывать.'
}

foundation_assert_ubuntu_2404() { foundation_exact_ubuntu_2404; }
foundation_reserved_path_preflight() { foundation_preflight_path_absent_or_owned "$@"; }

foundation_port_preflight() {
    local port=$1 resource=$2 label=$3 unit=${4:-}
    if ! foundation_port_in_use "$port"; then
        return 0
    fi
    if foundation_resource_owned_by_current_operation "$resource"; then
        [[ -z "$unit" ]] || systemctl is-active --quiet "$unit" 2>/dev/null
        return
    fi
    foundation_fail PORT_COLLISION \
        "$label port $port занят" \
        'listener не принадлежит manifest или текущей installer transaction' \
        'Освободите порт или выберите другой.'
}

foundation_setup_dedicated_redis() { foundation_write_dedicated_redis_config "$1"; }
foundation_noop_firewall() { foundation_firewall_noop; }
foundation_setup_nginx_and_tls() { foundation_setup_nginx_tls "$@"; }

if [[ "${INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY:-0}" != 1 && "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'installer_foundation_compat.sh is source-only\n' >&2
    exit 64
fi
