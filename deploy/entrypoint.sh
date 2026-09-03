#!/bin/sh
set -e

echo "Checking deploy security env..."
python manage.py check_deploy_security

echo "Waiting for database..."
DB_WAIT_MAX_ATTEMPTS="${DB_WAIT_MAX_ATTEMPTS:-60}"
db_wait_attempt=1
while ! python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')
django.setup()
from django.db import connection
connection.ensure_connection()
" > /dev/null 2>&1; do
    if [ "$db_wait_attempt" -ge "$DB_WAIT_MAX_ATTEMPTS" ]; then
        echo "ERROR: DB wait failed after ${DB_WAIT_MAX_ATTEMPTS} attempts." >&2
        echo "DB connection: DB_HOST=${DB_HOST:-unset} DB_PORT=${DB_PORT:-unset} DB_NAME=${DB_NAME:-unset} DB_USER=${DB_USER:-unset}" >&2
        exit 1
    fi
    echo "Database is not ready yet (attempt ${db_wait_attempt}/${DB_WAIT_MAX_ATTEMPTS}); retrying in 2s..."
    db_wait_attempt=$((db_wait_attempt + 1))
    sleep 2
done
echo "Database is ready."

if [ "$#" -gt 0 ]; then
    echo "Starting command: $*"
    exec "$@"
fi

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "Running migrations..."
python manage.py migrate --noinput

echo "Seeding groups..."
python manage.py seed_groups

echo "Seeding field config..."
python manage.py seed_field_config

echo "Preparing cache directory..."
mkdir -p /var/tmp/django_cache

echo "Starting gunicorn..."
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --limit-request-line "${GUNICORN_LIMIT_REQUEST_LINE:-4094}" \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --preload \
  --access-logfile - \
  --error-logfile -
