remove_nginx(){
    [[ -n "$DOMAIN" ]] || return 0
    foundation_manifest_has "nginx-site:$DOMAIN" || return 0
    local available="/etc/nginx/sites-available/$DOMAIN" enabled="/etc/nginx/sites-enabled/$DOMAIN" stash
    if [[ -e "$available" || -L "$available" ]]; then [[ -f "$available" && ! -L "$available" ]] && grep -Fq '# Managed by Just1kBot ownership manifest' "$available" || fail 'Nginx site ownership marker отсутствует' "$available"; fi
    if [[ -e "$enabled" || -L "$enabled" ]]; then [[ -L "$enabled" && "$(readlink -f "$enabled")" == "$(realpath -m "$available")" ]] || fail 'Nginx enabled link не принадлежит site'; fi
    stash=$(mktemp -d /run/just1kbot-uninstall-nginx.XXXXXX); [[ -f "$available" ]]&&mv "$available" "$stash/site"; [[ -L "$enabled" ]]&&{ readlink "$enabled">"$stash/link"; rm -f "$enabled"; }
    if command -v nginx >/dev/null && ! nginx -t; then [[ -f "$stash/site" ]]&&mv "$stash/site" "$available"; [[ -f "$stash/link" ]]&&ln -s "$(cat "$stash/link")" "$enabled"; rm -rf "$stash"; fail 'nginx -t failed; site restored'; fi
    systemctl is-active --quiet nginx 2>/dev/null && systemctl reload nginx || true; rm -rf "$stash"
}
remove_certificate(){
    [[ "$MODE" == purge && -n "$DOMAIN" ]] || return 0
    foundation_manifest_has "certbot:$DOMAIN" || return 0
    command -v certbot >/dev/null || fail 'certbot не найден; owned certificate нельзя проверить/удалить'
    certbot certificates --cert-name "$DOMAIN" >/dev/null 2>&1 || return 0
    certbot delete --non-interactive --cert-name "$DOMAIN" || fail 'certbot не смог удалить manifest-owned certificate'
}
prepare_postgres(){
    [[ "$MODE" == purge ]] || return 0
    pg_select_cluster || fail 'не удалось выбрать PostgreSQL cluster'; pg_start_cluster || fail 'не удалось проверить PostgreSQL cluster'
    foundation_manifest_has "postgresql:${PG_VERSION}/${PG_CLUSTER}:database:$PG_DATABASE" || fail 'PostgreSQL database ownership отсутствует в manifest'
    foundation_manifest_has "postgresql:${PG_VERSION}/${PG_CLUSTER}:role:$PG_ROLE" || fail 'PostgreSQL role ownership отсутствует в manifest'
}
purge_postgres(){
    [[ "$MODE" == purge ]] || return 0
    local databases database
    databases=$(pg_admin_psql_on_port "$PG_PORT" -v main="$PG_DATABASE" <<'SQL'
SELECT datname FROM pg_database WHERE datname=:'main' OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$' ORDER BY datname;
SQL
) || fail 'не удалось перечислить Just1kBot databases'
    while IFS= read -r database; do [[ -n "$database" ]]||continue; [[ "$database" == "$PG_DATABASE" || "$database" =~ ^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$ ]]||fail 'unexpected database name' "$database"; pg_admin_psql_on_port "$PG_PORT" -v db="$database" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:'db' AND pid<>pg_backend_pid();
SQL
        runuser -u postgres -- dropdb -h "$PG_SOCKET_DIR" -p "$PG_PORT" --if-exists --maintenance-db=postgres "$database" || fail 'dropdb failed' "$database"
    done <<<"$databases"
    pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" >/dev/null <<'SQL'
DROP ROLE IF EXISTS :"role";
SQL
}
remove_files(){
    rm -f -- /etc/systemd/system/just1kbot.service /etc/systemd/system/just1kbot-healthcheck.service /etc/systemd/system/just1kbot-healthcheck.timer /etc/systemd/system/just1kbot-backup.service /etc/systemd/system/just1kbot-backup.timer "$REDIS_UNIT" /usr/local/bin/just1kbot-backup.sh /usr/local/bin/just1kbot-restore.sh /usr/local/bin/just1kbot-healthcheck.sh /usr/local/bin/verify_backup.sh /usr/local/bin/restore_rehearsal.sh "$CLI_PATH" /etc/logrotate.d/just1kbot
    systemctl daemon-reload
    [[ ! -L "$PROJECT_DIR" ]]&&rm -rf --one-file-system -- "$PROJECT_DIR"
    [[ ! -L "$REDIS_DATA_DIR" ]]&&rm -rf --one-file-system -- "$REDIS_DATA_DIR"
    rm -f -- "$REDIS_CONFIG"; rmdir /etc/just1kbot 2>/dev/null||true
    rm -rf --one-file-system -- "$ACME_ROOT"
}
purge_saved(){
    [[ "$MODE" == purge ]] || return 0
    rm -rf --one-file-system -- /root/backups/just1kbot /var/lib/just1kbot/rollback-releases /var/lib/just1kbot/restore-transactions /var/lib/just1kbot/source-releases
    rm -f -- /etc/just1kbot-backup.conf /root/.config/just1kbot/backup.agekey
    rmdir /root/.config/just1kbot 2>/dev/null||true
}
remove_user(){ id "$BOT_USER" >/dev/null 2>&1 || return 0; pgrep -u "$BOT_USER" >/dev/null 2>&1&&fail 'service user still has processes'; userdel "$BOT_USER"||fail 'userdel failed'; [[ ! -L "$BOT_HOME" ]]&&rm -rf --one-file-system -- "$BOT_HOME"; }
post_verify(){
    local leftovers=() path unit
    for path in "$PROJECT_DIR" "$REDIS_CONFIG" "$REDIS_DATA_DIR" "$REDIS_UNIT" "$CLI_PATH" /etc/systemd/system/just1kbot.service; do [[ ! -e "$path" && ! -L "$path" ]]||leftovers+=("$path"); done
    id "$BOT_USER" >/dev/null 2>&1&&leftovers+=("user:$BOT_USER")
    for unit in just1kbot.service "$REDIS_SERVICE"; do systemctl is-active --quiet "$unit" 2>/dev/null&&leftovers+=("active:$unit"); done
    (( ${#leftovers[@]}==0 ))||{ printf 'ОШИБКА: uninstall оставил resources:\n  - %s\n' "${leftovers[@]}" >&2; return 1; }
}
