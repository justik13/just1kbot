#!/bin/bash
# scripts/docker/backup.sh

set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/just1kbot_${TIMESTAMP}.sql.gz"
ENCRYPTED_FILE="${BACKUP_FILE}.age"

mkdir -p "$BACKUP_DIR"

# Plaintext and partial encrypted dumps are always removed on failure/interruption.
trap 'rm -f "$BACKUP_FILE" "${ENCRYPTED_FILE}.tmp"' EXIT

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is not set. Backup cannot be encrypted."
    exit 1
fi

echo "Создание backup..."
export PGPASSWORD="$POSTGRES_PASSWORD"
PG_HOST="${POSTGRES_HOST:-db}"
PG_USER="${POSTGRES_USER:-just1kbot}"
PG_DB="${POSTGRES_DB:-just1kbot_bot}"

if ! pg_dump -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" | gzip > "$BACKUP_FILE"; then
    echo "ERROR: pg_dump or gzip pipeline failed."
    exit 1
fi

unset PGPASSWORD

if [ ! -s "$BACKUP_FILE" ] || ! gzip -t "$BACKUP_FILE" 2>/dev/null; then
    echo "ERROR: Backup archive is empty or failed gzip integrity check."
    exit 1
fi

echo "Шифрование backup (atomic write)..."
age -r "$BACKUP_AGE_RECIPIENT" -o "${ENCRYPTED_FILE}.tmp" "$BACKUP_FILE"
mv -f "${ENCRYPTED_FILE}.tmp" "$ENCRYPTED_FILE"
rm -f "$BACKUP_FILE"

# Optional independent remote storage. BACKUP_REMOTE_URI should be a HTTPS
# upload endpoint or a presigned PUT URL. If it contains {filename}, it is
# replaced with the encrypted filename. The encrypted artifact is uploaded;
# plaintext never leaves this container.
if [ -n "${BACKUP_REMOTE_URI:-}" ]; then
    REMOTE_URL="${BACKUP_REMOTE_URI//\{filename\}/$(basename "$ENCRYPTED_FILE")}"
    echo "Загрузка encrypted backup во внешнее хранилище..."
    CURL_ARGS=(--fail --silent --show-error --retry 3 --retry-delay 2 --upload-file "$ENCRYPTED_FILE")
    if [ -n "${BACKUP_REMOTE_TOKEN:-}" ]; then
        CURL_ARGS+=(--header "Authorization: Bearer ${BACKUP_REMOTE_TOKEN}")
    fi
    curl "${CURL_ARGS[@]}" "$REMOTE_URL"
    echo "Remote backup upload завершён."
fi

# Optional automated upload to Google Drive via rclone.
# Uses canonical configuration in /backups/rclone.conf (isolated only to this container).
# The encrypted artifact is uploaded; plaintext never leaves this container.
GDRIVE_ENABLED="${GDRIVE_BACKUP_ENABLED:-false}"
if [[ "$GDRIVE_ENABLED" == "true" ]]; then
    RCLONE_CONF="/backups/rclone.conf"
    UPLOAD_FAILED=false

    if [[ ! -f "$RCLONE_CONF" ]]; then
        echo "ERROR: Google Drive бэкап включен, но файл /backups/rclone.conf не найден! Запустите 'just1kbot gdrive' для настройки." >&2
        UPLOAD_FAILED=true
    else
        # Ensure correct secure permissions inside container mount
        chmod 600 "$RCLONE_CONF" 2>/dev/null || true

        # Mandatory folder check (Fail-Closed: protects root Google Drive from accidental upload/deletion)
        if ! grep -Eq '^[[:space:]]*root_folder_id[[:space:]]*=[[:space:]]*[^[:space:]]+' "$RCLONE_CONF"; then
            echo "ERROR: В /backups/rclone.conf не задан обязательный параметр root_folder_id (защита корня диска)!" >&2
            UPLOAD_FAILED=true
        fi

        # Mandatory token check
        if ! grep -Eq '^[[:space:]]*token[[:space:]]*=' "$RCLONE_CONF"; then
            echo "ERROR: В /backups/rclone.conf отсутствует параметр token!" >&2
            UPLOAD_FAILED=true
        fi
    fi

    if [[ "$UPLOAD_FAILED" != "true" ]]; then
        echo "Загрузка encrypted backup в Google Drive (rclone)..."
        if rclone --config "$RCLONE_CONF" copy "$ENCRYPTED_FILE" gdrive: --retries 3 --retries-sleep 2s --stats 0; then
            echo "Google Drive upload успешно завершён: $(basename "$ENCRYPTED_FILE")"

            # Валидация retention
            RETENTION="${GDRIVE_RETENTION_DAYS:-14}"
            if ! [[ "$RETENTION" =~ ^[0-9]+$ ]] || [ "$RETENTION" -le 0 ]; then
                echo "WARNING: Некорректное значение GDRIVE_RETENTION_DAYS='$RETENTION', используется 14." >&2
                RETENTION=14
            fi

            echo "Очистка устаревших бэкапов в Google Drive (старше ${RETENTION} дн.)..."
            if ! rclone --config "$RCLONE_CONF" delete --include "just1kbot_*.sql.gz.age" --min-age "${RETENTION}d" gdrive: --retries 3 --retries-sleep 2s --quiet; then
                echo "ERROR: Не удалось завершить очистку устаревших копий в Google Drive после 3 попыток!" >&2
                UPLOAD_FAILED=true
            fi
        else
            echo "ERROR: Ошибка выгрузки бэкапа в Google Drive!" >&2
            UPLOAD_FAILED=true
        fi
    fi

    # Fail-closed при ошибке облачного бэкапа или ротации
    if [[ "$UPLOAD_FAILED" == "true" ]]; then
        echo "ERROR: Локальный зашифрованный бэкап сохранен ($ENCRYPTED_FILE), но облачные операции в Google Drive завершились с ошибкой!" >&2
        exit 1
    fi
fi

echo "Backup создан: ${ENCRYPTED_FILE}"

# Keep local recovery window aligned with cli.sh policy (14 days); independent
# remote retention is managed by the remote storage policy.
find "$BACKUP_DIR" -type f \( -name "just1kbot_*.sql.gz*" -o -name "*.tmp" \) -mtime +14 -delete
echo "Старые локальные бекапы удалены."
