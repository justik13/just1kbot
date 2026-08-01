#!/bin/bash
set -Eeuo pipefail
umask 077
keep=false
[[ ${1:-} != --keep-test-db ]] || { keep=true; shift; }
artifact=${1:?usage: restore_rehearsal.sh [--keep-test-db] ARTIFACT}
VERIFY_BACKUP=${VERIFY_BACKUP:-/usr/local/bin/verify_backup.sh}
MAINTENANCE_DATABASE=${REHEARSAL_MAINTENANCE_DATABASE:-postgres}
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-rehearsal.XXXXXX")
test_db=""; work_result=failure; cleanup_status=not-needed

finish() {
    original_rc=$?; final_rc=$original_rc
    set +e
    rm -rf -- "$tmpdir"
    if [[ -n "$test_db" ]]; then
        if [[ "$keep" == true ]]; then
            cleanup_status=kept
        elif [[ "$test_db" == just1kbot_rehearsal_* ]]; then
            cleanup_status=failed
            dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DATABASE" "$test_db" >/dev/null 2>&1
            drop_rc=$?
            if (( drop_rc != 0 )); then
                printf 'rehearsal cleanup stage=dropdb exit_code=%s\n' "$drop_rc" >&2
                final_rc=8
            else
                database_count=$(psql -XAt -v ON_ERROR_STOP=1 -v "target_db=$test_db" -d "$MAINTENANCE_DATABASE" 2>/dev/null <<'SQL'
SELECT count(*)
FROM pg_database
WHERE datname = :'target_db';
SQL
                )
                query_rc=$?
                if (( query_rc != 0 )); then
                    printf 'rehearsal cleanup stage=verification exit_code=%s\n' "$query_rc" >&2
                    final_rc=8
                elif [[ "$database_count" != 0 ]]; then
                    printf 'rehearsal cleanup stage=verification exit_code=0 database_absent=false\n' >&2
                    final_rc=8
                else
                    cleanup_status=success
                fi
            fi
        else
            cleanup_status=failed; final_rc=8
        fi
    fi
    result=failure
    if (( final_rc == 0 )) && [[ "$work_result" == success ]] && [[ "$cleanup_status" == success || "$cleanup_status" == kept ]]; then result=success; fi
    [[ "$result" == success ]] || (( final_rc != 0 )) || final_rc=1
    printf 'timestamp=%s artifact=%s size=%s result=%s rehearsal_database=%s cleanup=%s\n' \
      "$(date -u +%FT%TZ)" "$(basename -- "$artifact")" "$(stat -c %s "$artifact" 2>/dev/null || printf 0)" "$result" "${test_db:-not-created}" "$cleanup_status"
    exit "$final_rc"
}
trap finish EXIT INT TERM
for command in createdb dropdb pg_restore psql python3; do command -v "$command" >/dev/null || { echo "rehearsal error: missing $command" >&2; exit 1; }; done
if [[ -n ${REHEARSAL_DATABASE_URL:-} ]]; then
    mapfile -d '' -t dbparts < <(python3 - <<'PY'
import os,urllib.parse
p=urllib.parse.urlsplit(os.environ['REHEARSAL_DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://',1))
if p.scheme not in ('postgresql','postgres') or not p.hostname: raise SystemExit('invalid REHEARSAL_DATABASE_URL')
for v in (p.hostname,str(p.port or 5432),urllib.parse.unquote(p.username or ''),urllib.parse.unquote(p.password or '')): print(v,end='\0')
PY
    )
    (( ${#dbparts[@]} == 4 )) || { echo 'rehearsal error: invalid database connection' >&2; exit 1; }
    export PGHOST=${dbparts[0]} PGPORT=${dbparts[1]} PGUSER=${dbparts[2]} PGPASSWORD=${dbparts[3]}
fi
"$VERIFY_BACKUP" --extract-dir "$tmpdir/verified" "$artifact" >/dev/null
expected_revision=$(MANIFEST="$tmpdir/verified/manifest.json" python3 - <<'PY'
import json,os
print(json.load(open(os.environ['MANIFEST']))['alembic_revision'])
PY
)
test_db="just1kbot_rehearsal_$(date -u +%Y%m%d%H%M%S)_$$_${RANDOM}"
[[ "$test_db" == just1kbot_rehearsal_* ]] || exit 1
createdb "$test_db"
pg_restore --exit-on-error --no-owner --no-acl --dbname="$test_db" "$tmpdir/verified/dump.custom" >/dev/null
tables=${REHEARSAL_CRITICAL_TABLES:-users,vpn_profiles,payments,payment_provider_operations,payment_fulfillment_operations,webhook_inbox,payment_events}
TEST_DB="$test_db" TABLES="$tables" EXPECTED_REVISION="$expected_revision" python3 - <<'PY'
import os,subprocess
db=os.environ['TEST_DB']; tables=[t for t in os.environ['TABLES'].split(',') if t]
def sql(q): return subprocess.check_output(['psql','-XAt','-d',db,'-c',q],text=True).strip()
if sql("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'")!='1': raise SystemExit('missing alembic_version')
if sql('SELECT count(*) FROM alembic_version')!='1': raise SystemExit('unexpected Alembic revision row count')
if sql('SELECT version_num FROM alembic_version')!=os.environ['EXPECTED_REVISION']: raise SystemExit('restored Alembic revision mismatch')
for table in tables:
    if not table.replace('_','').isalnum(): raise SystemExit('invalid critical table name')
    if sql(f"SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='{table}'")!='1': raise SystemExit(f'missing critical table: {table}')
    sql(f'SELECT count(*) FROM "{table}"')
sql('SELECT 1')
PY
work_result=success
