#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

UNINSTALL_LOG="/var/log/just1kbot-uninstall.log"
TEMP_FILES=()

log() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$UNINSTALL_LOG"; }
success() { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$UNINSTALL_LOG"; }
warn() { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$UNINSTALL_LOG"; }
error() { echo -e "${RED}[✗]${NC} $1" | tee -a "$UNINSTALL_LOG"; exit 1; }

cleanup() {
    for f in "${TEMP_FILES[@]}"; do rm -f "$f" 2>/dev/null; done
}
trap cleanup EXIT INT TERM

if [[ $EUID -ne 0 ]]; then error "Запустите с правами root (sudo)."; fi
mkdir -p /var/log
echo "=== Uninstall started: $(date) ===" > "$UNINSTALL_LOG"

log "Остановка сервиса..."
if systemctl is-active --quiet just1kbot-bot; then
    systemctl stop just1kbot-bot
    systemctl disable just1kbot-bot
    success "Сервис остановлен"
fi

log "Сканирование рабочей директории..."
PROJECT_DIR=$(systemctl show -p WorkingDirectory just1kbot-bot 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "[not set]" ]]; then
    PROJECT_DIR="/opt/just1kbot-bot"
fi

if [[ -n "$PROJECT_DIR" ]]; then
    PROJECT_DIR=$(readlink -f "$PROJECT_DIR")
fi

if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "/" || "$PROJECT_DIR" == "/opt" || "$PROJECT_DIR" == "/usr" || "$PROJECT_DIR" == "/root" || "$PROJECT_DIR" == "/home" || "$PROJECT_DIR" == "/etc" || "$PROJECT_DIR" == "/var" || "$PROJECT_DIR" == "/tmp" ]]; then
    error "Обнаружен небезопасный путь: '$PROJECT_DIR'. Прерывание."
fi

if [[ ! "$PROJECT_DIR" =~ just1kbot ]]; then
    error "Путь '$PROJECT_DIR' не содержит 'just1kbot'. Прерывание."
fi

success "Целевая директория: $PROJECT_DIR"

echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
echo -e "1) ${RED}Полное очищение${NC} (удалить ВСЁ)"
echo -e "2) ${GREEN}Удаление с сохранением данных${NC} (БД и .env в архив)"
echo -e "3) Отмена"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
read -p "Выберите вариант [1-3]: " choice

case $choice in
    1)
        read -p "⚠️ ${RED}ВНИМАНИЕ!${NC} Удалить ВСЁ безвозвратно? (yes/no): " confirm
        if [[ "$(echo "$confirm" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" != "yes" ]]; then
            success "Отменено"; exit 0
        fi

        log "Принудительный разрыв сессий PostgreSQL..."
        sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='just1kbot_bot' AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true
        
        log "Удаление БД и пользователя..."
        sudo -u postgres psql -c "DROP DATABASE IF EXISTS just1kbot_bot;" > /dev/null 2>&1 || warn "Не удалось удалить БД"
        sudo -u postgres psql -c "DROP USER IF EXISTS just1kbot;" > /dev/null 2>&1 || warn "Не удалось удалить юзера"
        success "PostgreSQL очищен"

        log "Тотальное удаление файлов..."
        if [[ -d "$PROJECT_DIR" ]]; then rm -rf "$PROJECT_DIR"; success "Папка проекта удалена"; fi
        if [[ -d "/root/backups/just1kbot" ]]; then rm -rf "/root/backups/just1kbot"; success "Бэкапы удалены"; fi
        ;;
    2)
        log "Создание безопасного архива..."
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        SAFE_BACKUP_DIR="/root/just1kbot-backup-$TIMESTAMP"
        mkdir -p "$SAFE_BACKUP_DIR"

        sudo -u postgres pg_dump -Fc just1kbot_bot > "$SAFE_BACKUP_DIR/just1kbot_db.dump" 2>/dev/null && success "БД сохранена" || warn "Дамп БД не удался"
        if [[ -f "$PROJECT_DIR/.env" ]]; then cp "$PROJECT_DIR/.env" "$SAFE_BACKUP_DIR/"; success ".env сохранён"; fi
        
        rm -rf "$PROJECT_DIR"
        success "Папка очищена. Архив в: $SAFE_BACKUP_DIR"
        ;;
    3) success "Отменено"; exit 0 ;;
    *) error "Некорректный выбор" ;;
esac

log "Очистка системных конфигураций..."
rm -f /etc/nginx/sites-enabled/just1kbot /etc/nginx/sites-available/just1kbot
systemctl reload nginx 2>/dev/null || true

rm -f /etc/systemd/system/just1kbot-bot.service
systemctl daemon-reload

if crontab -l >/dev/null 2>&1; then
    CRONTAB_TMP=$(mktemp)
    TEMP_FILES+=("$CRONTAB_TMP")
    crontab -l | grep -v "just1kbot-" > "$CRONTAB_TMP" || true
    if [ -s "$CRONTAB_TMP" ]; then crontab "$CRONTAB_TMP"; else crontab -r || true; fi
fi

rm -f /usr/local/bin/just1kbot-backup.sh /usr/local/bin/just1kbot-healthcheck.sh
rm -f /var/log/just1kbot-*.log 2>/dev/null

if id "just1kbot" &>/dev/null; then
    pkill -u just1kbot 2>/dev/null || true
    sleep 1
    userdel just1kbot 2>/dev/null || true
    groupdel just1kbot 2>/dev/null || true
    success "Пользователь just1kbot удалён"
fi

echo ""
success "✨ Деинсталляция завершена!"
if [[ "$choice" == "2" ]]; then
    echo -e "${GREEN}📦 Архив данных: ${BLUE}$SAFE_BACKUP_DIR${NC}"
fi