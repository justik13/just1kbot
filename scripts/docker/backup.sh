#!/bin/sh
# scripts/docker/backup.sh

set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/just1kbot_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

# Убеждаемся, что при любой ошибке (или прерывании) недошифрованный или plaintext дамп будет удален
trap 'rm -f "$BACKUP_FILE"' EXIT

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is not set. Backup cannot be encrypted."
    exit 1
fi

echo "Создание backup..."
# PGPASSWORD is automatically used by pg_dump
export PGPASSWORD="$POSTGRES_PASSWORD"
PG_HOST="${POSTGRES_HOST:-db}"
PG_USER="${POSTGRES_USER:-just1kbot}"
PG_DB="${POSTGRES_DB:-just1kbot_bot}"

pg_dump -h "$PG_HOST" -U "$PG_USER" -d "$PG_DB" | gzip > "$BACKUP_FILE"

echo "Шифрование backup..."
age -r "$BACKUP_AGE_RECIPIENT" -o "${BACKUP_FILE}.age" "$BACKUP_FILE"
rm "$BACKUP_FILE"

echo "Backup создан: ${BACKUP_FILE}.age"

# Очистка старых бекапов (старше 7 дней)
find "$BACKUP_DIR" -type f -name "*.sql.gz.age" -mtime +7 -exec rm {} \;
echo "Старые бекапы удалены."
