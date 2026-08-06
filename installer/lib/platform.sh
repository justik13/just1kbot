install_packages_apt() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    local packages=(
        ca-certificates curl tar git rsync util-linux age openssl
        python3 python3-pip python3-venv
        postgresql postgresql-client postgresql-contrib
        redis-server nginx certbot
    )
    apt-get install -y --no-install-recommends "${packages[@]}"
}

install_packages_dnf() {
    dnf install -y epel-release >/dev/null 2>&1 || true
    dnf install -y ca-certificates curl tar git rsync util-linux age openssl \
        python3.11 python3.11-pip postgresql-server postgresql redis nginx certbot
    if [[ ! -s /var/lib/pgsql/data/PG_VERSION ]]; then
        postgresql-setup --initdb
    fi
}

install_system_packages() {
    info "Устанавливаю системные зависимости..."
    if command -v apt-get >/dev/null 2>&1; then
        install_packages_apt
    elif command -v dnf >/dev/null 2>&1; then
        install_packages_dnf
    else
        die "Поддерживаются Debian/Ubuntu и RHEL-совместимые системы с dnf."
    fi
    check_python_version
    ok "Системные зависимости установлены."
}

setup_installer_logrotate() {
    cat > /etc/logrotate.d/just1kbot-installer <<ROTATE
${INSTALL_LOG} {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su root root
}
ROTATE
    chmod 644 /etc/logrotate.d/just1kbot-installer
}

unit_exists() {
    local unit="$1"
    [[ "$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)" != "not-found" ]] \
        && [[ -n "$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)" ]]
}

preflight() {
    [[ -d /run/systemd/system ]] || die "Требуется сервер с systemd в роли PID 1."
    local free_mb
    free_mb="$(df -Pm "$APP_ROOT" | awk 'NR==2 {print $4}')"
    [[ "$free_mb" =~ ^[0-9]+$ ]] || die "Не удалось определить свободное место."
    (( free_mb >= 1024 )) || die "Недостаточно места: требуется минимум 1 ГБ, доступно ${free_mb} МБ."
    curl -fsS --connect-timeout 10 --max-time 20 "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}" >/dev/null \
        || die "GitHub недоступен или репозиторий ${REPO_OWNER}/${REPO_NAME} не найден."
}

start_platform_services() {
    local pg_service=""
    if unit_exists postgresql.service; then
        pg_service="postgresql.service"
    elif unit_exists postgresql-server.service; then
        pg_service="postgresql-server.service"
    fi
    [[ -n "$pg_service" ]] || die "Служба PostgreSQL не найдена."
    systemctl enable --now "$pg_service"

    local redis_service=""
    if unit_exists redis-server.service; then
        redis_service="redis-server.service"
    elif unit_exists redis.service; then
        redis_service="redis.service"
    fi
    [[ -n "$redis_service" ]] || die "Служба Redis не найдена."
    systemctl enable --now "$redis_service"
    unit_exists nginx.service || die "Служба Nginx не найдена."
    systemctl enable --now nginx.service
}

migrate_legacy_env() {
    if [[ ! -f "$ENV_FILE" && -f "$LEGACY_ENV_FILE" ]]; then
        info "Переношу существующий .env в ${ENV_FILE}..."
        install -o root -g "$BOT_GROUP" -m 640 "$LEGACY_ENV_FILE" "$ENV_FILE"
    fi
    if [[ -f "$ENV_FILE" ]]; then
        ln -sfn "$ENV_FILE" "$LEGACY_ENV_FILE"
    fi
}

configure_env() {
    info "Проверяю production-конфигурацию..."
    mkdir -p "$CONFIG_DIR"
    if [[ ! -f "$ENV_FILE" ]]; then
        cat > "$ENV_FILE" <<'ENV'
# just1kbot production environment
ENV
        chown root:"$BOT_GROUP" "$ENV_FILE"
        chmod 640 "$ENV_FILE"
    fi

    require_env_value BOT_TOKEN "Telegram BOT_TOKEN" "" 1
    require_env_value ADMIN_IDS "Telegram ID администраторов в JSON-массиве, например [123456789]" ""
    require_env_value SUPPORT_USERNAME "Username поддержки без @" ""

    local db_url db_password db_password_encoded encryption_key
    db_url="$(get_env_value DATABASE_URL)"
    if is_placeholder_value DATABASE_URL "$db_url"; then
        if [[ -n "${DATABASE_URL:-}" ]] && ! is_placeholder_value DATABASE_URL "$DATABASE_URL"; then
            set_env_value DATABASE_URL "$DATABASE_URL"
        else
            db_password="${DATABASE_PASSWORD:-$(generate_secret)}"
            db_password_encoded="$(urlencode "$db_password")"
            set_env_value DATABASE_URL "postgresql+asyncpg://just1kbot:${db_password_encoded}@localhost:5432/just1kbot_bot"
        fi
    fi

    encryption_key="$(get_env_value DB_ENCRYPTION_KEY)"
    if [[ -z "$encryption_key" || "${encryption_key,,}" == *change_me* ]]; then
        if [[ -n "${DB_ENCRYPTION_KEY:-}" && "${DB_ENCRYPTION_KEY,,}" != *change_me* ]]; then
            set_env_value DB_ENCRYPTION_KEY "$DB_ENCRYPTION_KEY"
        else
            set_env_value DB_ENCRYPTION_KEY "$(generate_fernet_key)"
        fi
    fi

    local redis_password redis_password_encoded redis_url
    redis_password="$(get_env_value REDIS_PASSWORD)"
    redis_url="$(get_env_value REDIS_URL)"
    if is_placeholder_value REDIS_PASSWORD "$redis_password"; then
        if [[ -n "${REDIS_PASSWORD:-}" ]] && ! is_placeholder_value REDIS_PASSWORD "$REDIS_PASSWORD"; then
            redis_password="$REDIS_PASSWORD"
        elif [[ -n "${REDIS_URL:-}" ]] && parse_redis_url "$REDIS_URL" && [[ -n "$REDIS_URL_PASS" ]]; then
            redis_password="$REDIS_URL_PASS"
        else
            redis_password="$(generate_secret)"
        fi
        set_env_value REDIS_PASSWORD "$redis_password"
    fi
    if [[ -z "$redis_url" || "${redis_url,,}" == *change_me* ]]; then
        if [[ -n "${REDIS_URL:-}" && "${REDIS_URL,,}" != *change_me* ]]; then
            set_env_value REDIS_URL "$REDIS_URL"
        else
            redis_password_encoded="$(urlencode "$redis_password")"
            set_env_value REDIS_URL "redis://:${redis_password_encoded}@localhost:6379/0"
        fi
    fi

    require_env_value YOOKASSA_SHOP_ID "YOOKASSA_SHOP_ID" ""
    require_env_value YOOKASSA_SECRET_KEY "YOOKASSA_SECRET_KEY" "" 1
    [[ -n "$(get_env_value YOOKASSA_RETURN_URL)" ]] \
        || set_env_value YOOKASSA_RETURN_URL "${YOOKASSA_RETURN_URL:-https://t.me/{bot_username}}"
    [[ -n "$(get_env_value YOOKASSA_WEBHOOK_PORT)" ]] \
        || set_env_value YOOKASSA_WEBHOOK_PORT "${YOOKASSA_WEBHOOK_PORT:-8080}"
    require_env_value DOMAIN "Публичный домен без https://" ""
    require_env_value SSL_EMAIL "Email для Let's Encrypt" ""

    local default_key value legacy_key
    while IFS='=' read -r default_key value; do
        [[ -n "$(get_env_value "$default_key")" ]] || set_env_value "$default_key" "$value"
    done <<'DEFAULTS'
BALANCE_MIN_TOPUP_RUB=10
BALANCE_MAX_CUSTOM_TOPUP_RUB=5000
BALANCE_MAX_AVAILABLE_RUB=10000
BALANCE_MAX_PRESET_RUB=1000
BALANCE_MAX_UNFINISHED_TOPUPS=3
BALANCE_MAX_TOPUP_CREATIONS_24H=10
BALANCE_MAX_PRESET_OPTIONS=6
ALLOW_LOCAL_HTTP=false
ALLOW_LOCAL_HTTPS=false
DEFAULTS

    for legacy_key in AMNEZIA_API_URL AMNEZIA_API_KEY WEBHOOK_URL; do
        unset_env_value "$legacy_key"
    done

    chown root:"$BOT_GROUP" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    ln -sfn "$ENV_FILE" "$LEGACY_ENV_FILE"
    ok "Конфигурация подготовлена: ${ENV_FILE}"
}

setup_backup_key() {
    mkdir -p "$CONFIG_DIR"
    if [[ ! -s "$BACKUP_KEY_FILE" ]]; then
        info "Создаю ключ шифрования резервных копий..."
        local tmp
        tmp="$(make_temp_dir)"
        age-keygen -o "$tmp/backup.agekey" >/dev/null 2>&1
        install -o root -g root -m 600 "$tmp/backup.agekey" "$BACKUP_KEY_FILE"
    fi
    chown root:root "$BACKUP_KEY_FILE"
    chmod 600 "$BACKUP_KEY_FILE"
    age-keygen -y "$BACKUP_KEY_FILE" > "$BACKUP_RECIPIENT_FILE"
    chown root:root "$BACKUP_RECIPIENT_FILE"
    chmod 644 "$BACKUP_RECIPIENT_FILE"
}

setup_local_postgres() {
    local db_url
    db_url="$(get_env_value DATABASE_URL)"
    parse_database_url "$db_url" || die "Некорректный DATABASE_URL."
    is_local_host "$DB_HOST" || { info "DATABASE_URL указывает на внешнюю БД — автонастройка пропущена."; return 0; }
    [[ -n "$DB_PASS" ]] || die "Для локального PostgreSQL пароль в DATABASE_URL не может быть пустым."
    [[ "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "Некорректное имя пользователя PostgreSQL."
    [[ "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "Некорректное имя базы PostgreSQL."

    info "Настраиваю локальный PostgreSQL..."
    JUST1KBOT_DB_PASS="$DB_PASS" runuser --preserve-environment -u postgres -- \
        psql --set=ON_ERROR_STOP=1 --set=db_user="$DB_USER" <<'SQL'
\getenv db_pass JUST1KBOT_DB_PASS
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db_user') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_pass') \gexec
SQL
    runuser -u postgres -- psql --set=ON_ERROR_STOP=1 --set=db_user="$DB_USER" --set=db_name="$DB_NAME" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'db_user') \gexec
SQL
    runuser -u postgres -- psql --set=ON_ERROR_STOP=1 --dbname="$DB_NAME" --set=db_user="$DB_USER" <<'SQL'
SELECT format('ALTER SCHEMA public OWNER TO %I', :'db_user') \gexec
SELECT format('GRANT ALL ON SCHEMA public TO %I', :'db_user') \gexec
SQL
    ok "PostgreSQL готов: ${DB_NAME}."
}

find_redis_config() {
    local candidate
    for candidate in /etc/redis/redis.conf /etc/redis.conf; do
        [[ -f "$candidate" ]] && { printf '%s' "$candidate"; return 0; }
    done
    return 1
}

redis_service_name() {
    if unit_exists redis-server.service; then printf '%s' redis-server.service
    elif unit_exists redis.service; then printf '%s' redis.service
    else return 1
    fi
}

setup_local_redis() {
    local redis_url redis_password config service include_file
    redis_url="$(get_env_value REDIS_URL)"
    redis_password="$(get_env_value REDIS_PASSWORD)"
    parse_redis_url "$redis_url" || die "Некорректный REDIS_URL."
    is_local_host "$REDIS_HOST" || { info "REDIS_URL указывает на внешний Redis — автонастройка пропущена."; return 0; }
    [[ -n "$redis_password" ]] || die "REDIS_PASSWORD пуст."

    [[ -z "$REDIS_USER" || "$REDIS_USER" == "default" ]] \
        || die "Локальная автонастройка поддерживает только default Redis user. Для ACL user используйте внешний Redis."
    if [[ "$REDIS_URL_PASS" != "$redis_password" ]]; then
        local encoded encoded_user url_host auth_part
        encoded="$(urlencode "$redis_password")"
        encoded_user="$(urlencode "$REDIS_USER")"
        auth_part=":"
        [[ -n "$encoded_user" ]] && auth_part="${encoded_user}:"
        url_host="$REDIS_HOST"
        [[ "$url_host" == *:* ]] && url_host="[${url_host}]"
        set_env_value REDIS_URL "${REDIS_SCHEME}://${auth_part}${encoded}@${url_host}:${REDIS_PORT}/${REDIS_DB}"
    fi

    config="$(find_redis_config)" || die "Конфигурация Redis не найдена."
    service="$(redis_service_name)" || die "Служба Redis не найдена."
    include_file="$(dirname "$config")/just1kbot.conf"
    JUST1KBOT_REDIS_PASSWORD="$redis_password" "$PYTHON_BIN" - > "$include_file" <<'PY'
import os

raw = os.environ.pop("JUST1KBOT_REDIS_PASSWORD").encode("utf-8")
parts = ['"']
for byte in raw:
    if byte == 34:
        parts.append(r'\"')
    elif byte == 92:
        parts.append(r'\\')
    elif 32 <= byte <= 126:
        parts.append(chr(byte))
    else:
        parts.append(f"\\x{byte:02x}")
parts.append('"')
print("requirepass " + "".join(parts))
PY
    local redis_user redis_group
    redis_user="$(systemctl show -p User --value "$service" 2>/dev/null || true)"
    if [[ -n "$redis_user" ]] && id "$redis_user" >/dev/null 2>&1; then
        redis_group="$(id -gn "$redis_user")"
    else
        redis_group="$(stat -c '%G' "$config")"
    fi
    chown root:"$redis_group" "$include_file"
    chmod 640 "$include_file"
    grep -Fqx "include $include_file" "$config" || printf '\ninclude %s\n' "$include_file" >> "$config"

    systemctl restart "$service"
    for _ in {1..20}; do
        if REDISCLI_AUTH="$redis_password" redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -qx PONG; then
            ok "Локальный Redis защищён паролем и доступен."
            return 0
        fi
        sleep 1
    done
    die "Redis не отвечает после настройки."
}

resolve_remote_sha() {
    curl -fsSL --connect-timeout 10 --max-time 30 --retry 3 "$COMMIT_API_URL" \
        | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["sha"])'
}

download_source() {
    local ref="$1" destination="$2" archive tmp extract
    tmp="$(make_temp_dir)"
    archive="$tmp/source.tar.gz"
    curl -fL --connect-timeout 20 --max-time 180 --retry 3 \
        "https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/${ref}" -o "$archive"
    tar -xzf "$archive" -C "$tmp"
    extract="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d ! -path "$tmp" | head -n1)"
    [[ -d "$extract/bot" && -f "$extract/requirements.txt" && -f "$extract/alembic.ini" \
        && -f "$extract/installer/entrypoint.sh" ]] \
        || die "Архив репозитория не содержит обязательные файлы."
    mkdir -p "$destination"
    rsync -a --delete --exclude='.git' --exclude='.env' "$extract/" "$destination/"
}

