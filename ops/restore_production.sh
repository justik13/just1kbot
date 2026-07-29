#!/bin/bash
set -Eeuo pipefail
umask 077

# Manual, fail-closed production PostgreSQL cutover.  There is intentionally no
# timer/webhook entrypoint for this program.
ENV_FILE=${ENV_FILE:-/opt/just1kbot/.env}
PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}
SERVICE_NAME=${RESTORE_SERVICE_NAME:-just1kbot}
PRODUCTION_DB=${RESTORE_PRODUCTION_DATABASE:-just1kbot_bot}
MAINTENANCE_DB=${RESTORE_MAINTENANCE_DATABASE:-postgres}
OPERATION_DIR=${RESTORE_OPERATION_DIR:-/root/restore-operations}
BACKUP_DIR=${BACKUP_DIR:-/root/backups/just1kbot}
RESTORE_LOCK=${RESTORE_LOCK_FILE:-/run/lock/just1kbot-restore.lock}
DEPLOY_LOCK=${DEPLOY_LOCK_FILE:-/run/lock/just1kbot-deploy.lock}
BACKUP_LOCK=${BACKUP_LOCK_FILE:-/run/lock/just1kbot-backup.lock}
VERIFY_BACKUP=${VERIFY_BACKUP:-/usr/local/bin/verify_backup.sh}
BACKUP_COMMAND=${BACKUP_COMMAND:-/usr/local/bin/just1kbot-backup.sh}
REHEARSAL_COMMAND=${REHEARSAL_COMMAND:-/usr/local/bin/restore_rehearsal.sh}
VALIDATOR=${RESTORE_CANDIDATE_VALIDATOR:-$PROJECT_DIR/ops/validate_restore_candidate.py}
PYTHON=${RESTORE_PYTHON:-$PROJECT_DIR/venv/bin/python}
ALEMBIC=${RESTORE_ALEMBIC:-$PROJECT_DIR/venv/bin/alembic}
HEALTHCHECK=${RESTORE_HEALTHCHECK:-/usr/local/bin/just1kbot-healthcheck.sh}
HEARTBEAT_FILE=${RESTORE_HEARTBEAT_FILE:-$PROJECT_DIR/.heartbeat}
HEALTH_TIMEOUT=${RESTORE_HEALTH_TIMEOUT:-180}
FINALIZE_SAFETY_SECONDS=${RESTORE_FINALIZE_SAFETY_SECONDS:-86400}
CRITICAL_ROLLBACK_EXIT=42
workspace="" operation_id="" manifest_file="" candidate_db="" previous_db="" failed_db=""
emergency_artifact="" swapped=false service_stopped=false result=failure stage=initializing

log() { printf 'restore operation=%s stage=%s %s\n' "${operation_id:-none}" "$stage" "$*" >&2; }
fail() { log "result=failure exit_code=${2:-1} reason=$1"; exit "${2:-1}"; }
valid_db() { [[ $1 =~ ^[a-z][a-z0-9_]{0,62}$ ]]; }
valid_operation() { [[ $1 =~ ^restore_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}$ ]]; }
service() {
    if [[ -n ${RESTORE_SERVICE_ADAPTER:-} ]]; then "$RESTORE_SERVICE_ADAPTER" "$1" "$SERVICE_NAME"; else systemctl "$1" "$SERVICE_NAME"; fi
}
manifest_update() {
    local args=("$@")
    [[ -n "$manifest_file" ]] || return 0
    MANIFEST="$manifest_file" python3 - "${args[@]}" <<'PY'
import json, os, pathlib, sys, tempfile
p=pathlib.Path(os.environ['MANIFEST']); data={}
if p.exists(): data=json.loads(p.read_text())
for item in sys.argv[1:]:
    key,value=item.split('=',1)
    if value in ('true','false'): value=value=='true'
    data[key]=value
fd,name=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
try:
    with os.fdopen(fd,'w') as f: json.dump(data,f,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.chmod(name,0o600); os.replace(name,p)
finally:
    try: os.unlink(name)
    except FileNotFoundError: pass
PY
}
cleanup() {
    rc=$?; set +e
    [[ -z "$workspace" ]] || rm -rf -- "$workspace"
    if (( rc != 0 )) && [[ "$swapped" == false && -n "$candidate_db" && "$candidate_db" == just1kbot_candidate_* ]]; then
        dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DB" "$candidate_db" >/dev/null 2>&1 || true
    fi
    if (( rc != 0 )) && [[ "$service_stopped" == true && "$swapped" == false ]]; then service start >/dev/null 2>&1 || true; fi
    if [[ -n "$manifest_file" && -f "$manifest_file" && "$result" != success ]]; then
        manifest_update stage="$stage" result=failure finished_utc="$(date -u +%FT%TZ)" >/dev/null 2>&1 || true
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

load_db_env() {
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail 'configuration_file_unsafe'
    mapfile -d '' -t dbparts < <(ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os,pathlib,urllib.parse
v={}
for line in pathlib.Path(os.environ['ENV_FILE']).read_text().splitlines():
 if line and not line.lstrip().startswith('#') and '=' in line:
  k,x=line.split('=',1); x=x.strip(); v[k.strip()]=x[1:-1] if len(x)>1 and x[0]==x[-1] and x[0] in "'\"" else x
for k in ('DATABASE_URL','DB_ENCRYPTION_KEY'): 
 if not v.get(k): raise SystemExit('missing production configuration')
p=urllib.parse.urlsplit(v['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://',1))
if p.scheme not in ('postgresql','postgres') or not p.hostname or not p.path[1:]: raise SystemExit('invalid production database URL')
for x in (p.scheme,p.hostname,str(p.port or 5432),urllib.parse.unquote(p.username or ''),urllib.parse.unquote(p.password or ''),p.path[1:],v['DB_ENCRYPTION_KEY']): print(x,end='\0')
PY
    )
    (( ${#dbparts[@]} == 7 )) || fail 'configuration_parse_failed'
    [[ ${dbparts[5]} == "$PRODUCTION_DB" ]] || fail 'configured_database_name_mismatch'
    export PGHOST=${dbparts[1]} PGPORT=${dbparts[2]} PGUSER=${dbparts[3]} PGPASSWORD=${dbparts[4]}
    production_scheme=${dbparts[0]}; current_key=${dbparts[6]}
}
url_for() {
    DB="$1" python3 - <<'PY'
import os,urllib.parse
scheme='postgresql+asyncpg'; user=urllib.parse.quote(os.environ.get('PGUSER',''),safe=''); pw=urllib.parse.quote(os.environ.get('PGPASSWORD',''),safe='')
auth=user + ((':'+pw) if pw else '') + ('@' if user else '')
print(f"{scheme}://{auth}{os.environ['PGHOST']}:{os.environ['PGPORT']}/{os.environ['DB']}")
PY
}
psql_m() { psql -X -v ON_ERROR_STOP=1 -d "$MAINTENANCE_DB" "$@"; }
db_exists() { [[ $(psql_m -At -v target="$1" -c "SELECT count(*) FROM pg_database WHERE datname=:'target'") == 1 ]]; }
terminate_db() { psql_m -At -v target="$1" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:'target' AND pid<>pg_backend_pid()" >/dev/null; }
rename_db() { local from=$1 to=$2; valid_db "$from" && valid_db "$to" || return 1; psql_m -q -c "ALTER DATABASE \"$from\" RENAME TO \"$to\""; }
service_inactive() { ! service is-active >/dev/null 2>&1; }
stop_service() {
    service stop >/dev/null; service_inactive || return 1
    if [[ -z ${RESTORE_SERVICE_ADAPTER:-} ]] && pgrep -u just1kbot -f "$PROJECT_DIR" >/dev/null 2>&1; then return 1; fi
    service_stopped=true
}
health() {
    local deadline=$((SECONDS+HEALTH_TIMEOUT)) seen=0 last=""
    service is-active >/dev/null 2>&1 || return 1
    while (( SECONDS < deadline && seen < 2 )); do
        if [[ -f "$HEARTBEAT_FILE" ]]; then
            stamp=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null || true)
            now=$(date +%s)
            if [[ $stamp =~ ^[0-9]+$ ]] && (( now-stamp <= 120 )) && [[ $stamp != "$last" ]]; then seen=$((seen+1)); last=$stamp; fi
        elif [[ -n ${RESTORE_SERVICE_ADAPTER:-} ]]; then seen=2
        fi
        (( seen >= 2 )) || sleep "${RESTORE_HEALTH_POLL_SECONDS:-5}"
    done
    (( seen >= 2 )) || return 1
    [[ ! -x "$HEALTHCHECK" ]] || timeout "$HEALTH_TIMEOUT" "$HEALTHCHECK" >/dev/null
    psql -XAt -v ON_ERROR_STOP=1 -d "$PRODUCTION_DB" -c 'SELECT count(*) FROM payment_provider_operations; SELECT count(*) FROM payment_fulfillment_operations; SELECT count(*) FROM webhook_inbox' >/dev/null
    sleep "${RESTORE_CRASH_WINDOW_SECONDS:-5}"
    service is-active >/dev/null 2>&1
}
check_incomplete() {
    local f
    shopt -s nullglob
    for f in "$OPERATION_DIR"/*.json; do
        status=$(MANIFEST="$f" python3 - <<'PY'
import json,os
try: print(json.load(open(os.environ['MANIFEST'])).get('result',''))
except Exception: print('invalid')
PY
)
        [[ $status == success || $status == failure || $status == rolled_back || $status == finalized ]] || fail 'incomplete_operation_exists' 12
    done
}
inspect_incomplete() {
    mkdir -p -m 700 "$OPERATION_DIR"
    local f; shopt -s nullglob
    for f in "$OPERATION_DIR"/*.json; do
        MANIFEST="$f" SERVICE="$SERVICE_NAME" python3 - <<'PY'
import json,os,pathlib
p=pathlib.Path(os.environ['MANIFEST'])
try:
 d=json.loads(p.read_text())
except Exception: print(f'operation={p.stem} stage=invalid_manifest'); raise SystemExit
if d.get('result') not in ('success','rolled_back','finalized'):
 print('operation=%s stage=%s production_database=%s candidate_database=%s previous_database=%s failed_database=%s' % (d.get('operation_id',p.stem),d.get('stage','unknown'),d.get('original_production_database','unknown'),d.get('candidate_database','none'),d.get('previous_database_quarantine_name','none'),d.get('failed_candidate_name','none')))
PY
    done
    printf 'service=%s active=%s\n' "$SERVICE_NAME" "$(service is-active >/dev/null 2>&1 && echo true || echo false)"
}
rollback_names() {
    local previous=$1 failed=$2
    valid_db "$previous" && [[ $previous == just1kbot_previous_* ]] || return 1
    valid_db "$failed" && [[ $failed == just1kbot_failed_restore_* ]] || return 1
    stop_service || return 1; terminate_db "$PRODUCTION_DB"
    rename_db "$PRODUCTION_DB" "$failed" || return 1
    if ! rename_db "$previous" "$PRODUCTION_DB"; then rename_db "$failed" "$PRODUCTION_DB" || true; return 1; fi
    service start >/dev/null; service_stopped=false
    health
}
rollback_operation() {
    local id=$1 confirm=$2
    [[ $confirm == true ]] || fail 'production_rollback_confirmation_required' 2
    valid_operation "$id" || fail 'invalid_operation_id'
    manifest_file="$OPERATION_DIR/$id.json"; [[ -f "$manifest_file" && ! -L "$manifest_file" ]] || fail 'operation_manifest_missing'
    operation_id=$id
    mapfile -d '' -t names < <(MANIFEST="$manifest_file" python3 - <<'PY'
import json,os,re
p=os.environ['MANIFEST']; d=json.load(open(p))
if d.get('operation_id') != os.path.basename(p)[:-5]: raise SystemExit('operation mismatch')
for k in ('previous_database_quarantine_name','failed_candidate_name'): print(d.get(k,''),end='\0')
PY
    )
    previous_db=${names[0]}; failed_db=${names[1]:-just1kbot_failed_restore_$(date -u +%Y%m%d%H%M%S)_$(printf %04x "$RANDOM")}
    stage=manual_rollback; manifest_update stage="$stage" rollback_attempted=true failed_candidate_name="$failed_db"
    if rollback_names "$previous_db" "$failed_db"; then result=success; manifest_update result=rolled_back rollback_result=success service_health_result=success finished_utc="$(date -u +%FT%TZ)"; else manifest_update rollback_result=failure service_health_result=failure; exit "$CRITICAL_ROLLBACK_EXIT"; fi
}
finalize_operation() {
    local id=$1 confirm=$2
    [[ $confirm == true ]] || fail 'delete_previous_confirmation_required' 2
    valid_operation "$id" || fail 'invalid_operation_id'; manifest_file="$OPERATION_DIR/$id.json"; [[ -f "$manifest_file" && ! -L "$manifest_file" ]] || fail 'operation_manifest_missing'; operation_id=$id
    mapfile -d '' -t data < <(MANIFEST="$manifest_file" python3 - <<'PY'
import json,os,time,datetime
x=json.load(open(os.environ['MANIFEST']))
for k in ('result','previous_database_quarantine_name','emergency_backup_path','emergency_backup_sha256','finished_utc'): print(x.get(k,''),end='\0')
PY
    )
    [[ ${data[0]} == success ]] || fail 'operation_not_successful'; previous_db=${data[1]}; valid_db "$previous_db" && [[ $previous_db == just1kbot_previous_* ]] || fail 'unsafe_previous_database_name'
    finished_epoch=$(date -d "${data[4]}" +%s); (( $(date +%s)-finished_epoch >= FINALIZE_SAFETY_SECONDS )) || fail 'finalize_safety_window_not_elapsed'
    [[ -f ${data[2]} && ! -L ${data[2]} && $(sha256sum "${data[2]}" | awk '{print $1}') == "${data[3]}" ]] || fail 'emergency_backup_missing_or_changed'
    service is-active >/dev/null 2>&1 && health || fail 'service_unhealthy'
    before=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.tar.age' -printf '%f\n' | sort)
    BACKUP_SKIP_RETENTION=true "$BACKUP_COMMAND" >/dev/null || fail 'finalize_backup_failed'
    after=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.tar.age' -printf '%f\n' | sort); [[ $before != "$after" ]] || fail 'finalize_backup_not_created'
    db_exists "$previous_db" || fail 'manifest_previous_database_missing'; dropdb --maintenance-db="$MAINTENANCE_DB" "$previous_db"
    result=success; stage=finalized; manifest_update stage=finalized result=finalized finished_utc="$(date -u +%FT%TZ)"
}

mode=restore artifact="" confirm=false rollback_id="" finalize_id="" rollback_confirm=false finalize_confirm=false
while (($#)); do
    case $1 in
      --artifact) artifact=${2:-}; shift 2;; --confirm-production-restore) confirm=true; shift;;
      --inspect-incomplete) mode=inspect; shift;; --rollback-operation) mode=rollback; rollback_id=${2:-}; shift 2;;
      --confirm-production-rollback) rollback_confirm=true; shift;; --finalize-operation) mode=finalize; finalize_id=${2:-}; shift 2;;
      --confirm-delete-previous) finalize_confirm=true; shift;; *) fail 'unsupported_argument' 2;; esac
done
[[ $EUID -eq 0 || ${RESTORE_TEST_MODE:-false} == true ]] || fail 'root_required' 2
[[ $SERVICE_NAME =~ ^[a-zA-Z0-9@_.-]+$ ]] || fail 'invalid_service_name'
valid_db "$PRODUCTION_DB" || fail 'invalid_production_database_name'
[[ $PRODUCTION_DB != just1kbot_rehearsal_* && $PRODUCTION_DB != just1kbot_candidate_* && $PRODUCTION_DB != just1kbot_failed_* && $PRODUCTION_DB != just1kbot_previous_* ]] || fail 'unsafe_production_database_name'
for command in flock python3 psql pg_restore createdb dropdb sha256sum stat readlink df git timeout; do command -v "$command" >/dev/null || fail "required_command_unavailable:$command"; done
mkdir -p "$(dirname "$RESTORE_LOCK")" "$OPERATION_DIR"; chmod 700 "$OPERATION_DIR"
exec 9>"$RESTORE_LOCK"; flock -n 9 || fail 'another_restore_is_running' 3
exec 8>"$DEPLOY_LOCK"; flock -n 8 || fail 'deploy_is_active' 3
exec 7>"$BACKUP_LOCK"; flock -n 7 || fail 'backup_is_active' 3
case $mode in inspect) inspect_incomplete; result=success; exit 0;; rollback) load_db_env; rollback_operation "$rollback_id" "$rollback_confirm"; exit 0;; finalize) load_db_env; finalize_operation "$finalize_id" "$finalize_confirm"; exit 0;; esac
[[ $confirm == true ]] || fail 'production_restore_confirmation_required' 2
check_incomplete
[[ -n $artifact && -f $artifact && ! -L $artifact ]] || fail 'artifact_missing_or_unsafe'
[[ -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE ]] || fail 'identity_missing_or_unsafe'
[[ -n ${BACKUP_AGE_RECIPIENT:-} && $BACKUP_AGE_RECIPIENT == age1* ]] || fail 'backup_recipient_missing_or_invalid'
load_db_env
canonical=$(readlink -f -- "$artifact"); [[ -f $canonical && ! -L $canonical ]] || fail 'artifact_canonical_path_unsafe'
sidecar="$canonical.sha256"; [[ -f $sidecar && ! -L $sidecar ]] || fail 'sidecar_missing_or_unsafe'
fingerprint() { stat -Lc '%d:%i:%s' "$1"; printf ':%s' "$(sha256sum "$1"|awk '{print $1}')"; }
artifact_fp=$(fingerprint "$canonical"); sidecar_fp=$(fingerprint "$sidecar"); source_sha=$(sha256sum "$canonical"|awk '{print $1}')
# Conservative capacity: encrypted artifact x6 plus the existing production DB x3.
free=$(df -PB1 --output=avail "$(dirname "$canonical")" | tail -1 | tr -d ' '); size=$(stat -Lc %s "$canonical"); dbsize=$(psql_m -At -v target="$PRODUCTION_DB" -c "SELECT pg_database_size(:'target')")
required=$((size*6+dbsize*3+1073741824)); (( free >= required )) || fail 'insufficient_free_space'
operation_id="restore_$(date -u +%Y%m%dT%H%M%SZ)_$(printf %08x "$((RANDOM<<16|RANDOM))")"; manifest_file="$OPERATION_DIR/$operation_id.json"
candidate_db="just1kbot_candidate_$(date -u +%Y%m%d%H%M%S)_$(printf %04x "$RANDOM")"; previous_db="just1kbot_previous_$(date -u +%Y%m%d%H%M%S)_$(printf %04x "$RANDOM")"; failed_db="just1kbot_failed_restore_$(date -u +%Y%m%d%H%M%S)_$(printf %04x "$RANDOM")"
for x in "$candidate_db" "$previous_db" "$failed_db"; do valid_db "$x" || fail 'generated_database_name_unsafe'; done
manifest_update format_version=1 operation_id="$operation_id" started_utc="$(date -u +%FT%TZ)" finished_utc="" code_git_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo unavailable)" source_artifact_basename="$(basename "$canonical")" source_artifact_sha256="$source_sha" source_manifest_revision="" candidate_database="$candidate_db" original_production_database="$PRODUCTION_DB" previous_database_quarantine_name="$previous_db" failed_candidate_name="" emergency_backup_basename="" emergency_backup_path="" emergency_backup_sha256="" stage=verification result=in_progress rollback_attempted=false rollback_result=not_attempted service_health_result=not_checked
workspace=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-production-restore.XXXXXX")
stage=verification
"$VERIFY_BACKUP" --extract-production-components "$workspace/verified" "$canonical" >/dev/null
[[ $(fingerprint "$canonical") == "$artifact_fp" && $(fingerprint "$sidecar") == "$sidecar_fp" ]] || fail 'artifact_or_sidecar_changed_after_verification'
install -m 600 "$canonical" "$workspace/pinned.tar.age"; install -m 600 "$sidecar" "$workspace/pinned.tar.age.sha256"
[[ $(fingerprint "$canonical") == "$artifact_fp" && $(fingerprint "$sidecar") == "$sidecar_fp" ]] || fail 'artifact_or_sidecar_changed_before_restore'
mapfile -d '' -t backup_config < <(CONFIG="$workspace/verified/config.env" python3 - <<'PY'
import os,pathlib,urllib.parse,json
v={}
for line in pathlib.Path(os.environ['CONFIG']).read_text().splitlines():
 if line and not line.lstrip().startswith('#') and '=' in line:
  k,x=line.split('=',1); x=x.strip(); v[k.strip()]=x[1:-1] if len(x)>1 and x[0]==x[-1] and x[0] in "'\"" else x
p=urllib.parse.urlsplit(v.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://',1))
for x in (v.get('DB_ENCRYPTION_KEY',''),p.scheme,p.path[1:]): print(x,end='\0')
PY
)
if [[ ${backup_config[0]} != "$current_key" ]]; then printf 'encryption_key_match=false\n' >&2; fail 'configuration_incompatible'; fi
[[ ${backup_config[1]} == "$production_scheme" && ${backup_config[2]} == "$PRODUCTION_DB" ]] || fail 'backup_database_configuration_incompatible'
source_revision=$(MANIFEST="$workspace/verified/manifest.json" python3 -c 'import json,os; print(json.load(open(os.environ["MANIFEST"]))["alembic_revision"])')
manifest_update source_manifest_revision="$source_revision" stage=candidate_restore
stage=candidate_restore
owner=$(psql_m -At -v target="$PRODUCTION_DB" -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=:'target'"); [[ -n $owner ]] || fail 'production_database_missing'
# template0 plus production encoding/locale avoids inheriting objects and preserves compatibility.
mapfile -t attrs < <(psql_m -At -F $'\t' -v target="$PRODUCTION_DB" -c "SELECT pg_encoding_to_char(encoding),datcollate,datctype FROM pg_database WHERE datname=:'target'")
IFS=$'\t' read -r encoding collate ctype <<<"${attrs[0]}"
createdb --maintenance-db="$MAINTENANCE_DB" --owner="$owner" --template=template0 --encoding="$encoding" --lc-collate="$collate" --lc-ctype="$ctype" "$candidate_db"
pg_restore --exit-on-error --no-owner --no-acl --dbname="$candidate_db" "$workspace/verified/dump.custom" >/dev/null
check_candidate() {
 local expected=$1 rev; rev=$(psql -XAt -v ON_ERROR_STOP=1 -d "$candidate_db" -c 'SELECT version_num FROM alembic_version'); [[ $rev == "$expected" ]] || return 1
 psql -XAt -v ON_ERROR_STOP=1 -d "$candidate_db" -c "SELECT count(*) FROM users; SELECT count(*) FROM payments; SELECT count(*) FROM payment_provider_operations; SELECT count(*) FROM payment_fulfillment_operations; SELECT count(*) FROM webhook_inbox; SET CONSTRAINTS ALL IMMEDIATE; SELECT 1" >/dev/null
}
check_candidate "$source_revision" || fail 'candidate_source_validation_failed'
stage=candidate_migration; manifest_update stage="$stage"
[[ $candidate_db == just1kbot_candidate_* ]] || fail 'migration_target_guard_rejected'
mapfile -t heads < <("$ALEMBIC" -c "$PROJECT_DIR/alembic.ini" heads 2>/dev/null | awk '{print $1}'); (( ${#heads[@]} == 1 )) || fail 'alembic_must_have_exactly_one_head'; code_head=${heads[0]}
RESTORE_CANDIDATE_DATABASE_URL=$(url_for "$candidate_db"); export RESTORE_CANDIDATE_DATABASE_URL DATABASE_URL="$RESTORE_CANDIDATE_DATABASE_URL"
"$ALEMBIC" -c "$PROJECT_DIR/alembic.ini" upgrade head >/dev/null
check_candidate "$code_head" || fail 'candidate_final_validation_failed'
stage=candidate_smoke; manifest_update stage="$stage"; (cd "$PROJECT_DIR" && "$PYTHON" "$VALIDATOR") >/dev/null
# Candidate is complete before the first service-impacting action.
service is-active >/dev/null 2>&1 || fail 'production_service_not_healthy_before_stop'
stage=emergency_backup; manifest_update stage="$stage"; stop_service || fail 'service_did_not_stop'
# Release our probe of the backup lock: backup command owns the same lock itself.
flock -u 7
before=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.tar.age' -printf '%f\n' | sort)
if ! BACKUP_SKIP_RETENTION=true "$BACKUP_COMMAND" >"$workspace/emergency.log" 2>&1; then service start >/dev/null 2>&1 || true; service_stopped=false; health || true; dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DB" "$candidate_db" >/dev/null 2>&1 || true; fail 'emergency_backup_failed'; fi
after=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.tar.age' -printf '%f\n' | sort); emergency_name=$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | tail -1); [[ -n $emergency_name ]] || fail 'emergency_backup_not_identified'; emergency_artifact="$BACKUP_DIR/$emergency_name"
"$VERIFY_BACKUP" "$emergency_artifact" >/dev/null || { service start >/dev/null 2>&1 || true; service_stopped=false; health || true; dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DB" "$candidate_db" >/dev/null 2>&1 || true; fail 'emergency_backup_verification_failed'; }
"$REHEARSAL_COMMAND" "$emergency_artifact" >/dev/null || { service start >/dev/null 2>&1 || true; service_stopped=false; health || true; dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DB" "$candidate_db" >/dev/null 2>&1 || true; fail 'emergency_backup_rehearsal_failed'; }
emergency_sha=$(sha256sum "$emergency_artifact"|awk '{print $1}'); manifest_update emergency_backup_basename="$emergency_name" emergency_backup_path="$emergency_artifact" emergency_backup_sha256="$emergency_sha" stage=swap
stage=swap; service_inactive || fail 'service_became_active_before_swap'; psql_m -At -c "SELECT pg_advisory_lock(hashtext('just1kbot-production-restore'))" >/dev/null
terminate_db "$PRODUCTION_DB"; terminate_db "$candidate_db"
if ! rename_db "$PRODUCTION_DB" "$previous_db"; then fail 'first_database_rename_failed'; fi
if ! rename_db "$candidate_db" "$PRODUCTION_DB"; then rename_db "$previous_db" "$PRODUCTION_DB" || fail 'second_rename_and_immediate_revert_failed' "$CRITICAL_ROLLBACK_EXIT"; fail 'second_database_rename_failed'; fi
swapped=true; manifest_update stage=post_swap_health
stage=post_swap_health; service start >/dev/null; service_stopped=false
if health; then result=success; stage=success; manifest_update stage=success result=success rollback_attempted=false rollback_result=not_attempted service_health_result=success finished_utc="$(date -u +%FT%TZ)"; log 'result=success health=success rollback=not_attempted'; exit 0; fi
stage=automatic_rollback; manifest_update stage="$stage" rollback_attempted=true failed_candidate_name="$failed_db" service_health_result=failure
if rollback_names "$previous_db" "$failed_db"; then swapped=false; result=success; manifest_update result=rolled_back rollback_result=success service_health_result=success finished_utc="$(date -u +%FT%TZ)"; fail 'restored_database_health_failed_rollback_succeeded' 20; fi
manifest_update rollback_result=failure service_health_result=failure finished_utc="$(date -u +%FT%TZ)"
printf 'CRITICAL: rollback health failed. Diagnose with: systemctl status %s; psql -d %s -c "SELECT datname FROM pg_database"; inspect %s\n' "$SERVICE_NAME" "$MAINTENANCE_DB" "$manifest_file" >&2
exit "$CRITICAL_ROLLBACK_EXIT"
