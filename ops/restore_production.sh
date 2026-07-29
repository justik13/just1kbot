#!/bin/bash
set -Eeuo pipefail
umask 077

ENV_FILE=${ENV_FILE:-/opt/just1kbot/.env}; PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}
SERVICE_NAME=${RESTORE_SERVICE_NAME:-just1kbot}; PRODUCTION_DB=${RESTORE_PRODUCTION_DATABASE:-just1kbot_bot}
MAINTENANCE_DB=${RESTORE_MAINTENANCE_DATABASE:-postgres}; OPERATION_DIR=${RESTORE_OPERATION_DIR:-/root/restore-operations}
BACKUP_DIR=${BACKUP_DIR:-/root/backups/just1kbot}; RESTORE_LOCK=${RESTORE_LOCK_FILE:-/run/lock/just1kbot-restore.lock}
DEPLOY_LOCK=${DEPLOY_LOCK_FILE:-/run/lock/just1kbot-deploy.lock}; BACKUP_LOCK=${BACKUP_LOCK_FILE:-/run/lock/just1kbot-backup.lock}
VERIFY_BACKUP=${VERIFY_BACKUP:-/usr/local/bin/verify_backup.sh}; BACKUP_COMMAND=${BACKUP_COMMAND:-/usr/local/bin/just1kbot-backup.sh}
REHEARSAL_COMMAND=${REHEARSAL_COMMAND:-/usr/local/bin/restore_rehearsal.sh}; VALIDATOR=${RESTORE_CANDIDATE_VALIDATOR:-$PROJECT_DIR/ops/validate_restore_candidate.py}
PYTHON=${RESTORE_PYTHON:-$PROJECT_DIR/venv/bin/python}; ALEMBIC=${RESTORE_ALEMBIC:-$PROJECT_DIR/venv/bin/alembic}
ADVISORY_HELPER=${RESTORE_ADVISORY_HELPER:-/usr/local/bin/hold_restore_advisory_lock.py}
HEALTHCHECK=${RESTORE_HEALTHCHECK:-/usr/local/bin/just1kbot-healthcheck.sh}; HEARTBEAT_FILE=${RESTORE_HEARTBEAT_FILE:-$PROJECT_DIR/.heartbeat}
HEALTH_TIMEOUT=${RESTORE_HEALTH_TIMEOUT:-180}; FINALIZE_SAFETY_SECONDS=${RESTORE_FINALIZE_SAFETY_SECONDS:-86400}
CRITICAL_RECOVERY_EXIT=43; CRITICAL_ROLLBACK_EXIT=42
workspace="" operation_id="" manifest_file="" candidate_db="" previous_db="" failed_db="" emergency_artifact=""
stage=initializing result="" cutover_phase=before_stop advisory_pid="" advisory_fd="" cleanup_running=false operation_created=false rename_count=0

log(){ printf 'restore operation=%s stage=%s %s\n' "${operation_id:-none}" "$stage" "$*" >&2; }
fail(){ log "result=failure exit_code=${2:-1} reason=$1"; return "${2:-1}"; }
valid_db(){ [[ $1 =~ ^[a-z][a-z0-9_]{0,62}$ ]]; }; valid_operation(){ [[ $1 =~ ^restore_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}$ ]]; }
service(){ if [[ -n ${RESTORE_SERVICE_ADAPTER:-} ]]; then "$RESTORE_SERVICE_ADAPTER" "$1" "$SERVICE_NAME"; else systemctl "$1" "$SERVICE_NAME"; fi; }
psql_m(){ psql -X -v ON_ERROR_STOP=1 -d "$MAINTENANCE_DB" "$@"; }
db_exists(){ [[ $(psql_m -At -v target="$1" -c "SELECT count(*) FROM pg_database WHERE datname=:'target'" 2>/dev/null) == 1 ]]; }
terminate_db(){ psql_m -At -v target="$1" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:'target' AND pid<>pg_backend_pid()" >/dev/null; }
rename_db(){
 valid_db "$1" && valid_db "$2" || return 1; rename_count=$((rename_count+1))
 if [[ ${RESTORE_TEST_MODE:-false} == true && ${RESTORE_TEST_FAIL_RENAME_NUMBER:-0} == "$rename_count" ]]; then return 99; fi
 psql_m -q -c "ALTER DATABASE \"$1\" RENAME TO \"$2\""
}

manifest_update(){
 [[ -n $manifest_file ]] || return 0
 MANIFEST="$manifest_file" python3 - "$@" <<'PY'
import json,os,pathlib,sys,tempfile
p=pathlib.Path(os.environ['MANIFEST']); d=json.loads(p.read_text()) if p.exists() else {}
for item in sys.argv[1:]:
 k,v=item.split('=',1); d[k]=({'true':True,'false':False}.get(v,v))
fd,n=tempfile.mkstemp(prefix='.'+p.name+'.',dir=p.parent)
try:
 with os.fdopen(fd,'w') as f: json.dump(d,f,sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
 os.chmod(n,0o600); os.replace(n,p)
finally:
 try: os.unlink(n)
 except FileNotFoundError: pass
PY
}
validate_manifest(){
 local path=$1 expected_uid=0; [[ ${RESTORE_TEST_MODE:-false} != true ]] || expected_uid=$(id -u)
 [[ -f $path && ! -L $path && $(stat -c %u "$path") == "$expected_uid" ]] || return 1
 (( 8#$(stat -c %a "$path") <= 8#600 )) || return 1
 MANIFEST="$path" python3 - <<'PY'
import json,os,pathlib,re
p=pathlib.Path(os.environ['MANIFEST']); d=json.loads(p.read_text())
required={'format_version','operation_id','started_utc','finished_utc','code_git_sha','source_artifact_basename','source_artifact_sha256','source_manifest_revision','candidate_database','original_production_database','previous_database_quarantine_name','failed_candidate_name','emergency_backup_basename','emergency_backup_path','emergency_backup_sha256','finalize_backup_basename','finalize_backup_path','finalize_backup_sha256','stage','result','rollback_attempted','rollback_result','service_health_result'}
if set(d)!=required or d['format_version']!=1 or d['operation_id']!=p.stem: raise SystemExit(1)
if d['result'] not in {'in_progress','failed_safe','success','rolled_back','requires_manual_recovery','rollback_failed','finalized'}: raise SystemExit(1)
if not re.fullmatch(r'restore_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}',d['operation_id']): raise SystemExit(1)
stamp=d['operation_id'][8:23].replace('T','').replace('Z','')
for key,prefix in [('candidate_database','just1kbot_candidate_'),('previous_database_quarantine_name','just1kbot_previous_')]:
 if not re.fullmatch(prefix+r'[a-z0-9_]+',d[key]): raise SystemExit(1)
 if not d[key].startswith(prefix+stamp+'_'): raise SystemExit(1)
if d['failed_candidate_name'] and not re.fullmatch(r'just1kbot_failed_restore_[a-z0-9_]+',d['failed_candidate_name']): raise SystemExit(1)
PY
}

health(){
 local deadline=$((SECONDS+HEALTH_TIMEOUT)) seen=0 last="" stamp now
 service is-active >/dev/null 2>&1 || return 1
 while ((SECONDS<deadline && seen<2)); do
  if [[ -f $HEARTBEAT_FILE ]]; then stamp=$(stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null||:); now=$(date +%s); [[ $stamp =~ ^[0-9]+$ ]] && ((now-stamp<=120)) && [[ $stamp != "$last" ]] && { seen=$((seen+1)); last=$stamp; }
  elif [[ -n ${RESTORE_SERVICE_ADAPTER:-} ]]; then seen=2; fi
  ((seen>=2)) || sleep "${RESTORE_HEALTH_POLL_SECONDS:-5}"
 done
 ((seen>=2)) || return 1
 [[ ! -x $HEALTHCHECK ]] || timeout "$HEALTH_TIMEOUT" "$HEALTHCHECK" >/dev/null
 psql -XAt -v ON_ERROR_STOP=1 -d "$PRODUCTION_DB" -c 'SELECT count(*) FROM payment_provider_operations; SELECT count(*) FROM payment_fulfillment_operations; SELECT count(*) FROM webhook_inbox' >/dev/null
 sleep "${RESTORE_CRASH_WINDOW_SECONDS:-5}"; service is-active >/dev/null 2>&1
}
stop_service(){ service stop >/dev/null && ! service is-active >/dev/null 2>&1 || return 1; [[ -n ${RESTORE_SERVICE_ADAPTER:-} ]] || ! pgrep -u just1kbot -f "$PROJECT_DIR" >/dev/null 2>&1; }
start_advisory_lock(){
 [[ -z $advisory_pid ]] || return 0; [[ -x $ADVISORY_HELPER || -f $ADVISORY_HELPER ]] || return 1
 mkfifo "$workspace/advisory.release"; : >"$workspace/advisory.ready"
 RESTORE_MAINTENANCE_DATABASE="$MAINTENANCE_DB" "$PYTHON" "$ADVISORY_HELPER" <"$workspace/advisory.release" >"$workspace/advisory.ready" 2>"$workspace/advisory.error" & advisory_pid=$!
 exec {advisory_fd}>"$workspace/advisory.release"
 local deadline=$((SECONDS+30))
 while ((SECONDS<deadline)); do grep -qx 'advisory_lock=acquired' "$workspace/advisory.ready" 2>/dev/null && return 0; kill -0 "$advisory_pid" 2>/dev/null || return 1; sleep .1; done
 return 1
}
stop_advisory_lock(){
 [[ -n $advisory_pid ]] || return 0
 eval "exec ${advisory_fd}>&-" 2>/dev/null || true; wait "$advisory_pid" 2>/dev/null || true; advisory_pid=""; advisory_fd=""
}
safe_diagnostics(){ printf 'diagnose: systemctl status %s; psql -d %s -c "SELECT datname FROM pg_database"; inspect %s\n' "$SERVICE_NAME" "$MAINTENANCE_DB" "$manifest_file" >&2; }
recover_original_service_before_swap(){
 stage=recover_original_service; manifest_update stage="$stage"
 if ! db_exists "$PRODUCTION_DB" || db_exists "$previous_db"; then
  result=requires_manual_recovery; manifest_update result="$result" service_health_result=failure finished_utc="$(date -u +%FT%TZ)"; safe_diagnostics; return "$CRITICAL_RECOVERY_EXIT"
 fi
 if ! service start >/dev/null || ! service is-active >/dev/null 2>&1 || ! health; then
  result=requires_manual_recovery; manifest_update result="$result" service_health_result=failure finished_utc="$(date -u +%FT%TZ)"; safe_diagnostics; return "$CRITICAL_RECOVERY_EXIT"
 fi
 cutover_phase=resolved; result=failed_safe; manifest_update result=failed_safe service_health_result=success finished_utc="$(date -u +%FT%TZ)"; return 0
}
verify_safe_pre_swap_state(){ db_exists "$PRODUCTION_DB" && ! db_exists "$previous_db" && service is-active >/dev/null 2>&1 && health; }
cleanup(){
 local rc=$?; $cleanup_running && exit "$rc"; cleanup_running=true; set +e
 if ((rc!=0)) && [[ $cutover_phase == stopping || $cutover_phase == stopped_pre_swap ]]; then recover_original_service_before_swap; recovery_rc=$?; ((recovery_rc==0)) || rc=$recovery_rc; fi
 if ((rc!=0)) && [[ $cutover_phase == swap_started ]]; then result=requires_manual_recovery; manifest_update result="$result" service_health_result=failure finished_utc="$(date -u +%FT%TZ)"; safe_diagnostics; rc=$CRITICAL_RECOVERY_EXIT; fi
 if ((rc!=0)) && $operation_created && [[ -n $manifest_file && -z $result && $cutover_phase == before_stop ]]; then
  if verify_safe_pre_swap_state; then result=failed_safe; manifest_update result=failed_safe service_health_result=success finished_utc="$(date -u +%FT%TZ)"; else result=requires_manual_recovery; manifest_update result="$result" service_health_result=failure finished_utc="$(date -u +%FT%TZ)"; rc=$CRITICAL_RECOVERY_EXIT; fi
 fi
 stop_advisory_lock
 if ((rc!=0)) && [[ $result == failed_safe && -n $candidate_db && $candidate_db == just1kbot_candidate_* && -z $emergency_artifact ]]; then dropdb --force --if-exists --maintenance-db="$MAINTENANCE_DB" "$candidate_db" >/dev/null 2>&1 || true; fi
 [[ -z $workspace ]] || rm -rf -- "$workspace"; exit "$rc"
}
trap cleanup EXIT INT TERM

load_db_env(){
 [[ -f $ENV_FILE && ! -L $ENV_FILE ]] || return 1
 mapfile -d '' -t dbparts < <(ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os,pathlib,urllib.parse
v={}
for line in pathlib.Path(os.environ['ENV_FILE']).read_text().splitlines():
 if line and not line.lstrip().startswith('#') and '=' in line:
  k,x=line.split('=',1);x=x.strip();v[k.strip()]=x[1:-1] if len(x)>1 and x[0]==x[-1] and x[0] in "'\"" else x
p=urllib.parse.urlsplit(v.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://',1))
if p.scheme not in ('postgresql','postgres') or not p.hostname or not p.path[1:] or not v.get('DB_ENCRYPTION_KEY'): raise SystemExit(1)
for x in (p.scheme,p.hostname,str(p.port or 5432),urllib.parse.unquote(p.username or ''),urllib.parse.unquote(p.password or ''),p.path[1:],v['DB_ENCRYPTION_KEY']):print(x,end='\0')
PY
 ); (( ${#dbparts[@]}==7 )) || return 1; [[ ${dbparts[5]} == "$PRODUCTION_DB" ]] || return 1
 export PGHOST=${dbparts[1]} PGPORT=${dbparts[2]} PGUSER=${dbparts[3]} PGPASSWORD=${dbparts[4]}; production_scheme=${dbparts[0]}; current_key=${dbparts[6]}
}
url_for(){ DB="$1" python3 - <<'PY'
import os,urllib.parse
u=urllib.parse.quote(os.environ.get('PGUSER',''),safe='');p=urllib.parse.quote(os.environ.get('PGPASSWORD',''),safe='');a=u+((':'+p) if p else '')+('@' if u else '')
print(f"postgresql+asyncpg://{a}{os.environ['PGHOST']}:{os.environ['PGPORT']}/{os.environ['DB']}")
PY
}
parse_backup_result(){
 local f=$1 expected_pin=$2
 mapfile -d '' -t backup_result < <(RESULT="$f" BACKUP_DIR="$BACKUP_DIR" EXPECTED_PIN="$expected_pin" python3 - <<'PY'
import hashlib,os,pathlib,re
p=pathlib.Path(os.environ['RESULT']); root=pathlib.Path(os.environ['BACKUP_DIR']).resolve()
if not p.is_file() or p.is_symlink() or (p.stat().st_mode&0o077): raise SystemExit(1)
d={}
for line in p.read_text().splitlines():
 if line.count('=')!=1: raise SystemExit(1)
 k,v=line.split('=',1)
 if k in d: raise SystemExit(1)
 d[k]=v
if set(d)!={'format_version','artifact_path','artifact_sha256','artifact_pin'} or d['format_version']!='1' or d['artifact_pin']!=os.environ['EXPECTED_PIN'] or not re.fullmatch(r'[0-9a-f]{64}',d['artifact_sha256']): raise SystemExit(1)
a=pathlib.Path(d['artifact_path'])
if a.resolve().parent!=root or a.name!=a.resolve().name or not re.fullmatch(r'just1kbot-pg-v1-[0-9]{8}T[0-9]{6}Z\.tar\.age',a.name): raise SystemExit(1)
for x in (a,pathlib.Path(str(a)+'.sha256'),pathlib.Path(str(a)+'.pin')):
 if not x.is_file() or x.is_symlink(): raise SystemExit(1)
if hashlib.sha256(a.read_bytes()).hexdigest()!=d['artifact_sha256']: raise SystemExit(1)
pin=dict(line.split('=',1) for line in pathlib.Path(str(a)+'.pin').read_text().splitlines())
if pin!={'format_version':'1','artifact_pin':d['artifact_pin'],'artifact_sha256':d['artifact_sha256']}: raise SystemExit(1)
for x in (str(a),d['artifact_sha256'],d['artifact_pin']):print(x,end='\0')
PY
 ) || return 1; (( ${#backup_result[@]}==3 ))
}
create_pinned_backup(){
 local pin=$1 output=$2
 rm -f -- "$output"; flock -u 7
 if ! BACKUP_RESULT_FILE="$output" BACKUP_ARTIFACT_PIN="$pin" "$BACKUP_COMMAND" >/dev/null; then flock -n 7 || true; return 1; fi
 flock -n 7 || return 1
 parse_backup_result "$output" "$pin" || return 1
}
verify_recovery_artifact(){
 local path=$1 sha=$2 expected_pin=$3
 [[ -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE ]] || return 1
 [[ -f $path && ! -L $path && -f $path.sha256 && ! -L $path.sha256 && -f $path.pin && ! -L $path.pin ]] || return 1
 [[ $(sha256sum "$path"|awk '{print $1}') == "$sha" ]] || return 1
 PIN_FILE="$path.pin" EXPECTED_PIN="$expected_pin" EXPECTED_SHA="$sha" python3 - <<'PY' || return 1
import os,pathlib
d=dict(line.split('=',1) for line in pathlib.Path(os.environ['PIN_FILE']).read_text().splitlines())
if d!={'format_version':'1','artifact_pin':os.environ['EXPECTED_PIN'],'artifact_sha256':os.environ['EXPECTED_SHA']}: raise SystemExit(1)
PY
 "$VERIFY_BACKUP" "$path" >/dev/null && "$REHEARSAL_COMMAND" "$path" >/dev/null
}
check_incomplete(){
 local f status c p x; shopt -s nullglob
 for f in "$OPERATION_DIR"/*.json; do
  validate_manifest "$f" || { fail incomplete_or_invalid_operation 12; return; }
  mapfile -d '' -t x < <(MANIFEST="$f" python3 -c 'import json,os;d=json.load(open(os.environ["MANIFEST"]));[print(d[k],end="\0") for k in ("result","candidate_database","previous_database_quarantine_name","failed_candidate_name")]')
  status=${x[0]}; c=${x[1]}; p=${x[2]}
  [[ $status == failed_safe || $status == success || $status == rolled_back || $status == finalized ]] || { fail incomplete_operation_exists 12; return; }
  case $status in
   failed_safe) db_exists "$PRODUCTION_DB" && ! db_exists "$p" && service is-active >/dev/null 2>&1 || { fail operation_database_state_mismatch 12; return; };;
   success) db_exists "$PRODUCTION_DB" && db_exists "$p" && ! db_exists "$c" || { fail operation_database_state_mismatch 12; return; };;
   rolled_back) db_exists "$PRODUCTION_DB" && ! db_exists "$p" && [[ -n ${x[3]} ]] && db_exists "${x[3]}" || { fail operation_database_state_mismatch 12; return; };;
   finalized) db_exists "$PRODUCTION_DB" && ! db_exists "$p" || { fail operation_database_state_mismatch 12; return; };;
  esac
 done
}
inspect_incomplete(){
 local f; shopt -s nullglob
 for f in "$OPERATION_DIR"/*.json; do
  if ! validate_manifest "$f"; then printf 'operation=%s result=invalid stage=unknown recommended_action=repair_manifest_offline\n' "$(basename "$f" .json)"; continue; fi
  mapfile -d '' -t x < <(MANIFEST="$f" python3 -c 'import json,os;d=json.load(open(os.environ["MANIFEST"]));[print(d[k],end="\0") for k in ("operation_id","result","stage","candidate_database","previous_database_quarantine_name","failed_candidate_name")]')
  printf 'operation=%s result=%s stage=%s production_exists=%s candidate_exists=%s previous_exists=%s failed_exists=%s service_active=%s recommended_action=%s\n' "${x[0]}" "${x[1]}" "${x[2]}" "$(db_exists "$PRODUCTION_DB"&&echo true||echo false)" "$(db_exists "${x[3]}"&&echo true||echo false)" "$(db_exists "${x[4]}"&&echo true||echo false)" "$([[ -n ${x[5]} ]]&&db_exists "${x[5]}"&&echo true||echo false)" "$(service is-active >/dev/null 2>&1&&echo true||echo false)" "$([[ ${x[1]} == in_progress || ${x[1]} == requires_manual_recovery || ${x[1]} == rollback_failed ]]&&echo inspect_then_explicit_rollback||echo none)"
 done
}
rollback_database_names(){
 start_advisory_lock || return 1; stop_service || return 1; cutover_phase=swap_started
 if db_exists "$previous_db" && db_exists "$PRODUCTION_DB"; then terminate_db "$PRODUCTION_DB"; rename_db "$PRODUCTION_DB" "$failed_db" || return 1; rename_db "$previous_db" "$PRODUCTION_DB" || return 1
 elif db_exists "$previous_db" && ! db_exists "$PRODUCTION_DB"; then rename_db "$previous_db" "$PRODUCTION_DB" || return 1
 else return 1; fi
 cutover_phase=stopped_pre_swap
 if service start >/dev/null && service is-active >/dev/null 2>&1 && health; then cutover_phase=resolved; stop_advisory_lock; return 0; fi
 return 1
}
manual_rollback(){
 local id=$1 confirm=$2; [[ $confirm == true ]] || { fail production_rollback_confirmation_required 2; return; }; valid_operation "$id" || return 2
 manifest_file="$OPERATION_DIR/$id.json"; validate_manifest "$manifest_file" || return 1; operation_id=$id
 mapfile -d '' -t x < <(MANIFEST="$manifest_file" python3 -c 'import json,os;d=json.load(open(os.environ["MANIFEST"]));[print(d[k],end="\0") for k in ("result","previous_database_quarantine_name","failed_candidate_name")]')
 [[ ${x[0]} == in_progress || ${x[0]} == requires_manual_recovery || ${x[0]} == rollback_failed ]] || return 1
 previous_db=${x[1]}; failed_db=${x[2]:-just1kbot_failed_restore_$(date -u +%Y%m%d%H%M%S)_$(printf %04x "$RANDOM")}; valid_db "$failed_db" && [[ $failed_db == just1kbot_failed_restore_* ]] || return 1
 stage=manual_rollback; result=""; manifest_update stage="$stage" rollback_attempted=true failed_candidate_name="$failed_db"
 if rollback_database_names; then result=rolled_back; manifest_update result=rolled_back rollback_result=success service_health_result=success finished_utc="$(date -u +%FT%TZ)"; else result=rollback_failed; manifest_update result=rollback_failed rollback_result=failure service_health_result=failure finished_utc="$(date -u +%FT%TZ)"; safe_diagnostics; return "$CRITICAL_ROLLBACK_EXIT"; fi
}
finalize(){
 local id=$1 confirm=$2; [[ $confirm == true ]] || return 2; valid_operation "$id" || return 2; manifest_file="$OPERATION_DIR/$id.json"; validate_manifest "$manifest_file" || return 1; operation_id=$id
 mapfile -d '' -t x < <(MANIFEST="$manifest_file" python3 -c 'import json,os;d=json.load(open(os.environ["MANIFEST"]));[print(d[k],end="\0") for k in ("result","previous_database_quarantine_name","emergency_backup_path","emergency_backup_sha256","finished_utc")]')
 [[ ${x[0]} == success ]] || return 1; previous_db=${x[1]}; [[ $previous_db == just1kbot_previous_* ]] && valid_db "$previous_db" || return 1
 (( $(date +%s)-$(date -d "${x[4]}" +%s)>=FINALIZE_SAFETY_SECONDS )) || return 1
 verify_recovery_artifact "${x[2]}" "${x[3]}" "$operation_id" || return 1; health || return 1
 workspace=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-finalize.XXXXXX")
 create_pinned_backup "$operation_id:finalize" "$workspace/finalize.result" || return 1; finalize_artifact=${backup_result[0]}; finalize_sha=${backup_result[1]}
 verify_recovery_artifact "$finalize_artifact" "$finalize_sha" "$operation_id:finalize" || return 1
 manifest_update finalize_backup_basename="$(basename "$finalize_artifact")" finalize_backup_path="$finalize_artifact" finalize_backup_sha256="$finalize_sha" stage=finalize_verified
 health || return 1; db_exists "$previous_db" || return 1; dropdb --maintenance-db="$MAINTENANCE_DB" "$previous_db" || return 1
 result=finalized; manifest_update stage=finalized result=finalized finished_utc="$(date -u +%FT%TZ)"
}

mode=restore artifact="" confirm=false rollback_id="" finalize_id="" rollback_confirm=false finalize_confirm=false
while (($#)); do case $1 in --artifact) artifact=${2:-};shift 2;;--confirm-production-restore)confirm=true;shift;;--inspect-incomplete)mode=inspect;shift;;--rollback-operation)mode=rollback;rollback_id=${2:-};shift 2;;--confirm-production-rollback)rollback_confirm=true;shift;;--finalize-operation)mode=finalize;finalize_id=${2:-};shift 2;;--confirm-delete-previous)finalize_confirm=true;shift;;*)fail unsupported_argument 2;exit $?;;esac;done
[[ $EUID -eq 0 || ${RESTORE_TEST_MODE:-false} == true ]] || { fail root_required 2;exit $?; }
[[ $SERVICE_NAME =~ ^[a-zA-Z0-9@_.-]+$ ]] && valid_db "$PRODUCTION_DB" || exit 2
[[ $PRODUCTION_DB != just1kbot_rehearsal_* && $PRODUCTION_DB != just1kbot_candidate_* && $PRODUCTION_DB != just1kbot_failed_* && $PRODUCTION_DB != just1kbot_previous_* ]] || exit 2
for c in flock python3 psql pg_restore createdb dropdb sha256sum stat readlink df git timeout;do command -v "$c">/dev/null||exit 1;done
[[ ! -L $OPERATION_DIR ]]; mkdir -p "$(dirname "$RESTORE_LOCK")" "$OPERATION_DIR" "$BACKUP_DIR"; chmod 700 "$OPERATION_DIR" "$BACKUP_DIR"
expected_owner=0; [[ ${RESTORE_TEST_MODE:-false} != true ]] || expected_owner=$(id -u)
[[ ! -L $OPERATION_DIR && $(stat -c %u "$OPERATION_DIR") == "$expected_owner" && $(stat -c %a "$OPERATION_DIR") == 700 ]] || exit 1
exec 9>"$RESTORE_LOCK";flock -n 9||exit 3;exec 8>"$DEPLOY_LOCK";flock -n 8||exit 3;exec 7>"$BACKUP_LOCK";flock -n 7||exit 3
load_db_env || exit 1
case $mode in inspect)inspect_incomplete;result=success;exit 0;;rollback)workspace=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-rollback.XXXXXX");manual_rollback "$rollback_id" "$rollback_confirm";exit $?;;finalize)finalize "$finalize_id" "$finalize_confirm";exit $?;;esac
[[ $confirm == true ]]||{ fail production_restore_confirmation_required 2;exit $?;};check_incomplete
[[ -n $artifact && -f $artifact && ! -L $artifact && -n ${AGE_IDENTITY_FILE:-} && -f $AGE_IDENTITY_FILE && ! -L $AGE_IDENTITY_FILE && ${BACKUP_AGE_RECIPIENT:-} == age1* ]]||exit 1
canonical=$(readlink -f "$artifact");sidecar=$canonical.sha256;[[ -f $canonical && ! -L $canonical && -f $sidecar && ! -L $sidecar ]]||exit 1
fingerprint(){ stat -Lc '%d:%i:%s' "$1";printf ':%s' "$(sha256sum "$1"|awk '{print $1}')";};artifact_fp=$(fingerprint "$canonical");sidecar_fp=$(fingerprint "$sidecar");source_sha=$(sha256sum "$canonical"|awk '{print $1}');size=$(stat -c %s "$canonical");dbsize=$(psql_m -At -v target="$PRODUCTION_DB" -c "SELECT pg_database_size(:'target')");required=$((size*6+dbsize*3+1073741824))
workspace=$(mktemp -d "${TMPDIR:-/tmp}/just1kbot-production-restore.XXXXXX")
pgdata=$(psql_m -At -c 'SHOW data_directory'); for path in "$workspace" "$BACKUP_DIR"; do free=$(df -PB1 --output=avail "$path"|tail -1|tr -d ' '); ((free>=required))||exit 1; done
if [[ -d $pgdata ]]; then pgfree=$(df -PB1 --output=avail "$pgdata"|tail -1|tr -d ' '); else [[ ${RESTORE_TEST_MODE:-false} == true && ${RESTORE_PG_FREE_SPACE_BYTES:-} =~ ^[0-9]+$ ]] || exit 1; pgfree=$RESTORE_PG_FREE_SPACE_BYTES; fi; ((pgfree>=required))||exit 1
op_time=$(date -u +%Y%m%dT%H%M%SZ); op_compact=${op_time//[TZ]/}; operation_id="restore_${op_time}_$(printf %08x "$((RANDOM<<16|RANDOM))")";manifest_file=$OPERATION_DIR/$operation_id.json
candidate_db="just1kbot_candidate_${op_compact}_$(printf %04x "$RANDOM")";previous_db="just1kbot_previous_${op_compact}_$(printf %04x "$RANDOM")";failed_db="just1kbot_failed_restore_${op_compact}_$(printf %04x "$RANDOM")"
manifest_update format_version=1 operation_id="$operation_id" started_utc="$(date -u +%FT%TZ)" finished_utc= code_git_sha="$(git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null||echo unavailable)" source_artifact_basename="$(basename "$canonical")" source_artifact_sha256="$source_sha" source_manifest_revision= candidate_database="$candidate_db" original_production_database="$PRODUCTION_DB" previous_database_quarantine_name="$previous_db" failed_candidate_name= emergency_backup_basename= emergency_backup_path= emergency_backup_sha256= finalize_backup_basename= finalize_backup_path= finalize_backup_sha256= stage=verification result=in_progress rollback_attempted=false rollback_result=not_attempted service_health_result=not_checked
operation_created=true
"$VERIFY_BACKUP" --extract-production-components "$workspace/verified" "$canonical">/dev/null;[[ $(fingerprint "$canonical") == "$artifact_fp" && $(fingerprint "$sidecar") == "$sidecar_fp" ]]||exit 1;install -m600 "$canonical" "$workspace/pinned.tar.age";install -m600 "$sidecar" "$workspace/pinned.tar.age.sha256";[[ $(fingerprint "$canonical") == "$artifact_fp" && $(fingerprint "$sidecar") == "$sidecar_fp" ]]||exit 1
mapfile -d '' -t cfg < <(CONFIG="$workspace/verified/config.env" python3 - <<'PY'
import os,pathlib,urllib.parse
v={}
for l in pathlib.Path(os.environ['CONFIG']).read_text().splitlines():
 if l and not l.lstrip().startswith('#') and '=' in l:k,x=l.split('=',1);x=x.strip();v[k.strip()]=x[1:-1] if len(x)>1 and x[0]==x[-1] and x[0] in "'\"" else x
p=urllib.parse.urlsplit(v.get('DATABASE_URL','').replace('postgresql+asyncpg://','postgresql://',1))
for x in(v.get('DB_ENCRYPTION_KEY',''),p.scheme,p.path[1:]):print(x,end='\0')
PY
);[[ ${cfg[0]} == "$current_key" ]]||{ printf 'encryption_key_match=false\n'>&2;exit 1;};[[ ${cfg[1]} == "$production_scheme" && ${cfg[2]} == "$PRODUCTION_DB" ]]||exit 1
source_revision=$(MANIFEST="$workspace/verified/manifest.json" python3 -c 'import json,os;print(json.load(open(os.environ["MANIFEST"]))["alembic_revision"])');manifest_update source_manifest_revision="$source_revision" stage=candidate_restore
owner=$(psql_m -At -v target="$PRODUCTION_DB" -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=:'target'");IFS=$'\t' read -r encoding collate ctype<<<"$(psql_m -At -F $'\t' -v target="$PRODUCTION_DB" -c "SELECT pg_encoding_to_char(encoding),datcollate,datctype FROM pg_database WHERE datname=:'target'")";createdb --maintenance-db="$MAINTENANCE_DB" --owner="$owner" --template=template0 --encoding="$encoding" --lc-collate="$collate" --lc-ctype="$ctype" "$candidate_db";pg_restore --exit-on-error --no-owner --no-acl --dbname="$candidate_db" "$workspace/verified/dump.custom">/dev/null
check_candidate(){ [[ $(psql -XAt -v ON_ERROR_STOP=1 -d "$candidate_db" -c 'SELECT version_num FROM alembic_version') == "$1" ]]&&psql -XAt -v ON_ERROR_STOP=1 -d "$candidate_db" -c 'SELECT count(*) FROM users;SELECT count(*) FROM payments;SELECT count(*) FROM payment_provider_operations;SELECT count(*) FROM payment_fulfillment_operations;SELECT count(*) FROM webhook_inbox;SET CONSTRAINTS ALL IMMEDIATE;SELECT 1'>/dev/null;};check_candidate "$source_revision"
[[ $candidate_db == just1kbot_candidate_* ]]||exit 1;mapfile -t heads < <("$ALEMBIC" -c "$PROJECT_DIR/alembic.ini" heads|awk '{print $1}');((${#heads[@]}==1))||exit 1;export RESTORE_CANDIDATE_DATABASE_URL=$(url_for "$candidate_db") DATABASE_URL="$RESTORE_CANDIDATE_DATABASE_URL";"$ALEMBIC" -c "$PROJECT_DIR/alembic.ini" upgrade head>/dev/null;check_candidate "${heads[0]}";(cd "$PROJECT_DIR"&&"$PYTHON" "$VALIDATOR")>/dev/null
stage=pre_stop_health;manifest_update stage="$stage";health||exit 1;stage=emergency_backup;manifest_update stage="$stage";cutover_phase=stopping;stop_service||exit 1;cutover_phase=stopped_pre_swap
create_pinned_backup "$operation_id" "$workspace/emergency.result"||exit 1;emergency_artifact=${backup_result[0]};emergency_sha=${backup_result[1]};verify_recovery_artifact "$emergency_artifact" "$emergency_sha" "$operation_id"||exit 1;manifest_update emergency_backup_basename="$(basename "$emergency_artifact")" emergency_backup_path="$emergency_artifact" emergency_backup_sha256="$emergency_sha" stage=swap
start_advisory_lock||exit 1; if [[ ${RESTORE_TEST_MODE:-false} == true && -n ${RESTORE_TEST_AFTER_ADVISORY_HOOK:-} ]]; then "$RESTORE_TEST_AFTER_ADVISORY_HOOK"; fi; stage=swap;cutover_phase=swap_started;terminate_db "$PRODUCTION_DB";terminate_db "$candidate_db"
if ! rename_db "$PRODUCTION_DB" "$previous_db";then cutover_phase=stopped_pre_swap;exit 1;fi
if ! rename_db "$candidate_db" "$PRODUCTION_DB";then if rename_db "$previous_db" "$PRODUCTION_DB";then cutover_phase=stopped_pre_swap;else result=requires_manual_recovery;manifest_update result="$result" service_health_result=failure;fi;exit 1;fi
cutover_phase=swapped;stage=post_swap_health;manifest_update stage="$stage";service start>/dev/null||true
if health;then cutover_phase=resolved;stop_advisory_lock;result=success;manifest_update stage=success result=success rollback_attempted=false rollback_result=not_attempted service_health_result=success finished_utc="$(date -u +%FT%TZ)";exit 0;fi
stage=automatic_rollback;manifest_update stage="$stage" rollback_attempted=true failed_candidate_name="$failed_db" service_health_result=failure;service stop>/dev/null||true
terminate_db "$PRODUCTION_DB"||{ result=rollback_failed;manifest_update result="$result" rollback_result=failure;exit "$CRITICAL_ROLLBACK_EXIT";};rename_db "$PRODUCTION_DB" "$failed_db"||{ result=rollback_failed;manifest_update result="$result" rollback_result=failure;exit "$CRITICAL_ROLLBACK_EXIT";};rename_db "$previous_db" "$PRODUCTION_DB"||{ result=rollback_failed;manifest_update result="$result" rollback_result=failure;exit "$CRITICAL_ROLLBACK_EXIT";}
if service start>/dev/null&&health;then cutover_phase=resolved;stop_advisory_lock;result=rolled_back;manifest_update result=rolled_back rollback_result=success service_health_result=success finished_utc="$(date -u +%FT%TZ)";exit 20;fi
result=rollback_failed;manifest_update result=rollback_failed rollback_result=failure service_health_result=failure finished_utc="$(date -u +%FT%TZ)";safe_diagnostics;exit "$CRITICAL_ROLLBACK_EXIT"
