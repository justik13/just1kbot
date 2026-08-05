#!/bin/bash
# Manifest-driven Just1kBot uninstall. Never changes firewall or global Redis.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
FOUNDATION="$SCRIPT_DIR/lib/installer_foundation.sh"
PG_LIB="$SCRIPT_DIR/lib/postgresql.sh"
FOUNDATION_COMPAT="$SCRIPT_DIR/lib/installer_foundation_compat.sh"
[[ -f "$FOUNDATION" && ! -L "$FOUNDATION" && -f "$PG_LIB" && ! -L "$PG_LIB" && -f "$FOUNDATION_COMPAT" && ! -L "$FOUNDATION_COMPAT" ]] || { echo 'ОШИБКА: uninstall libraries отсутствуют или небезопасны' >&2; exit 1; }
INSTALLER_FOUNDATION_SOURCE_ONLY=1
# shellcheck source=lib/installer_foundation.sh
source "$FOUNDATION"
unset INSTALLER_FOUNDATION_SOURCE_ONLY
INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
# shellcheck source=lib/installer_foundation_compat.sh
source "$FOUNDATION_COMPAT"
unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY
# shellcheck source=lib/postgresql.sh
source "$PG_LIB"

MODE=
ASSUME_YES=false
INCOMPLETE_INSTALL=false
LOCK=/run/lock/just1kbot-deploy.lock
DOMAIN=
WEBHOOK_PORT=8080

for module in \
    "$SCRIPT_DIR/lib/uninstall_safe_core.sh" \
    "$SCRIPT_DIR/lib/uninstall_safe_actions.sh" \
    "$SCRIPT_DIR/lib/uninstall_safe_ownership.sh"; do
    [[ -f "$module" && ! -L "$module" ]] || { printf 'ОШИБКА: uninstall module отсутствует или небезопасен: %s\n' "$module" >&2; exit 1; }
    # shellcheck source=/dev/null
    source "$module"
done

main(){
    parse "$@"
    (( EUID==0 )) || fail 'запустите uninstall от root'
    acquire_lock
    manifest_preflight
    read_env
    resolve_managed_domain
    confirm
    prepare_postgres
    [[ "$MODE" == keep ]] && backup_before_keep
    prepare_uninstall_journal
    foundation_journal_update stopping-services
    stop_units
    foundation_journal_update removing-nginx
    remove_nginx
    remove_certificate
    foundation_journal_update removing-database
    purge_postgres
    foundation_journal_update removing-files
    remove_files
    purge_saved
    remove_user
    post_verify
    if [[ "$MODE" == keep ]]; then
        # Preserve only explicit data resources. Operational resources, including
        # Nginx/TLS and external proxy snippets, have already been removed.
        MANIFEST="$INSTALL_MANIFEST" python3 - <<'PY'
import json,os
from pathlib import Path
p=Path(os.environ['MANIFEST'])
x=json.loads(p.read_text())
x['managed_resources']=[
    v for v in x['managed_resources']
    if v.startswith('postgresql:') or v in (
        'path:/var/lib/just1kbot/backups',
        'path:/etc/just1kbot-backup.conf',
        'path:/etc/just1kbot/backup.agekey',
    )
]
x.setdefault('metadata', {})['application_removed'] = True
t=p.with_suffix('.tmp')
t.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
t.chmod(0o600)
t.replace(p)
PY
        chown root:root "$INSTALL_MANIFEST"
        chmod 0600 "$INSTALL_MANIFEST"
        foundation_journal_clear
        printf 'Приложение удалено; PostgreSQL и backups сохранены с residual ownership manifest.\n'
    else
        foundation_journal_clear
        rm -f "$INSTALL_MANIFEST"
        rmdir "$INSTALL_STATE_DIR" 2>/dev/null||true
        rmdir "$STATE_ROOT" 2>/dev/null||true
        printf 'Just1kBot и manifest-owned data полностью удалены. Firewall/global Redis/foreign resources не изменялись.\n'
    fi
}
main "$@"
