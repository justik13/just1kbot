#!/bin/bash
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
BACKUP_DIR=${BACKUP_DIR:-/root/backups/just1kbot}
BACKUP_CONF=${BACKUP_CONF:-/etc/just1kbot-backup.conf}
BACKUP_IDENTITY=${BACKUP_IDENTITY:-/root/.config/just1kbot/backup.agekey}
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

add_evidence() {
    INSTALL_STATE_EVIDENCE+=("$1")
}

path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

path_is_symlink() {
    [[ -L "$1" ]]
}

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
    grep -Fq "ExecStart=$PROJECT_DIR/" "$UNIT_FILE" || return 1
}

project_looks_managed() {
    [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" ]] || return 1
    [[ -f "$PROJECT_DIR/deploy.sh" && ! -L "$PROJECT_DIR/deploy.sh" ]] || return 1
    [[ -f "$PROJECT_DIR/scripts/deploy.sh" && ! -L "$PROJECT_DIR/scripts/deploy.sh" ]] || return 1
    grep -Fq 'Just1kBot' "$PROJECT_DIR/deploy.sh"
}

preserved_backup_looks_managed() {
    [[ -f "$BACKUP_CONF" && ! -L "$BACKUP_CONF" ]] || return 1
    grep -Eq '^BACKUP_AGE_RECIPIENT=age1[0-9a-z]+' "$BACKUP_CONF" || return 1
    [[ -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] || return 1
    find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -print -quit 2>/dev/null |
        grep -q .
}

manifest_is_valid() {
    [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || return 1
    MANIFEST_PATH=$MANIFEST EXPECTED_PROJECT_DIR=$PROJECT_DIR python3 - <<'PY' >/dev/null
import json
import os
import re
from pathlib import Path

path = Path(os.environ["MANIFEST_PATH"])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("schema_version") != 1:
    raise SystemExit(1)
installation_id = data.get("installation_id")
if not isinstance(installation_id, str) or not re.fullmatch(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    installation_id,
    re.IGNORECASE,
):
    raise SystemExit(1)
if data.get("project_dir") != os.environ["EXPECTED_PROJECT_DIR"]:
    raise SystemExit(1)
resources = data.get("managed_resources")
if not isinstance(resources, list) or not all(isinstance(item, str) for item in resources):
    raise SystemExit(1)
if len(resources) != len(set(resources)):
    raise SystemExit(1)
PY
}

collect_evidence() {
    path_exists "$PROJECT_DIR" && add_evidence "project_dir=$PROJECT_DIR" || true
    path_exists "$ENV_FILE" && add_evidence "env_file=$ENV_FILE" || true
    path_exists "$UNIT_FILE" && add_evidence "unit_file=$UNIT_FILE" || true
    path_exists "$BOT_HOME" && add_evidence "service_home=$BOT_HOME" || true
    path_exists "$STATE_ROOT" && add_evidence "state_root=$STATE_ROOT" || true
    path_exists "$STATE_DIR" && add_evidence "install_state_dir=$STATE_DIR" || true
    path_exists "$MANIFEST" && add_evidence "manifest=$MANIFEST" || true
    path_exists "$TRANSACTION" && add_evidence "transaction=$TRANSACTION" || true
    path_exists "$CLI_SBIN" && add_evidence "cli=$CLI_SBIN" || true
    path_exists "$CLI_BIN" && add_evidence "legacy_cli=$CLI_BIN" || true
    path_exists "$BACKUP_DIR" && add_evidence "backup_dir=$BACKUP_DIR" || true
    path_exists "$BACKUP_CONF" && add_evidence "backup_conf=$BACKUP_CONF" || true
    path_exists "$BACKUP_IDENTITY" && add_evidence "backup_identity=$BACKUP_IDENTITY" || true
    path_exists "$BACKUP_TOOL" && add_evidence "backup_tool=$BACKUP_TOOL" || true
    path_exists "$RESTORE_TOOL" && add_evidence "restore_tool=$RESTORE_TOOL" || true
    path_exists "$HEALTHCHECK_TOOL" && add_evidence "healthcheck_tool=$HEALTHCHECK_TOOL" || true
    path_exists "$VERIFY_TOOL" && add_evidence "verify_tool=$VERIFY_TOOL" || true
    path_exists "$REHEARSAL_TOOL" && add_evidence "rehearsal_tool=$REHEARSAL_TOOL" || true
    if [[ "${INSTALL_STATE_SKIP_USER_LOOKUP:-0}" != 1 ]] && id "$BOT_USER" >/dev/null 2>&1; then
        add_evidence "service_user=$BOT_USER"
    fi
}

unsafe_path_type() {
    local path
    for path in \
        "$PROJECT_DIR" "$ENV_FILE" "$UNIT_FILE" "$BOT_HOME" \
        "$STATE_ROOT" "$STATE_DIR" "$MANIFEST" "$TRANSACTION" \
        "$CLI_SBIN" "$CLI_BIN" "$BACKUP_DIR" "$BACKUP_CONF" \
        "$BACKUP_IDENTITY" "$BACKUP_TOOL" "$RESTORE_TOOL" \
        "$HEALTHCHECK_TOOL" "$VERIFY_TOOL" "$REHEARSAL_TOOL"; do
        if path_is_symlink "$path"; then
            INSTALL_STATE_REASON="зарезервированный путь является symlink: $path"
            INSTALL_STATE_ACTION="Проверьте symlink вручную. Installer не будет следовать по нему или автоматически удалять его."
            return 0
        fi
    done

    for path in "$PROJECT_DIR" "$BOT_HOME" "$STATE_ROOT" "$STATE_DIR" "$BACKUP_DIR"; do
        if path_exists "$path" && [[ ! -d "$path" ]]; then
            INSTALL_STATE_REASON="зарезервированный путь должен быть каталогом: $path"
            INSTALL_STATE_ACTION="Освободите путь или перенесите чужой объект; автоматическая перезапись запрещена."
            return 0
        fi
    done

    for path in \
        "$ENV_FILE" "$UNIT_FILE" "$MANIFEST" "$TRANSACTION" \
        "$CLI_SBIN" "$CLI_BIN" "$BACKUP_CONF" "$BACKUP_IDENTITY" \
        "$BACKUP_TOOL" "$RESTORE_TOOL" "$HEALTHCHECK_TOOL" \
        "$VERIFY_TOOL" "$REHEARSAL_TOOL"; do
        if path_exists "$path" && [[ ! -f "$path" ]]; then
            INSTALL_STATE_REASON="зарезервированный путь должен быть обычным файлом: $path"
            INSTALL_STATE_ACTION="Проверьте объект вручную; installer не будет менять объект неизвестного типа."
            return 0
        fi
    done
    return 1
}

has_confirmed_residual_marker() {
    unit_looks_managed && return 0
    project_looks_managed && return 0
    preserved_backup_looks_managed && return 0

    local path
    for path in "$BACKUP_TOOL" "$RESTORE_TOOL" "$HEALTHCHECK_TOOL" "$VERIFY_TOOL" "$REHEARSAL_TOOL"; do
        if path_exists "$path" && regular_root_owned_tool "$path"; then
            return 0
        fi
    done
    return 1
}

classify_state() {
    collect_evidence

    if unsafe_path_type; then
        INSTALL_STATE=foreign_collision
        return 0
    fi

    if path_exists "$MANIFEST"; then
        if ! manifest_is_valid; then
            INSTALL_STATE=corrupted_state
            INSTALL_STATE_REASON="ownership manifest повреждён или не соответствует schema: $MANIFEST"
            INSTALL_STATE_ACTION="Сохраните manifest и выполните doctor/support bundle. Destructive operation заблокирована."
            return 0
        fi

        if path_exists "$TRANSACTION"; then
            INSTALL_STATE=partial_install
            INSTALL_STATE_REASON="найден durable journal незавершённой операции: $TRANSACTION"
            INSTALL_STATE_ACTION="Завершите или откатите записанную транзакцию перед обычным deploy."
            return 0
        fi

        if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && unit_looks_managed; then
            INSTALL_STATE=installed_managed
            INSTALL_STATE_REASON="установка подтверждена ownership manifest и основными production-ресурсами"
            INSTALL_STATE_ACTION="Можно продолжать операцию после обычного preflight."
            return 0
        fi

        INSTALL_STATE=residual_managed
        INSTALL_STATE_REASON="manifest валиден, но часть production-ресурсов отсутствует"
        INSTALL_STATE_ACTION="Для удаления используйте uninstall recovery; deploy поверх остатков заблокирован."
        return 0
    fi

    if (( ${#INSTALL_STATE_EVIDENCE[@]} == 0 )); then
        INSTALL_STATE=clean
        INSTALL_STATE_REASON="зарезервированные ресурсы Just1kBot не найдены"
        INSTALL_STATE_ACTION="Можно выполнять первичную установку."
        return 0
    fi

    if path_exists "$UNIT_FILE" && ! unit_looks_managed; then
        INSTALL_STATE=foreign_collision
        INSTALL_STATE_REASON="имя just1kbot.service занято unit-файлом без ожидаемых markers"
        INSTALL_STATE_ACTION="Проверьте чужой unit и освободите зарезервированное имя."
        return 0
    fi

    if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && unit_looks_managed && project_looks_managed; then
        INSTALL_STATE=legacy_managed
        INSTALL_STATE_REASON="найдена полная установка старого формата без ownership manifest"
        INSTALL_STATE_ACTION="Разрешён update или uninstall; manifest должен быть создан отдельной migration."
        return 0
    fi

    if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && project_looks_managed; then
        INSTALL_STATE=partial_install
        INSTALL_STATE_REASON="найдены подтверждённые production directory и .env, но полная установка не собрана"
        INSTALL_STATE_ACTION="Запустите восстановительный preflight либо uninstall recovery."
        return 0
    fi

    if has_confirmed_residual_marker; then
        INSTALL_STATE=residual_managed
        INSTALL_STATE_REASON="найдены подтверждённые остатки старой установки Just1kBot"
        INSTALL_STATE_ACTION="Deploy поверх остатков запрещён; используйте repair или безопасный uninstall."
        return 0
    fi

    INSTALL_STATE=foreign_collision
    INSTALL_STATE_REASON="найдены зарезервированные ресурсы, но принадлежность Just1kBot не доказана"
    INSTALL_STATE_ACTION="Проверьте перечисленные объекты. Installer не выполнит rm, chown, chmod или перезапись поверх них."
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
    python3 - <<'PY'
import json
import os

evidence = [line for line in os.environ.get("INSTALL_STATE_EVIDENCE_VALUE", "").splitlines() if line]
print(json.dumps({
    "state": os.environ["INSTALL_STATE_VALUE"],
    "operation": os.environ["INSTALL_STATE_OPERATION_VALUE"],
    "reason": os.environ["INSTALL_STATE_REASON_VALUE"],
    "action": os.environ["INSTALL_STATE_ACTION_VALUE"],
    "evidence": evidence,
}, ensure_ascii=False, sort_keys=True))
PY
}

state_exit_code() {
    case "$OPERATION:$INSTALL_STATE" in
        deploy:clean|deploy:installed_managed|deploy:legacy_managed|deploy:partial_install)
            return 0
            ;;
        uninstall:clean|uninstall:installed_managed|uninstall:legacy_managed|uninstall:partial_install|uninstall:residual_managed)
            return 0
            ;;
        *:foreign_collision) return 20 ;;
        *:corrupted_state) return 21 ;;
        *:residual_managed) return 22 ;;
        *) return 23 ;;
    esac
}

main() {
    local argument
    while (( $# > 0 )); do
        argument=$1
        shift
        case "$argument" in
            --json) OUTPUT_MODE=json ;;
            --require-safe) REQUIRE_SAFE=1 ;;
            --operation)
                (( $# > 0 )) || {
                    printf '%s\n' '--operation требует значение deploy или uninstall' >&2
                    return 2
                }
                OPERATION=$1
                shift
                [[ "$OPERATION" == deploy || "$OPERATION" == uninstall ]] || {
                    printf 'Неизвестная operation: %s\n' "$OPERATION" >&2
                    return 2
                }
                ;;
            -h|--help)
                printf 'Использование: inspect_install_state.sh [--json] [--operation deploy|uninstall] [--require-safe]\n'
                return 0
                ;;
            *)
                printf 'Неизвестный аргумент inspect_install_state: %s\n' "$argument" >&2
                return 2
                ;;
        esac
    done

    classify_state
    if [[ "$OUTPUT_MODE" == json ]]; then
        print_json
    else
        print_text
    fi

    if (( REQUIRE_SAFE == 1 )); then
        state_exit_code
    fi
}

if [[ "${INSTALL_STATE_SOURCE_ONLY:-0}" == 1 ]]; then
    return 0 2>/dev/null || exit 0
fi

main "$@"
