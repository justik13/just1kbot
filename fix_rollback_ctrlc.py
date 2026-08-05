import re

with open('just1kbot.sh', 'r') as f:
    content = f.read()

# Fix Ctrl+C in menu loop
# We need to make sure that the caller of prompt_raw handles the return code
# or disable set -e for the prompt call in the menu loop.
# It's better to just allow the failure in show_menu

menu_prompt_fix = """
        echo ""
        local choice=""
        prompt_raw "Выберите действие [0-9]: " choice || continue
"""
content = re.sub(r'        echo ""\n        local choice=""\n        prompt_raw "Выберите действие \[0-9\]: " choice\n', menu_prompt_fix, content)

# Fix rollback coverage to include setup_venv and run_alembic_migrations
rollback_fix = """
    if ! deploy_code_from_dir "$src_dir"; then
        error "Ошибка копирования файлов! Запуск отката..."
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        return 1
    fi

    if ! setup_venv; then
        error "Ошибка настройки виртуального окружения. Откат..."
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        return 1
    fi

    if ! run_alembic_migrations; then
        error "Ошибка миграции БД. Откат..."
        cp -a "$backup_snapshot/bot" "$INSTALL_DIR/" 2>/dev/null || true
        # DB is harder to rollback perfectly without full restore, but code rollback is better than nothing
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
        return 1
    fi
"""
content = re.sub(r'    if ! deploy_code_from_dir "\$src_dir"; then[\s\S]*?fi\n    \n    setup_venv\n    run_alembic_migrations', rollback_fix, content)

with open('just1kbot.sh', 'w') as f:
    f.write(content)
