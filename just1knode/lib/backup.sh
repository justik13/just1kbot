#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека резервного копирования (lib/backup.sh)
# =============================================================================

BACKUP_DIR="${BACKUP_DIR:-/var/backups/just1knode}"
BACKUP_RETENTION_COUNT="${BACKUP_RETENTION_COUNT:-10}"

create_backup() {
    local target="$1"
    if [[ -f "$target" ]]; then
        mkdir -p "$BACKUP_DIR"
        chmod 700 "$BACKUP_DIR" 2>/dev/null || true
        local bkp
        bkp="${BACKUP_DIR}/$(basename "$target")_$(date +%Y%m%d_%H%M%S).bak"
        cp -p "$target" "$bkp"
        chmod 600 "$bkp" 2>/dev/null || true
        log "Создан защитный бэкап: $bkp"

        # Ограничение накопления резервных копий (Retention Policy)
        local base_name
        base_name="$(basename "$target")"
        local old_backups
        old_backups="$(find "$BACKUP_DIR" -maxdepth 1 -name "${base_name}_*.bak" -type f 2>/dev/null | sort -r | tail -n +$((BACKUP_RETENTION_COUNT + 1)) || true)"
        if [[ -n "$old_backups" ]]; then
            while IFS= read -r f; do
                [[ -n "$f" ]] && rm -f "$f" 2>/dev/null || true
            done <<< "$old_backups"
        fi
    fi
}
