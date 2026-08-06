#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting bot..."
exec python -m bot.main "$@"
