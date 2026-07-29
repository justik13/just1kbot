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
tmpdir=""
published=""
offsite_status=not-configured

finish() {
    rc=$?
    [[ -z "$tmpdir" ]] || rm -rf -- "$tmpdir"
    if (( rc != 0 )); then
        # A required off-site failure must not leave a newly published local artifact.
        [[ -z "$published" ]] || rm -f -- "$published" "$published.sha256"
        printf 'timestamp=%s artifact=%s size=0 result=failure checksum=unavailable offsite=%s\n' \
            "$(date -u +%FT%TZ)" "${published##*/}" "$offsite_status" >&2
    fi
    exit "$rc"
}
trap finish EXIT INT TERM

fail() { printf 'backup error: %s\n' "$1" >&2; exit "${2:-1}"; }
for command in age pg_dump pg_restore psql flock sha256sum tar python3; do
    command -v "$command" >/dev/null || fail "required command is unavailable: $command"
done
[[ ${BACKUP_AGE_RECIPIENT:-} == age1* ]] || fail 'BACKUP_AGE_RECIPIENT is missing or invalid' 2
[[ "$REQUIRE_OFFSITE" == true || "$REQUIRE_OFFSITE" == false ]] || fail 'BACKUP_REQUIRE_OFFSITE must be true or false'
[[ "$RETENTION_COUNT" =~ ^[0-9]+$ ]] && (( RETENTION_COUNT >= 2 )) || fail 'BACKUP_RETENTION_COUNT must be at least 2'
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail 'configuration file is missing or unsafe'

mkdir -p -- "$BACKUP_DIR" "$(dirname -- "$LOCK_FILE")"
chmod 700 "$BACKUP_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || fail 'another backup is already running' 3
tmpdir=$(mktemp -d "$BACKUP_DIR/.backup-work.XXXXXX")
# Validate the recipient before touching PostgreSQL.
printf '' | age -r "$BACKUP_AGE_RECIPIENT" -o "$tmpdir/recipient-check.age" || fail 'BACKUP_AGE_RECIPIENT is invalid' 2
rm -f "$tmpdir/recipient-check.age"

# Parse, but never source, the secret configuration file. Values travel through
# inherited environment variables rather than command-line arguments.
mapfile -d '' -t dbparts < <(ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os, pathlib, urllib.parse
values = {}
for line in pathlib.Path(os.environ['ENV_FILE']).read_text().splitlines():
    if not line or line.lstrip().startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    values[key.strip()] = value
for key in ('DATABASE_URL', 'DB_ENCRYPTION_KEY', 'REDIS_URL', 'BOT_TOKEN'):
    if not values.get(key):
        raise SystemExit(f'missing required configuration key: {key}')
url = values['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://', 1)
p = urllib.parse.urlsplit(url)
if p.scheme not in ('postgresql', 'postgres') or not p.hostname or not p.path[1:]:
    raise SystemExit('invalid DATABASE_URL')
for value in (p.hostname, str(p.port or 5432), urllib.parse.unquote(p.username or ''),
              urllib.parse.unquote(p.password or ''), p.path[1:]):
    print(value, end='\0')
PY
)
(( ${#dbparts[@]} == 5 )) || fail 'could not parse database configuration'
export PGHOST=${dbparts[0]} PGPORT=${dbparts[1]} PGUSER=${dbparts[2]} PGPASSWORD=${dbparts[3]} PGDATABASE=${dbparts[4]}

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
name="just1kbot-pg-v${FORMAT_VERSION}-${timestamp}.tar.age"
final="$BACKUP_DIR/$name"
[[ ! -e "$final" ]] || fail 'an artifact with this timestamp already exists'
pg_dump --format=custom --no-owner --no-acl --file="$tmpdir/dump.custom"
pg_restore --list "$tmpdir/dump.custom" >/dev/null
install -m 600 "$ENV_FILE" "$tmpdir/config.env"
alembic_revision=$(psql -Atqc 'SELECT version_num FROM alembic_version LIMIT 1' 2>/dev/null || printf unknown)
postgres_version=$(pg_dump --version | sed 's/[^0-9.]*\([0-9][0-9.]*\).*/\1/')
git_sha=$(git -C "${PROJECT_DIR:-/opt/just1kbot}" rev-parse HEAD 2>/dev/null || printf unavailable)
(cd "$tmpdir" && sha256sum dump.custom config.env > checksums.sha256)
TIMESTAMP="$timestamp" DATABASE_NAME="$PGDATABASE" POSTGRES_VERSION="$postgres_version" \
ALEMBIC_REVISION="$alembic_revision" GIT_SHA="$git_sha" python3 - <<'PY' >"$tmpdir/manifest.json"
import json, os, sys
json.dump({'format_version': 1, 'created_at_utc': os.environ['TIMESTAMP'],
           'database_name': os.environ['DATABASE_NAME'],
           'postgresql_version': os.environ['POSTGRES_VERSION'],
           'alembic_revision': os.environ['ALEMBIC_REVISION'],
           'git_commit_sha': os.environ['GIT_SHA'],
           'files': ['dump.custom', 'config.env']}, sys.stdout,
          sort_keys=True)
print()
PY
(cd "$tmpdir" && tar --format=ustar -cf bundle.tar manifest.json checksums.sha256 dump.custom config.env)
age -r "$BACKUP_AGE_RECIPIENT" -o "$BACKUP_DIR/.${name}.partial" "$tmpdir/bundle.tar"
[[ -s "$BACKUP_DIR/.${name}.partial" ]] || fail 'encryption produced an empty artifact'
chmod 600 "$BACKUP_DIR/.${name}.partial"
mv -- "$BACKUP_DIR/.${name}.partial" "$final"
published=$final
checksum=$(sha256sum "$final" | awk '{print $1}')
printf '%s  %s\n' "$checksum" "$name" >"$BACKUP_DIR/.${name}.sha256.partial"
mv -- "$BACKUP_DIR/.${name}.sha256.partial" "$final.sha256"

if [[ -n "$OFFSITE_DIR" ]]; then
    offsite_status=failure
    if mkdir -p -- "$OFFSITE_DIR" && cp -- "$final" "$OFFSITE_DIR/.${name}.partial" && \
       cp -- "$final.sha256" "$OFFSITE_DIR/.${name}.sha256.partial" && \
       [[ $(sha256sum "$OFFSITE_DIR/.${name}.partial" | awk '{print $1}') == "$checksum" ]]; then
        chmod 600 "$OFFSITE_DIR/.${name}.partial" "$OFFSITE_DIR/.${name}.sha256.partial"
        mv -- "$OFFSITE_DIR/.${name}.partial" "$OFFSITE_DIR/$name"
        mv -- "$OFFSITE_DIR/.${name}.sha256.partial" "$OFFSITE_DIR/$name.sha256"
        offsite_status=success
    else
        rm -f -- "$OFFSITE_DIR/.${name}.partial" "$OFFSITE_DIR/.${name}.sha256.partial" 2>/dev/null || true
        [[ "$REQUIRE_OFFSITE" == false ]] || fail 'required off-site publication failed' 4
    fi
elif [[ "$REQUIRE_OFFSITE" == true ]]; then
    offsite_status=failure
    fail 'off-site publication is required but BACKUP_OFFSITE_DIR is unset' 4
fi

# Only exact artifacts from this format participate; always retain newest two.
mapfile -t expired < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-????????T??????Z.tar.age' -printf '%T@ %p\n' | sort -rn | tail -n +$((RETENTION_COUNT + 1)) | cut -d' ' -f2-)
for old in "${expired[@]:-}"; do [[ -z "$old" ]] || rm -f -- "$old" "$old.sha256"; done
size=$(stat -c %s "$final")
printf 'timestamp=%s artifact=%s size=%s result=success checksum=%s offsite=%s\n' \
    "$(date -u +%FT%TZ)" "$name" "$size" "$checksum" "$offsite_status"
published=""
