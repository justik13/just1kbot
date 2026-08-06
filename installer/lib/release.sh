prepare_release() {
    local sha="$1" release_dir="${RELEASES_DIR}/${sha}" marker
    marker="${release_dir}/.prepared-sha"
    if [[ -x "$release_dir/.venv/bin/python" && -f "$marker" ]] \
        && [[ "$(cat "$marker" 2>/dev/null || true)" == "$sha" ]]; then
        printf '%s' "$release_dir"
        return 0
    fi
    info "Подготавливаю релиз ${sha:0:12}..."
    rm -rf -- "$release_dir"
    download_source "$sha" "$release_dir"
    "$PYTHON_BIN" -m venv "$release_dir/.venv"
    "$release_dir/.venv/bin/python" -m pip install --upgrade pip setuptools wheel >&2
    "$release_dir/.venv/bin/pip" install --requirement "$release_dir/requirements.txt" >&2
    "$release_dir/.venv/bin/python" -m compileall -q \
        "$release_dir/bot" "$release_dir/config" "$release_dir/database" \
        "$release_dir/services" "$release_dir/utils" "$release_dir/alembic"
    ln -sfn "$ENV_FILE" "$release_dir/.env"
    printf '%s\n' "$sha" > "$marker"
    chown -R root:"$BOT_GROUP" "$release_dir"
    chmod -R u=rwX,g=rX,o= "$release_dir"
    chmod +x "$release_dir/just1kbot.sh"
    printf '%s' "$release_dir"
}

capture_legacy_release() {
    [[ -L "$CURRENT_LINK" ]] && return 0
    [[ -d "$APP_ROOT/bot" && -x "$APP_ROOT/.venv/bin/python" ]] || return 0
    local snapshot="${RELEASES_DIR}/legacy-$(date -u +%Y%m%dT%H%M%SZ)"
    info "Сохраняю прежнюю layout-установку для rollback..."
    mkdir -p "$snapshot"
    rsync -a \
        --exclude='releases' --exclude='current' --exclude='.state' \
        --exclude='.env' --exclude='backups' \
        "$APP_ROOT/" "$snapshot/"
    ln -sfn "$ENV_FILE" "$snapshot/.env"
    chown -R root:"$BOT_GROUP" "$snapshot"
    chmod -R u=rwX,g=rX,o= "$snapshot"
    printf '%s' "$snapshot"
}

cleanup_legacy_layout() {
    local path
    for path in \
        "$APP_ROOT/bot" "$APP_ROOT/config" "$APP_ROOT/database" "$APP_ROOT/services" "$APP_ROOT/utils" \
        "$APP_ROOT/alembic" "$APP_ROOT/alembic.ini" "$APP_ROOT/requirements.txt" "$APP_ROOT/just1kbot.sh" \
        "$APP_ROOT/.venv" "$APP_ROOT/.env.example"; do
        rm -rf -- "$path"
    done
    ln -sfn "$ENV_FILE" "$LEGACY_ENV_FILE"
}

validate_release() {
    local release_dir="$1"
    info "Проверяю импорт приложения и production-настройки..."
    (
        cd "$release_dir"
        runuser -u "$BOT_USER" -- env PYTHONDONTWRITEBYTECODE=1 "$release_dir/.venv/bin/python" - <<'PY'
from config.settings import get_settings
settings = get_settings()
assert settings.BOT_TOKEN
assert settings.ADMIN_IDS
print("settings-ok")
PY
    ) >/dev/null
}

run_migrations() {
    local release_dir="$1"
    info "Применяю миграции Alembic..."
    (
        cd "$release_dir"
        runuser -u "$BOT_USER" -- env PYTHONDONTWRITEBYTECODE=1 \
            "$release_dir/.venv/bin/alembic" -c "$release_dir/alembic.ini" upgrade head
    )
}

setup_systemd_service() {
    cat > "$SERVICE_FILE" <<UNIT
[Unit]
Description=just1kbot Telegram VPN bot
Wants=network-online.target
After=network-online.target postgresql.service postgresql-server.service redis.service redis-server.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_GROUP}
WorkingDirectory=${CURRENT_LINK}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${CURRENT_LINK}/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=45
KillSignal=SIGTERM
UMask=0077
RuntimeDirectory=just1kbot
RuntimeDirectoryMode=0700
LogsDirectory=just1kbot
LogsDirectoryMode=0750
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
SystemCallArchitectures=native
ReadOnlyPaths=${APP_ROOT} ${CONFIG_DIR}

[Install]
WantedBy=multi-user.target
UNIT
    chmod 644 "$SERVICE_FILE"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
}

switch_release() {
    local release_dir="$1" tmp_link="${CURRENT_LINK}.new"
    [[ -x "$release_dir/.venv/bin/python" && -f "$release_dir/just1kbot.sh" ]] \
        || die "Релиз не подготовлен: $release_dir"
    ln -sfn "$release_dir" "$tmp_link"
    mv -Tf "$tmp_link" "$CURRENT_LINK"
    printf '%s\n' "$(basename "$release_dir")" > "$RELEASE_SHA_FILE"
    printf '%s\n' "$REPO_BRANCH" > "$REPO_BRANCH_FILE"
    chown root:"$BOT_GROUP" "$RELEASE_SHA_FILE" "$REPO_BRANCH_FILE"
    chmod 640 "$RELEASE_SHA_FILE" "$REPO_BRANCH_FILE"
    ln -sfn "$release_dir/just1kbot.sh" "$SELF_SYMLINK"
}

healthcheck_local() {
    local port
    port="$(get_env_value YOOKASSA_WEBHOOK_PORT)"
    port="${port:-8080}"
    for _ in {1..30}; do
        if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

setup_nginx_http() {
    local domain port
    domain="$(get_env_value DOMAIN)"
    port="$(get_env_value YOOKASSA_WEBHOOK_PORT)"
    mkdir -p "$ACME_WEBROOT"
    cat > "$NGINX_CONF" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};

    location ^~ /.well-known/acme-challenge/ {
        root ${ACME_WEBROOT};
        default_type text/plain;
    }

    location = /health {
        proxy_pass http://127.0.0.1:${port}/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location = /webhook/yookassa {
        proxy_pass http://127.0.0.1:${port}/webhook/yookassa;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        client_max_body_size 256k;
    }

    location / { return 404; }
}
NGINX
    nginx -t
    systemctl reload nginx
}

setup_nginx_tls() {
    if [[ "$INSTALL_TLS" != "1" ]]; then
        setup_nginx_http
        warn "TLS пропущен по INSTALL_TLS=0. YooKassa webhook нельзя считать production-защищённым без HTTPS."
        return 0
    fi
    local domain email port
    domain="$(get_env_value DOMAIN)"
    email="$(get_env_value SSL_EMAIL)"
    port="$(get_env_value YOOKASSA_WEBHOOK_PORT)"

    setup_nginx_http
    info "Получаю/обновляю сертификат Let's Encrypt для ${domain}..."
    certbot certonly --webroot --webroot-path "$ACME_WEBROOT" --domain "$domain" \
        --email "$email" --agree-tos --non-interactive --keep-until-expiring

    cat > "$NGINX_CONF" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    location ^~ /.well-known/acme-challenge/ { root ${ACME_WEBROOT}; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${domain};

    ssl_certificate /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;

    location = /health {
        proxy_pass http://127.0.0.1:${port}/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }

    location = /webhook/yookassa {
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:${port}/webhook/yookassa;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 30s;
        client_max_body_size 256k;
    }

    location / { return 404; }
}
NGINX
    nginx -t
    systemctl reload nginx
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
    ok "HTTPS настроен: https://${domain}/health"
}

create_backup() {
    require_root
    select_python || die "Требуется Python 3.10 или новее."
    setup_backup_key
    local db_url timestamp tmp archive recipient
    db_url="$(get_env_value DATABASE_URL)"
    parse_database_url "$db_url" || die "Некорректный DATABASE_URL."
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    tmp="$(make_temp_dir)"
    archive="${BACKUP_ROOT}/just1kbot_${timestamp}.tar.gz.age"
    recipient="$(cat "$BACKUP_RECIPIENT_FILE")"

    info "Создаю согласованный дамп PostgreSQL..."
    PGPASSWORD="$DB_PASS" pg_dump --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" \
        --dbname "$DB_NAME" --format=custom --no-owner --file "$tmp/database.dump"
    install -m 600 "$ENV_FILE" "$tmp/just1kbot.env"
    printf 'release=%s\nbranch=%s\ncreated_at=%s\n' \
        "$(cat "$RELEASE_SHA_FILE" 2>/dev/null || true)" "$REPO_BRANCH" "$timestamp" > "$tmp/metadata.txt"
    tar -C "$tmp" -czf - database.dump just1kbot.env metadata.txt \
        | age --recipient "$recipient" --output "$archive"
    chmod 600 "$archive"
    ok "Зашифрованный бэкап создан: ${archive}"
    printf '%s\n' "$archive"
}

restore_backup() {
    require_root
    select_python || die "Требуется Python 3.10 или новее."
    local archive="$1" tmp db_url
    [[ -f "$archive" ]] || die "Файл бэкапа не найден: $archive"
    confirm "Восстановление удалит текущие объекты БД. Продолжить?" n || die "Восстановление отменено."
    tmp="$(make_temp_dir)"
    age --decrypt --identity "$BACKUP_KEY_FILE" "$archive" | tar -xzf - -C "$tmp"
    [[ -f "$tmp/database.dump" ]] || die "В архиве отсутствует database.dump."
    db_url="$(get_env_value DATABASE_URL)"
    parse_database_url "$db_url" || die "Некорректный DATABASE_URL."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    PGPASSWORD="$DB_PASS" pg_restore --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" \
        --dbname "$DB_NAME" --clean --if-exists --no-owner "$tmp/database.dump"
    systemctl start "$SERVICE_NAME"
    healthcheck_local || die "База восстановлена, но health-check приложения не прошёл."
    ok "База данных восстановлена."
}

prune_old_releases() {
    local keep="${KEEP_RELEASES:-3}" current previous count=0 dir
    current="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
    previous="${1:-}"
    while IFS= read -r dir; do
        [[ "$dir" == "$current" || "$dir" == "$previous" ]] && continue
        count=$((count + 1))
        if (( count > keep )); then rm -rf -- "$dir"; fi
    done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-)
}

resume_previous_service() {
    local was_active="$1"
    if [[ "$was_active" == "1" ]]; then
        systemctl start "$SERVICE_NAME" 2>/dev/null || true
    fi
}

restore_previous_release() {
    local previous="$1" was_active="$2"
    [[ -n "$previous" && -d "$previous" ]] || return 0
    warn "Возвращаю предыдущий релиз: $previous"
    setup_systemd_service >/dev/null 2>&1 || true
    switch_release "$previous" >/dev/null 2>&1 || true
    if [[ "$was_active" == "1" ]]; then
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true
    else
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    fi
}

deploy() {
    local mode="$1" remote_sha release_dir previous backup_file="" was_active=0
    require_root "$mode"
    acquire_lock
    ensure_service_user
    install_system_packages
    preflight
    setup_installer_logrotate
    start_platform_services
    migrate_legacy_env
    configure_env
    setup_backup_key
    setup_local_postgres

    previous="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
    if [[ -z "$previous" ]]; then
        previous="$(capture_legacy_release || true)"
    fi

    remote_sha="$(resolve_remote_sha)"
    [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]] || die "GitHub вернул некорректный commit SHA."
    release_dir="$(prepare_release "$remote_sha")"
    validate_release "$release_dir"

    systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null && was_active=1 || true
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    if [[ -n "$previous" ]]; then
        if ! backup_file="$(create_backup | tail -n1)"; then
            if [[ "$ALLOW_UPDATE_WITHOUT_BACKUP" != "1" ]]; then
                resume_previous_service "$was_active"
                die "Обновление остановлено: не удалось создать резервную копию."
            fi
            warn "Продолжаю без бэкапа по ALLOW_UPDATE_WITHOUT_BACKUP=1."
        fi
    fi

    if ! setup_local_redis; then
        resume_previous_service "$was_active"
        die "Redis не настроен. Предыдущий релиз возвращён в исходное состояние запуска."
    fi

    if ! run_migrations "$release_dir"; then
        resume_previous_service "$was_active"
        die "Миграции не применены. Предыдущий релиз оставлен активным. Бэкап: ${backup_file:-не создан}"
    fi

    if ! setup_systemd_service || ! switch_release "$release_dir"; then
        restore_previous_release "$previous" "$was_active"
        die "Не удалось переключить release. Бэкап: ${backup_file:-не создан}"
    fi

    if ! systemctl restart "$SERVICE_NAME" || ! healthcheck_local; then
        error "Новый релиз не прошёл health-check."
        journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2 || true
        restore_previous_release "$previous" "$was_active"
        die "Деплой отменён. После миграций может потребоваться restore БД: ${backup_file:-бэкап не создан}"
    fi

    setup_nginx_tls
    cleanup_legacy_layout
    prune_old_releases "$previous"
    ok "${mode^} завершён. Активный commit: ${remote_sha}"
}

