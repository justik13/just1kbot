#!/bin/bash
# Shared, secret-safe diagnostics for installer control-plane scripts.

_INSTALLER_DIAGNOSTICS_ENABLED=0
_INSTALLER_FAILURE_PRINTED=0
INSTALLER_OPERATION=${INSTALLER_OPERATION:-unknown}
INSTALLER_STEP=${INSTALLER_STEP:-initialization}
INSTALLER_HINT=${INSTALLER_HINT:-}
INSTALLER_LOG_FILE=${INSTALLER_LOG_FILE:-/var/log/just1kbot-deploy.log}

installer_set_operation() {
    INSTALLER_OPERATION=${1:-unknown}
}

installer_set_step() {
    INSTALLER_STEP=${1:-unknown}
    INSTALLER_HINT=${2:-}
}

installer_set_log_file() {
    INSTALLER_LOG_FILE=${1:-}
}

installer_control_plane_command() {
    local project_root
    if [[ -f /opt/just1kbot/deploy.sh && ! -L /opt/just1kbot/deploy.sh ]]; then
        printf 'sudo bash /opt/just1kbot/deploy.sh'
        return 0
    fi

    project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
    if [[ -f "$project_root/deploy.sh" && ! -L "$project_root/deploy.sh" ]]; then
        printf 'sudo bash %q/deploy.sh' "$project_root"
        return 0
    fi

    printf 'sudo bash ./deploy.sh'
}

installer_print_diagnostic_footer() {
    local action=${1:-}
    local control_plane_command
    control_plane_command=$(installer_control_plane_command)

    printf '\nЧто сделать:\n' >&2
    if [[ -n "$action" ]]; then
        printf '  %s\n' "$action" >&2
    else
        printf '  1. Исправьте указанную выше причину.\n' >&2
        printf '  2. Повторите ту же команду.\n' >&2
    fi

    printf '\nКоманды диагностики:\n' >&2
    printf '  %s state\n' "$control_plane_command" >&2
    printf '  %s doctor\n' "$control_plane_command" >&2
    if [[ -n "$INSTALLER_LOG_FILE" ]]; then
        printf '  sudo tail -n 200 %q\n' "$INSTALLER_LOG_FILE" >&2
    fi
}

installer_fail() {
    local code=${1:-INSTALLER_ERROR}
    local summary=${2:-'операция не выполнена'}
    local details=${3:-}
    local action=${4:-}

    _INSTALLER_FAILURE_PRINTED=1
    printf '\n============================================================\n' >&2
    printf 'ОШИБКА JUST1KBOT [%s]\n' "$code" >&2
    printf 'Операция: %s\n' "$INSTALLER_OPERATION" >&2
    printf 'Этап: %s\n' "$INSTALLER_STEP" >&2
    printf 'Проблема: %s\n' "$summary" >&2
    if [[ -n "$details" ]]; then
        printf 'Причина: %s\n' "$details" >&2
    fi
    if [[ -n "$INSTALLER_HINT" ]]; then
        printf 'Контекст: %s\n' "$INSTALLER_HINT" >&2
    fi
    installer_print_diagnostic_footer "$action"
    printf '============================================================\n' >&2
    exit 1
}

installer_unhandled_error() {
    local rc=${1:-1}
    local line=${2:-unknown}
    local source=${3:-unknown}

    (( _INSTALLER_FAILURE_PRINTED == 0 )) || return "$rc"
    _INSTALLER_FAILURE_PRINTED=1

    printf '\n============================================================\n' >&2
    printf 'ОШИБКА JUST1KBOT [UNHANDLED_COMMAND_FAILURE]\n' >&2
    printf 'Операция: %s\n' "$INSTALLER_OPERATION" >&2
    printf 'Этап: %s\n' "$INSTALLER_STEP" >&2
    printf 'Проблема: команда завершилась с кодом %s.\n' "$rc" >&2
    printf 'Причина: подробное сообщение команды находится непосредственно выше.\n' >&2
    printf 'Место: %s:%s\n' "$source" "$line" >&2
    if [[ -n "$INSTALLER_HINT" ]]; then
        printf 'Контекст: %s\n' "$INSTALLER_HINT" >&2
    fi
    installer_print_diagnostic_footer 'Найдите первое сообщение «ОШИБКА» выше, исправьте его причину и повторите операцию.'
    printf '============================================================\n' >&2
    return "$rc"
}

installer_interrupted() {
    local signal=${1:-INT}
    (( _INSTALLER_FAILURE_PRINTED == 0 )) || exit 130
    _INSTALLER_FAILURE_PRINTED=1
    printf '\n============================================================\n' >&2
    printf 'ОПЕРАЦИЯ ПРЕРВАНА [%s]\n' "$signal" >&2
    printf 'Операция: %s\n' "$INSTALLER_OPERATION" >&2
    printf 'Этап: %s\n' "$INSTALLER_STEP" >&2
    printf 'Состояние: операция остановлена пользователем или системой.\n' >&2
    installer_print_diagnostic_footer 'Запустите state и doctor. Если обнаружена незавершённая транзакция, выполните предложенный recovery или rollback.'
    printf '============================================================\n' >&2
    exit 130
}

installer_enable_diagnostics() {
    (( _INSTALLER_DIAGNOSTICS_ENABLED == 0 )) || return 0
    _INSTALLER_DIAGNOSTICS_ENABLED=1
    trap 'rc=$?; installer_unhandled_error "$rc" "$LINENO" "${BASH_SOURCE[0]}"; exit "$rc"' ERR
    trap 'installer_interrupted INT' INT
    trap 'installer_interrupted TERM' TERM
}
