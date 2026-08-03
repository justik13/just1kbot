#!/bin/bash
# Bind PostgreSQL role/database ownership to the manifest installation ID.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

clone_function setup_postgresql_initial ownership_base_setup_postgresql_initial
clone_function record_existing_postgres ownership_base_record_existing_postgres

postgres_ownership_value() {
    printf 'managed-by=just1kbot;installation-id=%s' "$(foundation_manifest_id)"
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

postgres_set_ownership_comments() {
    local marker
    marker=$(postgres_ownership_value)
    pg_admin_psql_on_port "$PG_PORT" \
        -v db="$PG_DATABASE" -v role="$PG_ROLE" -v marker="$marker" >/dev/null <<'SQL'
COMMENT ON DATABASE :"db" IS :'marker';
COMMENT ON ROLE :"role" IS :'marker';
SQL
    foundation_manifest_set_metadata postgresql_ownership_comment "$marker"
}

postgres_assert_ownership_comments() {
    local expected database_comment role_comment legacy
    expected=$(postgres_ownership_value)
    database_comment=$(postgres_database_comment)
    role_comment=$(postgres_role_comment)

    if [[ "$database_comment" == "$expected" && "$role_comment" == "$expected" ]]; then
        return 0
    fi

    legacy=$(foundation_manifest_metadata legacy_migrated 2>/dev/null || true)
    if [[ "$legacy" == true && -z "$database_comment" && -z "$role_comment" ]]; then
        postgres_set_ownership_comments
        return 0
    fi

    foundation_fail POSTGRES_OWNERSHIP_MISMATCH \
        'PostgreSQL ownership marker не совпадает с installation ID' \
        "database_comment=${database_comment:-empty}; role_comment=${role_comment:-empty}" \
        'Не изменяйте role/database автоматически. Проверьте COMMENT и ownership manifest вручную.'
}

setup_postgresql_initial() {
    ownership_base_setup_postgresql_initial
    postgres_set_ownership_comments
}

record_existing_postgres() {
    ownership_base_record_existing_postgres
    postgres_assert_ownership_comments
}

if [[ "${INSTALL_SAFE_POSTGRES_OWNERSHIP_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_postgres_ownership.sh is source-only\n' >&2
    exit 64
fi
