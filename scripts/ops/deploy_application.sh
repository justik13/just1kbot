#!/bin/bash
# Transactional application deployment. This file is sourced by deploy.sh.
# Direct execution is allowed only in DEPLOY_TEST_MODE=1.

set -uo pipefail

: "${PROJECT_DIR:=/opt/just1kbot}"
: "${SOURCE_DIR:=$(pwd -P)}"
: "${SNAPSHOT_DIR:=/var/lib/just1kbot/rollback-releases}"
: "${SERVICE_NAME:=just1kbot}"
: "${UNIT_FILE:=/etc/systemd/system/${SERVICE_NAME}.service}"
: "${HEARTBEAT_FILE:=/run/just1kbot/heartbeat}"
: "${READINESS_TIMEOUT:=150}"
: "${READINESS_POLL_INTERVAL:=2}"
: "${HEALTHCHECK_COMMAND:=/usr/local/bin/just1kbot-healthcheck.sh}"
: "${SNAPSHOT_RETENTION:=3}"
: "${SERVICE_ADAPTER:=}"

DEPLOY_STAGE=preflight
ROLLBACK_SNAPSHOT=
MIGRATIONS_APPLIED=false

PREPARE_COMMAND=(true)
BACKUP_COMMAND=()
MIGRATION_COMMAND=(true)
ACTIVATION_COMMAND=(true)

deploy_log() {
    printf '[deploy] stage=%s %s\n' "$DEPLOY_STAGE" "$*"
}

set_stage() {
    DEPLOY_STAGE=$1
    deploy_log "stage_entered=true"
}

service_call() {
    if [[ -n "$SERVICE_ADAPTER" ]]; then
        "$SERVICE_ADAPTER" "$@"
        return
    fi

    case "$1" in
        state)
            systemctl is-active "$SERVICE_NAME" 2>/dev/null || printf 'inactive\n'
            ;;
        nrestarts)
            systemctl show "$SERVICE_NAME" -p NRestarts --value 2>/dev/null || printf '0\n'
            ;;
        mainpid)
            systemctl show "$SERVICE_NAME" -p MainPID --value 2>/dev/null || printf '0\n'
            ;;
        pid-exists)
            kill -0 "$2" 2>/dev/null
            ;;
        start|stop|enable)
            systemctl "$1" "$SERVICE_NAME"
            ;;
        daemon-reload)
            systemctl daemon-reload
            ;;
        status)
            systemctl status "$SERVICE_NAME" --no-pager --lines=30 2>&1 || :
            ;;
        journal)
            journalctl -u "$SERVICE_NAME" -n 80 --no-pager 2>&1 || :
            ;;
        *)
            return 64
            ;;
    esac
}

redact() {
    sed -E \
        -e 's#(postgres(ql)?|redis)://[^[:space:]]+#\1://[REDACTED]#gI' \
        -e 's#([A-Za-z_]*(TOKEN|PASSWORD|SECRET|KEY)[A-Za-z_]*=)[^[:space:]]+#\1[REDACTED]#gI' \
        -e 's#[0-9]{6,}:[A-Za-z0-9_-]{20,}#[REDACTED_TOKEN]#g'
}

snapshot_previous_release() {
    set_stage snapshot_previous_release

    if [[ ! -d "$PROJECT_DIR" ]]; then
        deploy_log 'previous_release=absent'
        return 0
    fi

    local stamp temporary final
    stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p -m 0700 "$SNAPSHOT_DIR" || return 1
    temporary="$SNAPSHOT_DIR/.incomplete-$stamp"
    final="$SNAPSHOT_DIR/release-$stamp"

    rm -rf -- "$temporary"
    mkdir -m 0700 "$temporary" || return 1

    # .env is persistent live state and must never come from a release snapshot.
    if ! rsync -a --delete \
        --exclude='.env' \
        --exclude='.heartbeat' \
        --exclude='*.log' \
        --exclude='*.tmp' \
        --exclude='__pycache__/' \
        "$PROJECT_DIR/" "$temporary/application/"; then
        rm -rf -- "$temporary"
        return 1
    fi

    if [[ -f "$UNIT_FILE" ]]; then
        install -m 0600 "$UNIT_FILE" "$temporary/systemd.service" || {
            rm -rf -- "$temporary"
            return 1
        }
    fi

    {
        printf 'created_utc=%s\n' "$(date -u +%FT%TZ)"
        printf 'source_commit=%s\n' "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
    } > "$temporary/VERSION" || {
        rm -rf -- "$temporary"
        return 1
    }

    chmod -R go-rwx "$temporary"
    mv -- "$temporary" "$final" || {
        rm -rf -- "$temporary"
        return 1
    }

    ROLLBACK_SNAPSHOT=$final
    deploy_log "snapshot=$(basename "$final") complete=true"
}

prepare_new_release() {
    set_stage prepare_new_release
    mkdir -p "$PROJECT_DIR" || return 1

    local source_real project_real
    source_real=$(cd "$SOURCE_DIR" && pwd -P) || return 1
    project_real=$(cd "$PROJECT_DIR" && pwd -P) || return 1

    if [[ "$source_real" == "$project_real" ]]; then
        deploy_log 'source_equals_live_project=true unsafe_update=true'
        return 1
    fi

    rsync -a --delete \
        --exclude='.git/' \
        --exclude='.env' \
        --exclude='.heartbeat' \
        --exclude='venv/' \
        --exclude='__pycache__/' \
        "$SOURCE_DIR/" "$PROJECT_DIR/"

    # Generate .release-version file
    {
        printf 'created_utc=%s\n' "$(date -u +%FT%TZ)"
        printf 'source_commit=%s\n' "$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
    } > "$PROJECT_DIR/.release-version"
}

heartbeat_mtime() {
    if [[ -e "$HEARTBEAT_FILE" ]]; then
        stat -c %Y "$HEARTBEAT_FILE"
    else
        printf '0\n'
    fi
}

readiness_gate() {
    local label=$1
    local started=$2
    local base_restarts=$3
    local old_heartbeat=$4
    local forbidden_pid=${5:-0}
    local deadline state restarts pid first_pid=0 first_fresh=0 mtime

    deadline=$(( $(date +%s) + READINESS_TIMEOUT ))

    while (( $(date +%s) <= deadline )); do
        state=$(service_call state | tail -n1)
        restarts=$(service_call nrestarts | tail -n1)
        pid=$(service_call mainpid | tail -n1)
        mtime=$(heartbeat_mtime)
        restarts=${restarts:-0}
        pid=${pid:-0}

        if [[ "$state" != active ]]; then
            deploy_log "readiness=$label state=$state"
            return 1
        fi
        if [[ ! "$restarts" =~ ^[0-9]+$ || ! "$base_restarts" =~ ^[0-9]+$ || "$restarts" -gt "$base_restarts" ]]; then
            deploy_log "readiness=$label restarts_changed=true"
            return 1
        fi
        if [[ ! "$pid" =~ ^[0-9]+$ || "$pid" -le 0 ]]; then
            deploy_log "readiness=$label invalid_mainpid=true"
            return 1
        fi
        if (( forbidden_pid > 0 && pid == forbidden_pid )); then
            deploy_log "readiness=$label old_mainpid_reused=true"
            return 1
        fi
        if (( first_pid == 0 )); then
            first_pid=$pid
        elif (( pid != first_pid )); then
            deploy_log "readiness=$label mainpid_changed=true"
            return 1
        fi

        if (( mtime >= started && mtime > old_heartbeat )); then
            if (( first_fresh == 0 )); then
                first_fresh=$mtime
            elif (( mtime > first_fresh )); then
                if "$HEALTHCHECK_COMMAND" >/dev/null 2>&1; then
                    deploy_log "readiness=$label success=true"
                    return 0
                fi
            fi
        fi

        sleep "$READINESS_POLL_INTERVAL"
    done

    deploy_log "readiness=$label timeout=true"
    return 1
}

capture_diagnostics() {
    local code=$1
    {
        printf 'stage=%s exit_code=%s NRestarts=%s\n' \
            "$DEPLOY_STAGE" "$code" "$(service_call nrestarts | tail -n1)"
        service_call status
        service_call journal
    } | redact
}

restore_snapshot() {
    [[ -n "$ROLLBACK_SNAPSHOT" && -d "$ROLLBACK_SNAPSHOT/application" ]] || return 1

    local saved_env=""
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        saved_env=$(mktemp)
        cp -p "$PROJECT_DIR/.env" "$saved_env" || return 1
    fi

    rsync -a --delete \
        --exclude='.env' \
        --exclude='.heartbeat' \
        "$ROLLBACK_SNAPSHOT/application/" "$PROJECT_DIR/" || return 1

    if [[ -n "$saved_env" ]]; then
        cp -p "$saved_env" "$PROJECT_DIR/.env" || return 1
        rm -f -- "$saved_env"
    fi

    if [[ -f "$ROLLBACK_SNAPSHOT/systemd.service" ]]; then
        install -m 0644 "$ROLLBACK_SNAPSHOT/systemd.service" "$UNIT_FILE" || return 1
        service_call daemon-reload || return 1
    fi
}

rollback_application() {
    local original_code=$1
    set_stage rollback_application

    local failed_pid
    failed_pid=$(service_call mainpid | tail -n1)
    failed_pid=${failed_pid:-0}

    service_call stop || :
    if [[ "$(service_call state | tail -n1)" != inactive ]]; then
        deploy_log 'service_stop_confirmed=false'
        return 2
    fi
    if [[ "$failed_pid" =~ ^[0-9]+$ && "$failed_pid" -gt 0 ]] && service_call pid-exists "$failed_pid"; then
        deploy_log 'failed_mainpid_gone=false'
        return 2
    fi

    capture_diagnostics "$original_code"
    deploy_log 'database_downgrade=not_performed'
    restore_snapshot || return 2

    set_stage rollback_validation
    local started base heartbeat
    started=$(date +%s)
    base=$(service_call nrestarts | tail -n1)
    heartbeat=$(heartbeat_mtime)

    service_call start || return 2
    if readiness_gate previous "$started" "$base" "$heartbeat" "$failed_pid"; then
        deploy_log 'deployment_result=rolled_back previous_release_healthy=true'
        return 1
    fi

    service_call stop || :
    deploy_log 'deployment_result=rollback_failed previous_release_healthy=false'
    deploy_log 'operator_action=inspect_snapshot_and_schema_compatibility database_downgrade=not_performed'
    return 2
}

cleanup_snapshots() {
    local keep=$SNAPSHOT_RETENTION
    local item count=0

    while IFS= read -r item; do
        count=$((count + 1))
        if (( count > keep )); then
            rm -rf -- "$item" || return 1
        fi
    done < <(
        find "$SNAPSHOT_DIR" -mindepth 1 -maxdepth 1 -type d \
            -name 'release-*' -printf '%T@ %p\n' 2>/dev/null \
            | sort -rn | cut -d' ' -f2-
    )
}

run_application_transaction() {
    set_stage preflight

    command -v rsync >/dev/null || return 64
    [[ "$READINESS_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || return 64
    [[ ${#PREPARE_COMMAND[@]} -gt 0 ]] || return 64
    [[ ${#MIGRATION_COMMAND[@]} -gt 0 ]] || return 64
    [[ ${#ACTIVATION_COMMAND[@]} -gt 0 ]] || return 64

    snapshot_previous_release || {
        deploy_log 'snapshot_failed=true activation=not_attempted'
        return 65
    }

    local old_state old_pid old_restarts old_heartbeat
    old_state=$(service_call state | tail -n1)
    old_pid=$(service_call mainpid | tail -n1)
    old_restarts=$(service_call nrestarts | tail -n1)
    old_heartbeat=$(heartbeat_mtime)
    old_pid=${old_pid:-0}
    old_restarts=${old_restarts:-0}

    deploy_log "previous_service_state=$old_state previous_mainpid=$old_pid"

    if [[ "$old_state" == active ]]; then
        set_stage stop_previous_release
        service_call stop || {
            deploy_log 'previous_service_stop=false mutation=not_attempted'
            return 65
        }
        if [[ "$(service_call state | tail -n1)" != inactive ]]; then
            deploy_log 'previous_service_stop=false mutation=not_attempted'
            return 65
        fi
        if [[ "$old_pid" =~ ^[0-9]+$ && "$old_pid" -gt 0 ]] && service_call pid-exists "$old_pid"; then
            deploy_log 'previous_mainpid_gone=false mutation=not_attempted'
            return 65
        fi
        deploy_log 'previous_service_stop=true previous_mainpid_gone=true'
    elif [[ "$old_state" != inactive && "$old_state" != failed && "$old_state" != unknown ]]; then
        deploy_log "unexpected_previous_state=$old_state mutation=not_attempted"
        return 65
    fi

    if [[ ${#BACKUP_COMMAND[@]} -gt 0 ]]; then
        set_stage database_backup
        if ! "${BACKUP_COMMAND[@]}"; then
            deploy_log 'database_backup_failed=true migration=not_attempted'
            rollback_application 66
            return $?
        fi
        deploy_log 'database_backup=success'
    fi

    prepare_new_release || {
        rollback_application 66
        return $?
    }

    "${PREPARE_COMMAND[@]}" || {
        rollback_application 66
        return $?
    }

    set_stage migrations
    if ! "${MIGRATION_COMMAND[@]}"; then
        deploy_log 'migration_failed=true activation=not_attempted database_downgrade=not_performed'
        rollback_application 67
        return $?
    fi
    MIGRATIONS_APPLIED=true

    set_stage activate_new_release
    "${ACTIVATION_COMMAND[@]}" || {
        rollback_application 68
        return $?
    }

    set_stage start_service
    local started base heartbeat
    started=$(date +%s)
    base=$(service_call nrestarts | tail -n1)
    heartbeat=$(heartbeat_mtime)

    service_call start || {
        rollback_application 69
        return $?
    }

    set_stage readiness_gate
    if ! readiness_gate new "$started" "$base" "$heartbeat" "$old_pid"; then
        rollback_application 70
        return $?
    fi

    set_stage success
    deploy_log 'deployment readiness=success'
    cleanup_snapshots || deploy_log 'warning=snapshot_cleanup_failed'
    return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    [[ "${DEPLOY_TEST_MODE:-0}" == 1 ]] || {
        echo 'direct execution is test-mode only' >&2
        exit 64
    }
    PREPARE_COMMAND=(true)
    BACKUP_COMMAND=()
    MIGRATION_COMMAND=("${TEST_MIGRATION_COMMAND:?}")
    ACTIVATION_COMMAND=("${TEST_ACTIVATION_COMMAND:?}")
    run_application_transaction
fi
