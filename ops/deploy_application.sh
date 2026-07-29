#!/bin/bash
# Transactional application deployment.  This file is sourced by deploy.sh and
# is also executable in the deliberately explicit DEPLOY_TEST_MODE=1 mode.

set -uo pipefail

: "${PROJECT_DIR:=/opt/just1kbot}"
: "${SOURCE_DIR:=$(pwd -P)}"
: "${SNAPSHOT_DIR:=/var/lib/just1kbot/rollback-releases}"
: "${SERVICE_NAME:=just1kbot}"
: "${UNIT_FILE:=/etc/systemd/system/${SERVICE_NAME}.service}"
: "${HEARTBEAT_FILE:=${PROJECT_DIR}/.heartbeat}"
: "${READINESS_TIMEOUT:=75}"
: "${READINESS_POLL_INTERVAL:=2}"
: "${HEALTHCHECK_COMMAND:=/usr/local/bin/just1kbot-healthcheck.sh}"
: "${SNAPSHOT_RETENTION:=3}"
: "${SERVICE_ADAPTER:=}"

DEPLOY_STAGE=preflight
ROLLBACK_SNAPSHOT=
MIGRATIONS_APPLIED=false

deploy_log() { printf '[deploy] stage=%s %s\n' "$DEPLOY_STAGE" "$*"; }
set_stage() { DEPLOY_STAGE=$1; deploy_log "stage_entered=true"; }

service_call() {
    if [[ -n "$SERVICE_ADAPTER" ]]; then "$SERVICE_ADAPTER" "$@"; else
        case "$1" in
            state) systemctl is-active "$SERVICE_NAME" 2>/dev/null || : ;;
            nrestarts) systemctl show "$SERVICE_NAME" -p NRestarts --value 2>/dev/null || printf '0\n' ;;
            mainpid) systemctl show "$SERVICE_NAME" -p MainPID --value 2>/dev/null || printf '0\n' ;;
            pid-exists) kill -0 "$2" 2>/dev/null ;;
            start|stop|enable) systemctl "$1" "$SERVICE_NAME" ;;
            daemon-reload) systemctl daemon-reload ;;
            status) systemctl status "$SERVICE_NAME" --no-pager --lines=20 2>&1 || : ;;
            journal) journalctl -u "$SERVICE_NAME" -n 40 --no-pager 2>&1 || : ;;
            *) return 64 ;;
        esac
    fi
}

redact() {
    sed -E \
      -e 's#(postgres(ql)?|redis)://[^[:space:]]+#\1://[REDACTED]#gI' \
      -e 's#([A-Za-z_]*(TOKEN|PASSWORD|SECRET|KEY)[A-Za-z_]*=)[^[:space:]]+#\1[REDACTED]#gI' \
      -e 's#[0-9]{6,}:[A-Za-z0-9_-]{20,}#[REDACTED_TOKEN]#g'
}

snapshot_previous_release() {
    set_stage snapshot_previous_release
    [[ -d "$PROJECT_DIR" ]] || { deploy_log 'previous_release=absent'; return 0; }
    local stamp temporary final
    stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p -m 0700 "$SNAPSHOT_DIR" || return 1
    temporary="$SNAPSHOT_DIR/.incomplete-$stamp"
    final="$SNAPSHOT_DIR/release-$stamp"
    rm -rf "$temporary"
    mkdir -m 0700 "$temporary" || return 1
    # .env remains live state, never release payload. Runtime/venv is included.
    if ! rsync -a --delete --exclude='.env' --exclude='.heartbeat' \
        --exclude='*.log' --exclude='*.tmp' --exclude='__pycache__/' \
        "$PROJECT_DIR/" "$temporary/application/"; then
        rm -rf "$temporary"; return 1
    fi
    if [[ -f "$UNIT_FILE" ]]; then install -m 0600 "$UNIT_FILE" "$temporary/systemd.service" || { rm -rf "$temporary"; return 1; }; fi
    {
        printf 'created_utc=%s\n' "$(date -u +%FT%TZ)"
        printf 'source_commit=%s\n' "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
    } >"$temporary/VERSION" || { rm -rf "$temporary"; return 1; }
    chmod -R go-rwx "$temporary"
    mv "$temporary" "$final" || { rm -rf "$temporary"; return 1; }
    ROLLBACK_SNAPSHOT=$final
    deploy_log "snapshot=$(basename "$final") complete=true"
}

prepare_new_release() {
    set_stage prepare_new_release
    mkdir -p "$PROJECT_DIR" || return 1
    rsync -a --delete --exclude='.git/' --exclude='.env' --exclude='.heartbeat' \
      --exclude='venv/' --exclude='__pycache__/' "$SOURCE_DIR/" "$PROJECT_DIR/"
}

heartbeat_mtime() { [[ -e "$HEARTBEAT_FILE" ]] && stat -c %Y "$HEARTBEAT_FILE" || printf '0\n'; }

readiness_gate() {
    local label=$1 started=$2 base_restarts=$3 old_heartbeat=$4 forbidden_pid=${5:-0}
    local deadline now state restarts pid first_pid=0 fresh=0 advanced=0 mtime
    deadline=$(( $(date +%s) + READINESS_TIMEOUT ))
    while (( $(date +%s) <= deadline )); do
        state=$(service_call state | tail -n1)
        restarts=$(service_call nrestarts | tail -n1); restarts=${restarts:-0}
        pid=$(service_call mainpid | tail -n1); pid=${pid:-0}
        mtime=$(heartbeat_mtime)
        [[ "$state" == active ]] || { deploy_log "readiness=$label state=$state"; return 1; }
        [[ "$restarts" =~ ^[0-9]+$ && "$base_restarts" =~ ^[0-9]+$ && "$restarts" -le "$base_restarts" ]] || { deploy_log "readiness=$label restarts_changed=true"; return 1; }
        [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] || return 1
        (( forbidden_pid == 0 || pid != forbidden_pid )) || { deploy_log "readiness=$label old_mainpid_reused=true"; return 1; }
        if (( first_pid == 0 )); then first_pid=$pid; elif (( pid != first_pid )); then deploy_log "readiness=$label mainpid_changed=true"; return 1; fi
        if (( mtime >= started && mtime > old_heartbeat )); then
            if (( fresh == 0 )); then fresh=$mtime; elif (( mtime > fresh )); then advanced=1; fi
        fi
        if (( advanced == 1 )) && "$HEALTHCHECK_COMMAND" >/dev/null 2>&1; then
            deploy_log "readiness=$label success=true"; return 0
        fi
        sleep "$READINESS_POLL_INTERVAL"
    done
    deploy_log "readiness=$label timeout=true"; return 1
}

capture_diagnostics() {
    local code=$1
    { printf 'stage=%s exit_code=%s NRestarts=%s\n' "$DEPLOY_STAGE" "$code" "$(service_call nrestarts | tail -n1)"; service_call status; service_call journal; } | redact
}

restore_snapshot() {
    [[ -n "$ROLLBACK_SNAPSHOT" && -d "$ROLLBACK_SNAPSHOT/application" ]] || return 1
    local saved_env=""
    [[ ! -f "$PROJECT_DIR/.env" ]] || { saved_env=$(mktemp); cp -p "$PROJECT_DIR/.env" "$saved_env" || return 1; }
    rsync -a --delete --exclude='.env' --exclude='.heartbeat' "$ROLLBACK_SNAPSHOT/application/" "$PROJECT_DIR/" || return 1
    [[ -z "$saved_env" ]] || { cp -p "$saved_env" "$PROJECT_DIR/.env"; rm -f "$saved_env"; }
    if [[ -f "$ROLLBACK_SNAPSHOT/systemd.service" ]]; then install -m 0644 "$ROLLBACK_SNAPSHOT/systemd.service" "$UNIT_FILE" || return 1; service_call daemon-reload || return 1; fi
}

rollback_application() {
    local original_code=$1
    set_stage rollback_application
    local failed_pid
    failed_pid=$(service_call mainpid | tail -n1); failed_pid=${failed_pid:-0}
    service_call stop || :
    [[ "$(service_call state | tail -n1)" == inactive ]] || { deploy_log 'service_stop_confirmed=false'; return 2; }
    if [[ "$failed_pid" =~ ^[0-9]+$ && "$failed_pid" -gt 0 ]] && service_call pid-exists "$failed_pid"; then deploy_log 'old_mainpid_gone=false'; return 2; fi
    capture_diagnostics "$original_code"
    deploy_log 'database_downgrade=not_performed'
    restore_snapshot || return 2
    set_stage rollback_validation
    local started base heartbeat
    started=$(date +%s); base=$(service_call nrestarts | tail -n1); heartbeat=$(heartbeat_mtime)
    service_call start || return 2
    if readiness_gate previous "$started" "$base" "$heartbeat" "$failed_pid"; then
        deploy_log 'deployment result=rolled_back previous_release_healthy=true'
        return 1
    fi
    service_call stop || :
    deploy_log 'deployment result=rollback_failed previous_release_healthy=false'
    deploy_log 'operator_action=inspect_snapshot_and_schema_compatibility database_downgrade=not_performed'
    return 2
}

cleanup_snapshots() {
    local keep=$SNAPSHOT_RETENTION item count=0
    while IFS= read -r item; do
        count=$((count + 1)); (( count <= keep )) || rm -rf -- "$item" || return 1
    done < <(find "$SNAPSHOT_DIR" -mindepth 1 -maxdepth 1 -type d -name 'release-*' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)
}

run_application_transaction() {
    set_stage preflight
    command -v rsync >/dev/null && [[ "$READINESS_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || return 64
    snapshot_previous_release || { deploy_log 'snapshot_failed=true activation=not_attempted'; return 65; }
    local old_state old_pid old_restarts old_heartbeat
    old_state=$(service_call state | tail -n1)
    old_pid=$(service_call mainpid | tail -n1); old_pid=${old_pid:-0}
    old_restarts=$(service_call nrestarts | tail -n1); old_restarts=${old_restarts:-0}
    old_heartbeat=$(heartbeat_mtime)
    deploy_log "previous_service_state=$old_state previous_mainpid=$old_pid"
    if [[ "$old_state" == active ]]; then
        set_stage stop_previous_release
        service_call stop || { deploy_log 'previous_service_stop=false mutation=not_attempted'; return 65; }
        [[ "$(service_call state | tail -n1)" == inactive ]] || { deploy_log 'previous_service_stop=false mutation=not_attempted'; return 65; }
        if [[ "$old_pid" =~ ^[0-9]+$ && "$old_pid" -gt 0 ]] && service_call pid-exists "$old_pid"; then deploy_log 'previous_mainpid_gone=false mutation=not_attempted'; return 65; fi
        deploy_log 'previous_service_stop=true previous_mainpid_gone=true'
    elif [[ "$old_state" != inactive && "$old_state" != failed && "$old_state" != unknown ]]; then
        deploy_log "previous_service_stop=false unexpected_state=$old_state mutation=not_attempted"; return 65
    fi
    prepare_new_release || { rollback_application 66; return $?; }
    "${PREPARE_COMMAND[@]}" || { rollback_application 66; return $?; }
    # Callbacks are supplied by deploy.sh; tests use executable shims.
    set_stage migrations
    if ! "${MIGRATION_COMMAND[@]}"; then deploy_log 'migration_failed=true activation=not_attempted database_downgrade=not_performed'; rollback_application 67; return $?; fi
    MIGRATIONS_APPLIED=true
    set_stage activate_new_release
    "${ACTIVATION_COMMAND[@]}" || { rollback_application 68; return $?; }
    set_stage start_service
    local started base heartbeat
    started=$(date +%s); base=$(service_call nrestarts | tail -n1); heartbeat=$(heartbeat_mtime)
    service_call start || { rollback_application 69; return $?; }
    set_stage readiness_gate
    if ! readiness_gate new "$started" "$base" "$heartbeat" "$old_pid"; then rollback_application 70; return $?; fi
    set_stage success
    deploy_log 'deployment readiness=success'
    cleanup_snapshots || deploy_log 'warning=snapshot_cleanup_failed'
    return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    [[ "${DEPLOY_TEST_MODE:-0}" == 1 ]] || { echo 'direct execution is test-mode only' >&2; exit 64; }
    MIGRATION_COMMAND=("${TEST_MIGRATION_COMMAND:?}")
    ACTIVATION_COMMAND=("${TEST_ACTIVATION_COMMAND:?}")
    run_application_transaction
fi
