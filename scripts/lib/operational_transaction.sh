#!/bin/bash
# Operational file and unit-state snapshot support for deploy rollback.
# This file is sourced by scripts/deploy.sh after deploy_application.sh.

OPERATIONAL_PATHS=()
OPERATIONAL_UNITS=()
OPERATIONAL_NGINX=false

operational_validate_path() {
    local path=$1
    [[ "$path" == /* && "$path" != / ]] || return 1
    [[ "$path" != *$'\n'* && "$path" != *$'\t'* ]] || return 1
    [[ "$path" != */../* && "$path" != */.. && "$path" != /../* ]] || return 1
}

snapshot_operational_files() {
    local snapshot=$1
    local root="$snapshot/operational/rootfs"
    local manifest="$snapshot/operational/files.tsv"
    local path target state

    install -d -m 0700 "$root"
    : > "$manifest"
    chmod 0600 "$manifest"

    for path in "${OPERATIONAL_PATHS[@]}"; do
        operational_validate_path "$path" || {
            deploy_log "operational_snapshot invalid_path=true"
            return 1
        }

        target="$root$path"
        if [[ -e "$path" || -L "$path" ]]; then
            if [[ -d "$path" && ! -L "$path" ]]; then
                deploy_log "operational_snapshot directory_not_allowed=true path=$path"
                return 1
            fi
            install -d -m 0700 "$(dirname "$target")"
            cp -a -- "$path" "$target" || return 1
            state=present
        else
            state=absent
        fi
        printf '%s\t%s\n' "$state" "$path" >> "$manifest"
    done
}

snapshot_operational_units() {
    local snapshot=$1
    local manifest="$snapshot/operational/units.tsv"
    local unit enabled active

    : > "$manifest"
    chmod 0600 "$manifest"

    for unit in "${OPERATIONAL_UNITS[@]}"; do
        [[ "$unit" =~ ^[A-Za-z0-9_.@:-]+$ ]] || {
            deploy_log "operational_snapshot invalid_unit=true"
            return 1
        }
        enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
        active=$(systemctl is-active "$unit" 2>/dev/null || true)
        printf '%s\t%s\t%s\n' "${enabled:-not-found}" "${active:-inactive}" "$unit" >> "$manifest"
    done
}

restore_operational_files() {
    local snapshot=$1
    local root="$snapshot/operational/rootfs"
    local manifest="$snapshot/operational/files.tsv"
    local state path source

    [[ -f "$manifest" && ! -L "$manifest" ]] || {
        deploy_log 'operational_restore files_manifest_missing=true'
        return 1
    }

    while IFS=$'\t' read -r state path; do
        operational_validate_path "$path" || return 1
        source="$root$path"
        case "$state" in
            present)
                [[ -e "$source" || -L "$source" ]] || return 1
                rm -rf -- "$path"
                install -d "$(dirname "$path")"
                cp -a -- "$source" "$path" || return 1
                ;;
            absent)
                rm -rf -- "$path"
                ;;
            *)
                return 1
                ;;
        esac
    done < "$manifest"
}

restore_operational_units() {
    local snapshot=$1
    local manifest="$snapshot/operational/units.tsv"
    local enabled active unit

    [[ -f "$manifest" && ! -L "$manifest" ]] || {
        deploy_log 'operational_restore units_manifest_missing=true'
        return 1
    }

    while IFS=$'\t' read -r enabled active unit; do
        [[ "$unit" =~ ^[A-Za-z0-9_.@:-]+$ ]] || return 1
        case "$enabled" in
            enabled|enabled-runtime)
                systemctl enable "$unit" >/dev/null 2>&1 || return 1
                ;;
            masked|masked-runtime)
                systemctl mask "$unit" >/dev/null 2>&1 || return 1
                ;;
            *)
                systemctl disable "$unit" >/dev/null 2>&1 || true
                ;;
        esac

        case "$active" in
            active|activating|reloading)
                systemctl start "$unit" >/dev/null 2>&1 || return 1
                ;;
            *)
                systemctl stop "$unit" >/dev/null 2>&1 || true
                ;;
        esac
    done < "$manifest"
}

reload_restored_nginx() {
    [[ "$OPERATIONAL_NGINX" == true ]] || return 0
    if systemctl is-active --quiet nginx; then
        nginx -t || return 1
        systemctl reload nginx || return 1
    fi
}

configure_operational_transaction() {
    local domain=${DOMAIN:-}
    local normalized=""

    if [[ "$INITIAL_INSTALL" == false ]]; then
        domain=$(read_env_value DOMAIN)
    fi
    if [[ -n "$domain" ]]; then
        normalized=$(normalize_domain "$domain") || {
            error "DOMAIN из production .env имеет неверный формат"
            return 1
        }
        DOMAIN=$normalized
    else
        DOMAIN=""
    fi

    OPERATIONAL_PATHS=(
        /usr/local/bin/just1kbot-backup.sh
        /usr/local/bin/verify_backup.sh
        /usr/local/bin/restore_rehearsal.sh
        /usr/local/bin/just1kbot-restore.sh
        /usr/local/bin/just1kbot-healthcheck.sh
        /etc/systemd/system/just1kbot-backup.service
        /etc/systemd/system/just1kbot-backup.timer
        /etc/systemd/system/just1kbot-healthcheck.service
        /etc/systemd/system/just1kbot-healthcheck.timer
        /etc/logrotate.d/just1kbot
        "$BACKUP_CONF"
        "$BACKUP_IDENTITY"
    )
    OPERATIONAL_UNITS=(
        just1kbot-backup.timer
        just1kbot-healthcheck.timer
    )
    OPERATIONAL_NGINX=false

    if [[ -n "$DOMAIN" ]]; then
        OPERATIONAL_PATHS+=(
            "/etc/nginx/sites-available/$DOMAIN"
            "/etc/nginx/sites-enabled/$DOMAIN"
            /etc/nginx/sites-enabled/default
        )
        OPERATIONAL_UNITS+=(nginx.service certbot.timer)
        OPERATIONAL_NGINX=true
    fi
}

install_operational_transaction_overrides() {
    clone_function snapshot_previous_release application_snapshot_previous_release
    clone_function restore_snapshot application_restore_snapshot

    snapshot_previous_release() {
        application_snapshot_previous_release || return 1
        [[ -n "$ROLLBACK_SNAPSHOT" && -d "$ROLLBACK_SNAPSHOT" ]] || return 1

        if ! snapshot_operational_files "$ROLLBACK_SNAPSHOT" ||
            ! snapshot_operational_units "$ROLLBACK_SNAPSHOT"; then
            deploy_log 'operational_snapshot complete=false'
            rm -rf -- "$ROLLBACK_SNAPSHOT"
            ROLLBACK_SNAPSHOT=""
            return 1
        fi

        chmod -R go-rwx "$ROLLBACK_SNAPSHOT/operational"
        deploy_log 'operational_snapshot complete=true'
    }

    restore_snapshot() {
        local rc=0

        application_restore_snapshot || rc=1
        restore_operational_files "$ROLLBACK_SNAPSHOT" || rc=1
        service_call daemon-reload || rc=1
        restore_operational_units "$ROLLBACK_SNAPSHOT" || rc=1
        reload_restored_nginx || rc=1

        if (( rc != 0 )); then
            deploy_log 'operational_restore complete=false'
            return 1
        fi
        deploy_log 'operational_restore complete=true'
    }
}
