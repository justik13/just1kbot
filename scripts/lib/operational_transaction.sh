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

ensure_operational_parent() {
    local path=$1
    local parent mode=0755

    parent=$(dirname "$path")
    if [[ -L "$parent" ]]; then
        return 1
    fi
    if [[ -d "$parent" ]]; then
        return 0
    fi
    if [[ -e "$parent" ]]; then
        return 1
    fi

    case "$parent" in
        /root|/root/*)
            mode=0700
            ;;
    esac
    install -d -m "$mode" "$parent"
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
            if [[ ! -f "$path" && ! -L "$path" ]]; then
                deploy_log "operational_snapshot unsupported_file_type=true path=$path"
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
                [[ -f "$source" || -L "$source" ]] || return 1
                rm -rf -- "$path"
                ensure_operational_parent "$path" || return 1
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

validate_restored_nginx() {
    local manifest=$1
    local enabled active unit

    [[ "$OPERATIONAL_NGINX" == true ]] || return 0
    while IFS=$'\t' read -r enabled active unit; do
        if [[ "$unit" == nginx.service ]]; then
            case "$active" in
                active|activating|reloading)
                    nginx -t
                    return
                    ;;
            esac
            return 0
        fi
    done < "$manifest"
    return 1
}

restore_operational_units() {
    local snapshot=$1
    local manifest="$snapshot/operational/units.tsv"
    local enabled active unit

    [[ -f "$manifest" && ! -L "$manifest" ]] || {
        deploy_log 'operational_restore units_manifest_missing=true'
        return 1
    }

    # Stop every tracked unit before changing masks or enablement. This avoids
    # preserving a process started from files that have just been rolled back.
    while IFS=$'\t' read -r enabled active unit; do
        [[ "$unit" =~ ^[A-Za-z0-9_.@:-]+$ ]] || return 1
        systemctl stop "$unit" >/dev/null 2>&1 || true
    done < "$manifest"

    # Temporarily unmask every unit. A unit can legitimately have been active
    # and masked at snapshot time; it must be started before its mask returns.
    while IFS=$'\t' read -r enabled active unit; do
        [[ "$unit" =~ ^[A-Za-z0-9_.@:-]+$ ]] || return 1
        systemctl unmask "$unit" >/dev/null 2>&1 || true
        case "$enabled" in
            enabled)
                systemctl enable "$unit" >/dev/null 2>&1 || return 1
                ;;
            enabled-runtime)
                systemctl enable --runtime "$unit" >/dev/null 2>&1 || return 1
                ;;
            masked|masked-runtime)
                ;;
            *)
                systemctl disable "$unit" >/dev/null 2>&1 || true
                ;;
        esac
    done < "$manifest"

    # Validate restored Nginx files only when Nginx was previously active and
    # is about to be started. An inactive prior state is restored as-is.
    validate_restored_nginx "$manifest" || return 1

    while IFS=$'\t' read -r enabled active unit; do
        [[ "$unit" =~ ^[A-Za-z0-9_.@:-]+$ ]] || return 1
        case "$active" in
            active|activating|reloading)
                systemctl start "$unit" >/dev/null 2>&1 || return 1
                ;;
        esac
    done < "$manifest"

    # Restore masks last so an active-and-masked snapshot remains both active
    # and protected from future starts after rollback.
    while IFS=$'\t' read -r enabled active unit; do
        [[ "$unit" =~ ^[A-Za-z0-9_.@:-]+$ ]] || return 1
        case "$enabled" in
            masked)
                systemctl mask "$unit" >/dev/null 2>&1 || return 1
                ;;
            masked-runtime)
                systemctl mask --runtime "$unit" >/dev/null 2>&1 || return 1
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
        local final incomplete

        application_snapshot_previous_release || return 1
        [[ -n "$ROLLBACK_SNAPSHOT" && -d "$ROLLBACK_SNAPSHOT" ]] || return 1

        # The application helper already publishes release-* atomically. Move
        # it back under an incomplete name while appending operational state so
        # a hard interruption never leaves a partially complete release-*.
        final=$ROLLBACK_SNAPSHOT
        incomplete="$SNAPSHOT_DIR/.incomplete-operational-$(basename "$final")"
        rm -rf -- "$incomplete"
        if ! mv -- "$final" "$incomplete"; then
            rm -rf -- "$final"
            ROLLBACK_SNAPSHOT=""
            return 1
        fi
        ROLLBACK_SNAPSHOT=$incomplete

        if ! snapshot_operational_files "$incomplete" ||
            ! snapshot_operational_units "$incomplete"; then
            deploy_log 'operational_snapshot complete=false'
            rm -rf -- "$incomplete"
            ROLLBACK_SNAPSHOT=""
            return 1
        fi

        chmod -R go-rwx "$incomplete/operational"
        if ! mv -- "$incomplete" "$final"; then
            rm -rf -- "$incomplete"
            ROLLBACK_SNAPSHOT=""
            return 1
        fi
        ROLLBACK_SNAPSHOT=$final
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
