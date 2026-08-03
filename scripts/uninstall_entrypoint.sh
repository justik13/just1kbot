#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
UNINSTALL=$SCRIPT_DIR/uninstall.sh
VERIFY_UNINSTALL=$SCRIPT_DIR/verify_uninstall_state.sh
DIAGNOSTICS=$SCRIPT_DIR/lib/installer_diagnostics.sh
VERIFY_MODE=--auto

fail() {
    if declare -F installer_fail >/dev/null 2>&1; then
        installer_fail UNINSTALL_ENTRYPOINT_ERROR "$1" "$1" 'Исправьте указанную причину и повторите uninstall.'
    fi
    printf 'ОШИБКА uninstall entrypoint: %s\n' "$*" >&2
    exit 1
}

safe_remove_orphan_home() {
    id "$BOT_USER" >/dev/null 2>&1 && return 0

    [[ "$BOT_HOME" == /home/just1kbot && "$BOT_HOME" != *'..'* ]] || fail 'unsafe BOT_HOME'
    [[ ! -L "$BOT_HOME" ]] || fail 'BOT_HOME является symlink'

    if [[ -e "$BOT_HOME" ]]; then
        [[ -d "$BOT_HOME" ]] || fail 'BOT_HOME существует и не является directory'
        [[ "$(realpath -m -- "$BOT_HOME")" == /home/just1kbot ]] || fail 'canonical BOT_HOME mismatch'
        rm -rf --one-file-system -- "$BOT_HOME"
    fi

    [[ ! -e "$BOT_HOME" && ! -L "$BOT_HOME" ]] || fail 'service home остался после purge'
}

select_verify_mode() {
    case ${1:-} in
        --keep-data) VERIFY_MODE=--keep-data ;;
        --purge-data) VERIFY_MODE=--purge-data ;;
        *) VERIFY_MODE=--auto ;;
    esac
}

main() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'запустите от root'
    [[ -f "$UNINSTALL" && ! -L "$UNINSTALL" ]] || fail 'основной uninstall script отсутствует или небезопасен'
    [[ -f "$VERIFY_UNINSTALL" && ! -L "$VERIFY_UNINSTALL" ]] || fail 'post-uninstall verifier отсутствует или небезопасен'
    [[ -f "$DIAGNOSTICS" && ! -L "$DIAGNOSTICS" ]] || fail 'installer diagnostics отсутствует или небезопасен'

    # shellcheck source=scripts/lib/installer_diagnostics.sh
    source "$DIAGNOSTICS"
    installer_set_operation uninstall
    installer_set_step 'Основное удаление' 'После destructive этапа обязательно выполняется независимая проверка остатков.'
    installer_set_log_file /var/log/just1kbot-deploy.log
    installer_enable_diagnostics

    select_verify_mode "${1:-}"
    bash "$UNINSTALL" "$@"

    installer_set_step 'Очистка service home' 'Домашний каталог удаляется только после подтверждённого удаления service user.'
    safe_remove_orphan_home

    installer_set_step 'Проверка отсутствия остатков' 'Успех разрешён только если filesystem, units, processes и purge-data PostgreSQL state очищены.'
    bash "$VERIFY_UNINSTALL" "$VERIFY_MODE"
}

main "$@"
