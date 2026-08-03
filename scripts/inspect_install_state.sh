#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

PROJECT_DIR=/opt/just1kbot
ENV_FILE=$PROJECT_DIR/.env
UNIT_FILE=/etc/systemd/system/just1kbot.service
BOT_USER=just1kbot
CLI_SBIN=/usr/local/sbin/just1kbot
CLI_BIN=/usr/local/bin/just1kbot
STATE_DIR=/var/lib/just1kbot/install-state
MANIFEST=$STATE_DIR/manifest.json
TRANSACTION=$STATE_DIR/transaction.json
INSTALL_STATE=unknown
INSTALL_STATE_REASON=
INSTALL_STATE_ACTION=
INSTALL_STATE_EVIDENCE=()
OUTPUT_MODE=text
REQUIRE_SAFE=0

add_evidence() {
    INSTALL_STATE_EVIDENCE+=("$1")
}

path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

path_is_symlink() {
    [[ -L "$1" ]]
}

unit_looks_managed() {
    [[ -f "$UNIT_FILE" && ! -L "$UNIT_FILE" ]] || return 1
    grep -Eq '^Description=.*Just1kBot' "$UNIT_FILE" || return 1
    grep -Eq '^ExecStart=/opt/just1kbot/' "$UNIT_FILE" || return 1
}

manifest_is_valid() {
    [[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || return 1
    MANIFEST_PATH=$MANIFEST python3 - <<'PY' >/dev/null
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
if data.get("project_dir") != "/opt/just1kbot":
    raise SystemExit(1)
resources = data.get("managed_resources")
if not isinstance(resources, list) or not all(isinstance(item, str) for item in resources):
    raise SystemExit(1)
PY
}

collect_evidence() {
    path_exists "$PROJECT_DIR" && add_evidence "project_dir=$PROJECT_DIR" || true
    path_exists "$ENV_FILE" && add_evidence "env_file=$ENV_FILE" || true
    path_exists "$UNIT_FILE" && add_evidence "unit_file=$UNIT_FILE" || true
    id "$BOT_USER" >/dev/null 2>&1 && add_evidence "service_user=$BOT_USER" || true
    path_exists "$CLI_SBIN" && add_evidence "cli=$CLI_SBIN" || true
    path_exists "$CLI_BIN" && add_evidence "legacy_cli=$CLI_BIN" || true
    path_exists "$MANIFEST" && add_evidence "manifest=$MANIFEST" || true
    path_exists "$TRANSACTION" && add_evidence "transaction=$TRANSACTION" || true
}

has_unsafe_type() {
    local path
    for path in "$PROJECT_DIR" "$ENV_FILE" "$UNIT_FILE" "$CLI_SBIN" "$CLI_BIN" "$STATE_DIR" "$MANIFEST" "$TRANSACTION"; do
        if path_is_symlink "$path"; then
            INSTALL_STATE_REASON="зарезервированный путь является symlink: $path"
            INSTALL_STATE_ACTION="Удалите или переименуйте symlink вручную только после проверки его назначения. Installer не будет следовать по нему."
            return 0
        fi
    done

    if path_exists "$PROJECT_DIR" && [[ ! -d "$PROJECT_DIR" ]]; then
        INSTALL_STATE_REASON="зарезервированный путь не является каталогом: $PROJECT_DIR"
        INSTALL_STATE_ACTION="Освободите /opt/just1kbot или перенесите чужой объект."
        return 0
    fi
    if path_exists "$ENV_FILE" && [[ ! -f "$ENV_FILE" ]]; then
        INSTALL_STATE_REASON="production .env существует, но не является обычным файлом: $ENV_FILE"
        INSTALL_STATE_ACTION="Исправьте тип production .env; автоматическое изменение запрещено."
        return 0
    fi
    if path_exists "$UNIT_FILE" && [[ ! -f "$UNIT_FILE" ]]; then
        INSTALL_STATE_REASON="systemd unit имеет небезопасный тип: $UNIT_FILE"
        INSTALL_STATE_ACTION="Освободите имя just1kbot.service после проверки существующего объекта."
        return 0
    fi
    return 1
}

classify_state() {
    collect_evidence

    if has_unsafe_type; then
        INSTALL_STATE=foreign_collision
        return 0
    fi

    if path_exists "$MANIFEST"; then
        if ! manifest_is_valid; then
            INSTALL_STATE=corrupted_state
            INSTALL_STATE_REASON="ownership manifest повреждён или не соответствует поддерживаемой schema: $MANIFEST"
            INSTALL_STATE_ACTION="Не удаляйте файлы вручную. Сохраните manifest и выполните doctor/support bundle для восстановления ownership."
            return 0
        fi

        if path_exists "$TRANSACTION"; then
            INSTALL_STATE=partial_install
            INSTALL_STATE_REASON="найден durable journal незавершённой операции: $TRANSACTION"
            INSTALL_STATE_ACTION="Откройте меню recovery и завершите либо откатите операцию."
            return 0
        fi

        if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && unit_looks_managed; then
            INSTALL_STATE=installed_managed
            INSTALL_STATE_REASON="установка подтверждена ownership manifest и ожидаемыми production-ресурсами"
            INSTALL_STATE_ACTION="Можно продолжать update/deploy после обычного preflight."
            return 0
        fi

        INSTALL_STATE=residual_managed
        INSTALL_STATE_REASON="manifest существует, но часть перечисляемых production-ресурсов отсутствует"
        INSTALL_STATE_ACTION="Запустите doctor/repair. Автоматическая установка поверх неоднозначных остатков запрещена."
        return 0
    fi

    if (( ${#INSTALL_STATE_EVIDENCE[@]} == 0 )); then
        INSTALL_STATE=clean
        INSTALL_STATE_REASON="зарезервированные ресурсы Just1kBot не найдены"
        INSTALL_STATE_ACTION="Можно выполнять первичную установку."
        return 0
    fi

    if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && unit_looks_managed; then
        INSTALL_STATE=legacy_managed
        INSTALL_STATE_REASON="найдена полная установка старого формата без ownership manifest"
        INSTALL_STATE_ACTION="Можно выполнить update; после внедрения manifest установка будет принята под управление отдельной миграцией."
        return 0
    fi

    if [[ -d "$PROJECT_DIR" && -f "$ENV_FILE" ]] && ! path_exists "$UNIT_FILE"; then
        INSTALL_STATE=partial_install
        INSTALL_STATE_REASON="найдены production directory и .env, но основной systemd unit отсутствует"
        INSTALL_STATE_ACTION="Существующий восстановительный preflight может проверить БД и восстановить только подтверждённые operational-файлы."
        return 0
    fi

    if path_exists "$UNIT_FILE" && ! unit_looks_managed; then
        INSTALL_STATE=foreign_collision
        INSTALL_STATE_REASON="имя just1kbot.service занято unit-файлом без ожидаемых Just1kBot markers"
        INSTALL_STATE_ACTION="Проверьте unit вручную и переименуйте чужой сервис либо выберите другие имена ресурсов."
        return 0
    fi

    INSTALL_STATE=foreign_collision
    INSTALL_STATE_REASON="найдены отдельные зарезервированные ресурсы, но их принадлежность Just1kBot не доказана"
    INSTALL_STATE_ACTION="Проверьте перечисленные объекты. Installer не будет выполнять rm, chown, chmod или перезапись поверх них."
}

print_text() {
    printf 'Состояние установки: %s\n' "$INSTALL_STATE"
    printf 'Причина: %s\n' "$INSTALL_STATE_REASON"
    printf 'Следующее действие: %s\n' "$INSTALL_STATE_ACTION"
    if (( ${#INSTALL_STATE_EVIDENCE[@]} > 0 )); then
        printf 'Найденные объекты:\n'
        printf '  - %s\n' "${INSTALL_STATE_EVIDENCE[@]}"
    fi
}

print_json() {
    INSTALL_STATE_VALUE=$INSTALL_STATE \
    INSTALL_STATE_REASON_VALUE=$INSTALL_STATE_REASON \
    INSTALL_STATE_ACTION_VALUE=$INSTALL_STATE_ACTION \
    INSTALL_STATE_EVIDENCE_VALUE=$(printf '%s\n' "${INSTALL_STATE_EVIDENCE[@]}") \
    python3 - <<'PY'
import json
import os

evidence = [line for line in os.environ.get("INSTALL_STATE_EVIDENCE_VALUE", "").splitlines() if line]
print(json.dumps({
    "state": os.environ["INSTALL_STATE_VALUE"],
    "reason": os.environ["INSTALL_STATE_REASON_VALUE"],
    "action": os.environ["INSTALL_STATE_ACTION_VALUE"],
    "evidence": evidence,
}, ensure_ascii=False, sort_keys=True))
PY
}

state_exit_code() {
    case "$INSTALL_STATE" in
        clean|installed_managed|legacy_managed|partial_install) return 0 ;;
        foreign_collision) return 20 ;;
        corrupted_state) return 21 ;;
        residual_managed) return 22 ;;
        *) return 23 ;;
    esac
}

main() {
    local argument
    for argument in "$@"; do
        case "$argument" in
            --json) OUTPUT_MODE=json ;;
            --require-safe) REQUIRE_SAFE=1 ;;
            -h|--help)
                printf 'Использование: inspect_install_state.sh [--json] [--require-safe]\n'
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
