#!/bin/bash
# Incremental uploads backup plus full database dump through the Django backup command.
# Exits non-zero if any backup/restore is active.
# Example cron: 0 2 * * 1-6 cd /srv/coal-shipments && docker compose exec -T app python manage.py create_backup --type incremental

set -euo pipefail

cd "${APP_DIR:-/srv/coal-shipments}"
docker compose exec -T app python manage.py create_backup --type incremental
