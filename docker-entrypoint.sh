#!/bin/bash
set -euo pipefail

# Generate DATABASE_URL and REDIS_URL with URL-encoded passwords and validate
# all environment variables required by the application before migrations or
# the bot process starts.
eval "$(python -c '
import os
import sys
import urllib.parse


def check_var(name):
    val = os.environ.get(name, "")
    if not val or "CHANGE_ME" in val.upper():
        print(f"echo \"CRITICAL ERROR: {name} is missing or contains a placeholder!\" >&2")
        print("exit 1")
        sys.exit(1)
    return val


# Validate every required runtime setting. Pydantic will perform the detailed
# type/format validation later; this check provides an early, readable failure
# before Alembic or the bot starts.
check_var("BOT_TOKEN")
check_var("ADMIN_IDS")
check_var("SUPPORT_USERNAME")
check_var("DB_ENCRYPTION_KEY")
check_var("POSTGRES_USER")
check_var("POSTGRES_PASSWORD")
check_var("POSTGRES_DB")
check_var("REDIS_PASSWORD")
check_var("YOOKASSA_SHOP_ID")
check_var("YOOKASSA_SECRET_KEY")
check_var("YOOKASSA_RETURN_URL")
check_var("DOMAIN")
check_var("SSL_EMAIL")
check_var("BACKUP_AGE_RECIPIENT")

pg_host = os.environ.get("POSTGRES_HOST", "db")
redis_host = os.environ.get("REDIS_HOST", "redis")

# URL-encode credentials exactly once. PostgreSQL and Redis receive the raw
# passwords, while the application connection URLs receive encoded values.
pg_pass_encoded = urllib.parse.quote(os.environ["POSTGRES_PASSWORD"], safe="")
redis_pass_encoded = urllib.parse.quote(os.environ["REDIS_PASSWORD"], safe="")

pg_user = urllib.parse.quote(os.environ["POSTGRES_USER"], safe="")
pg_db = urllib.parse.quote(os.environ["POSTGRES_DB"], safe="")

db_url = f"postgresql+asyncpg://{pg_user}:{pg_pass_encoded}@{pg_host}:5432/{pg_db}"
redis_url = f"redis://:{redis_pass_encoded}@{redis_host}:6379/0"

print(f"export DATABASE_URL=\"{db_url}\"")
print(f"export REDIS_URL=\"{redis_url}\"")
')"

if [ "${1:-}" = "bot" ]; then
    echo "Running database migrations..."
    alembic upgrade head

    echo "Starting bot..."
    exec python -m bot.main
fi

exec "$@"
