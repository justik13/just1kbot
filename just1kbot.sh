#!/bin/bash
# ==============================================================================
# Just1kBot Deployment & Operations Wrapper
# Единая точка входа для деплоя, обновления, отката, бэкапов и управления ботом.
# ==============================================================================
set -Eeuo pipefail
IFS=$'\n\t'
umask 022

# ==============================================================================
# КОНФИГУРАЦИЯ И ПУТИ
# ==============================================================================
# Пути по умолчанию (целевая ОС: Ubuntu 24.04)
JUST1KBOT_USER="just1kbot"
JUST1KBOT_GROUP="just1kbot"
INSTALL_DIR="/opt/just1kbot"
RELEASES_DIR="${INSTALL_DIR}/releases"
CURRENT_SYMLINK="${INSTALL_DIR}/current"
ENV_FILE="${INSTALL_DIR}/.env"
DATA_DIR="/var/lib/just1kbot"
STATE_FILE="${DATA_DIR}/state.json"
BACKUPS_DIR="${DATA_DIR}/backups"
LOGS_DIR="/var/log/just1kbot"
SERVICE_NAME="just1kbot.service"

# Скрипт-утилиты для логирования
log_info() { printf "\033[1;32m[INFO]\033[0m %s\n" "$*" >&2; }
log_warn() { printf "\033[1;33m[WARN]\033[0m %s\n" "$*" >&2; }
log_error() { printf "\033[1;31m[ERROR]\033[0m %s\n" "$*" >&2; }

require_root() {
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        log_error "Эта команда требует прав root (sudo)."
        exit 1
    fi
}

show_help() {
    cat <<EOF
Использование: $0 <команда> [опции]

Команды:
  install     - Установка бота (идемпотентно, на чистый или занятый сервер)
  update      - Обновление кода и применение миграций (с автооткатом при сбое)
  rollback    - Откат кода на предыдущий релиз (БД не трогается)
  uninstall   - Удаление сервиса и файлов установки (БД сохраняется по умолчанию)
  clean       - Безопасная очистка остатков от прошлых установок (только с подтверждением)
  status      - Показать статус systemd-сервиса бота
  doctor      - Диагностика системы и конфигурации (read-only)
  backup      - Создание бэкапа базы данных
  restore     - Восстановление базы данных из бэкапа
  logs        - Показать логи сервиса бота
  help        - Показать эту справку
EOF
}

# ==============================================================================
# ПОДКОМАНДЫ (Этап 1: status, doctor)
# ==============================================================================

cmd_status() {
    log_info "Статус сервиса ${SERVICE_NAME}..."
    if systemctl list-unit-files | grep -q "^${SERVICE_NAME}"; then
        systemctl status "$SERVICE_NAME" --no-pager || true
    else
        log_warn "Сервис ${SERVICE_NAME} не установлен в systemd."
    fi
}

cmd_doctor() {
    log_info "Запуск диагностики (read-only)..."

    echo "--- ОС и версия ---"
    cat /etc/os-release | grep -E 'PRETTY_NAME|VERSION_ID'
    echo ""

    echo "--- Зависимости ---"
    for cmd in python3 pg_isready redis-cli psql; do
        if command -v "$cmd" >/dev/null 2>&1; then
            echo "$cmd: установлен ($(command -v $cmd))"
            if [ "$cmd" = "python3" ]; then python3 --version; fi
        else
            echo "$cmd: НЕ УСТАНОВЛЕН"
        fi
    done
    echo ""

    echo "--- Каталоги и файлы ---"
    for d in "$INSTALL_DIR" "$RELEASES_DIR" "$CURRENT_SYMLINK" "$ENV_FILE" "$STATE_FILE"; do
        if [ -e "$d" ]; then
            ls -ld "$d"
        else
            echo "$d: НЕ НАЙДЕНО"
        fi
    done
    echo ""

    echo "--- Сервисы ---"
    echo "just1kbot.service:"
    if systemctl is-active --quiet "$SERVICE_NAME"; then echo "  Активен"; else echo "  Не активен"; fi
    echo "postgresql.service:"
    if systemctl is-active --quiet postgresql; then echo "  Активен"; else echo "  Не активен"; fi
    echo "redis-server.service:"
    if systemctl is-active --quiet redis-server; then echo "  Активен"; else echo "  Не активен"; fi
    echo ""

    echo "--- Права доступа (Текущий релиз) ---"
    if [ -L "$CURRENT_SYMLINK" ]; then
        ls -ld "$CURRENT_SYMLINK"
        # Без вывода секретов!
        echo "Владелец конфига:"
        ls -ld "$ENV_FILE" 2>/dev/null || echo "НЕТ КОНФИГА"
    fi
    echo ""

    echo "--- База данных (PostgreSQL) ---"
    if systemctl is-active --quiet postgresql; then
        sudo -u postgres psql -c "SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database WHERE datname = 'just1kbot_bot';" 2>/dev/null || echo "Не могу получить размер БД"
        echo "Миграции Alembic (текущая версия):"
        sudo -u postgres psql -d just1kbot_bot -c "SELECT version_num FROM alembic_version;" 2>/dev/null || echo "Таблица alembic_version не найдена"
    else
        echo "PostgreSQL не активен"
    fi
    echo ""

    echo "--- Состояние ---"
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
        echo ""
    else
        echo "Файл состояния не найден"
    fi
    echo ""

    log_info "Диагностика завершена."
}

# ==============================================================================
# УСТАНОВКА (Этап 2)
# ==============================================================================

_generate_env() {
    if [ ! -f "$ENV_FILE" ]; then
        log_info "Создание ${ENV_FILE} из .env.example..."
        (umask 077 && cp .env.example "$ENV_FILE")
        # Генерация ключа шифрования
        local key
        key=$(python3 -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
        sed -i "s/CHANGE_ME_BASE64_32_BYTES_FERNET_KEY/$key/" "$ENV_FILE"

        # Генерируем случайные пароли для БД и Redis
        local new_db_pass new_redis_pass
        new_db_pass=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
        new_redis_pass=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")

        sed -i "s/CHANGE_ME_DB_PASSWORD/${new_db_pass}/" "$ENV_FILE"
        sed -i "s/CHANGE_ME_REDIS_PASSWORD/${new_redis_pass}/g" "$ENV_FILE"

        log_warn "ВНИМАНИЕ: Отредактируйте ${ENV_FILE} и заполните BOT_TOKEN, YOOKASSA_* и другие параметры."
    else
        log_info "Файл ${ENV_FILE} уже существует."
    fi
}

_preflight_check() {
    log_info "Выполнение preflight проверки..."
    local conflicts=0

    # Проверка портов
    for port in 8080 6379 5432; do
        if ss -tuln | grep -q ":$port "; then
            if ! systemctl is-active --quiet postgresql && ! systemctl is-active --quiet redis-server && ! systemctl is-active --quiet just1kbot; then
                log_warn "Порт $port уже занят другим процессом!"
                conflicts=1
            fi
        fi
    done

    if [[ -f "$STATE_FILE" ]]; then
        log_info "Найден файл состояния $STATE_FILE. Установка будет обновлена или переиспользована."
    elif [[ -d "$INSTALL_DIR" ]]; then
        log_warn "Каталог $INSTALL_DIR существует, но файла состояния нет. Возможно, это остатки старой установки."
        conflicts=1
    fi

    if [[ $conflicts -eq 1 ]]; then
        read -p "Найдены потенциальные конфликты (чужие ресурсы или остатки). Продолжить установку? [y/N]: " ans
        if [[ ! "$ans" =~ ^[Yy]$ ]]; then
            log_error "Установка прервана. Вы можете использовать './just1kbot.sh clean' для очистки."
            exit 1
        fi
    fi
}

cmd_clean() {
    require_root
    log_warn "ВНИМАНИЕ: Эта команда удалит только управляемые ресурсы Just1kBot."
    read -p "Вы уверены, что хотите выполнить очистку? [y/N]: " ans
    if [[ ! "$ans" =~ ^[Yy]$ ]]; then
        log_info "Очистка отменена."
        exit 0
    fi

    log_info "Остановка сервиса $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}"
    systemctl daemon-reload

    log_info "Удаление файлов релизов и конфигурации..."
    rm -rf "$INSTALL_DIR"
    rm -f "$STATE_FILE"

    log_info "Очистка завершена. База данных и бэкапы не были удалены."
}

cmd_install() {
    require_root
    log_info "Начало установки Just1kBot..."

    _preflight_check

    # 1. Установка пакетов
    log_info "Установка системных зависимостей (PostgreSQL, Redis, Python venv)..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y -qq
    apt-get install -y -qq postgresql redis-server python3-venv git age > /dev/null

    # 2. Пользователь и каталоги
    log_info "Настройка пользователя и каталогов..."
    if ! id "$JUST1KBOT_USER" >/dev/null 2>&1; then
        useradd -m -s /bin/bash "$JUST1KBOT_USER"
    fi
    mkdir -p "$INSTALL_DIR" "$RELEASES_DIR" "$DATA_DIR" "$BACKUPS_DIR" "$LOGS_DIR"
    chown -R "$JUST1KBOT_USER:$JUST1KBOT_GROUP" "$INSTALL_DIR" "$DATA_DIR" "$LOGS_DIR"

    # 3. Генерация .env (должна быть перед БД, чтобы взять пароль)
    _generate_env

    local db_password redis_password
    db_password=$(grep -E "^DATABASE_URL=" "$ENV_FILE" | sed -n "s/.*:\(.*\)@.*/\1/p")
    redis_password=$(grep -E "^REDIS_PASSWORD=" "$ENV_FILE" | sed -n "s/^REDIS_PASSWORD=\(.*\)/\1/p" | sed "s/['\"]//g")

    # 4. Настройка PostgreSQL (идемпотентно)
    log_info "Настройка PostgreSQL..."
    systemctl enable --now postgresql > /dev/null
    sudo -u postgres psql -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'just1kbot') THEN CREATE ROLE just1kbot WITH LOGIN; END IF; END \$\$;"
    sudo -u postgres psql -c "\password just1kbot" <<< "$db_password" >/dev/null 2>&1 || sudo -u postgres psql -c "ALTER ROLE just1kbot WITH PASSWORD '$db_password';"
    sudo -u postgres psql -c "SELECT 1 FROM pg_database WHERE datname = 'just1kbot_bot'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE just1kbot_bot OWNER just1kbot;"

    # 5. Настройка Redis
    log_info "Настройка Redis..."
    systemctl enable --now redis-server > /dev/null
    if [[ -n "$redis_password" ]] && [[ "$redis_password" != "CHANGE_ME_REDIS_PASSWORD" ]]; then
        if ! grep -q "^requirepass " /etc/redis/redis.conf; then
            echo "requirepass $redis_password" >> /etc/redis/redis.conf
        else
            sed -i "s/^requirepass .*/requirepass $redis_password/" /etc/redis/redis.conf
        fi
        systemctl restart redis-server
    fi

    # 6. Клонирование и настройка релиза
    log_info "Создание релиза..."
    local release_name
    release_name=$(date -u +%Y%m%d_%H%M%S)
    local release_path="${RELEASES_DIR}/${release_name}"

    log_info "Клонирование кода из репозитория..."
    sudo -u "$JUST1KBOT_USER" git clone --depth 1 https://github.com/justik13/just1kbot "$release_path" > /dev/null

    # 7. Virtualenv и зависимости
    log_info "Настройка virtualenv..."
    sudo -u "$JUST1KBOT_USER" python3 -m venv "${release_path}/venv"
    sudo -u "$JUST1KBOT_USER" "${release_path}/venv/bin/pip" install -r "${release_path}/requirements.txt" > /dev/null

    # 8. Симлинк
    log_info "Переключение симлинка current..."
    ln -sfn "$release_path" "$CURRENT_SYMLINK"

    # 9. Миграции
    log_info "Применение миграций базы данных..."
    # Оборачиваем вызов alembic с загрузкой ENV_FILE.
    # Так как файл .env принадлежит root:root и имеет 600, just1kbot не сможет его прочитать.
    # Меняем владельца .env
    chown "$JUST1KBOT_USER:$JUST1KBOT_GROUP" "$ENV_FILE"
    sudo -u "$JUST1KBOT_USER" bash -c "cd $CURRENT_SYMLINK && set -a && source $ENV_FILE && set +a && ./venv/bin/alembic upgrade head"

    # 10. Systemd сервис
    log_info "Настройка systemd сервиса..."
    cat <<EOF > "/etc/systemd/system/${SERVICE_NAME}"
[Unit]
Description=Just1kBot Telegram Bot
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=${JUST1KBOT_USER}
Group=${JUST1KBOT_GROUP}
WorkingDirectory=${CURRENT_SYMLINK}
EnvironmentFile=${ENV_FILE}
Environment="PYTHONPATH=${CURRENT_SYMLINK}"
ExecStart=${CURRENT_SYMLINK}/venv/bin/python3 -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    systemctl start "$SERVICE_NAME"

    # 11. Состояние
    echo "{\"installed\": true, \"current_release\": \"$release_name\"}" > "$STATE_FILE"

    log_info "Установка завершена! Проверьте логи: ./just1kbot.sh logs"
}

cmd_update() {
    require_root
    log_info "Начало обновления Just1kBot..."

    if [[ ! -f "$STATE_FILE" ]] || [[ ! -d "$INSTALL_DIR" ]]; then
        log_error "Установка не найдена. Сначала выполните install."
        exit 1
    fi

    local current_release
    current_release=$(readlink "$CURRENT_SYMLINK" || true)

    # 1. Создание нового релиза
    log_info "Создание нового релиза..."
    local release_name
    release_name=$(date -u +%Y%m%d_%H%M%S)
    local release_path="${RELEASES_DIR}/${release_name}"

    log_info "Клонирование кода из репозитория..."
    sudo -u "$JUST1KBOT_USER" git clone --depth 1 https://github.com/justik13/just1kbot "$release_path" > /dev/null

    # 2. Virtualenv
    log_info "Настройка virtualenv для нового релиза..."
    sudo -u "$JUST1KBOT_USER" python3 -m venv "${release_path}/venv"
    sudo -u "$JUST1KBOT_USER" "${release_path}/venv/bin/pip" install -r "${release_path}/requirements.txt" > /dev/null

    # 3. Симлинк
    log_info "Переключение симлинка current..."
    ln -sfn "$release_path" "$CURRENT_SYMLINK"

    # 4. Авто-бэкап перед миграциями
    log_info "Автоматическое создание бэкапа перед миграциями..."
    cmd_backup

    # 5. Миграции
    log_info "Применение миграций..."
    if ! sudo -u "$JUST1KBOT_USER" bash -c "cd $CURRENT_SYMLINK && set -a && source $ENV_FILE && set +a && ./venv/bin/alembic upgrade head"; then
        log_error "Ошибка миграции. Откат симлинка..."
        if [[ -n "$current_release" ]]; then
            ln -sfn "$current_release" "$CURRENT_SYMLINK"
            systemctl restart "$SERVICE_NAME"
            log_info "Откат кода завершен. ВНИМАНИЕ: База данных не откатывается автоматически!"
        fi
        exit 1
    fi

    # 5. Перезапуск
    log_info "Перезапуск сервиса..."
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"

    # 6. Обновление состояния
    echo "{\"installed\": true, \"current_release\": \"$release_name\"}" > "$STATE_FILE"

    log_info "Обновление завершено!"
}

cmd_rollback() {
    require_root
    log_info "Начало отката (rollback)..."

    if [[ ! -f "$STATE_FILE" ]] || [[ ! -d "$RELEASES_DIR" ]]; then
        log_error "Установка не найдена."
        exit 1
    fi

    local current_target
    current_target=$(readlink "$CURRENT_SYMLINK" || true)

    # Получаем два последних релиза, отсортированных по имени (имя - это timestamp)
    local releases
    releases=$(ls -1 "$RELEASES_DIR" | sort -r)
    local prev_release=""

    for rel in $releases; do
        if [[ "${RELEASES_DIR}/${rel}" != "$current_target" ]]; then
            prev_release="${RELEASES_DIR}/${rel}"
            break
        fi
    done

    if [[ -z "$prev_release" ]]; then
        log_error "Предыдущий релиз не найден. Невозможно выполнить откат."
        exit 1
    fi

    log_info "Откат на релиз: $prev_release"
    ln -sfn "$prev_release" "$CURRENT_SYMLINK"

    log_info "Перезапуск сервиса..."
    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"

    # Обновление состояния
    local prev_name
    prev_name=$(basename "$prev_release")
    echo "{\"installed\": true, \"current_release\": \"$prev_name\"}" > "$STATE_FILE"

    log_info "Откат успешно завершен. База данных не была затронута."
}

cmd_uninstall() {
    require_root
    log_warn "Начало процесса удаления Just1kBot."
    read -p "Вы уверены, что хотите удалить сервис и файлы кода? [y/N]: " ans
    if [[ ! "$ans" =~ ^[Yy]$ ]]; then
        log_info "Удаление отменено."
        exit 0
    fi

    log_info "Остановка сервиса $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}"
    systemctl daemon-reload

    log_info "Удаление файлов релизов ($RELEASES_DIR) и конфигурации ($ENV_FILE)..."
    rm -rf "$RELEASES_DIR"
    rm -f "$CURRENT_SYMLINK"
    rm -f "$ENV_FILE"
    rm -f "$STATE_FILE"

    log_info "Удаление пользователя $JUST1KBOT_USER..."
    userdel "$JUST1KBOT_USER" 2>/dev/null || true

    log_warn "Удаление завершено."
    log_warn "ВНИМАНИЕ: База данных (just1kbot_bot), роль (just1kbot), бэкапы ($BACKUPS_DIR) и логи ($LOGS_DIR) не были удалены для сохранения ваших данных."
    log_warn "Вы можете удалить их вручную, если они больше не нужны."
}

cmd_backup() {
    require_root
    log_info "Создание резервной копии БД (age + pg_dump)..."

    # Генерация ключа age, если нет (для простоты в этом скрипте, в реальном мире ключ должен быть в безопасном месте)
    local age_key_file="${DATA_DIR}/backup_key.txt"
    if [[ ! -f "$age_key_file" ]]; then
        age-keygen -o "$age_key_file" 2>/dev/null
        chmod 600 "$age_key_file"
        log_info "Создан новый ключ для бэкапов: $age_key_file"
    fi
    local pubkey
    pubkey=$(grep "public key:" "$age_key_file" | awk '{print $4}')

    local timestamp
    timestamp=$(date -u +%Y%m%d_%H%M%S)
    local backup_file="${BACKUPS_DIR}/db_backup_${timestamp}.custom.age"
    local raw_dump
    raw_dump=$(mktemp "$BACKUPS_DIR/db_dump_${timestamp}_XXXXXX.custom")

    log_info "Создание дампа PostgreSQL..."
    sudo -u postgres pg_dump -Fc just1kbot_bot > "$raw_dump"

    log_info "Шифрование дампа..."
    age -r "$pubkey" -o "$backup_file" "$raw_dump"
    rm -f "$raw_dump"

    log_info "Создание контрольной суммы..."
    (cd "$BACKUPS_DIR" && sha256sum "$(basename "$backup_file")" > "${backup_file}.sha256")

    log_info "Резервная копия создана: $backup_file (с контрольной суммой)"
}

cmd_verify() {
    require_root
    log_info "Проверка целостности бэкапа..."
    local backup_file="${1:-}"
    if [[ -z "$backup_file" ]]; then
        backup_file=$(ls -1t "${BACKUPS_DIR}"/*.age 2>/dev/null | head -n 1)
        if [[ -z "$backup_file" ]]; then
            log_error "Бэкапы не найдены в $BACKUPS_DIR"
            exit 1
        fi
        log_info "Используется последний бэкап: $backup_file"
    fi

    if [[ ! -f "$backup_file" ]]; then
        log_error "Файл $backup_file не найден."
        exit 1
    fi

    local checksum_file="${backup_file}.sha256"
    if [[ -f "$checksum_file" ]]; then
        log_info "Проверка контрольной суммы..."
        if ! (cd "$BACKUPS_DIR" && sha256sum -c "$(basename "$checksum_file")" --quiet); then
            log_error "Контрольная сумма бэкапа не совпадает! Бэкап поврежден."
            exit 1
        fi
    else
        log_warn "Файл контрольной суммы ${checksum_file} не найден, проверка пропущена."
    fi

    local age_key_file="${DATA_DIR}/backup_key.txt"
    if [[ ! -f "$age_key_file" ]]; then
        log_error "Ключ $age_key_file не найден!"
        exit 1
    fi

    local raw_dump
    raw_dump=$(mktemp "$BACKUPS_DIR/db_verify_$$_XXXXXX.custom")
    log_info "Расшифровка во временный файл..."
    if ! age -d -i "$age_key_file" -o "$raw_dump" "$backup_file"; then
        log_error "Ошибка расшифровки бэкапа."
        rm -f "$raw_dump"
        exit 1
    fi

    log_info "Проверка структуры дампа (pg_restore --list)..."
    if ! pg_restore --list "$raw_dump" >/dev/null; then
        log_error "Бэкап поврежден или не является валидным дампом PostgreSQL."
        rm -f "$raw_dump"
        exit 1
    fi

    rm -f "$raw_dump"
    log_info "Бэкап успешно проверен: целостность не нарушена."
}

cmd_restore() {
    require_root
    log_warn "Восстановление перезапишет текущую базу данных just1kbot_bot!"
    read -p "Вы уверены, что хотите продолжить? [y/N]: " ans
    if [[ ! "$ans" =~ ^[Yy]$ ]]; then
        log_info "Восстановление отменено."
        exit 0
    fi

    local age_key_file="${DATA_DIR}/backup_key.txt"
    if [[ ! -f "$age_key_file" ]]; then
        log_error "Ключ $age_key_file не найден!"
        exit 1
    fi

    local target_backup="${1:-}"
    if [[ -z "$target_backup" ]]; then
        target_backup=$(ls -1t "${BACKUPS_DIR}"/*.age 2>/dev/null | head -n 1)
        if [[ -z "$target_backup" ]]; then
            log_error "Бэкапы не найдены в $BACKUPS_DIR"
            exit 1
        fi
        log_info "Используется последний бэкап: $target_backup"
    fi

    cmd_verify "$target_backup"

    log_info "Восстановление из бэкапа: $target_backup"
    local raw_dump
    raw_dump=$(mktemp "$BACKUPS_DIR/db_restore_$$_XXXXXX.custom")

    log_info "Расшифровка..."
    age -d -i "$age_key_file" -o "$raw_dump" "$target_backup"

    log_info "Остановка сервиса $SERVICE_NAME (чтобы избежать конфликтов)..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    log_info "Пересоздание базы данных..."
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS just1kbot_bot;"
    sudo -u postgres psql -c "CREATE DATABASE just1kbot_bot OWNER just1kbot;"

    log_info "Восстановление дампа..."
    # Даем доступ пользователю postgres на чтение временного дампа
    chmod 644 "$raw_dump"
    sudo -u postgres pg_restore -d just1kbot_bot "$raw_dump"
    rm -f "$raw_dump"

    log_info "Запуск сервиса..."
    systemctl start "$SERVICE_NAME" 2>/dev/null || true

    log_info "Восстановление завершено!"
}

cmd_logs() {
    if systemctl list-unit-files | grep -q "^${SERVICE_NAME}"; then
        journalctl -u "$SERVICE_NAME" -n 50 --no-pager
    else
        log_warn "Сервис ${SERVICE_NAME} не установлен."
    fi
}

# ==============================================================================
# ДИСПЕТЧЕР
# ==============================================================================

if [[ $# -eq 0 ]]; then
    show_help
    exit 0
fi

COMMAND="$1"
shift

case "$COMMAND" in
    install)   cmd_install "$@" ;;
    update)    cmd_update "$@" ;;
    rollback)  cmd_rollback "$@" ;;
    uninstall) cmd_uninstall "$@" ;;
    clean)     cmd_clean "$@" ;;
    status)    cmd_status "$@" ;;
    doctor)    cmd_doctor "$@" ;;
    backup)    cmd_backup "$@" ;;
    verify)    cmd_verify "$@" ;;
    restore)   cmd_restore "$@" ;;
    logs)      cmd_logs "$@" ;;
    help|-h|--help) show_help ;;
    *)
        log_error "Неизвестная команда: $COMMAND"
        show_help
        exit 1
        ;;
esac
