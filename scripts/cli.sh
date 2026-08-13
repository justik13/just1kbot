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

    mkdir -p backups
    local backup_count
    backup_count=$(find backups/ -maxdepth 1 -name "*.sql.gz.age" 2>/dev/null | wc -l | tr -d '[:space:]')
    if [[ "$backup_count" =~ ^[0-9]+$ ]] && [ "$backup_count" -gt 0 ]; then
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

    # Запуск логов в subshell для перехвата SIGINT без прерывания скрипта
    (
        trap 'echo ""' INT
        if [[ "$service" == "all" ]]; then
            docker compose logs -f --tail=100 || true
        else
            docker compose logs -f --tail=100 "$service" || true
        fi
    )
}

# --- 3. Безопасное обновление ---
cmd_update() {
    echo -e "\n${BOLD}${BLUE}=== 🔄 БЕЗОПАСНОЕ ОБНОВЛЕНИЕ JUST1KBOT ===${NC}\n"

    # Проверка наличия локальных незакоммиченных изменений
    local dirty_changes
    dirty_changes=$(git status --porcelain 2>/dev/null || true)
    if [[ -n "$dirty_changes" ]]; then
        warn "Обнаружены незакоммиченные локальные изменения:"
        git status -s
        echo ""
        read -r -p "Временно спрятать изменения (git stash) и продолжить обновление? (y/N): " stash_confirm
        if [[ "$stash_confirm" =~ ^[Yy]$ ]]; then
            git stash
            log "Локальные изменения сохранены в git stash."
        else
            info "Обновление отменено пользователем."
            return 0
        fi
    fi

    info "Шаг 1/5. Создание страховочного бэкапа базы данных..."
    cmd_backup

    local current_branch
    current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    info "Шаг 2/5. Получение обновлений из Git (ветка: $current_branch)..."
    git fetch origin "$current_branch"
    git pull origin "$current_branch"

    info "Шаг 3/5. Пересборка и запуск контейнеров..."
    docker compose up -d --build

    info "Шаг 4/5. Проверка статуса здоровья сервисов (Healthcheck)..."
    local timeout=60
    local elapsed=0
    local update_ok=false

    while [ "$elapsed" -lt "$timeout" ]; do
        local db_h
        local redis_h
        local bot_h
        local caddy_s
        local migrate_s

        db_h="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_db 2>/dev/null || echo starting)"
        redis_h="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_redis 2>/dev/null || echo starting)"
        bot_h="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_app 2>/dev/null || echo starting)"
        caddy_s="$(docker inspect --format='{{.State.Status}}' just1kbot_caddy 2>/dev/null || echo starting)"
        migrate_s="$(docker inspect --format='{{.State.Status}}/{{.State.ExitCode}}' just1kbot_migrate 2>/dev/null || echo missing)"

        if [[ "$migrate_s" == "exited/"* ]] && [[ "$migrate_s" != "exited/0" ]]; then
            echo ""
            error "Ошибка миграции базы данных после обновления: $migrate_s"
            docker compose logs migrate
            return 1
        fi

        if [ "$db_h" = "healthy" ] && [ "$redis_h" = "healthy" ] && [ "$bot_h" = "healthy" ] && [ "$caddy_s" = "running" ]; then
            update_ok=true
            break
        fi

        printf "."
        sleep 2
        elapsed=$((elapsed + 2))
    done

    echo ""
    if [ "$update_ok" = "true" ]; then
        log "Все сервисы успешно обновлены и работают (Healthy)!"
    else
        warn "Таймаут ожидания статуса healthy. Проверьте состояние:"
        docker compose ps
    fi

    info "Шаг 5/5. Просмотр последних логов бота:"
    echo -e "${CYAN}Нажмите Ctrl+C для возврата в меню...${NC}\n"
    cmd_logs "bot"
}

# --- 4. Создание бэкапа ---
cmd_backup() {
    echo -e "\n${BOLD}${BLUE}=== 💾 СОЗДАНИЕ ЗАШИФРОВАННОГО БЭКАПА БД ===${NC}\n"
    mkdir -p backups

    # Способ 1: Прямой дамп из работающего контейнера db + шифрование age (быстро и надежно)
    if command -v age >/dev/null 2>&1 && [[ -f "${PROJECT_DIR}/.env" ]]; then
        local age_recipient
        age_recipient=$(grep -E "^BACKUP_AGE_RECIPIENT=" "${PROJECT_DIR}/.env" | cut -d'=' -f2 | tr -d " '\"")
        if [[ -n "$age_recipient" ]]; then
            info "Создание зашифрованного дампа PostgreSQL..."
            local ts
            ts=$(date +%Y%m%d_%H%M%S)
            local backup_file="backups/just1kbot_${ts}.sql.gz.age"
            local tmp_gz="/tmp/backup_${ts}.sql.gz"

            if docker compose exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>/dev/null | gzip > "$tmp_gz"; then
                if age -r "$age_recipient" -o "$backup_file" "$tmp_gz" 2>/dev/null; then
                    rm -f "$tmp_gz"
                    log "Бэкап успешно создан: ${BOLD}${backup_file}${NC}"
                    ls -lh "$backup_file" | awk '{print "Размер: " $5 ", Создан: " $6 " " $7 " " $8}'
                    return 0
                fi
            fi
            rm -f "$tmp_gz"
        fi
    fi

    # Способ 2: Через отдельный контейнер backup (compose profile tools)
    if docker compose --profile tools run --rm backup; then
        local latest_backup
        # shellcheck disable=SC2012
        latest_backup=$(ls -t backups/*.sql.gz.age 2>/dev/null | head -1 || true)
        if [[ -n "$latest_backup" ]]; then
            log "Бэкап успешно создан: ${BOLD}${latest_backup}${NC}"
            ls -lh "$latest_backup" | awk '{print "Размер: " $5 ", Создан: " $6 " " $7 " " $8}'
            return 0
        fi
    fi

    error "Ошибка при создании бэкапа базы данных."
    return 1
}

# --- 5. Безопасное восстановление из бэкапа ---
cmd_restore() {
    echo -e "\n${BOLD}${RED}╔══════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${RED}║                 ⚠️  ВНИМАНИЕ: ВОССТАНОВЛЕНИЕ БАЗЫ ДАННЫХ                    ║${NC}"
    echo -e "${BOLD}${RED}╠══════════════════════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${BOLD}${RED}║ Данная операция ПОЛНОСТЬЮ УДАЛИТ текущую базу данных и перезапишет её        ║${NC}"
    echo -e "${BOLD}${RED}║ данными из выбранной резервной копии!                                        ║${NC}"
    echo -e "${BOLD}${RED}╚══════════════════════════════════════════════════════════════════════════════╝${NC}\n"

    read -r -p "Для подтверждения введите слово 'RESTORE' заглавными буквами: " confirm_code
    if [[ "$confirm_code" != "RESTORE" ]]; then
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
    info "Выбран файл для восстановления: $selected_backup"

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
        read -r -p "Укажите путь к файлу с приватным age-ключом (например /root/key.txt): " custom_key_path
        if [[ -f "$custom_key_path" ]]; then
            age_key_file="$custom_key_path"
        else
            error "Файл ключа '$custom_key_path' не найден."
            return 1
        fi
    fi

    # Изолированная временная директория с гарантированной очисткой
    local tmp_dir
    tmp_dir=$(mktemp -d -t just1kbot-restore-XXXXXX)
    local tmp_gz="${tmp_dir}/dump.sql.gz"
    local tmp_sql="${tmp_dir}/dump.sql"

    # Гарантируем запуск бота и удаление временных файлов при любых ошибках
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp_dir'; docker compose start bot >/dev/null 2>&1 || true" EXIT INT TERM

    info "1/5. Расшифровка бэкапа ключом age..."
    if ! age -d -i "$age_key_file" "$selected_backup" > "$tmp_gz"; then
        error "Ошибка расшифровки: неверный приватный ключ age."
        return 1
    fi

    info "2/5. Распаковка архива gzip..."
    gunzip -c "$tmp_gz" > "$tmp_sql"
    rm -f "$tmp_gz"

    info "3/5. Остановка бота перед восстановлением..."
    docker compose stop bot

    info "4/5. Полная переинициализация базы данных и накат дампа..."
    # Очищаем целевую БД и создаем заново для предотвращения конфликтов схем/таблиц
    local pg_user="${POSTGRES_USER:-just1kbot}"
    local pg_db="${POSTGRES_DB:-just1kbot_bot}"

    docker compose exec -T db dropdb -U "$pg_user" --if-exists "$pg_db" >/dev/null 2>&1 || true
    docker compose exec -T db createdb -U "$pg_user" "$pg_db"

    docker cp "$tmp_sql" just1kbot_db:/tmp/restore.sql
    rm -f "$tmp_sql"

    if ! docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f /tmp/restore.sql'; then
        docker compose exec -T db rm -f /tmp/restore.sql 2>/dev/null || true
        error "Ошибка при накате SQL дампа в PostgreSQL!"
        return 1
    fi

    docker compose exec -T db rm -f /tmp/restore.sql 2>/dev/null || true

    info "5/5. Запуск контейнера бота..."
    docker compose start bot

    log "База данных успешно восстановлена из $selected_backup!"
}

# --- 6. Управление сервисами ---
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
    read -r -p "Применить изменения и пересоздать контейнеры (docker compose up -d --force-recreate)? (Y/n): " apply_restart
    if [[ ! "$apply_restart" =~ ^[Nn]$ ]]; then
        info "Пересоздание контейнеров с новыми переменными окружения..."
        docker compose up -d --force-recreate
        log "Конфигурация успешно применена ко всем контейнерам."
    fi
}

# --- 8. Доктор (Диагностика) ---
cmd_doctor() {
    echo -e "\n${BOLD}${BLUE}=== 🩺 ДИАГНОСТИКА СИСТЕМЫ JUST1KBOT ===${NC}\n"

    # 1. Docker демон и сокет
    if docker info >/dev/null 2>&1; then
        log "Docker демон активен и отвечает."
    else
        error "Docker демон недоступен (проверьте права пользователя или 'systemctl status docker')!"
    fi

    # 2. Прослушивание портов
    for port in 80 443; do
        if ss -tlnp 2>/dev/null | grep -q ":${port} " || netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
            log "Порт $port слушается веб-сервером (Caddy)."
        else
            warn "Порт $port не слушается. Проверьте статус контейнера Caddy."
        fi
    done

    # 3. Чтение .env параметров
    if [[ -f "${PROJECT_DIR}/.env" ]]; then
        local domain
        domain=$(grep -E "^DOMAIN=" "${PROJECT_DIR}/.env" | cut -d'=' -f2 | tr -d " '\"")
        local bot_token
        bot_token=$(grep -E "^BOT_TOKEN=" "${PROJECT_DIR}/.env" | cut -d'=' -f2 | tr -d " '\"")

        # 4. Проверка DNS домена
        if [[ -n "$domain" ]]; then
            local dom_ip
            dom_ip=$(dig +short @8.8.8.8 "$domain" A 2>/dev/null | tail -1 || true)
            local srv_ip
            srv_ip=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
            if [[ -n "$dom_ip" ]] && [[ "$dom_ip" == "$srv_ip" ]]; then
                log "DNS резолвинг домена: $domain -> $srv_ip (OK)"
            else
                warn "DNS домена: $domain указывает на '$dom_ip', IP сервера: '$srv_ip'"
            fi
        fi

        # 5. Проверка Telegram API
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
        echo -e "  [${BOLD}6${NC}] 🔑 ${BOLD}Конфигурация${NC} (Редактировать .env файл с reload)"
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
