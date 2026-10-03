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

# Optional automated upload to Google Drive via rclone.
# Supports User OAuth token (Personal Google Drive / Pro) or Service Account.
# The encrypted artifact is uploaded; plaintext never leaves this container.
GDRIVE_ENABLED="${GDRIVE_BACKUP_ENABLED:-false}"
if [[ "$GDRIVE_ENABLED" == "true" ]]; then
    RCLONE_CONF="/tmp/rclone.conf"
    UPLOAD_FAILED=false

    # Case 1: Existing rclone.conf in /backups/ (isolated only to this container)
    if [[ -f "/backups/rclone.conf" ]]; then
        RCLONE_CONF="/backups/rclone.conf"
    # Case 2: OAuth token passed via GDRIVE_TOKEN_BASE64 (or GDRIVE_TOKEN)
    elif [[ -n "${GDRIVE_TOKEN_BASE64:-}" || -n "${GDRIVE_TOKEN:-}" ]]; then
        TOKEN_JSON=""
        if [[ -n "${GDRIVE_TOKEN_BASE64:-}" ]]; then
            TOKEN_JSON=$(echo "$GDRIVE_TOKEN_BASE64" | tr -d '\r\n ' | base64 -d 2>/dev/null || true)
        else
            TOKEN_JSON="$GDRIVE_TOKEN"
        fi

        if [[ -z "$TOKEN_JSON" ]] || ! echo "$TOKEN_JSON" | grep -q "refresh_token"; then
            echo "ERROR: Некорректный Google Drive OAuth токен (отсутствует refresh_token)." >&2
            UPLOAD_FAILED=true
        else
            cat <<EOF > /tmp/rclone.conf
[gdrive]
type = drive
scope = drive
token = ${TOKEN_JSON}
root_folder_id = ${GDRIVE_FOLDER_ID:-}
EOF
            chmod 600 /tmp/rclone.conf
        fi
    # Case 3: Service Account (Base64 or file, for Workspace Shared Drives)
    elif [[ -n "${GDRIVE_SA_BASE64:-}" || -f "${GDRIVE_SA_FILE:-/backups/gdrive_sa.json}" ]]; then
        GDRIVE_SA=""
        if [[ -n "${GDRIVE_SA_BASE64:-}" ]]; then
            echo "$GDRIVE_SA_BASE64" | tr -d '\r\n ' | base64 -d > /tmp/gdrive_sa.json 2>/dev/null || true
            chmod 600 /tmp/gdrive_sa.json 2>/dev/null || true
            if [[ -s /tmp/gdrive_sa.json ]] && grep -q '"type": *"service_account"' /tmp/gdrive_sa.json; then
                GDRIVE_SA="/tmp/gdrive_sa.json"
            else
                echo "ERROR: Некорректный GDRIVE_SA_BASE64 (не является JSON сервисного аккаунта)." >&2
                UPLOAD_FAILED=true
            fi
        fi

        if [[ -z "$GDRIVE_SA" && "$UPLOAD_FAILED" != "true" ]]; then
            GDRIVE_SA="${GDRIVE_SA_FILE:-/backups/gdrive_sa.json}"
            if [[ ! -f "$GDRIVE_SA" && -f "/backups/$(basename "$GDRIVE_SA")" ]]; then
                GDRIVE_SA="/backups/$(basename "$GDRIVE_SA")"
            fi
        fi

        if [[ -n "$GDRIVE_SA" && -f "$GDRIVE_SA" ]]; then
            cat <<EOF > /tmp/rclone.conf
[gdrive]
type = drive
scope = drive
service_account_file = ${GDRIVE_SA}
root_folder_id = ${GDRIVE_FOLDER_ID:-}
EOF
            chmod 600 /tmp/rclone.conf
        fi
    else
        echo "ERROR: Google Drive бэкап включен, но не найдена конфигурация (/backups/rclone.conf, GDRIVE_TOKEN_BASE64 или Service Account)." >&2
        UPLOAD_FAILED=true
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

    # Очистка временных конфигов
    rm -f /tmp/rclone.conf /tmp/gdrive_sa.json 2>/dev/null || true

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
