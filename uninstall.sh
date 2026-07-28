#!/bin/bash
# =============================================================================
# JUST1KBOT - Деинсталляция
# =============================================================================
# Использование:
#   sudo ./uninstall.sh            — интерактивный режим
#   sudo ./uninstall.sh --force    — без подтверждений (полное удаление)
#   sudo ./uninstall.sh --keep     — без подтверждений (сохранить данные)
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

# --- Константы ---
BOT_USER="just1kbot"
SERVICE_NAME="just1kbot"
WORKER_SERVICES=("just1kbot-traffic" "just1kbot-notifications" "just1kbot-cleanup" "just1kbot-stale-payments" "just1kbot-heartbeat")
LOG_FILE="/var/log/just1kbot-uninstall.log"
BACKUP_DIR="/root/backups/just1kbot"
SNAPSHOT_DIR="/root/.just1kbot-snapshots"

# --- Цвета ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Флаги ---
FORCE_MODE=""   # "", "full", "keep"

for arg in "$@"; do
    case "$arg" in
        --force) FORCE_MODE="full" ;;
        --keep)  FORCE_MODE="keep" ;;
        -h|--help)
            echo "Использование: sudo ./uninstall.sh [--force | --keep]"
            echo "  --force   Полное удаление без подтверждений"
            echo "  --keep    Удалить сервисы, сохранить данные в архив"
            exit 0
            ;;
    esac
done

# --- Временные файлы ---
TEMP_FILES=()
cleanup() {
    for f in "${TEMP_FILES[@]:-}"; do
        rm -f "$f" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

# --- Проверка root ---
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Ошибка: запустите с sudo.${NC}"
    exit 1
fi

# --- Логирование ---
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${GREEN}[$ts]${NC} $1"
    echo "[$ts] $1" >> "$LOG_FILE"
}

warn() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${YELLOW}[$ts] ВНИМАНИЕ:${NC} $1"
    echo "[$ts] WARNING: $1" >> "$LOG_FILE"
}

error() {
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    echo -e "${RED}[$ts] ОШИБКА:${NC} $1" >&2
    echo "[$ts] ERROR: $1" >> "$LOG_FILE"
}

info() {
    echo -e "${BLUE}$1${NC}"
}

# --- Определение директории проекта ---
PROJECT_DIR=""
if systemctl cat "$SERVICE_NAME" 2>/dev/null | grep -q "WorkingDirectory="; then
    PROJECT_DIR=$(systemctl cat "$SERVICE_NAME" 2>/dev/null | grep "WorkingDirectory=" | cut -d= -f2 | tr -d '[:space:]')
fi
if [[ -z "$PROJECT_DIR" ]]; then
    PROJECT_DIR="/opt/just1kbot"
fi

# --- Защита от удаления системных директорий ---
if [[ "$PROJECT_DIR" == "/" || "$PROJECT_DIR" == "/etc" || "$PROJECT_DIR" == "/usr" || "$PROJECT_DIR" == "/var" || "$PROJECT_DIR" == "/home" ]]; then
    error "Отказ: PROJECT_DIR='$PROJECT_DIR' является системной директорией."
fi
if [[ "$PROJECT_DIR" != *"just1kbot"* ]]; then
    error "Отказ: PROJECT_DIR='$PROJECT_DIR' не содержит 'just1kbot'. Проверьте конфигурацию."
fi

# =============================================================================
# Функции очистки
# =============================================================================

stop_services() {
    log "Остановка сервисов..."

    # Основной сервис
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME"
        log "Остановлен: $SERVICE_NAME"
    fi

    # Воркеры
    for worker in "${WORKER_SERVICES[@]}"; do
        if systemctl is-active --quiet "$worker" 2>/dev/null; then
            systemctl stop "$worker"
            log "Остановлен: $worker"
        fi
        if [[ -f "/etc/systemd/system/${worker}.service" ]]; then
            systemctl disable "$worker" > /dev/null 2>&1 || true
            rm -f "/etc/systemd/system/${worker}.service"
        fi
    done

    # Убиваем оставшиеся процессы пользователя
    pkill -u "$BOT_USER" 2>/dev/null || true
    sleep 1

    systemctl daemon-reload
    log "Все сервисы остановлены"
}

remove_systemd() {
    log "Удаление systemd сервисов..."
    systemctl disable "$SERVICE_NAME" > /dev/null 2>&1 || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    log "Systemd сервисы удалены"
}

remove_crontab() {
    log "Удаление cron-задач..."
    crontab -l 2>/dev/null | grep -v "just1kbot" | crontab - 2>/dev/null || true
    log "Cron-задачи удалены"
}

remove_scripts() {
    log "Удаление скриптов..."
    rm -f /usr/local/bin/just1kbot-backup.sh
    rm -f /usr/local/bin/just1kbot-restore.sh
    rm -f /usr/local/bin/just1kbot-healthcheck.sh
    log "Скрипты удалены"
}

remove_nginx() {
    log "Удаление Nginx конфигурации..."
    for conf in /etc/nginx/sites-available/just1kbot*; do
        if [[ -f "$conf" ]]; then
            local name
            name=$(basename "$conf")
            rm -f "/etc/nginx/sites-enabled/$name"
            rm -f "$conf"
            log "Удалён: $name"
        fi
    done
    # Amnezia конфиги + сертификаты
    for conf in /etc/nginx/sites-available/just1kbot-amnezia-*; do
        if [[ -f "$conf" ]]; then
            local name
            name=$(basename "$conf")
            rm -f "/etc/nginx/sites-enabled/$name"
            rm -f "$conf"
            local cert_domain
            cert_domain=$(echo "$name" | sed 's/just1kbot-amnezia-//')
            certbot delete --cert-name "$cert_domain" --non-interactive 2>/dev/null || true
            log "Удалён: $name + сертификат"
        fi
    done
    if command -v nginx &>/dev/null && nginx -t 2>/dev/null; then
        systemctl reload nginx 2>/dev/null || true
    fi
    log "Nginx конфигурация удалена"
}

remove_ufw_rules() {
    log "Удаление UFW правил..."
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "active"; then
        ufw delete allow 80/tcp > /dev/null 2>&1 || true
        ufw delete allow 443/tcp > /dev/null 2>&1 || true
        ufw delete allow 8443/tcp > /dev/null 2>&1 || true
        ufw delete deny 8080/tcp > /dev/null 2>&1 || true
        ufw delete deny 6379/tcp > /dev/null 2>&1 || true
        ufw delete deny 5432/tcp > /dev/null 2>&1 || true
        log "UFW правила удалены"
    else
        log "UFW не активен — пропускаю"
    fi
}

remove_redis_keys() {
    log "Удаление Redis ключей just1kbot_bot:*..."
    if command -v redis-cli &>/dev/null; then
        local redis_pass=""
        if [[ -f "$PROJECT_DIR/.env" ]]; then
            redis_pass=$(grep "^REDIS_URL=" "$PROJECT_DIR/.env" 2>/dev/null | sed 's/.*:\/\/:\([^@]*\)@.*/\1/' || true)
        fi

        if [[ -n "$redis_pass" ]]; then
            redis-cli -a "$redis_pass" --scan --pattern "just1kbot_bot:*" 2>/dev/null | \
                xargs -r redis-cli -a "$redis_pass" DEL > /dev/null 2>&1 || true
        else
            redis-cli --scan --pattern "just1kbot_bot:*" 2>/dev/null | \
                xargs -r redis-cli DEL > /dev/null 2>&1 || true
        fi
        log "Redis ключи удалены"
    else
        warn "redis-cli не найден — пропускаю очистку ключей"
    fi
}

remove_user() {
    log "Удаление пользователя $BOT_USER..."
    if id "$BOT_USER" &>/dev/null; then
        userdel -r "$BOT_USER" 2>/dev/null || userdel "$BOT_USER" 2>/dev/null || true
        log "Пользователь удалён"
    else
        log "Пользователь не существует"
    fi
}

remove_directories() {
    log "Удаление директорий..."
    rm -rf "$PROJECT_DIR"
    rm -rf "/var/log/just1kbot"
    rm -f /var/log/just1kbot-deploy.log
    rm -f /var/log/just1kbot-rollback.log
    rm -f /var/log/just1kbot-uninstall.log
    rm -f /var/log/just1kbot-amnezia-setup.log
    rm -f /var/log/just1kbot-backup.log
    rm -rf "$SNAPSHOT_DIR"
    rm -f /etc/logrotate.d/just1kbot
    rm -f /tmp/just1kbot_crash_count
    rm -f /tmp/just1kbot_heartbeat
    log "Директории удалены"
}

remove_database() {
    log "Удаление базы данных..."
    if command -v psql &>/dev/null; then
        su - postgres -c "psql -c \"DROP DATABASE IF EXISTS just1kbot_bot;\"" 2>/dev/null || true
        su - postgres -c "psql -c \"DROP ROLE IF EXISTS just1kbot;\"" 2>/dev/null || true
        log "База данных удалена"
    else
        warn "psql не найден — БД не удалена"
    fi
}

archive_data() {
    local archive_path="$1"
    log "Архивирование данных..."

    local tmpdir
    tmpdir=$(mktemp -d)
    TEMP_FILES+=("$tmpdir")

    # Бэкап БД
    if command -v pg_dump &>/dev/null; then
        su - postgres -c "pg_dump just1kbot_bot" > "$tmpdir/database.sql" 2>/dev/null || true
    fi

    # .env
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        cp "$PROJECT_DIR/.env" "$tmpdir/env.bak"
    fi

    # Бэкапы
    if [[ -d "$BACKUP_DIR" ]]; then
        cp -r "$BACKUP_DIR" "$tmpdir/backups" 2>/dev/null || true
    fi

    # Snapshots
    if [[ -d "$SNAPSHOT_DIR" ]]; then
        cp -r "$SNAPSHOT_DIR" "$tmpdir/snapshots" 2>/dev/null || true
    fi

    # Создаём архив
    tar -czf "$archive_path" -C "$tmpdir" .
    chmod 600 "$archive_path"

    rm -rf "$tmpdir"
    log "Архив создан: $archive_path ($(du -h "$archive_path" | cut -f1))"
}

# =============================================================================
# Main
# =============================================================================

main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     JUST1KBOT — Деинсталляция           ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
    echo ""
    info "Директория проекта: $PROJECT_DIR"
    echo ""

    # Определяем режим
    local mode=""
    if [[ "$FORCE_MODE" == "full" ]]; then
        mode="full"
    elif [[ "$FORCE_MODE" == "keep" ]]; then
        mode="keep"
    else
        echo "Выберите вариант:"
        echo ""
        echo "  1) Полное удаление (БД, файлы, пользователь, конфиги)"
        echo "  2) Удалить сервисы, сохранить данные в архив"
        echo ""
        read -rp "Выбор [1/2]: " choice
        case "$choice" in
            1) mode="full" ;;
            2) mode="keep" ;;
            *) echo "Неверный выбор."; exit 1 ;;
        esac
    fi

    # Подтверждение для полного удаления
    if [[ "$mode" == "full" && "$FORCE_MODE" != "full" ]]; then
        echo ""
        warn "ВНИМАНИЕ: будут удалены ВСЕ данные (БД, файлы, пользователь)."
        read -rp "Введите 'yes' для подтверждения: " confirm
        if [[ "$confirm" != "yes" ]]; then
            echo "Отменено."
            exit 0
        fi
    fi

    echo ""
    log "Начало деинсталляции (режим: $mode)..."
    echo ""

    # Остановка сервисов
    stop_services

    # Архивирование (если нужно)
    if [[ "$mode" == "keep" ]]; then
        local archive_path="/root/just1kbot-data-$(date +%Y%m%d-%H%M%S).tar.gz"
        archive_data "$archive_path"
        info "Данные сохранены: $archive_path"
        echo ""
    fi

    # Удаление systemd
    remove_systemd

    # Удаление cron
    remove_crontab

    # Удаление скриптов
    remove_scripts

    # Удаление nginx
    remove_nginx

    # Удаление UFW правил
    remove_ufw_rules

    # Очистка Redis
    remove_redis_keys

    # Удаление БД и пользователя (только для полного)
    if [[ "$mode" == "full" ]]; then
        remove_database
        remove_user
        remove_directories
        rm -rf "$BACKUP_DIR"
    else
        remove_directories
    fi

    # Итог
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  ДЕИНСТАЛЛЯЦИЯ ЗАВЕРШЕНА${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    if [[ "$mode" == "keep" ]]; then
        info "Данные сохранены в архив (см. выше)."
        info "Для полного удаления: удалите архив и БД вручную."
    else
        info "Все данные удалены."
    fi
    echo ""
    log "Деинсталляция завершена (режим: $mode)"
}

main