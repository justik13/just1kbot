#!/bin/bash
# Additional manifest/COMMENT verification and complete residual scan.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

clone_uninstall_function() {
    local source=$1 target=$2 definition
    definition=$(declare -f "$source") || return 1
    definition=${definition/#"$source ()"/"$target ()"}
    eval "$definition"
}

clone_uninstall_function prepare_postgres ownership_base_prepare_postgres
clone_uninstall_function remove_certificate ownership_base_remove_certificate
clone_uninstall_function remove_files ownership_base_remove_files
clone_uninstall_function purge_saved ownership_base_purge_saved
clone_uninstall_function post_verify ownership_base_post_verify

postgres_expected_marker() {
    printf 'managed-by=just1kbot;installation-id=%s' "$(foundation_manifest_id)"
}

prepare_postgres() {
    ownership_base_prepare_postgres
    [[ "$MODE" == purge ]] || return 0
    local expected database_comment role_comment
    expected=$(postgres_expected_marker)
    database_comment=$(pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_database'), '')
FROM pg_database
WHERE datname = :'db';
SQL
) || fail 'не удалось прочитать database ownership COMMENT'
    role_comment=$(pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_authid'), '')
FROM pg_authid
WHERE rolname = :'role';
SQL
) || fail 'не удалось прочитать role ownership COMMENT'
    [[ "$database_comment" == "$expected" ]] ||
        fail 'database ownership COMMENT не совпадает с manifest installation ID'
    [[ "$role_comment" == "$expected" ]] ||
        fail 'role ownership COMMENT не совпадает с manifest installation ID'
}

remove_certificate() {
    [[ -n "$DOMAIN" ]] || return 0
    foundation_manifest_has "certbot:$DOMAIN" || return 0
    command -v certbot >/dev/null 2>&1 ||
        fail 'certbot не найден; manifest-owned certificate нельзя удалить безопасно'
    if certbot certificates --cert-name "$DOMAIN" >/dev/null 2>&1; then
        certbot delete --non-interactive --cert-name "$DOMAIN" ||
            fail 'certbot не смог удалить manifest-owned certificate'
    fi
}

remove_files() {
    local proxy_snippet=/var/lib/just1kbot/install-state/external-proxy.nginx.conf
    if [[ -e "$proxy_snippet" || -L "$proxy_snippet" ]]; then
        remove_owned_file "$proxy_snippet"
    fi
    ownership_base_remove_files
}

purge_saved() {
    ownership_base_purge_saved
    [[ "$MODE" == purge ]] || return 0
    local support_dir=/var/lib/just1kbot/support-bundles
    if [[ -e "$support_dir" || -L "$support_dir" ]]; then
        remove_owned_tree "$support_dir"
    fi
}

verify_postgres_absent() {
    [[ "$MODE" == purge ]] || return 0
    local database_exists role_exists
    database_exists=$(pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db');
SQL
) || return 1
    role_exists=$(pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" -At <<'SQL'
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role');
SQL
) || return 1
    [[ "$database_exists" == f && "$role_exists" == f ]]
}

post_verify() {
    ownership_base_post_verify
    local leftovers=() path unit

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
        /etc/logrotate.d/just1kbot \
        /var/lib/just1kbot/install-state/external-proxy.nginx.conf; do
        [[ ! -e "$path" && ! -L "$path" ]] || leftovers+=("$path")
    done

    for unit in \
        just1kbot-healthcheck.service \
        just1kbot-healthcheck.timer \
        just1kbot-backup.service \
        just1kbot-backup.timer; do
        systemctl is-active --quiet "$unit" 2>/dev/null && leftovers+=("active:$unit")
    done

    if [[ -n "$DOMAIN" ]]; then
        [[ ! -e "/etc/nginx/sites-available/$DOMAIN" && ! -L "/etc/nginx/sites-available/$DOMAIN" ]] || leftovers+=("nginx-site:$DOMAIN")
        [[ ! -e "/etc/nginx/sites-enabled/$DOMAIN" && ! -L "/etc/nginx/sites-enabled/$DOMAIN" ]] || leftovers+=("nginx-enabled:$DOMAIN")
        if foundation_manifest_has "certbot:$DOMAIN" && certbot certificates --cert-name "$DOMAIN" >/dev/null 2>&1; then
            leftovers+=("certbot:$DOMAIN")
        fi
    fi

    if [[ "$MODE" == purge ]]; then
        verify_postgres_absent || leftovers+=("postgresql:$PG_ROLE/$PG_DATABASE")
        for path in \
            /root/backups/just1kbot \
            /etc/just1kbot-backup.conf \
            /root/.config/just1kbot/backup.agekey \
            /var/lib/just1kbot/rollback-releases \
            /var/lib/just1kbot/restore-transactions \
            /var/lib/just1kbot/source-releases \
            /var/lib/just1kbot/support-bundles; do
            [[ ! -e "$path" && ! -L "$path" ]] || leftovers+=("$path")
        done
    fi

    (( ${#leftovers[@]} == 0 )) || {
        printf 'ОШИБКА: ownership-aware uninstall verification found leftovers:\n' >&2
        printf '  - %s\n' "${leftovers[@]}" >&2
        return 1
    }
}

if [[ "${UNINSTALL_SAFE_OWNERSHIP_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'uninstall_safe_ownership.sh is source-only\n' >&2
    exit 64
fi
