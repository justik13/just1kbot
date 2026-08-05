#!/bin/bash
# Read-only classification of Just1kBot installation state.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}
ENV_FILE=${ENV_FILE:-$PROJECT_DIR/.env}
UNIT_FILE=${UNIT_FILE:-/etc/systemd/system/just1kbot.service}
BOT_USER=${BOT_USER:-just1kbot}
BOT_HOME=${BOT_HOME:-/home/just1kbot}
CLI_SBIN=${CLI_SBIN:-/usr/local/sbin/just1kbot}
CLI_BIN=${CLI_BIN:-/usr/local/bin/just1kbot}
STATE_ROOT=${STATE_ROOT:-/var/lib/just1kbot}
STATE_DIR=${STATE_DIR:-$STATE_ROOT/install-state}
MANIFEST=${MANIFEST:-$STATE_DIR/manifest.json}
TRANSACTION=${TRANSACTION:-$STATE_DIR/transaction.json}
REDIS_CONFIG=${REDIS_CONFIG:-/etc/just1kbot/redis.conf}
REDIS_DATA_DIR=${REDIS_DATA_DIR:-$STATE_ROOT/redis}
REDIS_UNIT=${REDIS_UNIT:-/etc/systemd/system/just1kbot-redis.service}
BACKUP_DIR=${BACKUP_DIR:-/var/lib/just1kbot/backups}
BACKUP_CONF=${BACKUP_CONF:-/etc/just1kbot-backup.conf}
BACKUP_IDENTITY=${BACKUP_IDENTITY:-/etc/just1kbot/backup.agekey}
BACKUP_TOOL=${BACKUP_TOOL:-/usr/local/bin/just1kbot-backup.sh}
RESTORE_TOOL=${RESTORE_TOOL:-/usr/local/bin/just1kbot-restore.sh}
HEALTHCHECK_TOOL=${HEALTHCHECK_TOOL:-/usr/local/bin/just1kbot-healthcheck.sh}
VERIFY_TOOL=${VERIFY_TOOL:-/usr/local/bin/verify_backup.sh}
REHEARSAL_TOOL=${REHEARSAL_TOOL:-/usr/local/bin/restore_rehearsal.sh}

INSTALL_STATE=unknown
INSTALL_STATE_REASON=
INSTALL_STATE_ACTION=
INSTALL_STATE_EVIDENCE=()
OUTPUT_MODE=text
REQUIRE_SAFE=0
OPERATION=deploy

add_evidence() { INSTALL_STATE_EVIDENCE+=("$1"); }
path_exists() { [[ -e "$1" || -L "$1" ]]; }

regular_root_owned_tool() {
    local path=$1 owner group mode
    [[ -f "$path" && ! -L "$path" ]] || return 1
    IFS=' ' read -r owner group mode < <(stat -c '%U %G %a' "$path")
    [[ "$owner" == root && "$group" == root ]] || return 1
    (( (8#$mode & 8#022) == 0 ))
}

unit_looks_managed() {
    [[ -f "$UNIT_FILE" && ! -L "$UNIT_FILE" ]] || return 1
    grep -Eq '^Description=.*Just1kBot' "$UNIT_FILE" || return 1
    grep -Fq "ExecStart=$PROJECT_DIR/" "$UNIT_FILE"
}

redis_unit_looks_managed() {
    [[ -f "$REDIS_UNIT" && ! -L "$REDIS_UNIT" ]] || return 1
    grep -Fq 'Description=Just1kBot dedicated Redis' "$REDIS_UNIT" || return 1
    grep -Fq "ExecStart=/usr/bin/redis-server $REDIS_CONFIG" "$REDIS_UNIT"
}

project_looks_managed() {
    [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || return 1
    [[ -f "$PROJECT_DIR/deploy.sh" && ! -L "$PROJECT_DIR/deploy.sh" ]] || return 1
    grep -Fq 'Just1kBot' "$PROJECT_DIR/deploy.sh"
}

legacy_cli_looks_managed() {
    # State inspection is diagnostic only. Destructive cleanup performs the
    # stricter root ownership check before deleting the launcher.
    [[ -f "$CLI_SBIN" && ! -L "$CLI_SBIN" ]] || return 1
    grep -Fq 'Just1kBot' "$CLI_SBIN" 2>/dev/null || return 1
    grep -Fq '/opt/just1kbot' "$CLI_SBIN" 2>/dev/null || return 1
}

preserved_backup_looks_managed() {
    [[ -f "$BACKUP_CONF" && ! -L "$BACKUP_CONF" ]] || return 1
    grep -Eq '^BACKUP_AGE_RECIPIENT=age1[0-9a-z]+' "$BACKUP_CONF" || return 1
    [[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] || return 1
    find "$BACKUP_DIR" -maxdepth 1 -type f \
        -name 'just1kbot-pg-v1-*.tar.age' -print -quit 2>/dev/null | grep -q .
}

manifest_is_valid() {
    [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || return 1
    [[ "$(stat -c '%U:%G %a' "$MANIFEST" 2>/dev/null || true)" == 'root:root 600' ]] || return 1
    MANIFEST_PATH="$MANIFEST" EXPECTED_PROJECT_DIR="$PROJECT_DIR" python3 - <<'PY_MANIFEST' >/dev/null
import json
import os
import re
from pathlib import Path

value = json.loads(Path(os.environ["MANIFEST_PATH"]).read_text(encoding="utf-8"))
if value.get("schema_version") != 1:
    raise SystemExit(1)
if value.get("project_dir") != os.environ["EXPECTED_PROJECT_DIR"]:
    raise SystemExit(1)
if re.fullmatch(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    str(value.get("installation_id", "")),
    re.IGNORECASE,
) is None:
    raise SystemExit(1)
resources = value.get("managed_resources")
metadata = value.get("metadata")
if not isinstance(resources, list):
    raise SystemExit(1)
if not all(isinstance(item, str) and item for item in resources):
    raise SystemExit(1)
if len(resources) != len(set(resources)):
    raise SystemExit(1)
allowed = (
    "path:", "systemd:", "service-user:", "postgresql:",
    "nginx-site:", "nginx-enabled:", "certbot:", "tcp:",
)
if any(not item.startswith(allowed) for item in resources):
    raise SystemExit(1)
if not isinstance(metadata, dict):
    raise SystemExit(1)
if metadata.get("firewall_managed") is not False:
    raise SystemExit(1)
if metadata.get("redis_mode") != "dedicated-service":
    raise SystemExit(1)
if metadata.get("redis_port") != 6380:
    raise SystemExit(1)
if metadata.get("platform") != "ubuntu-24.04":
    raise SystemExit(1)
PY_MANIFEST
}

transaction_is_valid() {
    [[ -f "$TRANSACTION" && ! -L "$TRANSACTION" ]] || return 1
    [[ "$(stat -c '%U:%G %a' "$TRANSACTION" 2>/dev/null || true)" == 'root:root 600' ]] || return 1
    TRANSACTION_PATH="$TRANSACTION" python3 - <<'PY_TRANSACTION' >/dev/null
import json
import os
from pathlib import Path

value = json.loads(Path(os.environ["TRANSACTION_PATH"]).read_text(encoding="utf-8"))
if value.get("schema_version") != 1:
    raise SystemExit(1)
if value.get("operation") not in {"install", "update", "uninstall"}:
    raise SystemExit(1)
if not isinstance(value.get("phase"), str) or not value["phase"]:
    raise SystemExit(1)
if not isinstance(value.get("created_resources"), list):
    raise SystemExit(1)
PY_TRANSACTION
}

collect_evidence() {
    local label path
    while IFS=$'\t' read -r label path; do
        path_exists "$path" && add_evidence "$label=$path" || true
    done <<EOF_EVIDENCE
project_dir	$PROJECT_DIR
env_file	$ENV_FILE
unit_file	$UNIT_FILE
service_home	$BOT_HOME
state_root	$STATE_ROOT
manifest	$MANIFEST
transaction	$TRANSACTION
redis_config	$REDIS_CONFIG
redis_data	$REDIS_DATA_DIR
redis_unit	$REDIS_UNIT
cli	$CLI_SBIN
legacy_cli	$CLI_BIN
backup_dir	$BACKUP_DIR
backup_conf	$BACKUP_CONF
backup_identity	$BACKUP_IDENTITY
backup_tool	$BACKUP_TOOL
restore_tool	$RESTORE_TOOL
healthcheck_tool	$HEALTHCHECK_TOOL
verify_tool	$VERIFY_TOOL
rehearsal_tool	$REHEARSAL_TOOL
EOF_EVIDENCE
    if [[ "${INSTALL_STATE_SKIP_USER_LOOKUP:-0}" != 1 ]] &&
       id "$BOT_USER" >/dev/null 2>&1; then
        add_evidence "service_user=$BOT_USER"
    fi
}

unsafe_path_type() {
    local path
    for path in \
        "$PROJECT_DIR" "$ENV_FILE" "$UNIT_FILE" "$BOT_HOME" \
        "$STATE_ROOT" "$STATE_DIR" "$MANIFEST" "$TRANSACTION" \
        "$REDIS_CONFIG" "$REDIS_DATA_DIR" "$REDIS_UNIT" \
        "$CLI_SBIN" "$CLI_BIN" "$BACKUP_DIR" "$BACKUP_CONF" \
        "$BACKUP_IDENTITY" "$BACKUP_TOOL" "$RESTORE_TOOL" \
        "$HEALTHCHECK_TOOL" "$VERIFY_TOOL" "$REHEARSAL_TOOL"; do
        if [[ -L "$path" ]]; then
            INSTALL_STATE_REASON="зарезервированный путь является symlink: $path"
            INSTALL_STATE_ACTION='Проверьте symlink вручную; installer не будет следовать по нему.'
            return 0
        fi
    done

    for path in \
        "$PROJECT_DIR" "$BOT_HOME" "$STATE_ROOT" "$STATE_DIR" \
        "$REDIS_DATA_DIR" "$BACKUP_DIR"; do
        if path_exists "$path" && [[ ! -d "$path" ]]; then
            INSTALL_STATE_REASON="зарезервированный путь должен быть каталогом: $path"
            INSTALL_STATE_ACTION='Освободите путь или перенесите чужой объект.'
            return 0
        fi
    done

    for path in \
        "$ENV_FILE" "$UNIT_FILE" "$MANIFEST" "$TRANSACTION" \
        "$REDIS_CONFIG" "$REDIS_UNIT" "$CLI_SBIN" "$CLI_BIN" \
        "$BACKUP_CONF" "$BACKUP_IDENTITY" "$BACKUP_TOOL" \
        "$RESTORE_TOOL" "$HEALTHCHECK_TOOL" "$VERIFY_TOOL" \
        "$REHEARSAL_TOOL"; do
        if path_exists "$path" && [[ ! -f "$path" ]]; then
            INSTALL_STATE_REASON="зарезервированный путь должен быть обычным файлом: $path"
            INSTALL_STATE_ACTION='Проверьте объект вручную; неизвестный тип не будет изменён.'
            return 0
        fi
    done
    return 1
}

has_confirmed_residual_marker() {
    unit_looks_managed && return 0
    redis_unit_looks_managed && return 0
    project_looks_managed && return 0
    legacy_cli_looks_managed && return 0
    preserved_backup_looks_managed && return 0

    local path
    for path in \
        "$BACKUP_TOOL" "$RESTORE_TOOL" "$HEALTHCHECK_TOOL" \
        "$VERIFY_TOOL" "$REHEARSAL_TOOL"; do
        path_exists "$path" && regular_root_owned_tool "$path" && return 0
    done
    return 1
}

classify_state() {
    collect_evidence
    if unsafe_path_type; then
        INSTALL_STATE=foreign_collision
        return
    fi

    if path_exists "$TRANSACTION" && ! path_exists "$MANIFEST"; then
        if ! transaction_is_valid; then
            INSTALL_STATE=corrupted_state
            INSTALL_STATE_REASON="pre-manifest durable journal повреждён: $TRANSACTION"
            INSTALL_STATE_ACTION='Сохраните journal и выполните ручной аудит; automatic deletion запрещён.'
            return
        fi
        INSTALL_STATE=partial_install
        INSTALL_STATE_REASON="найден pre-manifest journal незавершённой operation: $TRANSACTION"
        INSTALL_STATE_ACTION='Используйте install-rollback или scripts/reset_legacy_install.sh; обычный deploy заблокирован.'
        return
    fi

    if path_exists "$MANIFEST"; then
        if ! manifest_is_valid; then
            INSTALL_STATE=corrupted_state
            INSTALL_STATE_REASON="ownership manifest повреждён: $MANIFEST"
            INSTALL_STATE_ACTION='Сохраните manifest и выполните doctor; destructive operation заблокирована.'
            return
        fi
        if path_exists "$TRANSACTION"; then
            if ! transaction_is_valid; then
                INSTALL_STATE=corrupted_state
                INSTALL_STATE_REASON="durable journal повреждён: $TRANSACTION"
                INSTALL_STATE_ACTION='Сохраните journal и выполните ручной аудит.'
                return
            fi
            INSTALL_STATE=partial_install
            INSTALL_STATE_REASON="найден journal незавершённой operation: $TRANSACTION"
            INSTALL_STATE_ACTION='Используйте install-recover или install-rollback; обычный deploy заблокирован.'
            return
        fi
        if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] &&
           unit_looks_managed && redis_unit_looks_managed; then
            INSTALL_STATE=installed_managed
            INSTALL_STATE_REASON='installation подтверждена manifest, application unit и dedicated Redis'
            INSTALL_STATE_ACTION='Можно продолжать после read-only preflight.'
            return
        fi
        INSTALL_STATE=residual_managed
        INSTALL_STATE_REASON='manifest валиден, но часть production resources отсутствует'
        INSTALL_STATE_ACTION='Используйте manifest-driven uninstall или documented recovery.'
        return
    fi

    if (( ${#INSTALL_STATE_EVIDENCE[@]} == 0 )); then
        INSTALL_STATE=clean
        INSTALL_STATE_REASON='зарезервированные resources не найдены'
        INSTALL_STATE_ACTION='Можно выполнять первичную установку.'
        return
    fi

    if path_exists "$UNIT_FILE" && ! unit_looks_managed; then
        INSTALL_STATE=foreign_collision
        INSTALL_STATE_REASON='just1kbot.service занят unit без ожидаемых markers'
        INSTALL_STATE_ACTION='Проверьте чужой unit и освободите имя.'
        return
    fi
    if path_exists "$REDIS_UNIT" && ! redis_unit_looks_managed; then
        INSTALL_STATE=foreign_collision
        INSTALL_STATE_REASON='just1kbot-redis.service занят unit без ожидаемых markers'
        INSTALL_STATE_ACTION='Проверьте чужой unit и освободите имя.'
        return
    fi
    if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] &&
       unit_looks_managed && project_looks_managed; then
        INSTALL_STATE=legacy_managed
        INSTALL_STATE_REASON='найдена полная legacy installation без manifest'
        INSTALL_STATE_ACTION='Используйте documented migration или legacy reset; новый deploy не принимает её автоматически.'
        return
    fi
    if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && project_looks_managed; then
        INSTALL_STATE=partial_install
        INSTALL_STATE_REASON='production directory и .env найдены, но installation неполная'
        INSTALL_STATE_ACTION='Обычный deploy заблокирован; используйте recovery или safe reset.'
        return
    fi
    if legacy_cli_looks_managed; then
        INSTALL_STATE=residual_managed
        INSTALL_STATE_REASON="найден legacy global CLI без ownership manifest: $CLI_SBIN"
        INSTALL_STATE_ACTION='Используйте sudo bash scripts/reset_legacy_install.sh; новый deploy не будет перезаписывать legacy CLI.'
        return
    fi
    if has_confirmed_residual_marker; then
        INSTALL_STATE=residual_managed
        INSTALL_STATE_REASON='найдены подтверждённые остатки legacy installation'
        INSTALL_STATE_ACTION='Deploy поверх остатков запрещён; используйте legacy reset или safe uninstall.'
        return
    fi

    INSTALL_STATE=foreign_collision
    INSTALL_STATE_REASON='зарезервированные resources найдены, но ownership не доказан'
    INSTALL_STATE_ACTION='Installer не выполнит rm, chown, chmod или перезапись поверх них.'
}

print_text() {
    printf 'Состояние установки: %s\n' "$INSTALL_STATE"
    printf 'Операция: %s\n' "$OPERATION"
    printf 'Причина: %s\n' "$INSTALL_STATE_REASON"
    printf 'Следующее действие: %s\n' "$INSTALL_STATE_ACTION"
    if (( ${#INSTALL_STATE_EVIDENCE[@]} > 0 )); then
        printf 'Найденные объекты:\n'
        printf '  - %s\n' "${INSTALL_STATE_EVIDENCE[@]}"
    fi
}

print_json() {
    INSTALL_STATE_VALUE=$INSTALL_STATE \
    INSTALL_STATE_OPERATION_VALUE=$OPERATION \
    INSTALL_STATE_REASON_VALUE=$INSTALL_STATE_REASON \
    INSTALL_STATE_ACTION_VALUE=$INSTALL_STATE_ACTION \
    INSTALL_STATE_EVIDENCE_VALUE=$(printf '%s\n' "${INSTALL_STATE_EVIDENCE[@]}") \
    python3 - <<'PY_JSON'
import json
import os

print(json.dumps({
    "state": os.environ["INSTALL_STATE_VALUE"],
    "operation": os.environ["INSTALL_STATE_OPERATION_VALUE"],
    "reason": os.environ["INSTALL_STATE_REASON_VALUE"],
    "action": os.environ["INSTALL_STATE_ACTION_VALUE"],
    "evidence": [
        item
        for item in os.environ.get("INSTALL_STATE_EVIDENCE_VALUE", "").splitlines()
        if item
    ],
}, ensure_ascii=False, sort_keys=True))
PY_JSON
}

state_exit_code() {
    case "$OPERATION:$INSTALL_STATE" in
        deploy:clean|deploy:installed_managed|deploy:legacy_managed) return 0 ;;
        uninstall:clean|uninstall:installed_managed|uninstall:legacy_managed|uninstall:partial_install|uninstall:residual_managed|install-rollback:partial_install) return 0 ;;
        *:foreign_collision) return 20 ;;
        *:corrupted_state) return 21 ;;
        *:residual_managed) return 22 ;;
        *:partial_install) return 23 ;;
        *) return 24 ;;
    esac
}

main() {
    while (( $# > 0 )); do
        case "$1" in
            --json) OUTPUT_MODE=json ;;
            --require-safe) REQUIRE_SAFE=1 ;;
            --operation)
                shift
                (( $# > 0 )) || return 2
                OPERATION=$1
                [[ "$OPERATION" == deploy || "$OPERATION" == uninstall ]] || return 2
                ;;
            -h|--help)
                printf 'Usage: inspect_install_state.sh [--json] [--operation deploy|uninstall] [--require-safe]\n'
                return 0
                ;;
            *)
                printf 'Неизвестный argument: %s\n' "$1" >&2
                return 2
                ;;
        esac
        shift
    done

    classify_state
    [[ "$OUTPUT_MODE" == json ]] && print_json || print_text
    (( REQUIRE_SAFE == 0 )) || state_exit_code
}

main "$@"
