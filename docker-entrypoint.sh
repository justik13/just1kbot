#!/bin/bash
set -euo pipefail

# Generate DATABASE_URL and REDIS_URL with URL-encoded passwords and validate
# all environment variables required by the application before migrations or
# the bot process starts. Do not use eval here: secrets must never become
# shell source code.
readarray -t _runtime_urls < <(python -c '
import base64
import os
import sys
import urllib.parse


def check_var(name):
    val = os.environ.get(name, "")
    if isinstance(val, str):
        val = val.strip().strip("\x27\x22")
    if not val or "CHANGE_ME" in val.upper():
        print(f"CRITICAL ERROR: {name} is missing or contains a placeholder!", file=sys.stderr)
        sys.exit(1)
    return val


# Validate every required runtime setting. Pydantic will perform the detailed
# type/format validation later; this check provides an early, readable failure
# before Alembic or the bot starts.
check_var("BOT_TOKEN")
check_var("ADMIN_IDS")
check_var("SUPPORT_USERNAME")
check_var("DB_ENCRYPTION_KEY")
pg_user_raw = check_var("POSTGRES_USER")
pg_pass_raw = check_var("POSTGRES_PASSWORD")
pg_db_raw = check_var("POSTGRES_DB")
redis_pass_raw = check_var("REDIS_PASSWORD")
check_var("YOOKASSA_SHOP_ID")
check_var("YOOKASSA_SECRET_KEY")
check_var("YOOKASSA_RETURN_URL")
check_var("DOMAIN")
check_var("SSL_EMAIL")

pg_host = os.environ.get("POSTGRES_HOST", "db")
redis_host = os.environ.get("REDIS_HOST", "redis")

# URL-encode credentials exactly once after stripping any surrounding quotes.
# PostgreSQL and Redis receive raw passwords, while the application
# connection URLs receive encoded values.
pg_pass_encoded = urllib.parse.quote(pg_pass_raw, safe="")
redis_pass_encoded = urllib.parse.quote(redis_pass_raw, safe="")
pg_user_encoded = urllib.parse.quote(pg_user_raw, safe="")
pg_db_encoded = urllib.parse.quote(pg_db_raw, safe="")

db_url = f"postgresql+asyncpg://{pg_user_encoded}:{pg_pass_encoded}@{pg_host}:5432/{pg_db_encoded}"
redis_url = f"redis://:{redis_pass_encoded}@{redis_host}:6379/0"

# Base64 is used only as a transport format between the Python child process
# and this shell process. It prevents URL characters from being interpreted as
# shell syntax and avoids eval entirely.
for value in (db_url, redis_url):
    print(base64.b64encode(value.encode()).decode())
')

if [ "${#_runtime_urls[@]}" -ne 2 ]; then
    echo "CRITICAL ERROR: failed to construct runtime connection URLs" >&2
    exit 1
fi

export DATABASE_URL="$(printf '%s' "${_runtime_urls[0]}" | base64 -d)"
export REDIS_URL="$(printf '%s' "${_runtime_urls[1]}" | base64 -d)"
unset _runtime_urls

if [ "${1:-}" = "bot" ]; then
    echo "Running database migrations..."
    alembic upgrade head

    echo "Starting bot..."
    exec python -m bot.main
fi

exec "$@"
