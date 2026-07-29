#!/bin/bash
set -Eeuo pipefail
umask 077
keep=false
[[ ${1:-} != --keep-test-db ]] || { keep=true; shift; }
artifact=${1:?usage: restore_rehearsal.sh [--keep-test-db] ARTIFACT}
VERIFY_BACKUP=${VERIFY_BACKUP:-/usr/local/bin/verify_backup.sh}
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-rehearsal.XXXXXX")
test_db=""
result=failure
cleanup() {
    rc=$?
    rm -rf -- "$tmpdir"
    if [[ -n "$test_db" && "$keep" == false ]]; then dropdb --if-exists "$test_db" >/dev/null 2>&1 || true; fi
    printf 'timestamp=%s artifact=%s size=%s success=%s rehearsal_database=%s kept=%s\n' \
      "$(date -u +%FT%TZ)" "$(basename -- "$artifact")" "$(stat -c %s "$artifact" 2>/dev/null || printf 0)" "$result" "${test_db:-not-created}" "$keep"
    exit "$rc"
}
trap cleanup EXIT INT TERM
for command in createdb dropdb pg_restore psql python3; do command -v "$command" >/dev/null || { echo "rehearsal error: missing $command" >&2; exit 1; }; done
# Administrative rehearsal connection is supplied explicitly in the environment;
# it is never read from, nor written back to, the encrypted production config.
if [[ -n ${REHEARSAL_DATABASE_URL:-} ]]; then
    mapfile -d '' -t dbparts < <(python3 - <<'PY'
import os, urllib.parse
p = urllib.parse.urlsplit(os.environ['REHEARSAL_DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://', 1))
if p.scheme not in ('postgresql', 'postgres') or not p.hostname:
    raise SystemExit('invalid REHEARSAL_DATABASE_URL')
for value in (p.hostname, str(p.port or 5432), urllib.parse.unquote(p.username or ''), urllib.parse.unquote(p.password or '')):
    print(value, end='\0')
PY
    )
    (( ${#dbparts[@]} == 4 )) || { echo 'rehearsal error: invalid database connection' >&2; exit 1; }
    export PGHOST=${dbparts[0]} PGPORT=${dbparts[1]} PGUSER=${dbparts[2]} PGPASSWORD=${dbparts[3]}
fi
"$VERIFY_BACKUP" --extract-dir "$tmpdir/verified" "$artifact" >/dev/null
test_db="just1kbot_rehearsal_$(date -u +%Y%m%d%H%M%S)_$$_${RANDOM}"
[[ "$test_db" == just1kbot_rehearsal_* ]] || exit 1
createdb "$test_db"
pg_restore --exit-on-error --no-owner --no-acl --dbname="$test_db" "$tmpdir/verified/dump.custom" >/dev/null
tables=${REHEARSAL_CRITICAL_TABLES:-users,vpn_profiles,payments,payment_provider_operations,payment_fulfillment_operations}
TEST_DB="$test_db" TABLES="$tables" python3 - <<'PY'
import os, subprocess
db = os.environ['TEST_DB']
tables = [t for t in os.environ['TABLES'].split(',') if t]
def sql(query):
    return subprocess.check_output(['psql', '-XAt', '-d', db, '-c', query], text=True).strip()
if sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'") != '1':
    raise SystemExit('missing alembic_version')
if sql('SELECT count(*) FROM alembic_version') != '1':
    raise SystemExit('unexpected alembic revision row count')
for table in tables:
    if not table.replace('_', '').isalnum(): raise SystemExit('invalid critical table name')
    if sql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='{table}'") != '1':
        raise SystemExit(f'missing critical table: {table}')
    sql(f'SELECT count(*) FROM "{table}"')
sql('SELECT 1')  # a fresh psql process proves a new connection works
PY
result=success
