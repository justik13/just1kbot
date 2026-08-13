#!/bin/bash
# =============================================================================
# JUST1KBOT - Консоль управления и администрирования
# =============================================================================
#
# Использование:
#   just1kbot               - Открыть интерактивное меню
#   just1kbot status        - Проверить статус всех сервисов
#   just1kbot logs [name]   - Просмотр логов (по умолчанию: bot)
#   just1kbot update        - Безопасное обновление (бэкап + git pull + rebuild)
#   just1kbot backup        - Создать резервную копию базы данных
#   just1kbot restore [f]   - Восстановить базу данных из бэкапа
#   just1kbot restart [srv] - Перезапустить бота или указанный сервис
#   just1kbot start         - Запустить все контейнеры
#   just1kbot stop          - Остановить сервисы
#   just1kbot doctor        - Диагностика сети, SSL, портов и Telegram API
#   just1kbot clean         - Очистить старые слои Docker
#
# =============================================================================

set -euo pipefail

# Определение рабочей директории проекта
if [[ -n "${JUST1KBOT_DIR:-}" ]] && [[ -f "${JUST1KBOT_DIR}/docker-compose.yml" ]]; then
    PROJECT_DIR="${JUST1KBOT_DIR}"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${SCRIPT_DIR}/../docker-compose.yml" ]]; then
        PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
    else
        PROJECT_DIR="$(pwd)"
    fi
fi

if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    echo -e "\033[0;31m[✗] Ошибка: Не удалось найти проект Just1kBot (отсутствует docker-compose.yml в ${PROJECT_DIR}).\033[0m" >&2
    exit 1
fi

cd "$PROJECT_DIR"

# Цветовая палитра
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[+]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!]${NC} $1"
}

error() {
    echo -e "${RED}[✗]${NC} $1"
}

info() {
    echo -e "${CYAN}[i]${NC} $1"
}

# --- 1. Статус системы ---
cmd_status() {
    echo -e "\n${BOLD}${BLUE}=== 📊 СТАТУС СЕРВИСОВ JUST1KBOT ===${NC}\n"
    docker compose ps

    echo -e "\n${BOLD}${BLUE}=== 💻 ИСПОЛЬЗОВАНИЕ РЕСУРСОВ ===${NC}\n"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}" | grep -E "NAME|just1kbot" || true

    echo -e "\n${BOLD}${BLUE}=== 💾 РЕЗЕРВНЫЕ КОПИИ ===${NC}\n"
    local backup_count
    backup_count=$(find backups/ -maxdepth 1 -name "*.sql.gz.age" 2>/dev/null | wc -l || echo 0)
    if [ "$backup_count" -gt 0 ]; then
        log "Найдено активных бэкапов: ${BOLD}${backup_count}${NC}"
        # shellcheck disable=SC2012
        ls -lh backups/*.sql.gz.age 2>/dev/null | tail -5 | awk '{print "   • " $9 " (" $5 ", " $6 " " $7 " " $8 ")"}'
    else
        warn "Локальных бэкапов в папке ./backups/ пока нет."
    fi
    echo ""
}

# --- 2. Просмотр логов ---
cmd_logs() {
    local service="${1:-bot}"
    echo -e "\n${BOLD}${BLUE}=== 📜 ЛОГИ СЕРВИСА: ${service} (Ctrl+C для выхода) ===${NC}\n"

    if [[ "$service" == "all" ]]; then
        docker compose logs -f --tail=100
    else
        docker compose logs -f --tail=100 "$service"
    fi
}

# --- 3. Безопасное обновление ---
cmd_update() {
    echo -e "\n${BOLD}${BLUE}=== 🔄 БЕЗОПАСНОЕ ОБНОВЛЕНИЕ JUST1KBOT ===${NC}\n"

    info "Шаг 1/4. Создание страховочной резервной копии базы данных..."
    cmd_backup

    info "Шаг 2/4. Получение свежих обновлений из Git (origin/main)..."
    git fetch origin
    git pull origin main

    info "Шаг 3/4. Пересборка и запуск обновленных контейнеров..."
    docker compose up -d --build

    info "Шаг 4/4. Проверка состояния сервисов..."
    sleep 3
    docker compose ps

    echo ""
    log "Обновление завершено! Просмотр свежих логов бота:"
    echo -e "${CYAN}Нажмите Ctrl+C для возврата в меню...${NC}\n"
    sleep 2
    docker compose logs -f --tail=30 bot || true
}

# --- 4. Создание бэкапа ---
cmd_backup() {
    echo -e "\n${BOLD}${BLUE}=== 💾 СОЗДАНИЕ ЗАШИФРОВАННОГО БЭКАПА БД ===${NC}\n"
    mkdir -p backups

    if docker compose --profile tools run --rm backup; then
        local latest_backup
        latest_backup=$(find backups/ -maxdepth 1 -name "*.sql.gz.age" -type f -printf '%T@ %p\n' 2>/dev/null | sort -k 1nr | head -1 | cut -d' ' -f2-)
        if [[ -n "$latest_backup" ]]; then
            log "Бэкап успешно создан: ${BOLD}${latest_backup}${NC}"
            ls -lh "$latest_backup" | awk '{print "Размер: " $5 ", Создан: " $6 " " $7 " " $8}'
        fi
    else
        error "Ошибка при создании бэкапа базы данных."
        return 1
    fi
}

# --- 5. Восстановление из бэкапа ---
cmd_restore() {
    echo -e "\n${BOLD}${RED}=== ⚠️ ВОССТАНОВЛЕНИЕ БАЗЫ ДАННЫХ ИЗ БЭКАПА ===${NC}\n"

    warn "ВНИМАНИЕ: Восстановление базы данных полностью перезапишет текущие данные в PostgreSQL!"
    read -r -p "Вы действительно хотите продолжить? (yes/N): " confirm_restore
    if [[ "$confirm_restore" != "yes" ]]; then
        info "Восстановление отменено."
        return 0
    fi

    # Поиск доступных бэкапов
    local backups_list=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && backups_list+=("$f")
    done < <(find backups/ -maxdepth 1 -name "*.sql.gz.age" 2>/dev/null | sort -r)

    if [ "${#backups_list[@]}" -eq 0 ]; then
        error "В директории ./backups/ не найдено файлов .sql.gz.age для восстановления."
        return 1
    fi

    echo -e "\nДоступные резервные копии:"
    local i=1
    for b in "${backups_list[@]}"; do
        local sz
        sz=$(ls -lh "$b" | awk '{print $5}')
        echo -e "  [${BOLD}$i${NC}] $b ($sz)"
        ((i++))
    done

    echo ""
    read -r -p "Выберите номер файла для восстановления [1-${#backups_list[@]}]: " choice_idx
    if ! [[ "$choice_idx" =~ ^[0-9]+$ ]] || [ "$choice_idx" -lt 1 ] || [ "$choice_idx" -gt "${#backups_list[@]}" ]; then
        error "Неверный выбор."
        return 1
    fi

    local selected_backup="${backups_list[$((choice_idx-1))]}"
    info "Выбран файл: $selected_backup"

    # Запрос приватного ключа age
    local age_key_file=""
    if [[ -f "backup_private_key.txt" ]]; then
        info "Обнаружен локальный ключ: backup_private_key.txt"
        age_key_file="backup_private_key.txt"
    elif [[ -f "/root/just1kbot_backup_private_key.txt" ]]; then
        info "Обнаружен системный ключ: /root/just1kbot_backup_private_key.txt"
        age_key_file="/root/just1kbot_backup_private_key.txt"
    fi

    if [[ -z "$age_key_file" ]]; then
        read -r -p "Укажите путь к файлу с приватным age-ключом: " custom_key_path
        if [[ -f "$custom_key_path" ]]; then
            age_key_file="$custom_key_path"
        else
            error "Файл ключа '$custom_key_path' не найден."
            return 1
        fi
    fi

    # Временные файлы расшифровки
    local tmp_decrypted="/tmp/restore_$(date +%s).sql"
    local tmp_gz="/tmp/restore_$(date +%s).sql.gz"

    info "Расшифровка резервной копии утилитой age..."
    if ! age -d -i "$age_key_file" "$selected_backup" > "$tmp_gz"; then
        rm -f "$tmp_gz"
        error "Не удалось расшифровать бэкап. Проверьте правильность приватного age ключа."
        return 1
    fi

    info "Распаковка архива gzip..."
    gunzip -c "$tmp_gz" > "$tmp_decrypted"
    rm -f "$tmp_gz"

    info "Остановка бота перед накатом дампа..."
    docker compose stop bot

    info "Копирование дампа в PostgreSQL контейнер..."
    docker cp "$tmp_decrypted" just1kbot_db:/tmp/restore.sql
    rm -f "$tmp_decrypted"

    info "Применение SQL дампа в базу данных..."
    # shellcheck disable=SC2016
    docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/restore.sql'

    docker compose exec -T db rm -f /tmp/restore.sql

    info "Запуск контейнера бота..."
    docker compose start bot

    log "База данных успешно восстановлена из $selected_backup!"
}

# --- 6. Управление питанием сервисов ---
cmd_restart() {
    local service="${1:-bot}"
    echo -e "\n${BOLD}${BLUE}=== ⚡ ПЕРЕЗАПУСК СЕРВИСА: ${service} ===${NC}\n"
    if [[ "$service" == "all" ]]; then
        docker compose restart
    else
        docker compose restart "$service"
    fi
    log "Сервис $service перезапущен."
}

cmd_start() {
    echo -e "\n${BOLD}${BLUE}=== 🚀 ЗАПУСК ВСЕХ СЕРВИСОВ ===${NC}\n"
    docker compose up -d
    log "Сервисы запущены."
}

cmd_stop() {
    echo -e "\n${BOLD}${YELLOW}=== 🛑 ОСТАНОВКА СЕРВИСОВ ===${NC}\n"
    docker compose stop
    log "Сервисы остановлены."
}

# --- 7. Конфигурация (.env) ---
cmd_config() {
    echo -e "\n${BOLD}${BLUE}=== 🔑 РЕДАКТИРОВАНИЕ КОНФИГУРАЦИИ (.env) ===${NC}\n"
    local editor="${EDITOR:-nano}"
    if ! command -v "$editor" >/dev/null 2>&1; then
        editor="nano"
    fi
    "$editor" "${PROJECT_DIR}/.env"

    echo ""
    read -r -p "Перезапустить бота для применения изменений? (y/N): " apply_restart
    if [[ "$apply_restart" =~ ^[Yy]$ ]]; then
        docker compose restart bot
        log "Бот перезапущен с новыми параметрами."
    fi
}

# --- 8. Доктор (Диагностика) ---
cmd_doctor() {
    echo -e "\n${BOLD}${BLUE}=== 🩺 ДИАГНОСТИКА СИСТЕМЫ JUST1KBOT ===${NC}\n"

    # 1. Docker daemon
    if docker info >/dev/null 2>&1; then
        log "Docker демон активен и отвечает."
    else
        error "Docker демон недоступен!"
    fi

    # 2. Порты
    for port in 80 443; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} " || netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
            log "Порт $port слушается веб-сервером (Caddy)."
        else
            warn "Порт $port не слушается. Проверьте статус контейнера Caddy."
        fi
    done

    # 3. Чтение .env
    if [[ -f "${PROJECT_DIR}/.env" ]]; then
        local domain
        domain=$(grep -E "^DOMAIN=" "${PROJECT_DIR}/.env" | cut -d'=' -f2 | tr -d " '\"")
        local bot_token
        bot_token=$(grep -E "^BOT_TOKEN=" "${PROJECT_DIR}/.env" | cut -d'=' -f2 | tr -d " '\"")

        # 4. Проверка DNS
        if [[ -n "$domain" ]]; then
            local dom_ip
            dom_ip=$(dig +short @8.8.8.8 "$domain" A 2>/dev/null | tail -1 || true)
            local srv_ip
            srv_ip=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
            if [[ "$dom_ip" == "$srv_ip" ]]; then
                log "DNS резолвинг домена: $domain -> $srv_ip (OK)"
            else
                warn "DNS домена: $domain указывает на '$dom_ip', IP сервера: '$srv_ip'"
            fi
        fi

        # 5. Проверка Telegram Bot API
        if [[ -n "$bot_token" ]]; then
            local me_resp
            me_resp=$(curl -s --max-time 5 "https://api.telegram.org/bot${bot_token}/getMe" || true)
            if echo "$me_resp" | grep -q '"ok":true'; then
                local b_user
                b_user=$(echo "$me_resp" | grep -o '"username":"[^"]*' | cut -d'"' -f4)
                log "Связь с Telegram Bot API: @${b_user} (OK)"
            else
                error "Связь с Telegram Bot API нарушена (неверный токен или блокировка API)."
            fi
        fi
    fi

    # 6. Проверка здоровья контейнеров
    echo ""
    docker compose ps
    echo ""
}

# --- 9. Очистка диска ---
cmd_clean() {
    echo -e "\n${BOLD}${BLUE}=== 🧹 ОЧИСТКА СТАРЫХ ОБРАЗОВ DOCKER ===${NC}\n"
    docker image prune -f
    log "Неиспользуемые образы и слои Docker успешно удалены."
}

# --- Интерактивное меню ---
interactive_menu() {
    while true; do
        clear
        echo -e "${BOLD}${BLUE}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${BOLD}${BLUE}║                         🚀 JUST1KBOT CONTROL PANEL                           ║${NC}"
        echo -e "${BOLD}${BLUE}║                     Консоль управления Telegram-ботом                        ║${NC}"
        echo -e "${BOLD}${BLUE}╚══════════════════════════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "  [${BOLD}1${NC}] 📊 ${BOLD}Статус системы${NC} (Healthcheck контейнеров, RAM/CPU, бэкапы)"
        echo -e "  [${BOLD}2${NC}] 📜 ${BOLD}Просмотр логов${NC} (Live stream: Bot, Caddy, Postgres, Redis)"
        echo -e "  [${BOLD}3${NC}] 🔄 ${BOLD}Безопасное обновление${NC} (Auto-backup -> Git pull -> Rebuild)"
        echo -e "  [${BOLD}4${NC}] 💾 ${BOLD}Управление бэкапами${NC} (Создать бэкап сейчас / Восстановить БД)"
        echo -e "  [${BOLD}5${NC}] ⚡ ${BOLD}Перезапуск сервисов${NC} (Restart Bot / Restart All)"
        echo -e "  [${BOLD}6${NC}] 🔑 ${BOLD}Конфигурация${NC} (Редактировать .env файл)"
        echo -e "  [${BOLD}7${NC}] 🩺 ${BOLD}Диагностика (Doctor)${NC} (Проверка DNS, SSL, портов и Telegram API)"
        echo -e "  [${BOLD}8${NC}] 🧹 ${BOLD}Очистить дисковый кэш${NC} (Docker image prune)"
        echo -e "  [${BOLD}0${NC}] ❌ ${BOLD}Выход${NC}"
        echo ""
        echo -e "${CYAN}────────────────────────────────────────────────────────────────────────────────${NC}"
        read -r -p "Выберите действие [0-8]: " choice

        case "$choice" in
            1)
                cmd_status
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            2)
                echo -e "\nВыберите сервис для просмотра логов:"
                echo "  [1] Бот (bot) [По умолчанию]"
                echo "  [2] Caddy (caddy)"
                echo "  [3] PostgreSQL (db)"
                echo "  [4] Redis (redis)"
                echo "  [5] Все сервисы одновременно"
                read -r -p "Сервис [1-5]: " srv_choice
                case "$srv_choice" in
                    2) cmd_logs "caddy" ;;
                    3) cmd_logs "db" ;;
                    4) cmd_logs "redis" ;;
                    5) cmd_logs "all" ;;
                    *) cmd_logs "bot" ;;
                esac
                ;;
            3)
                cmd_update
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            4)
                echo -e "\n[1] Создать новый зашифрованный бэкап"
                echo "[2] Восстановить базу данных из бэкапа"
                read -r -p "Выберите [1-2]: " b_action
                if [[ "$b_action" == "2" ]]; then
                    cmd_restore
                else
                    cmd_backup
                fi
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            5)
                echo -e "\n[1] Перезапустить только бота (bot)"
                echo "[2] Перезапустить все контейнеры (all)"
                echo "[3] Остановить проект"
                echo "[4] Запустить проект"
                read -r -p "Выберите [1-4]: " p_action
                case "$p_action" in
                    2) cmd_restart "all" ;;
                    3) cmd_stop ;;
                    4) cmd_start ;;
                    *) cmd_restart "bot" ;;
                esac
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            6)
                cmd_config
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            7)
                cmd_doctor
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            8)
                cmd_clean
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            0|q|exit)
                echo -e "\n${GREEN}До свидания!${NC}\n"
                exit 0
                ;;
            *)
                warn "Неверный выбор."
                sleep 1
                ;;
        esac
    done
}

# --- Главный диспетчер аргументов ---
main() {
    if [[ $# -eq 0 ]]; then
        interactive_menu
    else
        case "$1" in
            status|ps)
                cmd_status
                ;;
            logs|log)
                cmd_logs "${2:-bot}"
                ;;
            update|pull)
                cmd_update
                ;;
            backup)
                cmd_backup
                ;;
            restore)
                cmd_restore
                ;;
            restart)
                cmd_restart "${2:-bot}"
                ;;
            start|up)
                cmd_start
                ;;
            stop|down)
                cmd_stop
                ;;
            config|env)
                cmd_config
                ;;
            doctor|check)
                cmd_doctor
                ;;
            clean|prune)
                cmd_clean
                ;;
            help|-h|--help)
                echo -e "Использование: just1kbot [status|logs|update|backup|restore|restart|start|stop|doctor|clean]"
                ;;
            *)
                error "Неизвестная команда: $1. Используйте 'just1kbot help'."
                ;;
        esac
    fi
}

main "$@"
