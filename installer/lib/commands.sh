status() {
    select_python || true
    line
    printf '%s\n' "$(cyan 'JUST1KBOT STATUS')"
    line
    local service_state="not-installed" enabled_state="no"
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then service_state="active";
    elif [[ -f "$SERVICE_FILE" ]]; then service_state="inactive"; fi
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then enabled_state="yes"; fi
    printf 'Service:  %s\n' "$service_state"
    printf 'Enabled:  %s\n' "$enabled_state"
    printf 'Release:  %s\n' "$(cat "$RELEASE_SHA_FILE" 2>/dev/null || echo unknown)"
    printf 'Branch:   %s\n' "$REPO_BRANCH"
    printf 'Config:   %s\n' "$ENV_FILE"
    printf 'Backups:  %s\n' "$BACKUP_ROOT"
    if [[ -L "$CURRENT_LINK" ]]; then printf 'Current:   %s\n' "$(readlink -f "$CURRENT_LINK")"; fi
    local remote=""
    if [[ -n "$PYTHON_BIN" ]]; then
        remote="$(resolve_remote_sha 2>/dev/null || true)"
        [[ -n "$remote" ]] && printf 'Remote:    %s\n' "$remote"
    fi
    line
}

doctor() {
    require_root doctor
    local failures=0 port
    select_python || true
    line
    printf '%s\n' "$(cyan 'JUST1KBOT DIAGNOSTICS')"
    line
    check_item() {
        local label="$1"; shift
        if "$@" >/dev/null 2>&1; then printf '[OK]   %s\n' "$label"; else printf '[FAIL] %s\n' "$label"; failures=$((failures + 1)); fi
    }
    if select_python; then printf '[OK]   Python >= 3.10 (%s)\n' "$PYTHON_BIN"; else printf '[FAIL] Python >= 3.10\n'; failures=$((failures + 1)); fi
    check_item "PostgreSQL active" bash -c 'systemctl is-active --quiet postgresql || systemctl is-active --quiet postgresql-server'
    check_item "Redis active" bash -c 'systemctl is-active --quiet redis-server || systemctl is-active --quiet redis'
    check_item "Nginx config" nginx -t
    check_item "Systemd unit" test -f "$SERVICE_FILE"
    check_item "Environment file" test -r "$ENV_FILE"
    check_item "Backup identity" test -s "$BACKUP_KEY_FILE"
    check_item "Current release" test -x "$CURRENT_LINK/.venv/bin/python"
    check_item "Service active" systemctl is-active --quiet "$SERVICE_NAME"
    port="$(get_env_value YOOKASSA_WEBHOOK_PORT)"; port="${port:-8080}"
    check_item "Local health endpoint" curl -fsS --max-time 5 "http://127.0.0.1:${port}/health"
    printf 'Disk free: %s\n' "$(df -h "$APP_ROOT" 2>/dev/null | awk 'NR==2 {print $4}' || df -h / | awk 'NR==2 {print $4}')"
    line
    (( failures == 0 )) || return 1
}

edit_environment() {
    require_root edit-env
    local editor="${EDITOR:-}"
    if [[ -z "$editor" ]]; then
        if command -v nano >/dev/null 2>&1; then editor="nano";
        elif command -v vi >/dev/null 2>&1; then editor="vi";
        else die "Редактор не найден. Установите nano/vi или задайте EDITOR."; fi
    fi
    "$editor" "$ENV_FILE"
}

uninstall_app() {
    require_root uninstall
    acquire_lock
    local purge="${1:-0}"
    confirm "Остановить и удалить службу just1kbot?" n || die "Удаление отменено."
    if [[ "$purge" == "1" ]]; then
        confirm "Также удалить конфигурацию, ключи и резервные копии?" n || purge=0
    fi
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE" "$SELF_SYMLINK" "$NGINX_CONF" /etc/logrotate.d/just1kbot-installer
    systemctl daemon-reload
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
    rm -rf "$RELEASES_DIR" "$CURRENT_LINK"
    if [[ "$purge" == "1" ]]; then
        rm -rf "$APP_ROOT" "$CONFIG_DIR" "$BACKUP_ROOT" /var/log/just1kbot
        rm -f "$INSTALL_LOG"
        userdel "$BOT_USER" 2>/dev/null || true
        groupdel "$BOT_GROUP" 2>/dev/null || true
    else
        warn "Сохранены ${ENV_FILE}, ${BACKUP_KEY_FILE} и ${BACKUP_ROOT}."
    fi
    ok "just1kbot удалён. PostgreSQL/Redis/Nginx не удалялись."
}

show_help() {
    cat <<HELP
Использование: just1kbot <команда>

Команды:
  install                 Полная production-установка
  update                  Безопасное обновление с бэкапом и rollback
  status                  Статус и версии
  doctor                  Полная диагностика
  restart|start|stop      Управление systemd-службой
  logs                    Последние логи и follow
  edit-env                Редактирование ${ENV_FILE}
  backup                  Зашифрованный бэкап БД + конфигурации
  restore <file.age>      Восстановление БД из бэкапа
  uninstall               Удалить приложение, сохранив данные
  uninstall --purge       Удалить приложение и данные

Переменные:
  REPO_BRANCH=bot         Ветка GitHub
  NON_INTERACTIVE=1       Запрет интерактивных вопросов
  INSTALL_TLS=0           Не выпускать TLS-сертификат
HELP
}

show_menu() {
    local choice=""
    while true; do
        clear 2>/dev/null || true
        status || true
        cat <<'MENU'
1) Install
2) Update
3) Status
4) Doctor
5) Restart
6) Logs
7) Backup
8) Edit .env
9) Uninstall
0) Exit
MENU
        read_tty "Выберите действие: " choice || return 0
        case "$choice" in
            1) deploy install ;;
            2) deploy update ;;
            3) status ;;
            4) doctor || true ;;
            5) systemctl restart "$SERVICE_NAME" ;;
            6) journalctl -u "$SERVICE_NAME" -n 100 -f ;;
            7) create_backup ;;
            8) edit_environment ;;
            9) uninstall_app 0 ;;
            0) return 0 ;;
            *) warn "Неизвестный пункт." ;;
        esac
        read_tty "Нажмите Enter..." choice || true
    done
}

main() {
    local command="${1:-}"
    case "$command" in
        install) deploy install ;;
        update|reinstall) deploy update ;;
        status) status ;;
        doctor|diag|diagnostics) doctor ;;
        restart|start|stop)
            require_root "$command"
            systemctl "$command" "$SERVICE_NAME"
            ;;
        logs) journalctl -u "$SERVICE_NAME" -n 100 -f ;;
        edit-env) edit_environment ;;
        backup) create_backup ;;
        restore)
            [[ -n "${2:-}" ]] || die "Укажите путь к бэкапу."
            restore_backup "$2"
            ;;
        uninstall) uninstall_app "$( [[ "${2:-}" == "--purge" ]] && echo 1 || echo 0 )" ;;
        help|-h|--help) show_help ;;
        '') show_menu ;;
        *) show_help; exit 2 ;;
    esac
}
