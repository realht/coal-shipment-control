import os

from django.conf import settings
from django.db import connection
from django.utils import timezone

from core import system_ops as _ops

from ..models import SystemState
from ._shared import get_backup_dir, get_media_root, logger


def database_health():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return True


def _dir_writable(path):
    return path.is_dir() and os.access(path, os.W_OK)


def get_readiness_status():
    checks = {}
    errors = []
    warnings = []

    try:
        _ops.database_health()
        checks["database"] = {"ok": True}
    except Exception:
        logger.exception("Database health check failed")
        checks["database"] = {"ok": False}
        errors.append("database недоступна")

    media_ok = _dir_writable(get_media_root())
    checks["media_root"] = {"ok": media_ok}
    if not media_ok:
        errors.append("media_root недоступен или не смонтирован")

    backup_ok = _dir_writable(get_backup_dir())
    checks["backup_dir"] = {"ok": backup_ok}
    if not backup_ok:
        warnings.append("backup_dir недоступен или не смонтирован")

    state = None if errors else _ops.get_system_state_readonly()
    warn_seconds = getattr(settings, "SCHEDULER_WARN_SECONDS", 180)
    heartbeat_age = None
    scheduler_ok = False
    if state is not None and state.scheduler_heartbeat_at is not None:
        heartbeat_age = (timezone.now() - state.scheduler_heartbeat_at).total_seconds()
        scheduler_ok = heartbeat_age <= warn_seconds
    checks["scheduler"] = {"ok": scheduler_ok, "heartbeat_age_seconds": heartbeat_age}
    if not scheduler_ok:
        warnings.append("scheduler heartbeat отсутствует или устарел")

    mode = state.mode if state is not None else SystemState.MODE_NORMAL
    mode_ok = mode == SystemState.MODE_NORMAL
    checks["system_mode"] = {"ok": mode_ok, "mode": mode}
    if not mode_ok:
        warnings.append(f"система в режиме {mode}")

    if errors:
        status = "error"
    elif warnings:
        status = "degraded"
    else:
        status = "ok"

    return {"status": status, "checks": checks, "errors": errors, "warnings": warnings}
