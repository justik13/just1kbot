#!/usr/bin/env bash

# Production Redis recovery layer.
# Loaded after the base installer and compat layer so legacy Redis settings
# can be repaired before the bot is migrated or started.

start_platform_services() {
    local pg_service=""
    if unit_exists postgresql.service; then
        pg_service="postgresql.service"
    elif unit_exists postgresql-server.service; then
        pg_service="postgresql-server.service"
    fi
    [[ -n "$pg_service" ]] || die "Служба PostgreSQL не найдена."
    systemctl enable --now "$pg_service"

    # Redis is intentionally not started here. The old installation may have
    # a broken just1kbot include or stale password. setup_local_redis repairs
    # that configuration after .env migration.
    unit_exists redis-server.service || unit_exists redis.service \
        || die "Служба Redis не найдена."

    unit_exists nginx.service || die "Служба Nginx не найдена."
    systemctl enable --now nginx.service
}

normalize_redis_environment() {
    local redis_url redis_password encoded host port db user scheme
    redis_url="$(get_env_value REDIS_URL)"
    redis_password="$(get_env_value REDIS_PASSWORD)"

    if [[ -n "$redis_url" ]] && parse_redis_url "$redis_url"; then
        scheme="$REDIS_SCHEME"
        host="$REDIS_HOST"
        port="$REDIS_PORT"
        db="$REDIS_DB"
        user="$REDIS_USER"

        if is_local_host "$host"; then
            if is_placeholder_value REDIS_PASSWORD "$redis_password"; then
                if [[ -n "$REDIS_URL_PASS" ]] && ! is_placeholder_value REDIS_PASSWORD "$REDIS_URL_PASS"; then
                    redis_password="$REDIS_URL_PASS"
                else
                    redis_password="$(generate_secret)"
                fi
                set_env_value REDIS_PASSWORD "$redis_password"
            fi

            if [[ "$REDIS_URL_PASS" != "$redis_password" || -z "$REDIS_URL_PASS" ]]; then
                encoded="$(urlencode "$redis_password")"
                [[ "$host" == *:* ]] && host="[${host}]"
                if [[ "$user" == "default" ]]; then
                    set_env_value REDIS_URL "${scheme}://default:${encoded}@${host}:${port}/${db}"
                else
                    set_env_value REDIS_URL "${scheme}://:${encoded}@${host}:${port}/${db}"
                fi
            fi
            return 0
        fi

        # External Redis: keep the supplied endpoint and copy the URL password
        # into REDIS_PASSWORD when the latter is missing or a legacy placeholder.
        if is_placeholder_value REDIS_PASSWORD "$redis_password" \
            && [[ -n "$REDIS_URL_PASS" ]] \
            && ! is_placeholder_value REDIS_PASSWORD "$REDIS_URL_PASS"; then
            set_env_value REDIS_PASSWORD "$REDIS_URL_PASS"
        fi
        return 0
    fi

    warn "REDIS_URL имеет некорректный формат. Переключаюсь на локальный Redis."
    if is_placeholder_value REDIS_PASSWORD "$redis_password"; then
        redis_password="$(generate_secret)"
    fi
    set_env_value REDIS_PASSWORD "$redis_password"
    encoded="$(urlencode "$redis_password")"
    set_env_value REDIS_URL "redis://:${encoded}@localhost:6379/0"
}

# Keep the ADMIN_IDS migration from compat.sh, then normalize Redis settings.
eval "$(declare -f configure_env | sed '1s/^configure_env /configure_env_before_redis_fix /')"
configure_env() {
    configure_env_before_redis_fix "$@"
    normalize_redis_environment
}

quote_redis_password() {
    JUST1KBOT_REDIS_PASSWORD="$1" "$PYTHON_BIN" - <<'PY'
import os

value = os.environ.pop("JUST1KBOT_REDIS_PASSWORD")
print('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"')
PY
}

repair_redis_main_config() {
    local config="$1"
    local backup="${config}.just1kbot-backup"
    if [[ ! -f "$backup" ]]; then
        cp -a "$config" "$backup"
    fi

    "$PYTHON_BIN" - "$config" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
filtered = []
for line in lines:
    stripped = line.strip()
    if stripped.startswith("include ") and "just1kbot.conf" in stripped:
        continue
    filtered.append(line)

path.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")
PY
}

show_redis_failure_details() {
    local service="$1"
    systemctl status "$service" --no-pager -l >&2 || true
    journalctl -u "$service" -n 80 --no-pager >&2 || true
}

setup_local_redis() {
    local redis_url redis_password config service include_file redis_user redis_group
    redis_url="$(get_env_value REDIS_URL)"
    redis_password="$(get_env_value REDIS_PASSWORD)"

    normalize_redis_environment
    redis_url="$(get_env_value REDIS_URL)"
    redis_password="$(get_env_value REDIS_PASSWORD)"
    parse_redis_url "$redis_url" || die "Не удалось исправить REDIS_URL."

    is_local_host "$REDIS_HOST" || {
        info "REDIS_URL указывает на внешний Redis — локальная служба Redis не настраивается."
        return 0
    }
    [[ -n "$redis_password" ]] || die "REDIS_PASSWORD пуст."
    [[ -z "$REDIS_USER" || "$REDIS_USER" == "default" ]] \
        || die "Локальная автонастройка поддерживает только default Redis user."

    config="$(find_redis_config)" || die "Конфигурация Redis не найдена."
    service="$(redis_service_name)" || die "Служба Redis не найдена."
    include_file="$(dirname "$config")/just1kbot.conf"

    repair_redis_main_config "$config"

    redis_user="$(systemctl show -p User --value "$service" 2>/dev/null || true)"
    if [[ -n "$redis_user" ]] && id "$redis_user" >/dev/null 2>&1; then
        redis_group="$(id -gn "$redis_user")"
    else
        redis_group="$(stat -c '%G' "$config")"
    fi

    cat > "$include_file" <<REDISCONF
# Managed by just1kbot. Do not edit manually.
protected-mode yes
bind 127.0.0.1 ::1
port 6379
requirepass $(quote_redis_password "$redis_password")
REDISCONF
    chown root:"$redis_group" "$include_file"
    chmod 640 "$include_file"
    printf '\ninclude %s\n' "$include_file" >> "$config"

    systemctl reset-failed "$service" 2>/dev/null || true
    systemctl enable "$service" >/dev/null 2>&1 || true

    if ! systemctl restart "$service"; then
        warn "Redis не запустился после очистки legacy-конфигурации. Восстанавливаю исходный конфиг."
        if [[ -f "${config}.just1kbot-backup" ]]; then
            cp -af "${config}.just1kbot-backup" "$config"
        fi
        rm -f "$include_file"
        systemctl reset-failed "$service" 2>/dev/null || true
        systemctl restart "$service" || {
            show_redis_failure_details "$service"
            die "Не удалось запустить Redis даже на исходной конфигурации."
        }

        repair_redis_main_config "$config"
        cat > "$include_file" <<REDISCONF
# Managed by just1kbot. Do not edit manually.
protected-mode yes
bind 127.0.0.1 ::1
port 6379
requirepass $(quote_redis_password "$redis_password")
REDISCONF
        chown root:"$redis_group" "$include_file"
        chmod 640 "$include_file"
        printf '\ninclude %s\n' "$include_file" >> "$config"
        systemctl reset-failed "$service" 2>/dev/null || true
        systemctl restart "$service" || {
            show_redis_failure_details "$service"
            die "Redis не удалось запустить после восстановления конфигурации."
        }
    fi

    for _ in {1..30}; do
        if REDISCLI_AUTH="$redis_password" redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -qx PONG; then
            ok "Локальный Redis настроен, защищён паролем и доступен."
            return 0
        fi
        sleep 1
    done

    show_redis_failure_details "$service"
    die "Redis запущен, но не отвечает на проверку подключения."
}
