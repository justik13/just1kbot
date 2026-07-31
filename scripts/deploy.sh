#!/bin/bash
# Safe adapter around the existing audited deploy implementation.
# It keeps the old behavior while fixing PostgreSQL cluster/port discovery,
# the first-install rollback path, and the repository layout.

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd -P)
LEGACY_DEPLOY="$SCRIPT_DIR/deploy_full.sh"
POSTGRES_LIBRARY="$SCRIPT_DIR/lib/postgresql.sh"

[[ -f "$LEGACY_DEPLOY" && ! -L "$LEGACY_DEPLOY" ]] || {
    printf 'Отсутствует scripts/deploy_full.sh\n' >&2
    exit 1
}
[[ -f "$POSTGRES_LIBRARY" && ! -L "$POSTGRES_LIBRARY" ]] || {
    printf 'Отсутствует scripts/lib/postgresql.sh\n' >&2
    exit 1
}

DEPLOY_FUNCTIONS_ONLY=1
# shellcheck source=deploy_full.sh
source "$LEGACY_DEPLOY"
unset DEPLOY_FUNCTIONS_ONLY

SOURCE_DIR="$ROOT_DIR"

# shellcheck source=lib/postgresql.sh
source "$POSTGRES_LIBRARY"

clone_function() {
    local original=$1 replacement=$2 definition
    definition=$(declare -f "$original") || return 1
    definition=${definition/#"$original ()"/"$replacement ()"}
    eval "$definition"
}

clone_function install_backup_tooling legacy_install_backup_tooling
clone_function setup_systemd legacy_setup_systemd
clone_function setup_firewall_initial legacy_setup_firewall_initial
clone_function show_status legacy_show_status

validate_env_file_safety() {
    if [[ -L "$ENV_FILE" || ! -f "$ENV_FILE" ]]; then
        error "Production .env отсутствует, не является regular file или является symlink"
        return 1
    fi

    local mode owner group
    mode=$(stat -c '%a' "$ENV_FILE")
    owner=$(stat -c '%U' "$ENV_FILE")
    group=$(stat -c '%G' "$ENV_FILE")

    if [[ "$owner" == root && "$group" == "$BOT_USER" && "$mode" == 640 ]]; then
        return 0
    fi

    # Transitional compatibility with installations made by the previous
    # deploy. The file is converted to root:just1kbot 0640 before migrations.
    if [[ "$owner" == "$BOT_USER" && "$group" == "$BOT_USER" && "$mode" == 600 ]]; then
        return 0
    fi

    error "Production .env должен быть root:${BOT_USER} 0640"
    error "Текущее состояние: owner=${owner} group=${group} mode=${mode}"
    return 1
}

validate_source_tree() {
    local required
    for required in \
        requirements.txt \
        alembic.ini \
        bot/main.py \
        scripts/deploy_full.sh \
        scripts/lib/postgresql.sh \
        scripts/ops/deploy_application.sh \
        scripts/ops/backup_postgres.sh \
        scripts/ops/verify_backup.sh \
        scripts/ops/restore_rehearsal.sh \
        scripts/ops/just1kbot-restore.sh; do
        [[ -f "$ROOT_DIR/$required" && ! -L "$ROOT_DIR/$required" ]] || {
            error "В исходном каталоге отсутствует безопасный файл $required"
            return 1
        }
    done

    if [[ "$INITIAL_INSTALL" == false ]]; then
        local source_real project_real
        source_real=$(cd "$ROOT_DIR" && pwd -P)
        project_real=$(cd "$PROJECT_DIR" && pwd -P)
        if [[ "$source_real" == "$project_real" ]]; then
            error "Обновление нельзя запускать прямо из live-каталога $PROJECT_DIR"
            error "Используйте отдельный checkout/release-каталог"
            return 1
        fi
    fi
}

ensure_env_permissions() {
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
        error "Production .env отсутствует или является symlink"
        return 1
    }
    chown root:"$BOT_USER" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    validate_env_file
}

create_env_if_missing() {
    if [[ -e "$ENV_FILE" || -L "$ENV_FILE" ]]; then
        validate_env_file
        ensure_env_permissions
        log "Существующий production .env сохранён; изменяется только подтверждённый PostgreSQL port"
        return
    fi

    [[ "$PG_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( PG_PORT <= 65535 )) || {
        error "PostgreSQL port не определён"
        return 1
    }

    log "Создание production .env с PostgreSQL port=$PG_PORT"
    install -o root -g "$BOT_USER" -m 0640 /dev/null "$ENV_FILE"

    if [[ -z "${DB_ENCRYPTION_KEY:-}" ]]; then
        DB_ENCRYPTION_KEY=$(python3 - <<'PY'
import base64
import secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
)
    fi

    local db_encoded redis_encoded
    db_encoded=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$DB_PASSWORD")
    redis_encoded=$(python3 -c 'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe=""))' "$REDIS_PASSWORD")

    write_env_var BOT_TOKEN "$BOT_TOKEN"
    write_env_var ADMIN_IDS "$(normalize_admin_ids_json "$ADMIN_IDS")"
    write_env_var DATABASE_URL "postgresql+asyncpg://just1kbot:${db_encoded}@127.0.0.1:${PG_PORT}/just1kbot_bot"
    write_env_var DB_ENCRYPTION_KEY "$DB_ENCRYPTION_KEY"
    write_env_var REDIS_URL "redis://:${redis_encoded}@127.0.0.1:6379/0"
    write_env_var REDIS_PASSWORD "$REDIS_PASSWORD"
    write_env_var AMNEZIA_API_URL "$AMNEZIA_API_URL"
    write_env_var AMNEZIA_API_KEY "$AMNEZIA_API_KEY"
    write_env_var YOOKASSA_SHOP_ID "$YOOKASSA_SHOP_ID"
    write_env_var YOOKASSA_SECRET_KEY "$YOOKASSA_SECRET_KEY"
    write_env_var YOOKASSA_RETURN_URL 'https://t.me/{bot_username}'
    write_env_var YOOKASSA_WEBHOOK_PORT '8080'
    if [[ -n "$DOMAIN" ]]; then
        write_env_var DOMAIN "$DOMAIN"
        write_env_var WEBHOOK_URL "https://${DOMAIN}/webhook/yookassa"
    fi

    ensure_env_permissions
}

setup_postgresql_initial() {
    log "Настройка PostgreSQL-кластера ${PG_VERSION}/${PG_CLUSTER} на порту ${PG_PORT}"
    pg_prepare_initial_database
}

patch_postgresql_unit_dependency() {
    local unit_file=$1
    [[ -f "$unit_file" && ! -L "$unit_file" ]] || return 0
    sed -i -E "s/postgresql[.]service/${PG_UNIT}/g" "$unit_file"
}

install_backup_tooling() {
    local saved_source=$SOURCE_DIR
    SOURCE_DIR="$SCRIPT_DIR"
    legacy_install_backup_tooling
    SOURCE_DIR=$saved_source

    patch_postgresql_unit_dependency /etc/systemd/system/just1kbot-backup.service
    systemctl daemon-reload
}

setup_systemd() {
    legacy_setup_systemd
    patch_postgresql_unit_dependency "$UNIT_FILE"
    systemctl daemon-reload
}

setup_firewall_initial() {
    legacy_setup_firewall_initial
    if [[ "$PG_PORT" != 5432 ]]; then
        ufw deny "${PG_PORT}/tcp" >/dev/null
    fi
}

show_status() {
    legacy_show_status
    if [[ -n "$PG_UNIT" ]]; then
        printf 'PostgreSQL cluster: %s (%s), port=%s\n' \
            "${PG_VERSION}/${PG_CLUSTER}" \
            "$(systemctl is-active "$PG_UNIT" 2>/dev/null || true)" \
            "$PG_PORT"
    fi
}

run_restore_rehearsal() {
    local artifact=$1

    [[ -f "$artifact" && ! -L "$artifact" ]] || {
        error "Backup-файл не найден или небезопасен: $artifact"
        return 1
    }
    [[ -n "${AGE_IDENTITY_FILE:-}" && -f "$AGE_IDENTITY_FILE" && ! -L "$AGE_IDENTITY_FILE" ]] || {
        error "Задайте AGE_IDENTITY_FILE с закрытым age-ключом"
        return 1
    }
    [[ -x /usr/local/bin/just1kbot-restore.sh ]] || {
        error "Restore tooling не установлен. Сначала выполните deploy/update"
        return 1
    }

    pg_prepare update || return 1
    pg_repair_env_port || return 1
    ensure_env_permissions || return 1

    local work artifact_copy identity_copy sidecar_copy="" rc
    work=$(mktemp -d /var/lib/postgresql/just1kbot-restore.XXXXXX)
    TEMP_DIRS+=("$work")
    chown postgres:postgres "$work"
    chmod 0700 "$work"

    artifact_copy="$work/$(basename "$artifact")"
    identity_copy="$work/identity.agekey"

    install -o postgres -g postgres -m 0600 "$artifact" "$artifact_copy"
    install -o postgres -g postgres -m 0400 "$AGE_IDENTITY_FILE" "$identity_copy"

    if [[ -f "$artifact.sha256" && ! -L "$artifact.sha256" ]]; then
        sidecar_copy="$artifact_copy.sha256"
        install -o postgres -g postgres -m 0600 "$artifact.sha256" "$sidecar_copy"
    fi

    set +e
    runuser -u postgres -- env \
        AGE_IDENTITY_FILE="$identity_copy" \
        REHEARSAL_MAINTENANCE_DATABASE=postgres \
        PGHOST="$PG_SOCKET_DIR" \
        PGPORT="$PG_PORT" \
        /usr/local/bin/just1kbot-restore.sh "$artifact_copy"
    rc=$?
    set -e

    rm -rf -- "$work"
    return "$rc"
}

install_rollback_override() {
    rollback_application() {
        local original_code=$1
        set_stage rollback_application

        local failed_pid
        failed_pid=$(service_call mainpid | tail -n1)
        failed_pid=${failed_pid:-0}

        service_call stop || :
        local stopped_state
        stopped_state=$(service_call state | tail -n1)
        if [[ "$stopped_state" != inactive && "$stopped_state" != failed ]]; then
            deploy_log "service_stop_confirmed=false state=$stopped_state"
            return 2
        fi
        if [[ "$failed_pid" =~ ^[0-9]+$ && "$failed_pid" -gt 0 ]] &&
            service_call pid-exists "$failed_pid"; then
            deploy_log 'failed_mainpid_gone=false'
            return 2
        fi

        capture_diagnostics "$original_code"
        deploy_log 'database_downgrade=not_performed'
        restore_snapshot || return 2

        # No systemd unit in the snapshot means there was no previous installed
        # service. A failed first/incomplete install must not try to start it.
        if [[ ! -f "$ROLLBACK_SNAPSHOT/systemd.service" ]]; then
            rm -f -- "$UNIT_FILE"
            service_call daemon-reload || return 2
            deploy_log 'deployment_result=rolled_back previous_service=absent start_not_attempted=true'
            return 1
        fi

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
}

run_management_action() {
    require_root
    init_logging

    case "$ACTION" in
        status)
            pg_select_cluster || true
            show_status
            ;;
        logs)
            exec journalctl -u "$SERVICE_NAME" -f -n 100
            ;;
        restart)
            acquire_deploy_lock
            pg_prepare update
            pg_repair_env_port
            ensure_env_permissions
            systemctl restart "$SERVICE_NAME"
            if ! wait_for_application_health; then
                journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2 || true
                printf 'Сервис перезапущен, но readiness/healthcheck не пройден.\n' >&2
                exit 1
            fi
            show_status
            ;;
        backup)
            pg_prepare update
            pg_repair_env_port
            ensure_env_permissions
            run_manual_backup
            ;;
        restore)
            run_restore_rehearsal "$ACTION_ARG"
            ;;
    esac
}

run_deploy() {
    require_root
    init_logging
    acquire_deploy_lock
    check_os
    determine_install_kind
    validate_source_tree

    if [[ "$DRY_RUN" == true ]]; then
        print_dry_run
        return
    fi

    if [[ "$INITIAL_INSTALL" == true ]]; then
        collect_initial_input
        validate_initial_input
        install_dependencies
    fi

    validate_runtime_commands
    command_required pg_lsclusters
    command_required pg_ctlcluster
    command_required pg_isready

    setup_user_and_dirs

    if [[ "$INITIAL_INSTALL" == true ]]; then
        pg_select_cluster
        pg_start_cluster
        setup_postgresql_initial
        setup_redis_initial
    else
        pg_prepare update
        pg_repair_env_port
        ensure_env_permissions

        systemctl is-active --quiet redis-server || {
            error "Redis не запущен"
            return 1
        }
    fi

    install_backup_tooling
    install_healthcheck
    setup_logrotate

    if [[ "$INITIAL_INSTALL" == true ]]; then
        setup_firewall_initial
        setup_nginx_initial
    else
        refresh_existing_nginx
    fi

    pause_operational_timers

    # shellcheck source=ops/deploy_application.sh
    source "$SCRIPT_DIR/ops/deploy_application.sh"
    install_rollback_override

    SOURCE_DIR="$ROOT_DIR"
    PREPARE_COMMAND=(prepare_release_runtime)
    MIGRATION_COMMAND=(init_database)
    ACTIVATION_COMMAND=(activate_release)
    BACKUP_COMMAND=()

    if [[ "$INITIAL_INSTALL" == false ]]; then
        BACKUP_COMMAND=(create_pre_migration_backup)
    fi

    if run_application_transaction; then
        resume_operational_timers
        show_status
        print_result
    else
        local code=$?
        resume_operational_timers || true
        error "Application deploy transaction failed (code=$code)"
        error "Database downgrade не выполнялся; проверьте журнал и rollback snapshot"
        return "$code"
    fi
}

main "$@"
