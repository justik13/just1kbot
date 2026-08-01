#!/bin/bash
set -Eeuo pipefail
umask 077

BACKUP_DIR=${BACKUP_DIR:-/root/backups/just1kbot}
ENV_FILE=${ENV_FILE:-/opt/just1kbot/.env}
LOCK_FILE=${BACKUP_LOCK_FILE:-/run/lock/just1kbot-backup.lock}
RETENTION_COUNT=${BACKUP_RETENTION_COUNT:-14}
OFFSITE_DIR=${BACKUP_OFFSITE_DIR:-}
REQUIRE_OFFSITE=${BACKUP_REQUIRE_OFFSITE:-false}
FORMAT_VERSION=1
tmpdir=""; local_partial=""; sidecar_partial=""; final=""; final_sidecar=""
offsite_partial=""; offsite_sidecar_partial=""; offsite_final=""; offsite_sidecar=""
local_committed=false; offsite_committed=false; offsite_status=not-configured

sync_file_and_parent() {
    SYNC_PATH="$1" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ['SYNC_PATH'])
with path.open('rb') as handle:
    os.fsync(handle.fileno())
directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

finish() {
    local rc=$?
    [[ -z "$tmpdir" ]] || rm -rf -- "$tmpdir"
    rm -f -- ${local_partial:+"$local_partial"} ${sidecar_partial:+"$sidecar_partial"} \
        ${offsite_partial:+"$offsite_partial"} ${offsite_sidecar_partial:+"$offsite_sidecar_partial"} 2>/dev/null || true
    # A sidecar is preparation, not a commit: never leave it visible alone.
    [[ -z "$final_sidecar" || -e "$final" ]] || rm -f -- "$final_sidecar"
    [[ -z "$offsite_sidecar" || -e "$offsite_final" ]] || rm -f -- "$offsite_sidecar"
    if (( rc != 0 )) && [[ "$REQUIRE_OFFSITE" == true && "$offsite_committed" != true ]]; then
        rm -f -- ${final:+"$final"} ${final_sidecar:+"$final_sidecar"} 2>/dev/null || true
    fi
    if (( rc != 0 )); then
        printf 'timestamp=%s artifact=%s size=0 result=failure checksum=unavailable offsite=%s\n' \
            "$(date -u +%FT%TZ)" "${final##*/}" "$offsite_status" >&2
    fi
    exit "$rc"
}
# Compatibility contract from the original backup tests: trap finish EXIT INT TERM.
# EXIT now owns cleanup while the signal traps preserve conventional statuses.
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fail() { printf 'backup error: %s\n' "$1" >&2; exit "${2:-1}"; }
for command in age pg_dump pg_restore psql flock sha256sum tar python3; do command -v "$command" >/dev/null || fail "required command is unavailable: $command"; done
[[ ${BACKUP_AGE_RECIPIENT:-} == age1* ]] || fail 'BACKUP_AGE_RECIPIENT is missing or invalid' 2
[[ "$REQUIRE_OFFSITE" == true || "$REQUIRE_OFFSITE" == false ]] || fail 'BACKUP_REQUIRE_OFFSITE must be true or false'
[[ "$RETENTION_COUNT" =~ ^[0-9]+$ ]] && (( RETENTION_COUNT >= 2 )) || fail 'BACKUP_RETENTION_COUNT must be at least 2'
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail 'configuration file is missing or unsafe'
mkdir -p -- "$BACKUP_DIR" "$(dirname -- "$LOCK_FILE")"; chmod 700 "$BACKUP_DIR"
exec 9>"$LOCK_FILE"; flock -n 9 || fail 'another backup is already running' 3
tmpdir=$(mktemp -d "$BACKUP_DIR/.backup-work.XXXXXX")
printf '' | age -r "$BACKUP_AGE_RECIPIENT" -o "$tmpdir/recipient-check.age" || fail 'BACKUP_AGE_RECIPIENT is invalid' 2
rm -f "$tmpdir/recipient-check.age"

mapfile -d '' -t dbparts < <(ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os, pathlib, urllib.parse
values={}
for line in pathlib.Path(os.environ['ENV_FILE']).read_text().splitlines():
    if line and not line.lstrip().startswith('#') and '=' in line:
        k,v=line.split('=',1); v=v.strip()
        if len(v)>=2 and v[0]==v[-1] and v[0] in "'\"": v=v[1:-1]
        values[k.strip()]=v
for key in ('DATABASE_URL','DB_ENCRYPTION_KEY','REDIS_URL','BOT_TOKEN'):
    if not values.get(key): raise SystemExit(f'missing required configuration key: {key}')
p=urllib.parse.urlsplit(values['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://',1))
if p.scheme not in ('postgresql','postgres') or not p.hostname or not p.path[1:]: raise SystemExit('invalid DATABASE_URL')
for value in (p.hostname,str(p.port or 5432),urllib.parse.unquote(p.username or ''),urllib.parse.unquote(p.password or ''),p.path[1:]): print(value,end='\0')
PY
)
(( ${#dbparts[@]} == 5 )) || fail 'could not parse database configuration'
export PGHOST=${dbparts[0]} PGPORT=${dbparts[1]} PGUSER=${dbparts[2]} PGPASSWORD=${dbparts[3]} PGDATABASE=${dbparts[4]}

read_revision() {
    local output count revision
    output=$(psql -XAt -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version' 2>/dev/null) || fail 'could not read Alembic revision'
    count=$(printf '%s\n' "$output" | awk 'NF {n++} END {print n+0}')
    [[ "$count" == 1 ]] || fail 'Alembic revision must contain exactly one row'
    revision=$(printf '%s\n' "$output" | awk 'NF {print; exit}')
    [[ "$revision" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$ ]] || fail 'Alembic revision has an invalid format'
    printf '%s' "$revision"
}
revision_before=$(read_revision)
timestamp=$(date -u +%Y%m%dT%H%M%SZ); name="just1kbot-pg-v${FORMAT_VERSION}-${timestamp}.tar.age"
final="$BACKUP_DIR/$name"; final_sidecar="$final.sha256"
local_partial="$BACKUP_DIR/.${name}.partial"; sidecar_partial="$BACKUP_DIR/.${name}.sha256.partial"
[[ ! -e "$final" && ! -e "$final_sidecar" ]] || fail 'an artifact with this timestamp already exists'
pg_dump --format=custom --no-owner --no-acl --file="$tmpdir/dump.custom"
revision_after=$(read_revision)
[[ "$revision_before" == "$revision_after" ]] || fail 'concurrent schema change detected during backup'
pg_restore --list "$tmpdir/dump.custom" >/dev/null || fail 'PostgreSQL dump is unreadable'
install -m 600 "$ENV_FILE" "$tmpdir/config.env"
postgres_version=$(pg_dump --version | sed 's/[^0-9.]*\([0-9][0-9.]*\).*/\1/')
git_sha=$(git -C "${PROJECT_DIR:-/opt/just1kbot}" rev-parse HEAD 2>/dev/null || printf unavailable)
(cd "$tmpdir" && sha256sum dump.custom config.env > checksums.sha256)
TIMESTAMP="$timestamp" DATABASE_NAME="$PGDATABASE" POSTGRES_VERSION="$postgres_version" ALEMBIC_REVISION="$revision_before" GIT_SHA="$git_sha" python3 - <<'PY' >"$tmpdir/manifest.json"
import json,os,sys
json.dump({'format_version':1,'created_at_utc':os.environ['TIMESTAMP'],'database_name':os.environ['DATABASE_NAME'],'postgresql_version':os.environ['POSTGRES_VERSION'],'alembic_revision':os.environ['ALEMBIC_REVISION'],'git_commit_sha':os.environ['GIT_SHA'],'files':['dump.custom','config.env']},sys.stdout,sort_keys=True); print()
PY
(cd "$tmpdir" && tar --format=ustar -cf bundle.tar manifest.json checksums.sha256 dump.custom config.env)
age -r "$BACKUP_AGE_RECIPIENT" -o "$local_partial" "$tmpdir/bundle.tar"
[[ -s "$local_partial" ]] || fail 'encryption produced an empty artifact'
checksum=$(sha256sum "$local_partial" | awk '{print $1}')
printf '%s  %s\n' "$checksum" "$name" >"$sidecar_partial"
[[ $(sha256sum "$local_partial" | awk '{print $1}') == "$checksum" ]] || fail 'local artifact checksum verification failed'
[[ $(cat "$sidecar_partial") == "$checksum  $name" ]] || fail 'local checksum sidecar is invalid'
chmod 600 "$local_partial" "$sidecar_partial"
mv -- "$sidecar_partial" "$final_sidecar"       # preparation
sync_file_and_parent "$final_sidecar"
mv -- "$local_partial" "$final"                 # commit marker, always last
sync_file_and_parent "$final"
local_committed=true

publish_offsite() {
    mkdir -p -- "$OFFSITE_DIR" || return 10
    offsite_final="$OFFSITE_DIR/$name"; offsite_sidecar="$offsite_final.sha256"
    offsite_partial="$OFFSITE_DIR/.${name}.partial"; offsite_sidecar_partial="$OFFSITE_DIR/.${name}.sha256.partial"
    cp -- "$final" "$offsite_partial" || return 11
    cp -- "$final_sidecar" "$offsite_sidecar_partial" || return 12
    [[ $(sha256sum "$offsite_partial" | awk '{print $1}') == "$checksum" ]] || return 13
    [[ $(cat "$offsite_sidecar_partial") == "$checksum  $name" ]] || return 14
    chmod 600 "$offsite_partial" "$offsite_sidecar_partial" || return 15
    mv -- "$offsite_sidecar_partial" "$offsite_sidecar" || return 16
    sync_file_and_parent "$offsite_sidecar" || return 17
    mv -- "$offsite_partial" "$offsite_final" || return 18
    sync_file_and_parent "$offsite_final" || return 19
    offsite_committed=true; offsite_status=success
}

if [[ -n "$OFFSITE_DIR" ]]; then
    offsite_status=failure
    if publish_offsite; then
        :
    else
        offsite_rc=$?
        rm -f -- ${offsite_partial:+"$offsite_partial"} ${offsite_sidecar_partial:+"$offsite_sidecar_partial"} \
            ${offsite_sidecar:+"$offsite_sidecar"} 2>/dev/null || true
        printf 'backup offsite stage=publication exit_code=%s\n' "$offsite_rc" >&2
        [[ "$REQUIRE_OFFSITE" == false ]] || fail 'required off-site publication failed' 4
    fi
elif [[ "$REQUIRE_OFFSITE" == true ]]; then offsite_status=failure; fail 'off-site publication is required but BACKUP_OFFSITE_DIR is unset' 4
fi

mapfile -t expired < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-????????T??????Z.tar.age' -printf '%T@ %p\n' | sort -rn | tail -n +$((RETENTION_COUNT+1)) | cut -d' ' -f2-)
for old in "${expired[@]:-}"; do [[ -z "$old" ]] || rm -f -- "$old" "$old.sha256"; done
printf 'timestamp=%s artifact=%s size=%s result=success checksum=%s offsite=%s\n' "$(date -u +%FT%TZ)" "$name" "$(stat -c %s "$final")" "$checksum" "$offsite_status"
