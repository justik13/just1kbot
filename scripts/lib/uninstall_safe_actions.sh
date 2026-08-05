remove_nginx() {
    [[ -n "$DOMAIN" ]] || return 0
    foundation_manifest_has "nginx-site:$DOMAIN" || return 0

    local available="/etc/nginx/sites-available/$DOMAIN"
    local enabled="/etc/nginx/sites-enabled/$DOMAIN"
    local stash

    if [[ -e "$available" || -L "$available" ]]; then
        [[ -f "$available" && ! -L "$available" ]] &&
            grep -Fq '# Managed by Just1kBot ownership manifest' "$available" ||
            fail 'Nginx site ownership marker отсутствует' "$available"
    fi
    if [[ -e "$enabled" || -L "$enabled" ]]; then
        foundation_manifest_has "nginx-enabled:$DOMAIN" ||
            fail 'Nginx enabled link отсутствует в ownership manifest' "$enabled"
        [[ -L "$enabled" && "$(readlink -f -- "$enabled")" == "$(realpath -m -- "$available")" ]] ||
            fail 'Nginx enabled link не принадлежит ожидаемому site' "$enabled"
    fi

    stash=$(mktemp -d /run/just1kbot-uninstall-nginx.XXXXXX)
    [[ -f "$available" ]] && mv -- "$available" "$stash/site"
    if [[ -L "$enabled" ]]; then
        readlink -- "$enabled" > "$stash/link"
        rm -f -- "$enabled"
    fi

    if command -v nginx >/dev/null 2>&1 && ! nginx -t; then
        [[ -f "$stash/site" ]] && mv -- "$stash/site" "$available"
        [[ -f "$stash/link" ]] && ln -s -- "$(cat "$stash/link")" "$enabled"
        rm -rf -- "$stash"
        fail 'nginx -t failed; site restored'
    fi
    systemctl is-active --quiet nginx 2>/dev/null && systemctl reload nginx || true
    rm -rf -- "$stash"
}

remove_certificate() {
    [[ "$MODE" == purge && -n "$DOMAIN" ]] || return 0
    foundation_manifest_has "certbot:$DOMAIN" || return 0
    command -v certbot >/dev/null 2>&1 ||
        fail 'certbot не найден; manifest-owned certificate нельзя проверить или удалить'
    certbot certificates --cert-name "$DOMAIN" >/dev/null 2>&1 || return 0
    certbot delete --non-interactive --cert-name "$DOMAIN" ||
        fail 'certbot не смог удалить manifest-owned certificate'
}

prepare_postgres() {
    [[ "$MODE" == purge ]] || return 0
    pg_select_cluster || fail 'не удалось выбрать PostgreSQL cluster'
    pg_start_cluster || fail 'не удалось проверить PostgreSQL cluster'
    foundation_manifest_has "postgresql:${PG_VERSION}/${PG_CLUSTER}:database:$PG_DATABASE" ||
        fail 'PostgreSQL database ownership отсутствует в manifest'
    foundation_manifest_has "postgresql:${PG_VERSION}/${PG_CLUSTER}:role:$PG_ROLE" ||
        fail 'PostgreSQL role ownership отсутствует в manifest'
}

postgres_database_owner_by_name() {
    local database=$1
    pg_admin_psql_on_port "$PG_PORT" -v db="$database" -At <<'SQL'
SELECT COALESCE(r.rolname, '')
FROM pg_database AS d
LEFT JOIN pg_roles AS r ON r.oid = d.datdba
WHERE d.datname = :'db';
SQL
}

purge_postgres() {
    [[ "$MODE" == purge ]] || return 0
    local databases database owner
    databases=$(pg_admin_psql_on_port "$PG_PORT" -v main="$PG_DATABASE" <<'SQL'
SELECT datname
FROM pg_database
WHERE datname = :'main'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$'
ORDER BY datname;
SQL
) || fail 'не удалось перечислить Just1kBot databases'

    while IFS= read -r database; do
        [[ -n "$database" ]] || continue
        [[ "$database" == "$PG_DATABASE" || "$database" =~ ^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$ ]] ||
            fail 'unexpected database name' "$database"

        if [[ "$database" != "$PG_DATABASE" ]]; then
            owner=$(postgres_database_owner_by_name "$database") ||
                fail 'не удалось определить owner остаточной PostgreSQL database' "$database"
            [[ "$owner" == "$PG_ROLE" ]] ||
                fail 'остаточная PostgreSQL database имеет неожиданного owner' \
                    "database=$database owner=${owner:-empty} expected=$PG_ROLE" \
                    'Удаление остановлено, чтобы не удалить чужую database с похожим именем.'
        fi

        pg_admin_psql_on_port "$PG_PORT" -v db="$database" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'db'
  AND pid <> pg_backend_pid();
SQL
        runuser -u postgres -- dropdb \
            -h "$PG_SOCKET_DIR" -p "$PG_PORT" \
            --if-exists --maintenance-db=postgres "$database" ||
            fail 'dropdb failed' "$database"
    done <<<"$databases"

    pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" >/dev/null <<'SQL'
DROP ROLE IF EXISTS :"role";
SQL
}

require_owned_path() {
    local path=$1 resource=${2:-path:$1}
    [[ -e "$path" || -L "$path" ]] || return 0
    foundation_manifest_has "$resource" ||
        fail 'отказ удалять resource без ownership proof' "$path отсутствует в manifest как $resource"
}

remove_owned_file() {
    local path=$1 resource=${2:-path:$1}
    require_owned_path "$path" "$resource"
    [[ ! -e "$path" && ! -L "$path" ]] || {
        [[ -f "$path" && ! -L "$path" ]] ||
            fail 'owned resource имеет небезопасный тип' "$path"
        rm -f -- "$path"
    }
}

remove_owned_tree() {
    local path=$1 resource=${2:-path:$1}
    require_owned_path "$path" "$resource"
    [[ ! -e "$path" && ! -L "$path" ]] || {
        [[ -d "$path" && ! -L "$path" ]] ||
            fail 'owned directory имеет небезопасный тип' "$path"
        rm -rf --one-file-system -- "$path"
    }
}

remove_files() {
    remove_owned_file /etc/systemd/system/just1kbot.service systemd:just1kbot.service
    remove_owned_file "$REDIS_UNIT" "systemd:$REDIS_SERVICE"

    local path
    for path in \
        /etc/systemd/system/just1kbot-healthcheck.service \
        /etc/systemd/system/just1kbot-healthcheck.timer \
        /etc/systemd/system/just1kbot-backup.service \
        /etc/systemd/system/just1kbot-backup.timer \
        /usr/local/bin/just1kbot-backup.sh \
        /usr/local/bin/just1kbot-restore.sh \
        /usr/local/bin/just1kbot-healthcheck.sh \
        /usr/local/bin/verify_backup.sh \
        /usr/local/bin/restore_rehearsal.sh \
        "$CLI_PATH" \
        /etc/logrotate.d/just1kbot; do
        remove_owned_file "$path"
    done

    systemctl daemon-reload
    remove_owned_tree "$PROJECT_DIR"
    remove_owned_tree "$REDIS_DATA_DIR"
    remove_owned_file "$REDIS_CONFIG"
    rmdir /etc/just1kbot 2>/dev/null || true

    if [[ -e "$ACME_ROOT" || -L "$ACME_ROOT" ]]; then
        require_owned_path "$ACME_ROOT"
        remove_owned_tree "$ACME_ROOT"
    fi
}

purge_saved() {
    [[ "$MODE" == purge ]] || return 0

    local path
    for path in \
        /var/lib/just1kbot/backups \
        /var/lib/just1kbot/rollback-releases \
        /var/lib/just1kbot/restore-transactions \
        /var/lib/just1kbot/source-releases; do
        remove_owned_tree "$path"
    done
    remove_owned_file /etc/just1kbot-backup.conf
    remove_owned_file /etc/just1kbot/backup.agekey
    rmdir /etc/just1kbot 2>/dev/null || true
}

remove_user() {
    id "$BOT_USER" >/dev/null 2>&1 || return 0
    foundation_manifest_has "service-user:$BOT_USER" ||
        fail 'service user ownership отсутствует в manifest' "$BOT_USER"
    pgrep -u "$BOT_USER" >/dev/null 2>&1 &&
        fail 'service user still has processes'
    userdel "$BOT_USER" || fail 'userdel failed'
    remove_owned_tree "$BOT_HOME"
}

post_verify() {
    local leftovers=() path unit
    for path in \
        "$PROJECT_DIR" "$REDIS_CONFIG" "$REDIS_DATA_DIR" "$REDIS_UNIT" \
        "$CLI_PATH" /etc/systemd/system/just1kbot.service; do
        [[ ! -e "$path" && ! -L "$path" ]] || leftovers+=("$path")
    done
    id "$BOT_USER" >/dev/null 2>&1 && leftovers+=("user:$BOT_USER")
    for unit in just1kbot.service "$REDIS_SERVICE"; do
        systemctl is-active --quiet "$unit" 2>/dev/null && leftovers+=("active:$unit")
    done
    (( ${#leftovers[@]} == 0 )) || {
        printf 'ОШИБКА: uninstall оставил resources:\n  - %s\n' "${leftovers[@]}" >&2
        return 1
    }
}
