#!/bin/bash
# Full backup through the Django backup command. Exits non-zero if any backup/restore is active.
# Example cron: 0 2 * * 0 cd /srv/coal-shipments && docker compose exec -T app python manage.py create_backup --type full

set -euo pipefail

cd "${APP_DIR:-/srv/coal-shipments}"
docker compose exec -T app python manage.py create_backup --type full
