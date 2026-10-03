#!/bin/bash
# scripts/docker/backup.sh

set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/just1kbot_${TIMESTAMP}.sql.gz"
ENCRYPTED_FILE="${BACKUP_FILE}.age"

mkdir -p "$BACKUP_DIR"

# Plaintext and partial encrypted dumps are always removed on failure/interruption.
trap 'rm -f "$BACKUP_FILE" "${ENCRYPTED_FILE}.tmp" /tmp/rclone.conf' EXIT

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
# Supports User OAuth token (Personal Google Drive / Pro).
# The encrypted artifact is uploaded; plaintext never leaves this container.
GDRIVE_ENABLED="${GDRIVE_BACKUP_ENABLED:-false}"
if [[ "$GDRIVE_ENABLED" == "true" ]]; then
    RCLONE_CONF="/tmp/rclone.conf"
    UPLOAD_FAILED=false

    # Case 1: Pre-existing rclone.conf in /backups/ (isolated only to this container)
    if [[ -f "/backups/rclone.conf" ]]; then
        RCLONE_CONF="/backups/rclone.conf"
    # Case 2: OAuth token passed via GDRIVE_TOKEN_BASE64
    else
        # Folder ID is strictly mandatory when GDRIVE_BACKUP_ENABLED=true and rclone.conf is not provided
        FOLDER_ID="${GDRIVE_FOLDER_ID:-}"
        if [[ -z "$FOLDER_ID" ]]; then
            echo "ERROR: Google Drive бэкап включен, но GDRIVE_FOLDER_ID не задан в .env!" >&2
            UPLOAD_FAILED=true
        fi

        TOKEN_B64="${GDRIVE_TOKEN_BASE64:-}"
        if [[ -z "$TOKEN_B64" ]]; then
            echo "ERROR: Google Drive бэкап включен, но не найдена конфигурация (/backups/rclone.conf или GDRIVE_TOKEN_BASE64)!" >&2
            UPLOAD_FAILED=true
        fi

        if [[ "$UPLOAD_FAILED" != "true" ]]; then
            TOKEN_JSON=$(echo "$TOKEN_B64" | tr -d '\r\n ' | base64 -d 2>/dev/null || true)
            if [[ -z "$TOKEN_JSON" ]] || ! echo "$TOKEN_JSON" | grep -q "refresh_token"; then
                echo "ERROR: Некорректный Google Drive OAuth токен (не декодируется или отсутствует refresh_token)." >&2
                UPLOAD_FAILED=true
            else
                cat <<EOF > /tmp/rclone.conf
[gdrive]
type = drive
scope = drive
token = ${TOKEN_JSON}
root_folder_id = ${FOLDER_ID}
EOF
                chmod 600 /tmp/rclone.conf
            fi
        fi
    fi

    if [[ "$UPLOAD_FAILED" != "true" && -f "$RCLONE_CONF" ]]; then
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
            if ! rclone --config "$RCLONE_CONF" delete --include "just1kbot_*.sql.gz.age" --min-age "${RETENTION}d" gdrive: --quiet; then
                echo "WARNING: Не удалось завершить очистку устаревших копий в Google Drive." >&2
            fi
        else
            echo "ERROR: Ошибка выгрузки бэкапа в Google Drive!" >&2
            UPLOAD_FAILED=true
        fi
    fi

    # Очистка временного конфига
    rm -f /tmp/rclone.conf 2>/dev/null || true

    # Fail-closed при ошибке облачного бэкапа
    if [[ "$UPLOAD_FAILED" == "true" ]]; then
        echo "ERROR: Локальный зашифрованный бэкап сохранен ($ENCRYPTED_FILE), но выгрузка в Google Drive завершилась с ошибкой!" >&2
        exit 1
    fi
fi

echo "Backup создан: ${ENCRYPTED_FILE}"

# Keep local recovery window aligned with cli.sh policy (14 days); independent
# remote retention is managed by the remote storage policy.
find "$BACKUP_DIR" -type f \( -name "just1kbot_*.sql.gz*" -o -name "*.tmp" \) -mtime +14 -delete
echo "Старые локальные бекапы удалены."
