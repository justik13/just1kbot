set -Eeuo pipefail
IFS=$'\n\t'
umask 027

OPERATION_LOCK=/run/lock/just1kbot-deploy.lock

die() {
    installer_fail CONTROL_PLANE_ERROR \
        "${1:-операция не выполнена}" \
        "${2:-${1:-операция не выполнена}}" \
        "${3:-Исправьте указанную причину и повторите команду.}"
}

call_script() {
    local relative=$1
    shift
    local target="$SCRIPTS_DIR/$relative"
    installer_set_step "Запуск $relative" \
        'Дочерний script обязан вывести первичную причину ошибки.'
    require_safe_script "$target"
    bash "$target" "$@"
}

run_locked_script() {
    install -d -o root -g root -m 0755 "$(dirname "$OPERATION_LOCK")"
    exec 201>"$OPERATION_LOCK"
    flock -n 201 || die \
        'operation заблокирована' \
        'другой deploy/backup/restore/uninstall уже выполняется' \
        'Дождитесь завершения и повторите команду.'
    call_script "$@"
}

state() {
    call_script inspect_install_state.sh "$@"
}

preflight() {
    installer_set_step 'Read-only state preflight' \
        'Foreign collision или повреждённый manifest блокируют изменения.'
    state --operation deploy --require-safe
    call_script preflight_install_state.sh "$@"
}

smoke() {
    if ! call_script ops/doctor.sh --smoke; then
        die \
            'итоговая диагностика не пройдена' \
            'основная operation завершилась, но production health checks вернули ошибку; автоматический rollback на этом этапе не выполнялся' \
            'Запустите doctor и logs, затем используйте documented recovery.'
    fi
}

usage() {
    cat <<'EOF_USAGE'
Just1kBot shared-server-safe control plane

  sudo bash deploy.sh state [--json]
  sudo bash deploy.sh update [--sha COMMIT] [--yes] [--check]
  sudo bash deploy.sh deploy [--yes] [--dry-run]
  sudo bash deploy.sh install-recover
  sudo bash deploy.sh install-rollback
  sudo bash deploy.sh status
  sudo bash deploy.sh doctor
  sudo bash deploy.sh logs
  sudo bash deploy.sh restart
  sudo bash deploy.sh backup
  sudo bash deploy.sh verify-backup FILE
  sudo bash deploy.sh restore-test FILE
  sudo bash deploy.sh restore-production FILE
  sudo bash deploy.sh restore-status
  sudo bash deploy.sh restore-recover
  sudo bash deploy.sh restore-rollback
  sudo bash deploy.sh restore-finalize
  sudo bash deploy.sh uninstall --keep-data|--purge-data

Legacy direct install flags remain supported:
  sudo bash deploy.sh --yes
  sudo bash deploy.sh --dry-run

Installer supports only Ubuntu 24.04. It uses dedicated Redis on 127.0.0.1:6380,
never changes firewall state or /etc/redis/redis.conf, does not remove Nginx default
site, and records ownership in /var/lib/just1kbot/install-state/manifest.json.
Standalone setup-amnezia-api.sh is not part of this control plane.
EOF_USAGE
}

dispatch() {
    local command=${1:-}
    shift || true
    installer_set_operation "${command:-menu}"

    case "$command" in
        state|install-state)
            state "$@"
            ;;
        update)
            preflight "$@"
            call_script update_from_github.sh "$@"
            case " $* " in
                *' --check '*|*' --dry-run '*) ;;
                *) smoke ;;
            esac
            ;;
        deploy)
            preflight "$@"
            call_script install_safe.sh "$@"
            case " $* " in
                *' --dry-run '*) ;;
                *) smoke ;;
            esac
            ;;
        install-recover)
            call_script install_safe.sh --recover "$@"
            ;;
        install-rollback)
            call_script install_safe.sh --rollback-incomplete "$@"
            ;;
        status)
            call_script install_safe.sh --status "$@"
            call_script ops/doctor.sh
            ;;
        doctor)
            call_script ops/doctor.sh "$@"
            ;;
        logs)
            call_script install_safe.sh --logs "$@"
            ;;
        restart)
            call_script install_safe.sh --restart "$@"
            smoke
            ;;
        backup)
            run_locked_script install_safe.sh --backup "$@"
            ;;
        verify-backup)
            (( $# == 1 )) || die 'verify-backup требует один файл'
            call_script ops/verify_backup.sh "$1"
            ;;
        restore-test)
            (( $# == 1 )) || die 'restore-test требует один файл'
            run_locked_script ops/just1kbot-restore.sh rehearsal "$1"
            ;;
        restore-production)
            call_script ops/just1kbot-restore.sh production "$@"
            ;;
        restore-status)
            (( $# == 0 )) || die 'restore-status не принимает аргументы'
            call_script ops/just1kbot-restore.sh status
            ;;
        restore-recover)
            (( $# == 0 )) || die 'restore-recover не принимает аргументы'
            call_script ops/just1kbot-restore.sh recover
            ;;
        restore-rollback)
            call_script ops/just1kbot-restore.sh rollback "$@"
            ;;
        restore-finalize)
            call_script ops/just1kbot-restore.sh finalize "$@"
            ;;
        uninstall)
            call_script uninstall_entrypoint.sh "$@"
            ;;
        help|-h|--help)
            usage
            ;;
        '')
            menu
            ;;
        --yes|--dry-run)
            dispatch deploy "$command" "$@"
            ;;
        --status)
            dispatch status "$@"
            ;;
        --logs)
            dispatch logs "$@"
            ;;
        --restart)
            dispatch restart "$@"
            ;;
        --backup)
            dispatch backup "$@"
            ;;
        --restore)
            dispatch restore-test "$@"
            ;;
        *)
            printf 'Неизвестная команда: %s\n\n' "$command" >&2
            usage >&2
            return 2
            ;;
    esac
}

menu() {
    local choice file identity mode
    while true; do
        cat <<'EOF_MENU'

Just1kBot
1. Update from GitHub
2. Deploy current checkout
3. State / ownership
4. Doctor
5. Logs
6. Restart
7. Backup
8. Verify backup
9. Restore rehearsal
10. Restore production
11. Restore status
12. Recover interrupted restore
13. Roll back last restore
14. Finalize restore
15. Install recovery status
16. Roll back incomplete first install
17. Uninstall
0. Exit
EOF_MENU
        read -rp 'Выберите действие: ' choice
        case "$choice" in
            1) dispatch update ;;
            2) dispatch deploy ;;
            3) dispatch state ;;
            4) dispatch doctor ;;
            5) dispatch logs ;;
            6) dispatch restart ;;
            7) dispatch backup ;;
            8)
                read -rp 'Путь к backup.tar.age: ' file
                dispatch verify-backup "$file"
                ;;
            9)
                read -rp 'Путь к backup.tar.age: ' file
                dispatch restore-test "$file"
                ;;
            10)
                read -rp 'Путь к production backup.tar.age: ' file
                read -rp 'Путь к age identity: ' identity
                AGE_IDENTITY_FILE=$identity dispatch restore-production "$file"
                ;;
            11) dispatch restore-status ;;
            12) dispatch restore-recover ;;
            13) dispatch restore-rollback ;;
            14) dispatch restore-finalize ;;
            15) dispatch install-recover ;;
            16) dispatch install-rollback ;;
            17)
                read -rp 'Режим (--keep-data или --purge-data): ' mode
                dispatch uninstall "$mode"
                ;;
            0) return 0 ;;
            *) printf 'Неизвестный пункт.\n' >&2 ;;
        esac
    done
}
