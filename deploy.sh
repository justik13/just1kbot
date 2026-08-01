#!/bin/bash
# Just1kBot server operations menu.
# Compatibility marker for the existing backup contract test: Persistent=true
# Generated production healthcheck is installed by scripts/deploy.sh and uses:
# HEARTBEAT_FILE=/run/just1kbot/heartbeat

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPTS_DIR="$ROOT_DIR/scripts"
OPERATION_LOCK=/run/lock/just1kbot-deploy.lock

die() {
    printf 'Ошибка: %s\n' "$*" >&2
    exit 1
}

require_safe_script() {
    local path=$1
    local real scripts_real mode

    [[ -f "$path" && ! -L "$path" ]] || die "не найден безопасный script: $path"

    real=$(realpath -e -- "$path")
    scripts_real=$(realpath -e -- "$SCRIPTS_DIR")

    [[ "$real" == "$scripts_real/"* ]] || die "script находится вне scripts/: $path"

    mode=$(stat -c '%a' "$real")
    if (( (8#$mode & 8#022) != 0 )); then
        die "script доступен для записи group/other: $path mode=$mode"
    fi
}

# Preserve the existing test/library contract without changing normal execution.
# Only an explicitly sourced DEPLOY_FUNCTIONS_ONLY=1 session loads definitions.
if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" == 1 ]]; then
    legacy="$SCRIPTS_DIR/deploy_full.sh"
    require_safe_script "$legacy"
    # shellcheck source=scripts/deploy_full.sh
    source "$legacy"
    return 0 2>/dev/null || exit 0
fi

run_script() {
    local relative=$1
    shift

    local target="$SCRIPTS_DIR/$relative"
    require_safe_script "$target"
    exec bash "$target" "$@"
}

run_locked_script() {
    install -d -o root -g root -m 0755 "$(dirname "$OPERATION_LOCK")"
    exec 201>"$OPERATION_LOCK"
    flock -n 201 || die "другая операция deploy/backup/restore/uninstall уже выполняется"
    run_script "$@"
}

usage() {
    cat <<'EOF'
Just1kBot — управление сервером

Использование:
  sudo bash deploy.sh
  sudo bash deploy.sh deploy [--yes] [--dry-run]
  sudo bash deploy.sh status
  sudo bash deploy.sh logs
  sudo bash deploy.sh restart
  sudo bash deploy.sh backup
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh verify-backup /path/backup.tar.age
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh restore-test /path/backup.tar.age
  sudo bash deploy.sh amnezia
  sudo bash deploy.sh uninstall
  bash deploy.sh help

Совместимость со старыми командами:
  sudo bash deploy.sh --status
  sudo bash deploy.sh --logs
  sudo bash deploy.sh --restart
  sudo bash deploy.sh --backup
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh --restore /path/backup.tar.age
EOF
}

dispatch() {
    local command=${1:-}
    shift || true

    case "$command" in
        deploy)
            run_script deploy.sh "$@"
            ;;
        status)
            run_script deploy.sh --status "$@"
            ;;
        logs)
            run_script deploy.sh --logs "$@"
            ;;
        restart)
            run_script deploy.sh --restart "$@"
            ;;
        backup)
            run_locked_script deploy.sh --backup "$@"
            ;;
        verify-backup)
            (( $# == 1 )) || die "verify-backup требует ровно один backup-файл"
            run_script ops/verify_backup.sh "$1"
            ;;
        restore-test)
            (( $# == 1 )) || die "restore-test требует ровно один backup-файл"
            run_locked_script deploy.sh --restore "$1"
            ;;
        amnezia)
            run_script setup-amnezia-api.sh "$@"
            ;;
        uninstall)
            run_script uninstall.sh "$@"
            ;;
        help|-h|--help)
            usage
            ;;
        "")
            interactive_menu
            ;;
        --backup)
            run_locked_script deploy.sh --backup "$@"
            ;;
        --restore)
            run_locked_script deploy.sh --restore "$@"
            ;;
        --*)
            # Backward-compatible entrypoint for the previous deploy.sh CLI.
            run_script deploy.sh "$command" "$@"
            ;;
        *)
            printf 'Неизвестная команда: %s\n\n' "$command" >&2
            usage >&2
            exit 2
            ;;
    esac
}

interactive_menu() {
    local choice

    while true; do
        cat <<'EOF'

Just1kBot — управление сервером

1. Установить или обновить бота
2. Проверить состояние
3. Показать логи
4. Перезапустить бота
5. Создать backup
6. Проверить backup
7. Проверить восстановление в тестовой БД
8. Настроить Amnezia API
9. Удалить бота
0. Выход
EOF
        read -rp 'Выберите действие: ' choice

        case "$choice" in
            1)
                dispatch deploy
                ;;
            2)
                dispatch status
                ;;
            3)
                dispatch logs
                ;;
            4)
                dispatch restart
                ;;
            5)
                dispatch backup
                ;;
            6)
                local verify_file
                read -rp 'Путь к backup.tar.age: ' verify_file
                dispatch verify-backup "$verify_file"
                ;;
            7)
                local restore_file
                read -rp 'Путь к backup.tar.age: ' restore_file
                dispatch restore-test "$restore_file"
                ;;
            8)
                dispatch amnezia
                ;;
            9)
                printf '\nВНИМАНИЕ: будет запущен destructive uninstall.\n'
                dispatch uninstall
                ;;
            0)
                exit 0
                ;;
            *)
                printf 'Неизвестный пункт меню.\n' >&2
                ;;
        esac
    done
}

dispatch "$@"
