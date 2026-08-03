#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
UNINSTALL=$SCRIPT_DIR/uninstall.sh
VERIFY_UNINSTALL=$SCRIPT_DIR/verify_uninstall_state.sh
INSPECT_STATE=$SCRIPT_DIR/inspect_install_state.sh
PREFLIGHT_RESOURCES=$SCRIPT_DIR/preflight_uninstall_resources.sh
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

    [[ ! -e "$BOT_HOME" && ! -L "$BOT_HOME" ]] || fail 'service home остался после uninstall'
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
    [[ -f "$INSPECT_STATE" && ! -L "$INSPECT_STATE" ]] || fail 'install-state inspector отсутствует или небезопасен'
    [[ -f "$PREFLIGHT_RESOURCES" && ! -L "$PREFLIGHT_RESOURCES" ]] || fail 'uninstall resource preflight отсутствует или небезопасен'
    [[ -f "$DIAGNOSTICS" && ! -L "$DIAGNOSTICS" ]] || fail 'installer diagnostics отсутствует или небезопасен'

    # shellcheck source=scripts/lib/installer_diagnostics.sh
    source "$DIAGNOSTICS"
    # Load the read-only verifier before uninstall removes /opt/just1kbot.
    VERIFY_UNINSTALL_SOURCE_ONLY=1
    export VERIFY_UNINSTALL_SOURCE_ONLY
    # shellcheck source=scripts/verify_uninstall_state.sh
    source "$VERIFY_UNINSTALL"
    unset VERIFY_UNINSTALL_SOURCE_ONLY
    declare -F verify_uninstall_main >/dev/null 2>&1 || fail 'post-uninstall verifier function не загружена'

    installer_set_operation uninstall
    installer_set_log_file /var/log/just1kbot-deploy.log
    installer_enable_diagnostics

    installer_set_step 'Проверка ownership перед удалением' 'Foreign collision, symlink или повреждённый manifest блокируют destructive operation.'
    bash "$INSPECT_STATE" --require-safe

    installer_set_step 'Проверка каждого удаляемого ресурса' 'До stop/disable/rm проверяются все systemd units, operational tools и основной Nginx site.'
    bash "$PREFLIGHT_RESOURCES"

    installer_set_step 'Основное удаление' 'После destructive этапа обязательно выполняется независимая проверка остатков.'
    select_verify_mode "${1:-}"
    bash "$UNINSTALL" "$@"

    installer_set_step 'Очистка service home' 'Домашний каталог удаляется только после подтверждённого удаления service user.'
    safe_remove_orphan_home

    installer_set_step 'Проверка отсутствия остатков' 'Успех разрешён только если filesystem, units, service user, processes и purge-data PostgreSQL state очищены.'
    verify_uninstall_main "$VERIFY_MODE"
}

main "$@"
