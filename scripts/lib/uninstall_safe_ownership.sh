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
clone_uninstall_function purge_postgres ownership_base_purge_postgres
clone_uninstall_function remove_certificate ownership_base_remove_certificate
clone_uninstall_function remove_files ownership_base_remove_files
clone_uninstall_function post_verify ownership_base_post_verify

POSTGRES_RESOURCES_OWNED=false

postgres_expected_marker() {
    printf 'managed-by=just1kbot;installation-id=%s' "$(foundation_manifest_id)"
}

postgres_manifest_state() {
    local database_marker role_marker
    database_marker="postgresql:${PG_VERSION}/${PG_CLUSTER}:database:$PG_DATABASE"
    role_marker="postgresql:${PG_VERSION}/${PG_CLUSTER}:role:$PG_ROLE"
    local has_database=false has_role=false
    foundation_manifest_has "$database_marker" && has_database=true
    foundation_manifest_has "$role_marker" && has_role=true
    if [[ "$has_database" == true && "$has_role" == true ]]; then
        POSTGRES_RESOURCES_OWNED=true
        return 0
    fi
    if [[ "$has_database" == false && "$has_role" == false && "$INCOMPLETE_INSTALL" == true ]]; then
        POSTGRES_RESOURCES_OWNED=false
        return 0
    fi
    fail \
        'PostgreSQL ownership markers неполны' \
        "database_marker=$has_database role_marker=$has_role" \
        'Сохраните manifest и выполните ручной аудит; partial ownership нельзя удалять автоматически.'
}

postgres_database_exists() {
    local result
    result=$(pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT EXISTS (
    SELECT 1
    FROM pg_database
    WHERE datname = :'db'
);
SQL
) || return 1
    [[ "$result" == t ]]
}

postgres_role_exists() {
    local result
    result=$(pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" -At <<'SQL'
SELECT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'role'
);
SQL
) || return 1
    [[ "$result" == t ]]
}

postgres_database_owner() {
    pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT COALESCE(r.rolname, '')
FROM pg_database AS d
LEFT JOIN pg_roles AS r ON r.oid = d.datdba
WHERE d.datname = :'db';
SQL
}

postgres_database_comment() {
    pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_database'), '')
FROM pg_database
WHERE datname = :'db';
SQL
}

postgres_role_comment() {
    pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_authid'), '')
FROM pg_authid
WHERE rolname = :'role';
SQL
}

prepare_postgres() {
    [[ "$MODE" == purge ]] || return 0

    # Select the cluster first so exact manifest resource names can be checked.
    pg_select_cluster || fail 'не удалось выбрать PostgreSQL cluster'
    if [[ "$PG_STATUS" != online ]]; then
        pg_start_cluster || fail 'не удалось проверить PostgreSQL cluster'
    fi
    postgres_manifest_state
    [[ "$POSTGRES_RESOURCES_OWNED" == true ]] || return 0

    local expected database_owner database_comment role_comment
    postgres_database_exists || fail \
        'manifest-owned PostgreSQL database отсутствует' \
        "$PG_VERSION/$PG_CLUSTER:$PG_DATABASE" \
        'Manifest заявляет ownership, но database не найдена; автоматическое удаление остановлено.'
    postgres_role_exists || fail \
        'manifest-owned PostgreSQL role отсутствует' \
        "$PG_VERSION/$PG_CLUSTER:$PG_ROLE" \
        'Manifest заявляет ownership, но role не найдена; автоматическое удаление остановлено.'

    database_owner=$(postgres_database_owner) || fail \
        'не удалось определить владельца PostgreSQL database' \
        "$PG_DATABASE" \
        'Автоматическое удаление остановлено, чтобы не удалить чужой объект.'
    [[ "$database_owner" == "$PG_ROLE" ]] || fail \
        'PostgreSQL database принадлежит неожиданной role' \
        "database=$PG_DATABASE owner=${database_owner:-empty} expected=$PG_ROLE" \
        'Проверьте ownership вручную; uninstall не будет удалять database/role с несовпадающим владельцем.'

    expected=$(postgres_expected_marker)
    database_comment=$(postgres_database_comment) || fail \
        'не удалось прочитать database ownership COMMENT' \
        "$PG_DATABASE" \
        'Проверьте PostgreSQL и повторите uninstall.'
    role_comment=$(postgres_role_comment) || fail \
        'не удалось прочитать role ownership COMMENT' \
        "$PG_ROLE" \
        'Проверьте PostgreSQL и повторите uninstall.'

    if [[ "$database_comment" == "$expected" && "$role_comment" == "$expected" ]]; then
        log 'PostgreSQL ownership подтверждён manifest + database owner + COMMENT'
        return 0
    fi

    # Older managed installs may have a valid ownership manifest and matching
    # database owner but no PostgreSQL COMMENT markers. Keep destructive removal
    # safe by rejecting partial/mismatched markers, while allowing the explicit
    # interactive --purge-data confirmation to authorize a marker-less legacy state.
    if [[ -z "$database_comment" && -z "$role_comment" ]]; then
        log 'WARNING: PostgreSQL COMMENT markers отсутствуют; ownership подтверждён manifest и database owner, продолжение разрешено явным --purge-data confirmation'
        return 0
    fi

    fail \
        'PostgreSQL ownership COMMENT не подтверждает manifest installation ID' \
        "database_comment=${database_comment:-empty}; role_comment=${role_comment:-empty}; expected=$expected" \
        'Не удаляйте role/database вручную. Исправьте ownership metadata или выполните ручной аудит.'
}

purge_postgres() {
    [[ "$MODE" == purge && "$POSTGRES_RESOURCES_OWNED" == true ]] || return 0
    ownership_base_purge_postgres
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

verify_postgres_absent() {
    [[ "$MODE" == purge && "$POSTGRES_RESOURCES_OWNED" == true ]] || return 0
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
    local owned_nginx_site=false owned_nginx_enabled=false owned_certificate=false
    [[ -n "$DOMAIN" ]] && foundation_manifest_has "nginx-site:$DOMAIN" && owned_nginx_site=true
    [[ -n "$DOMAIN" ]] && foundation_manifest_has "nginx-enabled:$DOMAIN" && owned_nginx_enabled=true
    [[ -n "$DOMAIN" ]] && foundation_manifest_has "certbot:$DOMAIN" && owned_certificate=true

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

    if [[ "$owned_nginx_site" == true ]]; then
        [[ ! -e "/etc/nginx/sites-available/$DOMAIN" && ! -L "/etc/nginx/sites-available/$DOMAIN" ]] || leftovers+=("nginx-site:$DOMAIN")
    fi
    if [[ "$owned_nginx_enabled" == true ]]; then
        [[ ! -e "/etc/nginx/sites-enabled/$DOMAIN" && ! -L "/etc/nginx/sites-enabled/$DOMAIN" ]] || leftovers+=("nginx-enabled:$DOMAIN")
    fi
    if [[ "$owned_certificate" == true ]] &&
       command -v certbot >/dev/null 2>&1 &&
       certbot certificates --cert-name "$DOMAIN" >/dev/null 2>&1; then
        leftovers+=("certbot:$DOMAIN")
    fi

    if [[ "$MODE" == purge ]]; then
        verify_postgres_absent || leftovers+=("postgresql:$PG_ROLE/$PG_DATABASE")
        for path in \
            /root/backups/just1kbot \
            /etc/just1kbot-backup.conf \
            /root/.config/just1kbot/backup.agekey \
            /var/lib/just1kbot/rollback-releases \
            /var/lib/just1kbot/restore-transactions \
            /var/lib/just1kbot/source-releases; do
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
