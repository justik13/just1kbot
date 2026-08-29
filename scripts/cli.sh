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
PROJECT_DIR=""

# 1. Проверяем переменную окружения JUST1KBOT_DIR
if [[ -n "${JUST1KBOT_DIR:-}" ]] && [[ -f "${JUST1KBOT_DIR}/docker-compose.yml" ]]; then
    PROJECT_DIR="${JUST1KBOT_DIR}"
fi

# 2. Если не найдено, определяем реальный путь к скрипту с раскрытием всех симлинков
if [[ -z "$PROJECT_DIR" ]]; then
    SOURCE="${BASH_SOURCE[0]}"
    max_links=20
    while [ -h "$SOURCE" ] && [ "$max_links" -gt 0 ]; do
        DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
        SOURCE="$(readlink "$SOURCE")"
        [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
        ((max_links--))
    done
    SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

    if [[ -f "${SCRIPT_DIR}/../docker-compose.yml" ]]; then
        PROJECT_DIR="$(cd -P "${SCRIPT_DIR}/.." && pwd)"
    elif [[ -f "${SCRIPT_DIR}/docker-compose.yml" ]]; then
        PROJECT_DIR="${SCRIPT_DIR}"
    fi
fi

# 3. Если все еще не найдено, проверяем стандартные директории установки
if [[ -z "$PROJECT_DIR" ]]; then
    for candidate in /opt/just1kbot /root/just1kbot /home/*/just1kbot /var/www/just1kbot "$(pwd)"; do
        if [[ -d "$candidate" ]] && [[ -f "${candidate}/docker-compose.yml" ]]; then
            PROJECT_DIR="$(cd -P "$candidate" && pwd)"
            break
        fi
    done
fi

if [[ -z "$PROJECT_DIR" ]] || [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    echo -e "\033[0;31m[✗] Ошибка: Не удалось найти проект Just1kBot (отсутствует docker-compose.yml в ${PROJECT_DIR:-$(pwd)}).\033[0m" >&2
    echo -e "\033[0;33m[!] Убедитесь, что проект установлен в /opt/just1kbot, /root/just1kbot или задайте переменную JUST1KBOT_DIR=/путь/к/проекту\033[0m" >&2
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

# --- 2.5. Предварительная проверка конфигурации (Pre-flight Check) ---
cmd_preflight() {
    echo -e "\n${BOLD}${BLUE}=== 🔍 ПРЕДВАРИТЕЛЬНАЯ ПРОВЕРКА (PRE-FLIGHT CHECK) ===${NC}\n"
    local has_errors=false

    # 1. Проверка наличия .env
    if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
        error "Файл конфигурации ${PROJECT_DIR}/.env не найден!"
        return 1
    fi

    # 2. Проверка прав .env (строго 600)
    local env_perms
    env_perms=$(stat -c "%a" "${PROJECT_DIR}/.env" 2>/dev/null || stat -f "%Lp" "${PROJECT_DIR}/.env" 2>/dev/null || echo "")
    if [[ "$env_perms" != "600" ]]; then
        warn "Файл .env имеет права ($env_perms). Устанавливаем строго 600: chmod 600 ${PROJECT_DIR}/.env"
        if ! chmod 600 "${PROJECT_DIR}/.env" 2>/dev/null; then
            error "Не удалось применить chmod 600 к ${PROJECT_DIR}/.env!"
            has_errors=true
        else
            env_perms=$(stat -c "%a" "${PROJECT_DIR}/.env" 2>/dev/null || stat -f "%Lp" "${PROJECT_DIR}/.env" 2>/dev/null || echo "")
            if [[ "$env_perms" != "600" ]]; then
                error "Права файла ${PROJECT_DIR}/.env ($env_perms) не равны 600!"
                has_errors=true
            fi
        fi
    fi

    # 2.1. Проверка отсутствия дублирующихся переменных в .env
    local duplicate_keys
    duplicate_keys=$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f1 | sort | uniq -d | tr '\n' ' ' || true)
    if [[ -n "${duplicate_keys// }" ]]; then
        error "Обнаружены дублирующиеся переменные в .env: $duplicate_keys"
        has_errors=true
    fi

    # 3. Обязательные переменные окружения
    local req_vars=(BOT_TOKEN POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB DB_ENCRYPTION_KEY BACKUP_AGE_RECIPIENT SUPPORT_USERNAME YOOKASSA_SHOP_ID YOOKASSA_SECRET_KEY DOMAIN SSL_EMAIL)
    for var_name in "${req_vars[@]}"; do
        local val
        val=$(grep -E "^${var_name}=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || true)
        if [[ -z "$val" ]]; then
            error "В .env отсутствует или пуста обязательная переменная: $var_name"
            has_errors=true
        fi
    done

    # 3.1. Проверка идентификаторов администратора (ADMIN_IDS или ADMIN_TELEGRAM_ID)
    local admin_raw
    admin_raw=$(grep -E "^(ADMIN_IDS|ADMIN_TELEGRAM_ID)=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"[]" || true)
    if [[ -z "$admin_raw" ]]; then
        error "В .env отсутствует обязательная переменная администратора: ADMIN_IDS='[123456789]'"
        has_errors=true
    else
        IFS=',' read -ra id_tokens <<< "$admin_raw"
        for token in "${id_tokens[@]}"; do
            token="$(echo "$token" | tr -d ' ')"
            if [[ -z "$token" ]] || [[ ! "$token" =~ ^[0-9]+$ ]] || [[ "$token" -le 0 ]]; then
                error "Некорректный ID администратора в .env: '$token'. Должен быть положительным целым числом (Telegram user ID)."
                has_errors=true
            fi
        done
    fi

    # 4. Проверка устаревших/неподдерживаемых переменных
    local legacy_vars=(AMNEZIA_API_URL AMNEZIA_API_KEY WEBHOOK_URL INCY_HOST INCY_API_KEY AMNEZIA_BRIDGE_HMAC_SECRET)
    for legacy_name in "${legacy_vars[@]}"; do
        if grep -Eq "^${legacy_name}=" "${PROJECT_DIR}/.env" 2>/dev/null; then
            warn "Обнаружена устаревшая переменная $legacy_name в .env. Она больше не поддерживается проектом."
        fi
    done

    # 5. Проверка формата SSL_EMAIL (если задан)
    local ssl_email
    ssl_email=$(grep -E "^SSL_EMAIL=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || true)
    if [[ -n "$ssl_email" ]] && [[ ! "$ssl_email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        error "Некорректный email для Let's Encrypt в SSL_EMAIL: '$ssl_email'"
        has_errors=true
    fi

    # 6. Проверка формата DOMAIN (если задан)
    local domain
    domain=$(grep -E "^DOMAIN=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || true)
    if [[ -n "$domain" ]] && [[ "$domain" =~ ^https?:// ]]; then
        error "DOMAIN не должен содержать протокол 'http://' или 'https://' (укажите только имя хоста, например: vpn.example.com)"
        has_errors=true
    fi

    # 7. Проверка доступности Docker daemon и Docker Compose
    if ! command -v docker >/dev/null 2>&1; then
        error "Docker CLI не установлен в системе!"
        has_errors=true
    elif ! docker info >/dev/null 2>&1; then
        error "Docker daemon недоступен или служба Docker не запущена!"
        has_errors=true
    elif ! docker compose version >/dev/null 2>&1; then
        error "Docker Compose (v2) не установлен или недоступен!"
        has_errors=true
    fi

    # 8. Проверка свободного места на диске (минимум 500 МБ)
    local free_kb
    free_kb=$(df -kP "${PROJECT_DIR}" 2>/dev/null | awk 'NR==2 {print $4}' || echo "")
    if [[ "$free_kb" =~ ^[0-9]+$ ]] && (( free_kb < 512000 )); then
        error "Недостаточно свободного места на диске: $(( free_kb / 1024 )) МБ. Требуется минимум 500 МБ для безопасной сборки и создания бэкапов."
        has_errors=true
    fi

    if [ "$has_errors" = "true" ]; then
        error "Предварительная проверка окружения завершилась с ошибками. Исправьте конфигурацию перед продолжением."
        return 1
    fi

    log "Предварительная проверка базового окружения (preflight) успешно пройдена."
    return 0
}

# --- 3. Безопасное обновление ---
cmd_update() {
    echo -e "\n${BOLD}${BLUE}=== 🔄 БЕЗОПАСНОЕ ОБНОВЛЕНИЕ JUST1KBOT ===${NC}\n"

    info "Шаг 1/6. Предварительная проверка конфигурации (Preflight Check)..."
    if ! cmd_preflight; then
        error "Обновление остановлено: предварительная проверка не пройдена."
        return 1
    fi

    # Проверка наличия локальных незакоммиченных изменений
    local did_stash=false
    local dirty_changes
    dirty_changes=$(git status --porcelain 2>/dev/null || true)
    if [[ -n "$dirty_changes" ]]; then
        warn "Обнаружены незакоммиченные локальные изменения:"
        git status -s
        echo ""
        read -r -p "Временно спрятать изменения (git stash) и продолжить обновление? (y/N): " stash_confirm
        if [[ "$stash_confirm" =~ ^[Yy]$ ]]; then
            git stash
            did_stash=true
            log "Локальные изменения сохранены в git stash."
        else
            info "Обновление отменено пользователем."
            return 0
        fi
    fi

    local rollback_commit=""
    rollback_commit="$(git rev-parse HEAD 2>/dev/null || true)"

    info "Шаг 2/6. Создание страховочного бэкапа базы данных..."
    LAST_BACKUP_FILE=""
    if ! cmd_backup || [[ -z "$LAST_BACKUP_FILE" ]] || [[ ! -f "$LAST_BACKUP_FILE" ]]; then
        error "Обновление остановлено: не удалось создать страховочный бэкап базы данных."
        return 1
    fi
    local pre_update_backup="$LAST_BACKUP_FILE"

    local current_branch
    current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    info "Шаг 3/6. Получение обновлений из Git (ветка: $current_branch)..."
    if ! git fetch origin "$current_branch"; then
        error "Обновление остановлено: не удалось связаться с origin/$current_branch."
        return 1
    fi

    local local_hash remote_hash base_hash
    local_hash=$(git rev-parse HEAD 2>/dev/null || true)
    remote_hash=$(git rev-parse "origin/$current_branch" 2>/dev/null || true)
    base_hash=$(git merge-base HEAD "origin/$current_branch" 2>/dev/null || true)

    if [[ "$local_hash" == "$remote_hash" ]]; then
        info "Установлена актуальная версия ($local_hash). Новых коммитов в origin/$current_branch нет."
        read -r -p "Пересобрать образы и перезапустить контейнеры без обновления кода? (y/N): " force_rebuild
        if [[ ! "$force_rebuild" =~ ^[Yy]$ ]]; then
            log "Обновление завершено (код уже актуален)."
            if [ "$did_stash" = "true" ]; then
                echo ""
                warn "⚠️ ВНИМАНИЕ: Ваши локальные изменения сохранены в git stash (stash@{0}) и НЕ были восстановлены автоматически."
                info "Для просмотра сохранённых изменений: git stash show -p"
                info "Для применения изменений: git stash pop"
            fi
            return 0
        fi
    elif [[ "$base_hash" == "$local_hash" ]]; then
        info "Обнаружены новые коммиты в origin/$current_branch. Выполняем безопасное обновление (fast-forward pull)..."
        if ! git pull --ff-only origin "$current_branch"; then
            error "Ошибка при выполнении git pull --ff-only!"
            return 1
        fi
    elif [[ "$base_hash" == "$remote_hash" ]]; then
        local ahead_count
        ahead_count=$(git rev-list --count "origin/$current_branch..HEAD" 2>/dev/null || echo "несколько")
        warn "Локальная ветка опережает origin/$current_branch на $ahead_count коммит(ов)."
        warn "Автоматическая перезапись отменена во избежание потери локальных коммитов или hotfix."
        echo ""
        read -r -p "Принудительно перезаписать локальные коммиты версией из origin/$current_branch? (y/N): " force_overwrite
        if [[ "$force_overwrite" =~ ^[Yy]$ ]]; then
            local backup_branch
            backup_branch="backup-local-ahead-$(date +%Y%m%d_%H%M%S)"
            git branch "$backup_branch"
            log "Локальные коммиты сохранены в резервной ветке: $backup_branch"
            if ! git reset --hard "origin/$current_branch"; then
                error "Ошибка сброса ветки к origin/$current_branch!"
                return 1
            fi
        else
            info "Обновление отменено пользователем."
            return 0
        fi
    else
        warn "Обнаружено расхождение истории коммитов между локальной версией и origin/$current_branch."
        echo ""
        read -r -p "Создать резервную ветку и синхронизировать с origin/$current_branch? (y/N): " confirm_diverge
        if [[ "$confirm_diverge" =~ ^[Yy]$ ]]; then
            local backup_branch
            backup_branch="backup-diverged-$(date +%Y%m%d_%H%M%S)"
            git branch "$backup_branch"
            log "Локальная история сохранена в ветке $backup_branch"
            if ! git reset --hard "origin/$current_branch"; then
                error "Ошибка сброса ветки к origin/$current_branch!"
                return 1
            fi
        else
            info "Обновление отменено пользователем."
            return 0
        fi
    fi

    info "Шаг 4/6. Сборка образов, валидация конфигурации и применение миграций..."
    if ! docker compose build; then
        error "Ошибка при сборке Docker-образов новой версии!"
        if [[ -n "$rollback_commit" ]]; then
            warn "🚨 Выполняем автоматический откат исходного кода к коммиту $rollback_commit..."
            git reset --hard "$rollback_commit"
        fi
        return 1
    fi

    info "Валидация конфигурации через Pydantic Settings в обновлённом образе..."
    local pydantic_err=""
    if ! pydantic_err=$(docker compose run --rm --no-deps bot python -c "from config.settings import get_settings; get_settings()" 2>&1); then
        error "Ошибка валидации конфигурации Pydantic Settings в новой версии кода!"
        echo "$pydantic_err" | tail -n 5 >&2
        if [[ -n "$rollback_commit" ]]; then
            warn "🚨 Отменяем обновление и возвращаем исходный код к коммиту $rollback_commit..."
            git reset --hard "$rollback_commit"
        fi
        return 1
    fi

    info "Применение миграций базы данных..."
    if ! docker compose run --rm migrate; then
        error "Ошибка при применении миграций базы данных! Запуск новых контейнеров отменён."
        if [[ -n "$rollback_commit" ]]; then
            warn "🚨 Выполняем откат исходного кода к коммиту $rollback_commit..."
            git reset --hard "$rollback_commit"
            docker compose up -d --build

            info "Проверка работоспособности сервисов старой версии..."
            local mig_timeout=60
            local mig_elapsed=0
            local mig_healthy=false

            while [ "$mig_elapsed" -lt "$mig_timeout" ]; do
                local m_db m_redis m_bot m_caddy
                m_db="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_db 2>/dev/null || echo starting)"
                m_redis="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_redis 2>/dev/null || echo starting)"
                m_bot="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_app 2>/dev/null || echo starting)"
                m_caddy="$(docker inspect --format='{{.State.Status}}' just1kbot_caddy 2>/dev/null || echo starting)"

                if [ "$m_db" = "healthy" ] && [ "$m_redis" = "healthy" ] && [ "$m_bot" = "healthy" ] && [ "$m_caddy" = "running" ]; then
                    mig_healthy=true
                    break
                fi
                printf "."
                sleep 2
                mig_elapsed=$((mig_elapsed + 2))
            done
            echo ""

            if [ "$mig_healthy" = "true" ]; then
                log "Старая версия сервисов восстановлена и работает в штатном режиме."
            else
                error "КРИТИЧЕСКАЯ ОШИБКА: Старая версия сервисов не смогла запуститься (возможно, из-за частично применённых миграций)!"
                if [[ -n "$pre_update_backup" ]] && [[ -f "$pre_update_backup" ]]; then
                    warn "Для полного восстановления базы данных к исходному состоянию перед обновлением выполните:"
                    echo -e "${BOLD}${YELLOW}    just1kbot restore $pre_update_backup${NC}"
                else
                    warn "Для полного восстановления рабочей базы данных выполните: just1kbot restore"
                fi
                docker compose logs --tail=50 bot
            fi
        fi
        return 1
    fi

    info "Шаг 5/6. Запуск обновлённых сервисов..."
    docker compose up -d

    info "Шаг 6/6. Проверка статуса здоровья сервисов (Healthcheck)..."
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
            update_ok=false
            break
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
        error "Сервисы не смогли перейти в состояние Healthy после обновления!"
        warn "ВАЖНО: Миграции базы данных уже были применены к PostgreSQL."
        docker compose logs --tail=50 bot
        if [[ -n "$rollback_commit" ]]; then
            echo ""
            warn "🚨 Выполняем возврат исходного кода к предыдущему коммиту ($rollback_commit)..."
            git reset --hard "$rollback_commit"
            docker compose up -d --build

            info "Проверка работоспособности сервисов после отката кода..."
            local rb_timeout=60
            local rb_elapsed=0
            local rb_healthy=false

            while [ "$rb_elapsed" -lt "$rb_timeout" ]; do
                local rb_db rb_redis rb_bot rb_caddy
                rb_db="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_db 2>/dev/null || echo starting)"
                rb_redis="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_redis 2>/dev/null || echo starting)"
                rb_bot="$(docker inspect --format='{{.State.Health.Status}}' just1kbot_app 2>/dev/null || echo starting)"
                rb_caddy="$(docker inspect --format='{{.State.Status}}' just1kbot_caddy 2>/dev/null || echo starting)"

                if [ "$rb_db" = "healthy" ] && [ "$rb_redis" = "healthy" ] && [ "$rb_bot" = "healthy" ] && [ "$rb_caddy" = "running" ]; then
                    rb_healthy=true
                    break
                fi
                printf "."
                sleep 2
                rb_elapsed=$((rb_elapsed + 2))
            done
            echo ""

            if [ "$rb_healthy" = "true" ]; then
                warn "⚠️ Исходный код возвращён к коммиту $rollback_commit. Сервисы запущены."
                warn "Обратите внимание: схема базы данных осталась в обновлённом состоянии."
                if [[ -n "$pre_update_backup" ]] && [[ -f "$pre_update_backup" ]]; then
                    warn "Для полного возврата схемы БД к исходному состоянию перед обновлением выполните:"
                    echo -e "${BOLD}${YELLOW}    just1kbot restore $pre_update_backup${NC}"
                fi
            else
                error "КРИТИЧЕСКАЯ ОШИБКА: Сервисы не смогли подняться после отката кода!"
                warn "Вероятная причина: применённая миграция изменила схему БД и несовместима со старой версией кода."
                if [[ -n "$pre_update_backup" ]] && [[ -f "$pre_update_backup" ]]; then
                    warn "Для полного восстановления рабочей базы данных выполните:"
                    echo -e "${BOLD}${YELLOW}    just1kbot restore $pre_update_backup${NC}"
                else
                    warn "Для полного восстановления рабочей базы данных выполните: just1kbot restore"
                fi
                docker compose logs --tail=50 bot
            fi
        fi
        return 1
    fi

    if [ "$did_stash" = "true" ]; then
        echo ""
        warn "⚠️ ВНИМАНИЕ: Ваши локальные изменения сохранены в git stash (stash@{0}) и НЕ были восстановлены автоматически."
        info "Для просмотра сохранённых изменений: git stash show -p"
        info "Для применения изменений поверх новой версии: git stash pop"
    fi

    info "Просмотр последних логов бота:"
    echo -e "${CYAN}Нажмите Ctrl+C для возврата в меню...${NC}\n"
    cmd_logs "bot"
}

# Вспомогательная функция ротации бэкапов
rotate_backups() {
    local keep_count="${1:-14}"
    local backups=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && backups+=("$f")
    done < <(find backups/ -maxdepth 1 -name "*.sql.gz.age" -type f -exec stat -c "%Y %n" {} + 2>/dev/null | sort -rn | awk '{print $2}' || find backups/ -maxdepth 1 -name "*.sql.gz.age" -type f 2>/dev/null | sort -r)

    local total="${#backups[@]}"
    if (( total > keep_count )); then
        local to_delete=$(( total - keep_count ))
        info "Ротация бэкапов: найдено $total копий (лимит хранения: $keep_count). Удаление $to_delete устаревших бэкапов..."
        for (( i=keep_count; i<total; i++ )); do
            local old_file="${backups[$i]}"
            if [[ -f "$old_file" ]]; then
                rm -f "$old_file"
                log "Удален устаревший бэкап: $(basename "$old_file")"
            fi
        done
    fi
}

# --- 4. Создание бэкапа ---
cmd_backup() {
    echo -e "\n${BOLD}${BLUE}=== 💾 СОЗДАНИЕ ЗАШИФРОВАННОГО БЭКАПА БД ===${NC}\n"
    mkdir -p backups

    # Способ 1: Прямой дамп из работающего контейнера db + шифрование age (быстро и надежно)
    if command -v age >/dev/null 2>&1 && [[ -f "${PROJECT_DIR}/.env" ]]; then
        local age_recipient
        age_recipient=$(grep -E "^BACKUP_AGE_RECIPIENT=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
        if [[ -n "$age_recipient" ]]; then
            info "Создание зашифрованного дампа PostgreSQL..."
            local ts
            ts=$(date +%Y%m%d_%H%M%S)
            local backup_file="backups/just1kbot_${ts}.sql.gz.age"
            local tmp_backup_dir
            tmp_backup_dir=$(mktemp -d -t just1kbot-backup-XXXXXX)
            local tmp_gz="${tmp_backup_dir}/backup.sql.gz"
            local dump_err="${tmp_backup_dir}/backup.err"

            local pg_user pg_db
            pg_user=$(grep -E "^POSTGRES_USER=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
            pg_db=$(grep -E "^POSTGRES_DB=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
            pg_user="${pg_user:-just1kbot}"
            pg_db="${pg_db:-just1kbot_bot}"

            docker compose exec -T db pg_dump -U "$pg_user" -d "$pg_db" 2>"$dump_err" | gzip > "$tmp_gz"
            local dump_status=("${PIPESTATUS[@]}")
            if [[ "${dump_status[0]}" -eq 0 ]] && [[ "${dump_status[1]}" -eq 0 ]]; then
                if [[ -s "$tmp_gz" ]] && gzip -t "$tmp_gz" 2>/dev/null; then
                    if age -r "$age_recipient" -o "$backup_file" "$tmp_gz" 2>/dev/null; then
                        rm -rf "$tmp_backup_dir"
                        log "Бэкап успешно создан: ${BOLD}${backup_file}${NC}"
                        # shellcheck disable=SC2012
                        ls -lh "$backup_file" | awk '{print "Размер: " $5 ", Создан: " $6 " " $7 " " $8}'
                        LAST_BACKUP_FILE="$backup_file"
                        rotate_backups 14
                        return 0
                    else
                        warn "Ошибка шифрования дампа утилитой age."
                    fi
                else
                    warn "Дамп PostgreSQL пуст или поврежден (gzip integrity check failed)."
                fi
            else
                warn "Ошибка выполнения pg_dump в контейнере db (pg_dump code=${dump_status[0]}, gzip code=${dump_status[1]})."
            fi

            if [[ -s "$dump_err" ]]; then
                warn "pg_dump stderr: $(tail -n 3 "$dump_err")"
            fi
            rm -rf "$tmp_backup_dir"
        fi
    fi

    # Способ 2: Через отдельный контейнер backup (compose profile tools)
    if docker compose --profile tools run --rm backup; then
        local latest_backup
        latest_backup=$(find backups/ -maxdepth 1 -name "*.sql.gz.age" -type f -exec stat -c "%Y %n" {} + 2>/dev/null | sort -rn | awk '{print $2}' | head -1 || find backups/ -maxdepth 1 -name "*.sql.gz.age" 2>/dev/null | sort -r | head -1 || echo "")
        if [[ -n "$latest_backup" ]] && [[ -f "$latest_backup" ]] && [[ -s "$latest_backup" ]]; then
            log "Бэкап успешно создан: ${BOLD}${latest_backup}${NC}"
            # shellcheck disable=SC2012
            ls -lh "$latest_backup" | awk '{print "Размер: " $5 ", Создан: " $6 " " $7 " " $8}'
            LAST_BACKUP_FILE="$latest_backup"
            rotate_backups 14
            return 0
        fi
    fi

    error "Ошибка при создании бэкапа базы данных."
    return 1
}

# --- 5. Безопасное восстановление из бэкапа ---
# shellcheck disable=SC2120
cmd_restore() {
    local direct_backup_file="${1:-}"
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

    local selected_backup=""
    if [[ -n "$direct_backup_file" ]] && [[ -f "$direct_backup_file" ]]; then
        selected_backup="$direct_backup_file"
        info "Выбран файл для восстановления: $selected_backup"
    else
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
            # shellcheck disable=SC2012
            sz=$(ls -lh "$b" | awk '{print $5}')
            echo -e "  [${BOLD}$i${NC}] $b ($sz)"
            i=$((i+1))
        done

        echo ""
        read -r -p "Выберите номер файла для восстановления [1-${#backups_list[@]}]: " choice_idx
        if ! [[ "$choice_idx" =~ ^[0-9]+$ ]] || [ "$choice_idx" -lt 1 ] || [ "$choice_idx" -gt "${#backups_list[@]}" ]; then
            error "Неверный выбор."
            return 1
        fi

        selected_backup="${backups_list[$((choice_idx-1))]}"
        info "Выбран файл для восстановления: $selected_backup"
    fi

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

    info "3.1/5. Создание предварительного страховочного дампа текущей БД..."
    local pre_restore_backup_file="${tmp_dir}/pre_restore_safety.sql"
    docker compose exec -T db pg_dump -U "$pg_user" -d "$pg_db" > "$pre_restore_backup_file" 2>/dev/null || true

    info "4/5. Полная переинициализация базы данных и накат дампа..."
    docker compose exec -T db dropdb -U "$pg_user" --if-exists "$pg_db" >/dev/null 2>&1 || true
    docker compose exec -T db createdb -U "$pg_user" "$pg_db"

    if ! docker compose exec -T db psql -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=1 < "$tmp_sql"; then
        error "Ошибка при накате SQL дампа в PostgreSQL!"
        if [[ -s "$pre_restore_backup_file" ]]; then
            warn "Попытка отката к состоянию базы данных до начала операции восстановления..."
            docker compose exec -T db dropdb -U "$pg_user" --if-exists "$pg_db" >/dev/null 2>&1 || true
            docker compose exec -T db createdb -U "$pg_user" "$pg_db"
            docker compose exec -T db psql -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=1 < "$pre_restore_backup_file" >/dev/null 2>&1 || true
            warn "Исходное состояние базы данных возвращено."
        fi
        return 1
    fi

    rm -rf "$tmp_dir"
    trap - EXIT INT TERM

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

    # 0. Проверка версии ядра Linux
    local kernel_ver
    kernel_ver="$(uname -r 2>/dev/null || echo 'unknown')"
    if [[ "$kernel_ver" =~ ^([0-9]+)\.([0-9]+) ]]; then
        local k_major="${BASH_REMATCH[1]}"
        local k_minor="${BASH_REMATCH[2]}"
        if (( k_major > 5 || (k_major == 5 && k_minor >= 6) )); then
            log "Ядро Linux ($kernel_ver): поддержка AmneziaWG на уровне ядра (OK)."
        else
            warn "Ядро Linux ($kernel_ver) старше 5.6. Для оптимальной работы AmneziaWG рекомендуется ядро >= 5.6."
        fi
    else
        info "Версия ядра Linux: $kernel_ver"
    fi

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
                if ! cmd_update; then
                    warn "Операция обновления остановлена."
                fi
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            4)
                echo -e "\n[1] Создать новый зашифрованный бэкап"
                echo "[2] Восстановить базу данных из бэкапа"
                read -r -p "Выберите [1-2]: " b_action
                if [[ "$b_action" == "2" ]]; then
                    if ! cmd_restore; then
                        warn "Операция восстановления отменена или завершилась с ошибкой."
                    fi
                else
                    if ! cmd_backup; then
                        warn "Создание резервной копии завершилось с ошибкой."
                    fi
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
                    2) cmd_restart "all" || true ;;
                    3) cmd_stop || true ;;
                    4) cmd_start || true ;;
                    *) cmd_restart "bot" || true ;;
                esac
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            6)
                cmd_config || true
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            7)
                if ! cmd_doctor; then
                    warn "Диагностика выявила замечания."
                fi
                read -r -p "Нажмите Enter для возврата в меню..."
                ;;
            8)
                cmd_clean || true
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
                cmd_restore "${2:-}"
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
            preflight|check-env)
                cmd_preflight
                ;;
            clean|prune)
                cmd_clean
                ;;
            help|-h|--help)
                echo -e "Использование: just1kbot [status|logs|update|preflight|backup|restore|restart|start|stop|config|doctor|clean]"
                ;;
            *)
                error "Неизвестная команда: $1. Используйте 'just1kbot help'."
                ;;
        esac
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
