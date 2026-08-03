#!/bin/bash
# Additional ownership checks for the production doctor.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

clone_doctor_function() {
    local source=$1 target=$2 definition
    definition=$(declare -f "$source") || return 1
    definition=${definition/#"$source ()"/"$target ()"}
    eval "$definition"
}

clone_doctor_function check_manifest doctor_base_check_manifest
clone_doctor_function check_nginx doctor_base_check_nginx

check_cli_launcher() {
    local cli=/usr/local/sbin/just1kbot state
    if [[ ! -f "$cli" || -L "$cli" ]]; then
        fail "Global CLI missing/unsafe: $cli"
        return
    fi
    state=$(stat -c '%U:%G %a' "$cli" 2>/dev/null || true)
    [[ "$state" == 'root:root 750' ]] && ok 'Global CLI permissions: root:root 750' || fail "Global CLI permissions: $state"
    grep -Fq 'CONTROL=/opt/just1kbot/deploy.sh' "$cli" && ok 'Global CLI target' || fail 'Global CLI target mismatch'
    foundation_manifest_has "path:$cli" && ok 'Global CLI ownership manifest' || fail 'Global CLI ownership absent from manifest'
}

check_postgres_ownership_comments() {
    local library="$ROOT_DIR/scripts/lib/postgresql.sh"
    [[ -f "$library" && ! -L "$library" ]] || {
        fail "PostgreSQL library missing/unsafe: $library"
        return
    }
    # shellcheck source=../lib/postgresql.sh
    source "$library"
    pg_select_cluster >/dev/null 2>&1 || {
        fail 'PostgreSQL cluster selection failed for ownership check'
        return
    }
    [[ "$PG_STATUS" == online ]] || {
        fail "PostgreSQL cluster not online: ${PG_VERSION}/${PG_CLUSTER}"
        return
    }
    local expected database_comment role_comment
    expected="managed-by=just1kbot;installation-id=$(foundation_manifest_id)"
    database_comment=$(pg_admin_psql_on_port "$PG_PORT" -v db="$PG_DATABASE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_database'), '')
FROM pg_database
WHERE datname = :'db';
SQL
) || {
        fail 'Cannot read PostgreSQL database ownership comment'
        return
    }
    role_comment=$(pg_admin_psql_on_port "$PG_PORT" -v role="$PG_ROLE" -At <<'SQL'
SELECT COALESCE(shobj_description(oid, 'pg_authid'), '')
FROM pg_authid
WHERE rolname = :'role';
SQL
) || {
        fail 'Cannot read PostgreSQL role ownership comment'
        return
    }
    [[ "$database_comment" == "$expected" ]] && ok 'PostgreSQL database ownership comment' || fail 'PostgreSQL database ownership comment mismatch'
    [[ "$role_comment" == "$expected" ]] && ok 'PostgreSQL role ownership comment' || fail 'PostgreSQL role ownership comment mismatch'
}

check_manifest() {
    doctor_base_check_manifest
    foundation_manifest_validate >/dev/null 2>&1 || return 0
    check_cli_launcher
    check_postgres_ownership_comments
}

check_external_proxy() {
    local snippet=/var/lib/just1kbot/install-state/external-proxy.nginx.conf
    local port domain listener
    [[ -f "$snippet" && ! -L "$snippet" ]] || {
        fail "External proxy contract missing/unsafe: $snippet"
        return
    }
    [[ "$(stat -c '%U:%G %a' "$snippet")" == 'root:root 600' ]] && ok 'External proxy contract permissions' || fail 'External proxy contract permissions mismatch'
    foundation_manifest_has "path:$snippet" && ok 'External proxy contract ownership' || fail 'External proxy contract absent from manifest'
    port=$(foundation_manifest_metadata internal_webhook_port 2>/dev/null || true)
    domain=$(foundation_manifest_metadata external_proxy_domain 2>/dev/null || true)
    [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) || {
        fail 'External proxy internal port metadata invalid'
        return
    }
    foundation_validate_domain "$domain" && ok "External proxy domain: $domain" || fail 'External proxy domain metadata invalid'
    listener=$(ss -H -ltn "( sport = :$port )" 2>/dev/null || true)
    if [[ -n "$listener" ]] && grep -Eq '127[.]0[.]0[.]1|\[::1\]' <<<"$listener"; then
        ok "Application loopback listener: $port"
    else
        fail "Application is not listening on loopback port $port"
    fi
}

check_nginx() {
    local mode
    if ! foundation_manifest_validate >/dev/null 2>&1; then
        doctor_base_check_nginx
        return
    fi
    mode=$(foundation_manifest_metadata proxy_mode 2>/dev/null || true)
    case "$mode" in
        external) check_external_proxy ;;
        managed|'') doctor_base_check_nginx ;;
        *) fail "Unknown proxy mode in manifest: $mode" ;;
    esac
}

if [[ "${DOCTOR_COMPLETION_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'doctor_completion.sh is source-only\n' >&2
    exit 64
fi
