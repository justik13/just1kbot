#!/bin/bash
# scripts/docker/backup.sh

set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/just1kbot_${TIMESTAMP}.sql.gz"
ENCRYPTED_FILE="${BACKUP_FILE}.age"

mkdir -p "$BACKUP_DIR"

# Plaintext and partial encrypted dumps are always removed on failure/interruption.
trap 'rm -f "$BACKUP_FILE" "${ENCRYPTED_FILE}.tmp" /tmp/rclone.conf /tmp/gdrive_sa.json' EXIT

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

# Optional automated upload to Google Drive via rclone (Google Service Account).
# The encrypted artifact is uploaded; plaintext never leaves this container.
GDRIVE_ENABLED="${GDRIVE_BACKUP_ENABLED:-false}"
if [[ "$GDRIVE_ENABLED" == "true" ]] || [[ -n "${GDRIVE_FOLDER_ID:-}" && (-n "${GDRIVE_SA_BASE64:-}" || -f "${GDRIVE_SA_FILE:-/backups/gdrive_sa.json}") ]]; then
    GDRIVE_SA=""
    if [[ -n "${GDRIVE_SA_BASE64:-}" ]]; then
        echo "$GDRIVE_SA_BASE64" | tr -d '\r\n ' | base64 -d > /tmp/gdrive_sa.json 2>/dev/null || true
        chmod 600 /tmp/gdrive_sa.json 2>/dev/null || true
        if [[ -s /tmp/gdrive_sa.json ]]; then
            GDRIVE_SA="/tmp/gdrive_sa.json"
        fi
    fi

    if [[ -z "$GDRIVE_SA" ]]; then
        GDRIVE_SA="${GDRIVE_SA_FILE:-/backups/gdrive_sa.json}"
        if [[ ! -f "$GDRIVE_SA" && -f "/backups/$(basename "$GDRIVE_SA")" ]]; then
            GDRIVE_SA="/backups/$(basename "$GDRIVE_SA")"
        fi
    fi

    if [[ -n "$GDRIVE_SA" && -f "$GDRIVE_SA" && -n "${GDRIVE_FOLDER_ID:-}" ]]; then
        echo "Загрузка encrypted backup в Google Drive (rclone)..."
        cat <<EOF > /tmp/rclone.conf
[gdrive]
type = drive
scope = drive.file
service_account_file = ${GDRIVE_SA}
root_folder_id = ${GDRIVE_FOLDER_ID}
EOF
        chmod 600 /tmp/rclone.conf

        if rclone --config /tmp/rclone.conf copy "$ENCRYPTED_FILE" gdrive: --retries 3 --retries-sleep 2s --stats 0; then
            echo "Google Drive upload завершён: $(basename "$ENCRYPTED_FILE")"
            RETENTION="${GDRIVE_RETENTION_DAYS:-14}"
            echo "Очистка устаревших бэкапов в Google Drive (старше ${RETENTION} дн.)..."
            rclone --config /tmp/rclone.conf delete --min-age "${RETENTION}d" gdrive: --quiet || true
        else
            echo "ПРЕДУПРЕЖДЕНИЕ: Ошибка загрузки бэкапа в Google Drive." >&2
        fi
        rm -f /tmp/rclone.conf /tmp/gdrive_sa.json
    else
        echo "ПРЕДУПРЕЖДЕНИЕ: Google Drive включен, но не задан ключ Service Account (GDRIVE_SA_BASE64 или файл) либо GDRIVE_FOLDER_ID." >&2
    fi
fi

echo "Backup создан: ${ENCRYPTED_FILE}"

# Keep local recovery window aligned with cli.sh policy (14 days); independent
# remote retention is managed by the remote storage policy.
find "$BACKUP_DIR" -type f \( -name "just1kbot_*.sql.gz*" -o -name "*.tmp" \) -mtime +14 -delete
echo "Старые локальные бекапы удалены."
