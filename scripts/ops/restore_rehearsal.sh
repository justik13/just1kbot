#!/bin/bash
set -Eeuo pipefail
umask 077

keep=false
[[ ${1:-} != --keep-test-db ]] || { keep=true; shift; }
artifact=${1:?usage: restore_rehearsal.sh [--keep-test-db] ARTIFACT}
VERIFY_BACKUP=${VERIFY_BACKUP:-/usr/local/bin/verify_backup.sh}
PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}
ENV_FILE=${ENV_FILE:-$PROJECT_DIR/.env}
POSTGRES_LIBRARY=${POSTGRES_LIBRARY:-$PROJECT_DIR/scripts/lib/postgresql.sh}
MAINTENANCE_DATABASE=${REHEARSAL_MAINTENANCE_DATABASE:-postgres}
RESTORE_TIMEOUT=${REHEARSAL_RESTORE_TIMEOUT:-600}
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-rehearsal.XXXXXX")
test_db=""
postgres_work=""
restore_dump=""
work_result=failure
cleanup_status=not-needed
USE_POSTGRES_OS_USER=false

log_error() { printf 'rehearsal error: %s\n' "$*" >&2; }

database_command() {
    if [[ "$USE_POSTGRES_OS_USER" == true ]]; then
        runuser -u postgres -- env PGHOST="$PGHOST" PGPORT="$PGPORT" "$@"
    else
        "$@"
    fi
}

sql() {
    database_command psql -XAt -v ON_ERROR_STOP=1 -d "$test_db" -c "$1"
}

finish() {
    original_rc=$?
    final_rc=$original_rc
    set +e
    rm -rf -- "$tmpdir"
    [[ -z "$postgres_work" ]] || rm -rf -- "$postgres_work"
    if [[ -n "$test_db" ]]; then
        if [[ "$keep" == true ]]; then
            cleanup_status=kept
        elif [[ "$test_db" == just1kbot_rehearsal_* ]]; then
            cleanup_status=failed
            database_command dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DATABASE" "$test_db" >/dev/null 2>&1
            drop_rc=$?
            if (( drop_rc != 0 )); then
                printf 'rehearsal cleanup stage=dropdb exit_code=%s\n' "$drop_rc" >&2
                final_rc=8
            else
                database_count=$(database_command psql -XAt -v ON_ERROR_STOP=1 \
                    -v "target_db=$test_db" -d "$MAINTENANCE_DATABASE" 2>/dev/null <<'SQL'
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
            cleanup_status=failed
            final_rc=8
        fi
    fi
    result=failure
    if (( final_rc == 0 )) && [[ "$work_result" == success ]] &&
       [[ "$cleanup_status" == success || "$cleanup_status" == kept ]]; then
        result=success
    fi
    [[ "$result" == success ]] || (( final_rc != 0 )) || final_rc=1
    printf 'timestamp=%s artifact=%s size=%s result=%s rehearsal_database=%s cleanup=%s\n' \
      "$(date -u +%FT%TZ)" "$(basename -- "$artifact")" \
      "$(stat -c %s "$artifact" 2>/dev/null || printf 0)" \
      "$result" "${test_db:-not-created}" "$cleanup_status"
    exit "$final_rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for command in createdb dropdb pg_restore psql python3 timeout; do
    command -v "$command" >/dev/null || { log_error "missing $command"; exit 1; }
done
[[ "$RESTORE_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { log_error 'invalid restore timeout'; exit 1; }

if (( EUID == 0 )); then
    command -v runuser >/dev/null || { log_error 'missing runuser'; exit 1; }
    [[ -f "$POSTGRES_LIBRARY" && ! -L "$POSTGRES_LIBRARY" ]] || {
        log_error "PostgreSQL library is missing or unsafe: $POSTGRES_LIBRARY"
        exit 1
    }
    # shellcheck source=/opt/just1kbot/scripts/lib/postgresql.sh
    source "$POSTGRES_LIBRARY"
    pg_prepare update || exit 1
    export PGHOST="$PG_SOCKET_DIR" PGPORT="$PG_PORT"
    USE_POSTGRES_OS_USER=true
elif [[ -n ${REHEARSAL_DATABASE_URL:-} ]]; then
    mapfile -d '' -t dbparts < <(python3 - <<'PY'
import os, urllib.parse
p=urllib.parse.urlsplit(os.environ['REHEARSAL_DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://',1))
if p.scheme not in ('postgresql','postgres') or not p.hostname:
    raise SystemExit('invalid REHEARSAL_DATABASE_URL')
for v in (p.hostname,str(p.port or 5432),urllib.parse.unquote(p.username or ''),urllib.parse.unquote(p.password or '')):
    print(v,end='\0')
PY
    )
    (( ${#dbparts[@]} == 4 )) || { log_error 'invalid database connection'; exit 1; }
    export PGHOST=${dbparts[0]} PGPORT=${dbparts[1]} PGUSER=${dbparts[2]} PGPASSWORD=${dbparts[3]}
fi

"$VERIFY_BACKUP" --extract-dir "$tmpdir/verified" "$artifact" >/dev/null
restore_dump="$tmpdir/verified/dump.custom"
if [[ "$USE_POSTGRES_OS_USER" == true ]]; then
    [[ -d /var/lib/postgresql && ! -L /var/lib/postgresql ]] || { log_error 'unsafe PostgreSQL workspace parent'; exit 1; }
    postgres_work=$(mktemp -d /var/lib/postgresql/just1kbot-rehearsal.XXXXXX)
    chown postgres:postgres "$postgres_work"
    chmod 0700 "$postgres_work"
    install -o postgres -g postgres -m 0600 "$restore_dump" "$postgres_work/dump.custom"
    restore_dump="$postgres_work/dump.custom"
fi
expected_revision=$(MANIFEST="$tmpdir/verified/manifest.json" python3 - <<'PY'
import json, os
print(json.load(open(os.environ['MANIFEST'], encoding='utf-8'))['alembic_revision'])
PY
)

test_db="just1kbot_rehearsal_$(date -u +%Y%m%d%H%M%S)_$$_${RANDOM}"
[[ "$test_db" == just1kbot_rehearsal_* ]] || exit 1
database_command createdb "$test_db"
if [[ "$USE_POSTGRES_OS_USER" == true ]]; then
    timeout --foreground "$RESTORE_TIMEOUT" runuser -u postgres -- \
        env PGHOST="$PGHOST" PGPORT="$PGPORT" \
        pg_restore --exit-on-error --no-owner --no-acl --dbname="$test_db" \
        "$restore_dump" >/dev/null
else
    timeout --foreground "$RESTORE_TIMEOUT" \
        pg_restore --exit-on-error --no-owner --no-acl --dbname="$test_db" \
        "$restore_dump" >/dev/null
fi

[[ $(sql "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='alembic_version'") == 1 ]] || {
    log_error 'missing alembic_version'; exit 1;
}
[[ $(sql 'SELECT count(*) FROM alembic_version') == 1 ]] || {
    log_error 'unexpected Alembic revision row count'; exit 1;
}
[[ $(sql 'SELECT version_num FROM alembic_version') == "$expected_revision" ]] || {
    log_error 'restored Alembic revision mismatch'; exit 1;
}

tables=${REHEARSAL_CRITICAL_TABLES:-users,vpn_profiles,payments,payment_provider_operations,payment_fulfillment_operations,webhook_inbox,payment_events}
IFS=',' read -r -a table_list <<< "$tables"
for table in "${table_list[@]}"; do
    [[ "$table" =~ ^[a-z_][a-z0-9_]*$ ]] || { log_error "invalid critical table name: $table"; exit 1; }
    [[ $(sql "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='$table'") == 1 ]] || {
        log_error "missing critical table: $table"; exit 1;
    }
    sql "SELECT count(*) FROM \"$table\"" >/dev/null
done
sql 'SELECT 1' >/dev/null
work_result=success
