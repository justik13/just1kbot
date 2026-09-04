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
PROJECT_DIR="${PROJECT_DIR:-}"

# 1. Проверяем переменную окружения JUST1KBOT_DIR или PROJECT_DIR
if [[ -n "${JUST1KBOT_DIR:-}" ]] && [[ -f "${JUST1KBOT_DIR}/docker-compose.yml" ]]; then
    PROJECT_DIR="${JUST1KBOT_DIR}"
elif [[ -n "${PROJECT_DIR:-}" ]] && [[ -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    PROJECT_DIR="${PROJECT_DIR}"
else
    PROJECT_DIR=""
fi

# 2. Если не найдено, определяем реальный путь к скрипту с раскрытием всех симлинков
if [[ -z "$PROJECT_DIR" ]]; then
    SOURCE="${BASH_SOURCE[0]:-$0}"
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

run_privileged() {
    if [[ -n "${JUST1KBOT_NO_SUDO:-}" ]] || [[ ${EUID:-$(id -u)} -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo env "PATH=$PATH" "$@"
    else
        "$@"
    fi
}

print_ai_diagnostic_report() {
    local component="$1"
    local issue="$2"
    local action="$3"
    local error_details="$4"
    local resolution="$5"

    echo "" >&2
    echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}" >&2
    echo -e "${BOLD}${RED}🚨 JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT (AI-FRIENDLY)${NC}" >&2
    echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}" >&2
    echo -e "${BOLD}Компонент:${NC}          $component" >&2
    echo -e "${BOLD}Обнаруженная проблема:${NC} $issue" >&2
    echo -e "${BOLD}Действие:${NC}            $action" >&2
    echo -e "${BOLD}Детали ошибки:${NC}" >&2
    echo "$error_details" | sed 's/^/    /' >&2
    echo -e "${BOLD}Решение (скопируйте этот блок для отправки AI):${NC}" >&2
    echo "$resolution" | sed 's/^/    /' >&2
    echo -e "${RED}════════════════════════════════════════════════════════════════════════════════${NC}" >&2
    echo "" >&2
}

# Обнаружение активных пользовательских сайтов в Nginx (для предотвращения случайного даунтайма)
detect_existing_nginx_sites() {
    local base_dir="${1:-/etc/nginx}"
    local sites_found=()
    local conf_dirs=("$base_dir/sites-enabled" "$base_dir/conf.d")

    for cdir in "${conf_dirs[@]}"; do
        [[ ! -d "$cdir" ]] && continue
        while IFS= read -r -d '' f; do
            local fname
            fname="$(basename "$f")"
            [[ "$fname" =~ ^(just1k|sub-wl|xhttp).* ]] && continue
            [[ "$fname" =~ .*\.(bak|old|tmp|disabled)$ ]] && continue

            if [[ "$fname" == "default" ]]; then
                if grep -Eq '(^|[[:space:]])server_name[[:space:]]+[^_;]' "$f" 2>/dev/null; then
                    local sname
                    sname="$(grep -E '(^|[[:space:]])server_name[[:space:]]+' "$f" 2>/dev/null | head -n1 | sed -E 's/.*server_name[[:space:]]+//; s/;.*//')"
                    sites_found+=("$fname ($sname)")
                fi
                continue
            fi

            if grep -Eq '(server_name|listen|proxy_pass)[[:space:]]+' "$f" 2>/dev/null; then
                local sname
                sname="$(grep -E '(^|[[:space:]])server_name[[:space:]]+' "$f" 2>/dev/null | head -n1 | sed -E 's/.*server_name[[:space:]]+//; s/;.*//' || echo "")"
                if [[ -n "$sname" && "$sname" != "_" ]]; then
                    sites_found+=("$fname ($sname)")
                else
                    sites_found+=("$fname")
                fi
            fi
        done < <(find "$cdir" -maxdepth 1 \( -type f -o -type l \) -print0 2>/dev/null)
    done

    if [[ ${#sites_found[@]} -gt 0 ]]; then
        printf '%s\n' "${sites_found[@]}"
        return 0
    fi
    return 1
}

is_external_nginx_enabled() {
    local val=""
    if [[ -f "${PROJECT_DIR}/.env" ]]; then
        val=$(grep -E "^USE_EXTERNAL_NGINX=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
    fi
    if [[ "$val" == "true" || "$val" == "1" || "${USE_EXTERNAL_NGINX:-}" == "true" || "${USE_EXTERNAL_NGINX:-}" == "1" ]]; then
        return 0
    fi
    return 1
}

get_env_var() {
    local key="$1"
    local default_val="${2:-}"
    local env_file="${PROJECT_DIR}/.env"
    if [[ -f "$env_file" ]]; then
        local val
        val=$(grep -E "^${key}=" "$env_file" 2>/dev/null | head -n1 | cut -d'=' -f2- | tr -d " '\"" || echo "")
        if [[ -n "$val" ]]; then
            echo "$val"
            return 0
        fi
    fi
    echo "$default_val"
}

set_env_var() {
    local key="$1"
    local val="$2"
    local env_file="${PROJECT_DIR}/.env"
    [[ ! -f "$env_file" ]] && return 1

    if grep -Eq "^${key}=" "$env_file" 2>/dev/null; then
        sed -i -E "s|^${key}=.*|${key}=${val}|" "$env_file"
    else
        echo "${key}=${val}" >> "$env_file"
    fi
}

dc_up() {
    local scale_args=()
    if is_external_nginx_enabled; then
        scale_args=(--scale caddy=0)
    fi
    docker compose up -d "${scale_args[@]}" "$@"
}

setup_external_nginx_integration() {
    local base_dir="${1:-/etc/nginx}"
    local domain ssl_email bot_port
    domain=$(grep -E "^DOMAIN=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
    ssl_email=$(grep -E "^SSL_EMAIL=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
    bot_port=$(grep -E "^BOT_PORT=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "8080")
    bot_port="${bot_port:-8080}"

    if [[ -z "$domain" ]]; then
        error "DOMAIN не задан в .env! Невозможно настроить виртуальный хост Nginx."
        return 1
    fi

    info "Настройка совместной работы Just1kBot с системным Nginx (домен: $domain, порт бота: $bot_port)..."

    local sites_avail="$base_dir/sites-available"
    local sites_enb="$base_dir/sites-enabled"
    local certbot_webroot="/var/www/certbot"
    local config_file="$sites_avail/just1kbot.conf"

    if ! command -v nginx >/dev/null 2>&1 && [[ ! -d "$base_dir" ]]; then
        print_ai_diagnostic_report \
            "Host Nginx" \
            "Команда nginx не найдена в системе" \
            "Настройка Nginx reverse proxy для Just1kBot" \
            "Директория $base_dir не существует, команда 'nginx' отсутствует в PATH." \
            "Установите Nginx (sudo apt update && sudo apt install -y nginx) или используйте Caddy (USE_EXTERNAL_NGINX=false)."
        return 1
    fi

    run_privileged mkdir -p "$sites_avail" "$sites_enb" "$certbot_webroot" 2>/dev/null || true
    run_privileged chmod 755 "$certbot_webroot" 2>/dev/null || true

    local cert_dir="/etc/letsencrypt/live/${domain}"
    local has_ssl=false

    if [[ -f "${cert_dir}/fullchain.pem" ]] && [[ -f "${cert_dir}/privkey.pem" ]]; then
        has_ssl=true
    elif command -v certbot >/dev/null 2>&1 && [[ -n "$ssl_email" ]]; then
        info "Попытка запроса SSL-сертификата Let's Encrypt для ${domain} через certbot webroot..."
        local bootstrap_conf="$sites_avail/just1kbot-bootstrap.conf"
        cat <<EOF > /tmp/just1kbot-bootstrap.tmp
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    location ^~ /.well-known/acme-challenge/ {
        root ${certbot_webroot};
        default_type "text/plain";
    }
}
EOF
        run_privileged cp /tmp/just1kbot-bootstrap.tmp "$bootstrap_conf" 2>/dev/null || true
        rm -f /tmp/just1kbot-bootstrap.tmp
        run_privileged ln -sf "$bootstrap_conf" "$sites_enb/" 2>/dev/null || true
        if run_privileged nginx -t 2>/dev/null; then
            run_privileged systemctl reload nginx 2>/dev/null || true
            if run_privileged certbot certonly --webroot -w "$certbot_webroot" -d "$domain" --non-interactive --agree-tos --email "$ssl_email" 2>/dev/null; then
                if [[ -f "${cert_dir}/fullchain.pem" ]]; then
                    has_ssl=true
                    log "SSL-сертификат Let's Encrypt успешно получен."
                fi
            else
                warn "Не удалось выпустить сертификат через certbot webroot (DNS еще не обновился или порт 80 недоступен извне)."
            fi
        fi
        run_privileged rm -f "$bootstrap_conf" "$sites_enb/just1kbot-bootstrap.conf" 2>/dev/null || true
    fi

    if [[ "$has_ssl" != "true" ]]; then
        local allow_http
        allow_http=$(get_env_var "ALLOW_LOCAL_HTTP" "false")
        if [[ "$allow_http" != "true" ]]; then
            print_ai_diagnostic_report \
                "Let's Encrypt SSL Issuance" \
                "Отсутствует SSL-сертификат для домена ${domain}" \
                "Настройка внешнего Nginx прервана, откат к незащищенному HTTP заблокирован" \
                "Сертификаты Let's Encrypt не обнаружены в ${cert_dir} и certbot не смог выпустить сертификат. Telegram Bot API и ЮKassa требуют HTTPS webhook." \
                "1. Проверьте A-запись DNS для ${domain}\n2. Убедитесь, что порт 80 открыт извне для ACME challenge\n3. Получите сертификат вручную: certbot certonly --webroot -w ${certbot_webroot} -d ${domain}\n4. Повторите: just1kbot nginx-config"
            return 1
        fi
        warn "ВНИМАНИЕ: Активирован режим ALLOW_LOCAL_HTTP=true. Будет создан HTTP-прокси без SSL (только для локальной разработки)."
    fi

    local tmp_conf
    tmp_conf=$(mktemp)

    if [[ "$has_ssl" == "true" ]]; then
        cat <<EOF > "$tmp_conf"
# Just1kBot Reverse Proxy Configuration (Managed by Just1kBot)
# Domain: ${domain} -> 127.0.0.1:${bot_port}

server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    location ^~ /.well-known/acme-challenge/ {
        root ${certbot_webroot};
        default_type "text/plain";
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${domain};

    ssl_certificate ${cert_dir}/fullchain.pem;
    ssl_certificate_key ${cert_dir}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header X-Robots-Tag "noindex, nofollow, noarchive" always;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:${bot_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF
    else
        cat <<EOF > "$tmp_conf"
# Just1kBot Reverse Proxy Configuration (Managed by Just1kBot - HTTP mode)
# Domain: ${domain} -> 127.0.0.1:${bot_port}

server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    location ^~ /.well-known/acme-challenge/ {
        root ${certbot_webroot};
        default_type "text/plain";
    }

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:${bot_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
EOF
    fi

    if ! run_privileged cp "$tmp_conf" "$config_file" 2>/dev/null; then
        rm -f "$tmp_conf"
        error "Не удалось записать конфигурационный файл $config_file (проверьте права доступа)!"
        return 1
    fi
    rm -f "$tmp_conf"
    run_privileged chmod 644 "$config_file" 2>/dev/null || true

    if ! run_privileged ln -sf "$config_file" "$sites_enb/" 2>/dev/null; then
        error "Не удалось создать символическую ссылку для $config_file в $sites_enb!"
        return 1
    fi

    local nginx_err=""
    if ! nginx_err=$(run_privileged nginx -t 2>&1); then
        run_privileged rm -f "$sites_enb/$(basename "$config_file")" 2>/dev/null || true
        print_ai_diagnostic_report \
            "Host Nginx Configuration" \
            "Ошибка валидации синтаксиса Nginx (nginx -t)" \
            "Активация виртуального хоста $config_file отменена для защиты работающих сайтов" \
            "$nginx_err" \
            "1. Проверьте синтаксис ваших существующих сайтов: sudo nginx -t\n2. Исправьте ошибки в конфигурациях /etc/nginx/sites-enabled/\n3. Повторите: just1kbot nginx-config"
        return 1
    fi

    local reload_err=""
    if ! reload_err=$(run_privileged systemctl reload nginx 2>&1); then
        run_privileged rm -f "$sites_enb/$(basename "$config_file")" 2>/dev/null || true
        run_privileged systemctl reload nginx 2>/dev/null || true
        print_ai_diagnostic_report \
            "Host Nginx Reload" \
            "Ошибка перезагрузки Nginx (systemctl reload nginx)" \
            "Активация Just1kBot виртуального хоста отменена, Nginx возвращен в исходное состояние" \
            "$reload_err" \
            "1. Проверьте журнал Nginx: sudo journalctl -u nginx -n 50\n2. Проверьте статус: sudo systemctl status nginx\n3. Повторите: just1kbot nginx-config"
        return 1
    fi

    log "Nginx успешно перезагружен (systemctl reload nginx). Существующие сайты работают параллельно без даунтайма."
    set_env_var "USE_EXTERNAL_NGINX" "true"
    log "Интеграция с Nginx активирована: USE_EXTERNAL_NGINX=true закреплено в .env."
    return 0
}

cmd_nginx_config() {
    echo -e "\n${BOLD}${BLUE}=== 🌐 НАСТРОЙКА ИНТЕГРАЦИИ С СИСТЕМНЫМ NGINX ===${NC}\n"
    if setup_external_nginx_integration; then
        log "Конфигурация Nginx для Just1kBot успешно настроена."
        info "Перезапуск контейнеров с отключением Caddy (dc_up)..."
        dc_up --force-recreate bot
    else
        error "Настройка интеграции с Nginx завершилась с ошибкой."
        return 1
    fi
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

    # 9. Проверка портов и веб-серверов
    if is_external_nginx_enabled; then
        info "Режим внешнего Nginx активен (USE_EXTERNAL_NGINX=true). Контейнер Caddy отключен."
        if command -v systemctl >/dev/null 2>&1 && ! systemctl is-active --quiet nginx 2>/dev/null; then
            warn "Системная служба Nginx не активна! Пытаемся запустить Nginx..."
            run_privileged systemctl start nginx 2>/dev/null || true
            if ! systemctl is-active --quiet nginx 2>/dev/null; then
                print_ai_diagnostic_report \
                    "System Nginx" \
                    "Служба Nginx не запущена на хосте" \
                    "Проверка готовности хостового веб-сервера" \
                    "systemctl is-active nginx вернул статус 'inactive' или 'failed'." \
                    "Запустите Nginx вручную: sudo systemctl start nginx && sudo systemctl status nginx"
                has_errors=true
            fi
        fi

        # Проверка синтаксиса Nginx
        local ng_err=""
        if command -v nginx >/dev/null 2>&1; then
            if ! ng_err=$(nginx -t 2>&1); then
                print_ai_diagnostic_report \
                    "System Nginx" \
                    "Конфигурация Nginx содержит синтаксические ошибки" \
                    "nginx -t" \
                    "$ng_err" \
                    "Исправьте ошибки в файлах /etc/nginx/ и перезапустите: just1kbot preflight"
                has_errors=true
            else
                log "Конфигурация системного Nginx проверена (nginx -t: OK)."
            fi
        fi

        # Проверка доступности локального порта бота (BOT_PORT на 127.0.0.1)
        local bot_port
        bot_port=$(grep -E "^BOT_PORT=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "8080")
        bot_port="${bot_port:-8080}"
        local bot_running
        bot_running=$(docker inspect --format='{{.State.Status}}' just1kbot_app 2>/dev/null || echo "")
        if [[ "$bot_running" != "running" ]] && command -v python3 >/dev/null 2>&1; then
            local port_in_use
            port_in_use=$(python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.3)
try:
    if s.connect_ex(('127.0.0.1', int('$bot_port'))) == 0:
        print('used')
except Exception:
    pass
finally:
    s.close()
" 2>/dev/null || echo "")
            if [[ "$port_in_use" == "used" ]]; then
                print_ai_diagnostic_report \
                    "Bot Loopback Port" \
                    "Локальный порт 127.0.0.1:${bot_port} уже занят другим процессом" \
                    "Проверка сокета для контейнера Just1kBot" \
                    "Порт 127.0.0.1:${bot_port} слушается активным процессом." \
                    "1. Задайте другой порт в .env, например: BOT_PORT=8081\n2. Обновите конфигурацию Nginx: just1kbot nginx-config"
                has_errors=true
            fi
        fi
    else
        # Стандартный режим Caddy (проверка конфликтов с системными веб-серверами)
        local host_webservers=(nginx apache2 caddy lighttpd)
        for svc in "${host_webservers[@]}"; do
            if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$svc" 2>/dev/null; then
                if [[ "$svc" == "nginx" ]]; then
                    local existing_sites=()
                    while IFS= read -r s; do
                        [[ -n "$s" ]] && existing_sites+=("$s")
                    done < <(detect_existing_nginx_sites 2>/dev/null || true)

                    if [[ ${#existing_sites[@]} -gt 0 ]]; then
                        warn "Обнаружена активная системная служба Nginx со следующими работающими сайтами:"
                        for s in "${existing_sites[@]}"; do
                            echo -e "    ${BOLD}• $s${NC}"
                        done
                        info "Just1kBot может работать параллельно с вашими сайтами, настроив проксирование через Nginx."

                        if [[ -t 0 ]]; then
                            read -r -p "Настроить совместную работу (Nginx будет проксировать Just1kBot, сайты НЕ пострадают)? (Y/n): " confirm_coexist
                            if [[ ! "$confirm_coexist" =~ ^[Nn]$ ]]; then
                                if setup_external_nginx_integration; then
                                    log "Совместная работа с Nginx успешно сконфигурирована."
                                else
                                    has_errors=true
                                fi
                            else
                                read -r -p "Вы уверены, что хотите остановить Nginx? Ваши существующие сайты станут НЕДОСТУПНЫ! (y/N): " confirm_kill
                                if [[ "$confirm_kill" =~ ^[Yy]$ ]]; then
                                    info "Остановка и отключение nginx..."
                                    run_privileged systemctl stop nginx 2>/dev/null || true
                                    run_privileged systemctl disable nginx 2>/dev/null || true
                                    log "Служба nginx остановлена."
                                else
                                    error "Служба Nginx продолжает занимать порты 80/443. Запуск Just1kBot отменён."
                                    has_errors=true
                                fi
                            fi
                        else
                            # Non-interactive mode with active user sites
                            local auto_domain
                            auto_domain=$(grep -E "^DOMAIN=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
                            if [[ -n "$auto_domain" ]] && (nginx -t >/dev/null 2>&1); then
                                info "Non-interactive режим: на хосте обнаружены сайты в Nginx. Выполняем безопасную совместную настройку (USE_EXTERNAL_NGINX=true)..."
                                if setup_external_nginx_integration; then
                                    log "Совместная работа с Nginx успешно настроена."
                                else
                                    has_errors=true
                                fi
                            else
                                print_ai_diagnostic_report \
                                    "Host Nginx / Port 80,443 Conflict" \
                                    "Обнаружены активные пользовательские сайты в системном Nginx" \
                                    "Защита существующих сайтов от даунтайма в non-interactive режиме (Fail-Closed)" \
                                    "Nginx обслуживает сайты: $(printf '%s, ' "${existing_sites[@]}")" \
                                    "1. Для совместной работы укажите в .env: USE_EXTERNAL_NGINX=true\n2. Выполните: just1kbot nginx-config"
                                has_errors=true
                            fi
                        fi
                    else
                        # Другие веб-серверы (apache2, lighttpd)
                        warn "Обнаружена активная системная служба '$svc' на хосте, которая блокирует порты 80/443 для Just1kBot Caddy!"
                        if [[ -t 0 ]]; then
                            read -r -p "Остановить и отключить системную службу '$svc' для нормальной работы Just1kBot? (Y/n): " confirm_svc
                            if [[ ! "$confirm_svc" =~ ^[Nn]$ ]]; then
                                run_privileged systemctl stop "$svc" 2>/dev/null || true
                                run_privileged systemctl disable "$svc" 2>/dev/null || true
                                log "Служба $svc успешно остановлена и отключена."
                            else
                                error "Служба '$svc' продолжает занимать порт 80/443. Обновление не может быть продолжено."
                                has_errors=true
                            fi
                        else
                            run_privileged systemctl stop "$svc" 2>/dev/null || true
                            run_privileged systemctl disable "$svc" 2>/dev/null || true
                            log "Служба $svc успешно остановлена и отключена."
                        fi
                    fi
                fi
            fi
        done

        # Проверка доступности портов 80 и 443 для Caddy (только если не переключились на внешний Nginx)
        if ! is_external_nginx_enabled; then
            local caddy_running
            caddy_running=$(docker inspect --format='{{.State.Status}}' just1kbot_caddy 2>/dev/null || echo "")
            if [[ "$caddy_running" != "running" ]] && command -v python3 >/dev/null 2>&1; then
                local port_conflict
                port_conflict=$(python3 -c "
import socket, errno

for p in [80, 443]:
    s_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s_conn.settimeout(0.3)
    try:
        if s_conn.connect_ex(('127.0.0.1', p)) == 0:
            print(f'{p}:Port already in use by active listener on 127.0.0.1:{p}')
            break
    except Exception:
        pass
    finally:
        s_conn.close()

    s_bind = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s_bind.bind(('0.0.0.0', p))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f'{p}:Address already in use ({e})')
            break
    except Exception:
        pass
    finally:
        try:
            s_bind.close()
        except Exception:
            pass
" 2>/dev/null || echo "")
                if [[ -n "$port_conflict" ]]; then
                    print_ai_diagnostic_report \
                        "Ports 80/443" \
                        "Порт для Caddy недоступен ($port_conflict)" \
                        "Проверка привязки портов 80 и 443" \
                        "Порт занят другим процессом на хосте." \
                        "1. Проверьте занятость портов: sudo ss -tulpn | grep -E ':80|:443'\n2. Если у вас уже работает Nginx с сайтами, выполните: just1kbot nginx-config"
                    has_errors=true
                fi
            fi
        fi
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
        local stash_confirm="n"
        if ! read -r -t 60 -p "Временно спрятать изменения (git stash) и продолжить обновление? (y/N): " stash_confirm 2>/dev/null; then
            stash_confirm="n"
        fi
        if [[ "$stash_confirm" =~ ^[Yy]$ ]]; then
            git stash
            did_stash=true
            log "Локальные изменения сохранены в git stash."
        else
            if [[ ! -t 0 ]]; then
                print_ai_diagnostic_report \
                    "Git Working Tree" \
                    "Обнаружены незакоммиченные локальные изменения в non-interactive режиме" \
                    "Обновление отменено для защиты пользовательских файлов (Fail-Closed)" \
                    "$(git status -s)" \
                    "1. Проверьте изменения: git status\n2. Сохраните их: git stash или закоммитьте: git commit -am 'local fixes'\n3. Повторите: just1kbot update"
                return 1
            fi
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
    if [[ "$current_branch" == "HEAD" ]] || [[ -z "$current_branch" ]]; then
        warn "Обнаружено состояние detached HEAD. Переключаемся на основную ветку main..."
        if ! git checkout main 2>/dev/null; then
            error "Не удалось переключиться на ветку main из detached HEAD."
            return 1
        fi
        current_branch="main"
    fi

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
        local force_rebuild="n"
        if ! read -r -t 60 -p "Пересобрать образы и перезапустить контейнеры без обновления кода? (y/N): " force_rebuild 2>/dev/null; then
            force_rebuild="n"
        fi
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
        local force_overwrite="n"
        if ! read -r -t 60 -p "Принудительно перезаписать локальные коммиты версией из origin/$current_branch? (y/N): " force_overwrite 2>/dev/null; then
            force_overwrite="n"
        fi
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
            if [[ ! -t 0 ]]; then
                print_ai_diagnostic_report \
                    "Git Synchronization" \
                    "Локальная ветка опережает origin/$current_branch на $ahead_count коммит(ов)" \
                    "Обновление отменено в non-interactive режиме для защиты локальных коммитов" \
                    "$(git log -n 3 --oneline "origin/$current_branch..HEAD" 2>/dev/null || true)" \
                    "1. Проверьте локальные коммиты: git log origin/$current_branch..HEAD\n2. Если хотите принудительно обновить: git branch backup-local && git reset --hard origin/$current_branch"
                return 1
            fi
            info "Обновление отменено пользователем."
            return 0
        fi
    else
        warn "Обнаружено расхождение истории коммитов между локальной версией и origin/$current_branch."
        echo ""
        local confirm_diverge="n"
        if ! read -r -t 60 -p "Создать резервную ветку и синхронизировать с origin/$current_branch? (y/N): " confirm_diverge 2>/dev/null; then
            confirm_diverge="n"
        fi
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
            if [[ ! -t 0 ]]; then
                print_ai_diagnostic_report \
                    "Git Synchronization" \
                    "История коммитов локальной ветки разошлась с origin/$current_branch (Diverged)" \
                    "Обновление отменено в non-interactive режиме во избежание потери данных" \
                    "Локальный HEAD: $local_hash, Remote: $remote_hash, Base: $base_hash" \
                    "1. Проверьте различия: git log --graph --oneline HEAD...origin/$current_branch\n2. Синхронизируйте ветку вручную: git reset --hard origin/$current_branch"
                return 1
            fi
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
            dc_up --build

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

                local m_caddy_ok=false
                if is_external_nginx_enabled; then
                    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null && run_privileged nginx -t >/dev/null 2>&1; then
                        m_caddy_ok=true
                    else
                        m_caddy_ok=false
                    fi
                elif [ "$m_caddy" = "running" ]; then
                    m_caddy_ok=true
                fi

                if [ "$m_db" = "healthy" ] && [ "$m_redis" = "healthy" ] && [ "$m_bot" = "healthy" ] && [ "$m_caddy_ok" = "true" ]; then
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
    dc_up

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

        local caddy_ok=false
        if is_external_nginx_enabled; then
            if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null && run_privileged nginx -t >/dev/null 2>&1; then
                caddy_ok=true
            else
                caddy_ok=false
            fi
        elif [ "$caddy_s" = "running" ]; then
            caddy_ok=true
        fi

        if [[ "$migrate_s" == "exited/"* ]] && [[ "$migrate_s" != "exited/0" ]]; then
            echo ""
            error "Ошибка миграции базы данных после обновления: $migrate_s"
            docker compose logs migrate
            update_ok=false
            break
        fi

        if [ "$db_h" = "healthy" ] && [ "$redis_h" = "healthy" ] && [ "$bot_h" = "healthy" ] && [ "$caddy_ok" = "true" ]; then
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

        # Закрепление безопасных прав доступа на хосте
        chmod 600 "${PROJECT_DIR}/.env" 2>/dev/null || true
        chmod 700 "${PROJECT_DIR}/backups" 2>/dev/null || true

        if is_external_nginx_enabled; then
            info "Проверка внешнего Nginx после обновления контейнеров..."
            if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
                local nginx_post_err=""
                if nginx_post_err=$(run_privileged nginx -t 2>&1); then
                    local reload_post_err=""
                    if ! reload_post_err=$(run_privileged systemctl reload nginx 2>&1); then
                        error "Не удалось перезагрузить Nginx после обновления: $reload_post_err"
                        return 1
                    else
                        log "Nginx успешно перезагружен (systemctl reload nginx)."
                    fi
                else
                    error "Обнаружена ошибка синтаксиса Nginx после обновления: $nginx_post_err"
                    return 1
                fi
            else
                error "Служба системного Nginx неактивна после обновления!"
                return 1
            fi
        fi
    else
        error "Сервисы не смогли перейти в состояние Healthy после обновления!"
        warn "ВАЖНО: Миграции базы данных уже были применены к PostgreSQL."
        local bot_err_logs=""
        bot_err_logs="$(docker compose logs --tail=50 bot 2>/dev/null || true)"
        echo "$bot_err_logs"
        if [[ -n "$rollback_commit" ]]; then
            echo ""
            warn "🚨 Выполняем возврат исходного кода к предыдущему коммиту ($rollback_commit)..."
            git reset --hard "$rollback_commit"
            dc_up --build

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

                local rb_caddy_ok=false
                if is_external_nginx_enabled; then
                    rb_caddy_ok=true
                elif [ "$rb_caddy" = "running" ]; then
                    rb_caddy_ok=true
                fi

                if [ "$rb_db" = "healthy" ] && [ "$rb_redis" = "healthy" ] && [ "$rb_bot" = "healthy" ] && [ "$rb_caddy_ok" = "true" ]; then
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
                print_ai_diagnostic_report \
                    "Just1kBot Update Rollback" \
                    "Новая версия не прошла healthcheck. Выполнен откат кода к $rollback_commit" \
                    "Контейнеры возвращены к предыдущей версии, но схема БД могла обновиться" \
                    "$bot_err_logs" \
                    "1. Проверьте логи бота: just1kbot logs bot\n2. При необходимости восстановите БД: just1kbot restore $pre_update_backup"
            else
                error "КРИТИЧЕСКАЯ ОШИБКА: Сервисы не смогли подняться после отката кода!"
                warn "Вероятная причина: применённая миграция изменила схему БД и несовместима со старой версией кода."
                if [[ -n "$pre_update_backup" ]] && [[ -f "$pre_update_backup" ]]; then
                    warn "Для полного восстановления рабочей базы данных выполните:"
                    echo -e "${BOLD}${YELLOW}    just1kbot restore $pre_update_backup${NC}"
                else
                    warn "Для полного восстановления рабочей базы данных выполните: just1kbot restore"
                fi
                print_ai_diagnostic_report \
                    "Just1kBot Critical Rollback Failure" \
                    "Сервисы старой версии не смогли подняться после отката кода" \
                    "Схема БД несовместима со старой версией кода" \
                    "$(docker compose ps 2>/dev/null || true)\n\n$bot_err_logs" \
                    "1. Немедленно восстановите рабочую базу данных: just1kbot restore $pre_update_backup\n2. Проверьте логи: just1kbot logs bot"
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

    if [[ -t 0 ]]; then
        info "Просмотр последних логов бота:"
        echo -e "${CYAN}Нажмите Ctrl+C для возврата в меню...${NC}\n"
        cmd_logs "bot"
    fi
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
    chmod 700 backups 2>/dev/null || true

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
                        chmod 600 "$backup_file" 2>/dev/null || true
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
            chmod 600 "$latest_backup" 2>/dev/null || true
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

    # pg_user/pg_db were previously resolved as `local` inside cmd_backup and
    # therefore undefined here (crash under `set -u`). Resolve them locally.
    local pg_user pg_db
    pg_user=$(grep -E "^POSTGRES_USER=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
    pg_db=$(grep -E "^POSTGRES_DB=" "${PROJECT_DIR}/.env" 2>/dev/null | cut -d'=' -f2- | tr -d " '\"" || echo "")
    pg_user="${pg_user:-just1kbot}"
    pg_db="${pg_db:-just1kbot_bot}"

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

    # Страховочный дамп текущего состояния БД сохраняется в ./backups/ (НЕ во
    # временной директории): он должен пережить завершение restore, чтобы к
    # нему можно было вернуться, если выбранный бэкап оказался неудачным.
    info "3.1/5. Создание предварительного страховочного дампа текущей БД..."
    local restore_ts
    restore_ts=$(date +%Y%m%d_%H%M%S)
    local pre_restore_backup_file="${PROJECT_DIR}/backups/pre_restore_${restore_ts}.sql.gz"
    mkdir -p "${PROJECT_DIR}/backups"
    # Safety dump is a hard precondition of the destructive phase. Fail-closed:
    # pg_dump/gzip failure (pipefail is global) or an invalid archive aborts
    # restore BEFORE any DROP DATABASE. No plaintext dump with loose perms.
    umask 077
    if ! docker compose exec -T db pg_dump -U "$pg_user" -d "$pg_db" 2>/dev/null | gzip -c > "$pre_restore_backup_file"; then
        error "Не удалось создать страховочный дамп текущей БД. Восстановление отменено — база НЕ изменена."
        docker compose start bot >/dev/null 2>&1 || true
        return 1
    fi
    if ! gzip -t "$pre_restore_backup_file" 2>/dev/null || [[ ! -s "$pre_restore_backup_file" ]]; then
        error "Страховочный дамп пуст или повреждён. Восстановление отменено — база НЕ изменена."
        docker compose start bot >/dev/null 2>&1 || true
        return 1
    fi

    info "4/5. Полная переинициализация базы данных и накат дампа..."
    # Destructive phase is strict: any dropdb/createdb failure aborts BEFORE
    # the database is left in a partially reinitialized state.
    if ! docker compose exec -T db dropdb -U "$pg_user" --if-exists "$pg_db" >/dev/null 2>&1; then
        error "Не удалось удалить текущую базу данных '$pg_db'. Восстановление отменено."
        docker compose start bot >/dev/null 2>&1 || true
        return 1
    fi
    if ! docker compose exec -T db createdb -U "$pg_user" "$pg_db"; then
        error "Не удалось создать базу данных '$pg_db' после удаления. Автоматический откат невозможен — восстановите исходное состояние вручную из: $pre_restore_backup_file"
        docker compose start bot >/dev/null 2>&1 || true
        return 1
    fi

    if ! docker compose exec -T db psql -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=1 < "$tmp_sql"; then
        error "Ошибка при накате SQL дампа в PostgreSQL!"
        if [[ -s "$pre_restore_backup_file" ]]; then
            warn "Попытка отката к состоянию базы данных до начала операции восстановления..."
            # Rollback is also strict: if reinitialize+replay of the safety
            # dump cannot be completed, say so loudly instead of pretending.
            if docker compose exec -T db dropdb -U "$pg_user" --if-exists "$pg_db" >/dev/null 2>&1 \
                && docker compose exec -T db createdb -U "$pg_user" "$pg_db" \
                && gunzip -c "$pre_restore_backup_file" | docker compose exec -T db psql -U "$pg_user" -d "$pg_db" -v ON_ERROR_STOP=1 >/dev/null 2>&1; then
                warn "Исходное состояние базы данных возвращено."
            else
                warn "Автоматический откат не удался. Страховочный дамп сохранён: $pre_restore_backup_file — восстановите его вручную."
            fi
        fi
        docker compose start bot >/dev/null 2>&1 || true
        return 1
    fi

    rm -rf "$tmp_dir"
    trap - EXIT INT TERM

    # Страховочный дамп сохранён в ./backups/ и переживает restore; храним
    # ограниченное окно последних дампов, чтобы каталог не рос бесконечно.
    find "${PROJECT_DIR}/backups" -name "pre_restore_*.sql.gz" -mtime +14 -delete 2>/dev/null || true
    if [[ -s "$pre_restore_backup_file" ]]; then
        chmod 600 "$pre_restore_backup_file" 2>/dev/null || true
        log "Страховочный дамп состояния до восстановления сохранён: ${BOLD}${pre_restore_backup_file}${NC}"
    fi

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
    dc_up
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
        dc_up --force-recreate
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

    # 0.1 Проверка памяти ядра для Redis
    if [[ -f /proc/sys/vm/overcommit_memory ]]; then
        local overcommit
        overcommit="$(cat /proc/sys/vm/overcommit_memory 2>/dev/null || echo '0')"
        if [[ "$overcommit" == "1" ]]; then
            log "Параметр ядра vm.overcommit_memory=1 активен (Redis BGSAVE защищен)."
        else
            warn "vm.overcommit_memory=$overcommit. Рекомендуется установить 'sysctl vm.overcommit_memory=1' для предотвращения сбоев Redis BGSAVE."
        fi
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
            if is_external_nginx_enabled; then
                log "Порт $port слушается системным Nginx (Reverse Proxy)."
            else
                log "Порт $port слушается веб-сервером (Caddy)."
            fi
        else
            if is_external_nginx_enabled; then
                warn "Порт $port не слушается. Проверьте статус Nginx: systemctl status nginx"
            else
                warn "Порт $port не слушается. Проверьте статус контейнера Caddy."
            fi
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
        echo -e "  [${BOLD}9${NC}] 🌐 ${BOLD}Интеграция с Nginx${NC} (Настроить совместную работу / Reverse Proxy)"
        echo -e "  [${BOLD}0${NC}] ❌ ${BOLD}Выход${NC}"
        echo ""
        echo -e "${CYAN}────────────────────────────────────────────────────────────────────────────────${NC}"
        read -r -p "Выберите действие [0-9]: " choice

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
            9)
                cmd_nginx_config || true
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
            nginx-config|nginx)
                cmd_nginx_config
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
                echo -e "Использование: just1kbot [status|logs|update|preflight|backup|restore|restart|start|stop|config|nginx-config|doctor|clean]"
                ;;
            *)
                error "Неизвестная команда: $1. Используйте 'just1kbot help'."
                ;;
        esac
    fi
}

if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" || -z "${BASH_SOURCE[0]:-}" ]]; then
    main "$@"
fi
