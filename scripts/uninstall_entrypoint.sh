#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BOT_USER=just1kbot
BOT_HOME=/home/just1kbot
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
UNINSTALL=$SCRIPT_DIR/uninstall.sh

fail() {
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

main() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'запустите от root'
    [[ -f "$UNINSTALL" && ! -L "$UNINSTALL" ]] || fail 'основной uninstall script отсутствует или небезопасен'

    bash "$UNINSTALL" "$@"
    safe_remove_orphan_home
}

main "$@"
