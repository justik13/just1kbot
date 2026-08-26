#!/bin/bash
# scripts/docker/backup.sh

set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/just1kbot_${TIMESTAMP}.sql.gz"
ENCRYPTED_FILE="${BACKUP_FILE}.age"

mkdir -p "$BACKUP_DIR"

# Plaintext dumps are always removed, including on failure/interruption.
trap 'rm -f "$BACKUP_FILE"' EXIT

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is not set. Backup cannot be encrypted."
    exit 1
fi

echo "Создание backup..."
export PGPASSWORD="$POSTGRES_PASSWORD"
PG_HOST="${POSTGRES_HOST:-db}"
PG_USER="${POSTGRES_USER:-just1kbot}"
PG_DB="${POSTGRES_DB:-just1kbot_bot}"

pg_dump -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" | gzip > "$BACKUP_FILE"

unset PGPASSWORD

echo "Шифрование backup..."
age -r "$BACKUP_AGE_RECIPIENT" -o "$ENCRYPTED_FILE" "$BACKUP_FILE"
rm "$BACKUP_FILE"

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

echo "Backup создан: ${ENCRYPTED_FILE}"

# Keep a short local recovery window; independent remote retention is managed
# by the remote storage policy.
find "$BACKUP_DIR" -type f -name "*.sql.gz.age" -mtime +7 -delete
echo "Старые локальные бекапы удалены."
