#!/bin/bash
# ════════════════════════════════════════════════════════════════════
# HOST-LEVEL / LEGACY FALLBACK
# This script calls standard MariaDB client binaries directly. It is a fallback
# for an explicitly reviewed recovery procedure.
#
# Основной механизм восстановления —
# Django-команда: start_restore через UI (/settings/system/).
# Используйте настоящий скрипт только если Django-контейнер недоступен
# or when the Django container is unavailable.
# ════════════════════════════════════════════════════════════════════
# Восстановление БД и uploads из резервной копии.
# Использование:
#   ./restore.sh --db /srv/coal-shipments-backups/db.sql.gz --yes-i-understand-this-will-overwrite-data
#   ./restore.sh --uploads /srv/coal-shipments-backups/uploads.tar.gz --yes-i-understand-this-will-overwrite-data
#   ./restore.sh --db <файл> --uploads <файл> --yes-i-understand-this-will-overwrite-data
#
# ВНИМАНИЕ: restore перезаписывает текущую БД/uploads. Обязательно сделайте backup перед запуском.

set -euo pipefail

DB_HOST="${DB_HOST:-}"
DB_PORT="${DB_PORT:-}"
DB_NAME="${DB_NAME:-}"
DB_USER="${DB_USER:-}"
DB_PASSWORD="${DB_PASSWORD:-${DB_PASS:-}}"
UPLOADS_PARENT="${UPLOADS_PARENT:-/srv}"
UPLOADS_ROOT_NAME="${UPLOADS_ROOT_NAME:-coal_shipments_uploads}"

DB_FILE=""
UPLOADS_FILE=""
CONFIRMED=false
STAGING_PARENT=""
BACKUP_OLD_DIR=""

require_env() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" ]]; then
    echo "ОШИБКА: required environment variable $name is not set"
    exit 1
  fi
}

cleanup_staging() {
  if [[ -n "${STAGING_PARENT:-}" && -d "$STAGING_PARENT" ]]; then
    rm -rf "$STAGING_PARENT"
  fi
}

trap cleanup_staging EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB_FILE="$2"; shift 2 ;;
    --uploads) UPLOADS_FILE="$2"; shift 2 ;;
    --yes-i-understand-this-will-overwrite-data) CONFIRMED=true; shift ;;
    *) echo "Неизвестный аргумент: $1"; exit 1 ;;
  esac
done

if [[ -z "$DB_FILE" && -z "$UPLOADS_FILE" ]]; then
  echo "Укажите --db <файл> и/или --uploads <файл>"
  exit 1
fi

if [[ "$CONFIRMED" != "true" ]]; then
  echo ""
  echo "ВНИМАНИЕ: restore перезапишет текущие данные БД/uploads без возможности отмены."
  echo ""
  echo "Для подтверждения добавьте флаг:"
  echo "  --yes-i-understand-this-will-overwrite-data"
  echo ""
  echo "Рекомендуется сначала сделать backup:"
  echo "  ./backup.sh"
  exit 1
fi

if [[ -n "$DB_FILE" ]]; then
  require_env DB_HOST
  require_env DB_PORT
  require_env DB_NAME
  require_env DB_USER
  require_env DB_PASSWORD
fi

echo "[$(date '+%F %T')] Начало восстановления"

if [[ -n "$DB_FILE" ]]; then
  echo "[$(date '+%F %T')] Восстановление БД из $DB_FILE"
  zcat "$DB_FILE" | MYSQL_PWD="$DB_PASSWORD" mysql \
    -u "$DB_USER" \
    --host="$DB_HOST" --port="$DB_PORT" \
    "$DB_NAME"
  echo "[$(date '+%F %T')] БД восстановлена"
fi

if [[ -n "$UPLOADS_FILE" ]]; then
  echo "[$(date '+%F %T')] Восстановление uploads из $UPLOADS_FILE"
  bad_entry=$(tar -tzf "$UPLOADS_FILE" | awk -v root="$UPLOADS_ROOT_NAME" '
    {
      entry = $0
      if (entry == "") next
      if (entry ~ /^\// || entry ~ /(^|\/)\.\.(\/|$)/) { print entry; exit }
      if (entry != root && entry !~ ("^" root "/")) { print entry; exit }
    }
  ')
  if [[ -n "$bad_entry" ]]; then
    echo "ОШИБКА: Небезопасный путь в uploads-архиве: $bad_entry"
    exit 1
  fi
  bad_type=$(tar -tvzf "$UPLOADS_FILE" | awk 'substr($0, 1, 1) != "-" && substr($0, 1, 1) != "d" { print; exit }')
  if [[ -n "$bad_type" ]]; then
    echo "ОШИБКА: Небезопасный тип entry в uploads-архиве: $bad_type"
    exit 1
  fi
  mkdir -p "$UPLOADS_PARENT"
  STAGING_PARENT="${UPLOADS_PARENT}/.${UPLOADS_ROOT_NAME}.restore_uploads_staging.$$"
  STAGING_UPLOADS_DIR="$STAGING_PARENT/$UPLOADS_ROOT_NAME"
  TARGET_UPLOADS_DIR="$UPLOADS_PARENT/$UPLOADS_ROOT_NAME"
  BACKUP_OLD_DIR="${UPLOADS_PARENT}/.${UPLOADS_ROOT_NAME}.restore_old.$$"

  rm -rf "$STAGING_PARENT" "$BACKUP_OLD_DIR"
  mkdir -p "$STAGING_PARENT"
  tar -xzf "$UPLOADS_FILE" -C "$STAGING_PARENT"
  if [[ ! -d "$STAGING_UPLOADS_DIR" ]]; then
    echo "ОШИБКА: uploads-архив не содержит ожидаемый каталог $UPLOADS_ROOT_NAME"
    exit 1
  fi

  if [[ -e "$TARGET_UPLOADS_DIR" ]]; then
    mv "$TARGET_UPLOADS_DIR" "$BACKUP_OLD_DIR"
  fi
  if ! mv "$STAGING_UPLOADS_DIR" "$TARGET_UPLOADS_DIR"; then
    if [[ -e "$BACKUP_OLD_DIR" ]]; then
      mv "$BACKUP_OLD_DIR" "$TARGET_UPLOADS_DIR"
    fi
    echo "ОШИБКА: не удалось заменить uploads каталог"
    exit 1
  fi
  rm -rf "$BACKUP_OLD_DIR"
  BACKUP_OLD_DIR=""
  echo "[$(date '+%F %T')] Uploads восстановлены"
fi

echo "[$(date '+%F %T')] Готово"
