#!/bin/bash
set -Eeuo pipefail

# This script is intended to be run by systemd (ExecStartPre) as the just1kbot user

# Load environment
ENV_FILE=${ENV_FILE:-/opt/just1kbot/.env}
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: Environment file $ENV_FILE not found." >&2
    exit 1
fi

# We don't source it because values might contain spaces/quotes, we extract what we need
if ! grep -q "^DATABASE_URL=" "$ENV_FILE"; then
    echo "ERROR: DATABASE_URL is missing in $ENV_FILE." >&2
    exit 1
fi

if ! grep -q "^REDIS_URL=" "$ENV_FILE"; then
    echo "ERROR: REDIS_URL is missing in $ENV_FILE." >&2
    exit 1
fi

if ! grep -q "^BOT_TOKEN=" "$ENV_FILE"; then
    echo "ERROR: BOT_TOKEN is missing in $ENV_FILE." >&2
    exit 1
fi

# Check backup key presence and permissions
CONFIG_DIR=${JUST1KBOT_CONFIG_DIR:-/etc/just1kbot}
BACKUP_KEY="$CONFIG_DIR/backup.agekey"

if grep -q "^DB_ENCRYPTION_KEY=" "$ENV_FILE"; then
    if [ ! -f "$BACKUP_KEY" ]; then
        echo "ERROR: DB_ENCRYPTION_KEY is present but $BACKUP_KEY is missing." >&2
        exit 1
    fi
    # Check if the service user can read the key
    if ! [ -r "$BACKUP_KEY" ]; then
        echo "ERROR: Service user cannot read $BACKUP_KEY. Check permissions." >&2
        exit 1
    fi
fi

echo "Preflight check passed."
exit 0
