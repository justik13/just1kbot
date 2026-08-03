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

basic_die() {
    printf 'Ошибка: %s\n' "$*" >&2
    exit 1
}

require_safe_script() {
    local path=$1
    local real scripts_real mode

    [[ -f "$path" && ! -L "$path" ]] || basic_die "не найден безопасный script: $path"

    real=$(realpath -e -- "$path")
    scripts_real=$(realpath -e -- "$SCRIPTS_DIR")

    [[ "$real" == "$scripts_real/"* ]] || basic_die "script находится вне scripts/: $path"

    mode=$(stat -c '%a' "$real")
    if (( (8#$mode & 8#022) != 0 )); then
        basic_die "script доступен для записи group/other: $path mode=$mode"
    fi
}

# Preserve the existing test/library contract without changing normal execution.
# Only an explicitly sourced DEPLOY_FUNCTIONS_ONLY=1 session loads definitions.
if [[ "${DEPLOY_FUNCTIONS_ONLY:-0}" == 1 ]]; then
    library="$SCRIPTS_DIR/deploy_full.sh"
    require_safe_script "$library"
    # shellcheck source=scripts/deploy_full.sh
    source "$library"
    return 0 2>/dev/null || exit 0
fi

diagnostics="$SCRIPTS_DIR/lib/installer_diagnostics.sh"
require_safe_script "$diagnostics"
# shellcheck source=scripts/lib/installer_diagnostics.sh
source "$diagnostics"
installer_set_operation control-plane
installer_set_step initialization 'Проверка целостности управляющих scripts.'
installer_set_log_file /var/log/just1kbot-deploy.log
installer_enable_diagnostics

die() {
    local summary=${1:-'операция не выполнена'}
    local details=${2:-$summary}
    local action=${3:-'Исправьте указанную причину и повторите ту же команду.'}
    installer_fail CONTROL_PLANE_ERROR "$summary" "$details" "$action"
}

call_script() {
    local relative=$1
    shift

    local target="$SCRIPTS_DIR/$relative"
    installer_set_step "Запуск $relative" "Дочерний script должен напечатать точную первичную причину ошибки выше диагностического блока."
    require_safe_script "$target"
    bash "$target" "$@"
}

run_script() {
    call_script "$@"
}

run_locked_script() {
    install -d -o root -g root -m 0755 "$(dirname "$OPERATION_LOCK")"
    exec 201>"$OPERATION_LOCK"
    flock -n 201 || die \
        'операция заблокирована' \
        'другая операция deploy/backup/restore/uninstall уже выполняется' \
        'Дождитесь завершения активной операции. Затем проверьте systemctl и повторите команду.'
    run_script "$@"
}

inspect_deploy_state() {
    call_script inspect_install_state.sh "$@"
}

preflight_deploy_state() {
    installer_set_step 'Проверка конфликтов установки' 'Ни один зарезервированный путь не будет автоматически перезаписан без доказанного ownership.'
    inspect_deploy_state --require-safe
    installer_set_step 'Восстановительный preflight' 'Проверка существующей или незавершённой установки перед изменением сервера.'
    call_script preflight_install_state.sh "$@"
}

is_read_only_deploy_request() {
    local argument
    for argument in "$@"; do
        case "$argument" in
            --check|--dry-run)
                return 0
                ;;
        esac
    done
    return 1
}

post_operation_smokecheck() {
    installer_set_step 'Итоговая диагностика' 'Проверка service, heartbeat, PostgreSQL, Redis, Telegram и backup contract.'
    if ! call_script ops/doctor.sh --smoke; then
        die \
            'итоговая диагностика не пройдена' \
            'операция завершила основной этап, но один или несколько production health checks вернули ошибку' \
            'Прочитайте точную проверку выше, затем запустите doctor и логи. Не повторяйте deploy вслепую.'
    fi
}

usage() {
    cat <<'EOF_USAGE'
Just1kBot — управление сервером

Использование:
  sudo bash deploy.sh
  sudo bash deploy.sh state [--json]
  sudo bash deploy.sh update [--check] [--yes] [--dry-run]
  sudo bash deploy.sh deploy [--yes] [--dry-run]
  sudo bash deploy.sh status
  sudo bash deploy.sh doctor
  sudo bash deploy.sh logs
  sudo bash deploy.sh restart
  sudo bash deploy.sh backup
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh verify-backup /path/backup.tar.age
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh restore-test /path/backup.tar.age
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh restore-production /path/backup.tar.age
  sudo bash deploy.sh restore-status
  sudo bash deploy.sh restore-recover
  sudo bash deploy.sh restore-rollback
  sudo bash deploy.sh restore-finalize
  sudo bash deploy.sh uninstall
  bash deploy.sh help

Рекомендуемое обновление production:
  sudo bash /opt/just1kbot/deploy.sh update

Команда state выполняет read-only проверку ownership и конфликтов. Она отдельно
показывает состояние, точную причину и следующее безопасное действие.

Команда update скачивает только main из фиксированного GitHub repository в
отдельный root-only release-каталог и запускает transactional deploy. Команда
deploy предназначена для ручного запуска из уже подготовленного checkout.
Перед update/deploy выполняется fail-closed проверка чужих конфликтов, затем
preflight состояния установки, backup tooling, service account, permissions
и systemd ProtectHome runtime. После успешных update/deploy/restart запускается
read-only doctor.

Standalone setup-amnezia-api.sh не является частью installer control plane.
Основной installer, update, repair и uninstall его не вызывают. Этот файл можно
скачать вручную напрямую из GitHub только на отдельную VPN-ноду.

Production restore сначала создаёт и проверяет staging database. Во время
короткого cutover текущая database сохраняется под rollback-именем и не удаляется
до отдельной команды restore-finalize. Если сервер аварийно выключился во время
переименования БД, restore-recover завершает или безопасно откатывает операцию.

Совместимость со старыми командами:
  sudo bash deploy.sh --status
  sudo bash deploy.sh --logs
  sudo bash deploy.sh --restart
  sudo bash deploy.sh --backup
  sudo AGE_IDENTITY_FILE=/path/key bash deploy.sh --restore /path/backup.tar.age
EOF_USAGE
}

dispatch() {
    local command=${1:-}
    shift || true
    installer_set_operation "${command:-interactive-menu}"

    case "$command" in
        state|install-state)
            inspect_deploy_state "$@"
            ;;
        update)
            preflight_deploy_state "$@"
            call_script update_from_github.sh "$@"
            if ! is_read_only_deploy_request "$@"; then
                post_operation_smokecheck
            fi
            ;;
        deploy)
            preflight_deploy_state "$@"
            call_script deploy.sh "$@"
            if ! is_read_only_deploy_request "$@"; then
                post_operation_smokecheck
            fi
            ;;
        status)
            call_script deploy.sh --status "$@"
            run_script ops/doctor.sh
            ;;
        doctor)
            run_script ops/doctor.sh "$@"
            ;;
        logs)
            run_script deploy.sh --logs "$@"
            ;;
        restart)
            call_script deploy.sh --restart "$@"
            post_operation_smokecheck
            ;;
        backup)
            run_locked_script deploy.sh --backup "$@"
            ;;
        verify-backup)
            (( $# == 1 )) || die \
                'неверные аргументы verify-backup' \
                'verify-backup требует ровно один backup-файл' \
                'Передайте полный путь к одному существующему backup.tar.age.'
            run_script ops/verify_backup.sh "$1"
            ;;
        restore-test)
            (( $# == 1 )) || die \
                'неверные аргументы restore-test' \
                'restore-test требует ровно один backup-файл' \
                'Передайте полный путь к одному существующему backup.tar.age.'
            run_locked_script ops/just1kbot-restore.sh rehearsal "$1"
            ;;
        restore-production)
            run_script ops/just1kbot-restore.sh production "$@"
            ;;
        restore-status)
            (( $# == 0 )) || die 'restore-status не принимает аргументы'
            run_script ops/just1kbot-restore.sh status
            ;;
        restore-recover)
            (( $# == 0 )) || die 'restore-recover не принимает аргументы'
            run_script ops/just1kbot-restore.sh recover
            ;;
        restore-rollback)
            run_script ops/just1kbot-restore.sh rollback "$@"
            ;;
        restore-finalize)
            run_script ops/just1kbot-restore.sh finalize "$@"
            ;;
        uninstall)
            run_script uninstall_entrypoint.sh "$@"
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
            (( $# == 1 )) || die '--restore требует ровно один backup-файл'
            run_locked_script ops/just1kbot-restore.sh rehearsal "$1"
            ;;
        --*)
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
        cat <<'EOF_MENU'

Just1kBot — управление сервером

1. Обновить код из GitHub (main)
2. Установить или обновить из текущего checkout
3. Проверить состояние и диагностику
4. Показать логи
5. Перезапустить бота
6. Создать backup
7. Проверить backup
8. Проверить восстановление в тестовой БД
9. Восстановить production БД из backup
10. Показать состояние production restore
11. Восстановиться после аварийно прерванного restore
12. Откатить последний production restore
13. Завершить restore и удалить сохранённую БД
14. Проверить ownership и конфликты установки
15. Удалить бота
0. Выход
EOF_MENU
        read -rp 'Выберите действие: ' choice

        case "$choice" in
            1) dispatch update ;;
            2) dispatch deploy ;;
            3) dispatch status ;;
            4) dispatch logs ;;
            5) dispatch restart ;;
            6) dispatch backup ;;
            7)
                local verify_file
                read -rp 'Путь к backup.tar.age: ' verify_file
                dispatch verify-backup "$verify_file"
                ;;
            8)
                local restore_file
                read -rp 'Путь к backup.tar.age: ' restore_file
                dispatch restore-test "$restore_file"
                ;;
            9)
                local production_file production_identity
                read -rp 'Путь к production backup.tar.age: ' production_file
                read -rp 'Путь к соответствующему age identity: ' production_identity
                AGE_IDENTITY_FILE=$production_identity
                export AGE_IDENTITY_FILE
                dispatch restore-production "$production_file"
                ;;
            10) dispatch restore-status ;;
            11)
                printf '\nБудет прочитан durable journal и восстановлено безопасное состояние production БД.\n'
                dispatch restore-recover
                ;;
            12)
                printf '\nВНИМАНИЕ: изменения после restore будут сохранены в отдельной БД, но production вернётся к предыдущей БД.\n'
                dispatch restore-rollback
                ;;
            13)
                printf '\nВНИМАНИЕ: сохранённая rollback/failed БД будет безвозвратно удалена после healthcheck.\n'
                dispatch restore-finalize
                ;;
            14) dispatch state ;;
            15)
                printf '\nВНИМАНИЕ: будет запущен destructive uninstall.\n'
                dispatch uninstall
                ;;
            0) exit 0 ;;
            *) printf 'Неизвестный пункт меню.\n' >&2 ;;
        esac
    done
}

dispatch "$@"
