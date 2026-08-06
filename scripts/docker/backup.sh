#!/bin/sh
# scripts/docker/backup.sh

set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/just1kbot_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
    echo "ERROR: BACKUP_AGE_RECIPIENT is not set. Backup cannot be encrypted."
    exit 1
fi

echo "Создание backup..."
# PGPASSWORD is automatically used by pg_dump
export PGPASSWORD="$POSTGRES_PASSWORD"
pg_dump -h db -U just1kbot just1kbot_bot | gzip > "$BACKUP_FILE"

echo "Шифрование backup..."
age -r "$BACKUP_AGE_RECIPIENT" -o "${BACKUP_FILE}.age" "$BACKUP_FILE"
rm "$BACKUP_FILE"

echo "Backup создан: ${BACKUP_FILE}.age"

# Очистка старых бекапов (старше 7 дней)
find "$BACKUP_DIR" -type f -name "*.sql.gz.age" -mtime +7 -exec rm {} \;
echo "Старые бекапы удалены."
