#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# HOST-LEVEL / LEGACY FALLBACK
# This script calls standard MariaDB client binaries directly. It is a fallback
# when the Django container cannot execute the managed backup workflow.
#
# Основной механизм резервного копирования —
# Django-команда: python manage.py create_backup (через UI или scheduler).
# Используйте настоящий скрипт только если Django-контейнер недоступен
# or during an explicitly reviewed recovery procedure.
# ════════════════════════════════════════════════════════════════════
# Back up a database and uploads directory on a controlled host.
# Example cron: 0 2 * * * /srv/coal-shipments/deploy/backup.sh >> /srv/coal-shipments-backups/backup.log 2>&1

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/srv/coal-shipments-backups}"
UPLOADS_DIR="${UPLOADS_DIR:-/srv/coal-shipments-uploads}"
DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-}"
DB_NAME="${DB_NAME:-}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-${DB_PASS:-}}"
KEEP_DAYS="${KEEP_DAYS:-30}"

require_env() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    echo "ОШИБКА: required environment variable $name is not set"
    exit 1
  fi
}

require_env DB_HOST
require_env DB_PORT
require_env DB_NAME
require_env DB_USER
require_env DB_PASSWORD

DATE=$(date +%F_%H-%M-%S)
mkdir -p "$BACKUP_DIR"

echo "[$(date '+%F %T')] Начало резервного копирования"

# --- БД ---
SQL_FILE="$BACKUP_DIR/db_${DB_NAME}_${DATE}.sql.gz"
MYSQL_PWD="$DB_PASSWORD" mysqldump \
  -u "$DB_USER" \
  --host="$DB_HOST" --port="$DB_PORT" \
  --single-transaction --routines --triggers \
  "$DB_NAME" | gzip > "$SQL_FILE"
echo "[$(date '+%F %T')] БД → $SQL_FILE"

# --- Uploads ---
TAR_FILE="$BACKUP_DIR/uploads_${DATE}.tar.gz"
tar -czf "$TAR_FILE" -C "$(dirname "$UPLOADS_DIR")" "$(basename "$UPLOADS_DIR")"
echo "[$(date '+%F %T')] Uploads → $TAR_FILE"

# --- Удалить старые бэкапы ---
find "$BACKUP_DIR" -maxdepth 1 -name "db_*.sql.gz" -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name "uploads_*.tar.gz" -mtime +"$KEEP_DAYS" -delete
echo "[$(date '+%F %T')] Старые бэкапы (>${KEEP_DAYS} дней) удалены"

echo "[$(date '+%F %T')] Готово"
