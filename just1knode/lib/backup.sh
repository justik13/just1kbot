#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека резервного копирования (lib/backup.sh)
# =============================================================================

BACKUP_DIR="${BACKUP_DIR:-/var/backups/just1knode}"

create_backup() {
    local target="$1"
    if [[ -f "$target" ]]; then
        mkdir -p "$BACKUP_DIR"
        local bkp
        bkp="${BACKUP_DIR}/$(basename "$target")_$(date +%Y%m%d_%H%M%S).bak"
        cp "$target" "$bkp"
        log "Создан защитный бэкап: $bkp"
    fi
}
