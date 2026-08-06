#!/bin/bash
set -e

# Generate DATABASE_URL and REDIS_URL with URL-encoded passwords
# and validate essential environment variables
eval "$(python -c '
import os, sys, urllib.parse

def check_var(name):
    val = os.environ.get(name, "")
    if not val or "CHANGE_ME" in val:
        print(f"echo \"CRITICAL ERROR: {name} is missing or contains placeholder!\" >&2")
        print("exit 1")
        sys.exit(1)
    return val

# Validate critical vars
bot_token = check_var("BOT_TOKEN")
admin_ids = check_var("ADMIN_IDS")
db_key = check_var("DB_ENCRYPTION_KEY")
pg_user = check_var("POSTGRES_USER")
pg_pass = check_var("POSTGRES_PASSWORD")
pg_db = check_var("POSTGRES_DB")
redis_pass = check_var("REDIS_PASSWORD")
domain = check_var("DOMAIN")
backup_key = check_var("BACKUP_AGE_RECIPIENT")

pg_host = os.environ.get("POSTGRES_HOST", "db")
redis_host = os.environ.get("REDIS_HOST", "redis")

# URL encode passwords
pg_pass_encoded = urllib.parse.quote(pg_pass, safe="")
redis_pass_encoded = urllib.parse.quote(redis_pass, safe="")

db_url = f"postgresql+asyncpg://{pg_user}:{pg_pass_encoded}@{pg_host}:5432/{pg_db}"
redis_url = f"redis://:{redis_pass_encoded}@{redis_host}:6379/0"

print(f"export DATABASE_URL=\"{db_url}\"")
print(f"export REDIS_URL=\"{redis_url}\"")
')"

if [ "$1" = 'bot' ]; then
    echo "Running database migrations..."
    alembic upgrade head
    
    echo "Starting bot..."
    exec python -m bot.main
fi

exec "$@"
