import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from imports.models import ImportRowResult

from core import system_ops as _ops

from ..models import BackupRun, RestoreRun, SystemState
from ._shared import _scheduler_heartbeat_paused, logger


def _run_daily_scheduler_maintenance(now=None):
    now = now or timezone.now()
    _ops.call_command("clearsessions")
    import_cutoff = now - timedelta(days=getattr(settings, "IMPORT_ROW_RESULTS_KEEP_DAYS", 180))
    old_row_results = ImportRowResult.objects.filter(import_log__created_at__lt=import_cutoff)
    import_deleted, _ = old_row_results.delete()
    document_result = _ops._cleanup_deleted_document_files(now)
    retention_result = _ops._apply_retention(now=now)
    result = {
        "clearsessions": True,
        "import_row_results_deleted": import_deleted,
        **document_result,
        "backup_retention": retention_result,
    }
    SystemState.objects.filter(singleton_key=1).update(
        daily_cleanup_last_run_at=now,
        daily_cleanup_last_result=result,
    )
    return result


def _maybe_recalculate_uploads_size(now):
    state = SystemState.objects.filter(singleton_key=1).first()
    if state is None:
        return
    if state.uploads_size_calculated_at is None or (
        now - state.uploads_size_calculated_at
    ).total_seconds() > 3600:
        _ops.recalculate_uploads_size()


def _maybe_run_daily_scheduler_maintenance(now):
    state = SystemState.objects.filter(singleton_key=1).first()
    if state is None:
        return
    if (
        state.daily_cleanup_last_run_at is None
        or timezone.localtime(state.daily_cleanup_last_run_at).date()
        < timezone.localtime(now).date()
    ):
        try:
            _ops._run_daily_scheduler_maintenance(now=now)
        except Exception:
            # V17-MED-6: устойчивая ошибка уборки не должна крутиться каждый тик.
            # Фиксируем метку последней попытки, чтобы maintenance подождал до
            # следующего дня, а очередь backup/restore продолжила обрабатываться.
            logger.exception("Daily scheduler maintenance failed")
            SystemState.objects.filter(singleton_key=1).update(
                daily_cleanup_last_run_at=now,
                daily_cleanup_last_result={"error": "maintenance failed; see logs"},
            )


def _run_with_scheduler_heartbeat(fn):
    stop_event = threading.Event()
    interval = max(1, getattr(settings, "SCHEDULER_HEARTBEAT_INTERVAL_SECONDS", 60))

    def heartbeat_loop():
        while not stop_event.wait(interval):
            if _scheduler_heartbeat_paused.is_set():
                continue
            try:
                _ops._touch_scheduler_heartbeat()
            except Exception:
                logger.exception("Failed to refresh scheduler heartbeat during operation")

    _ops._touch_scheduler_heartbeat()
    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    try:
        return fn()
    finally:
        stop_event.set()
        thread.join(timeout=1)
        _ops._touch_scheduler_heartbeat()


def run_scheduler_tick(now=None):
    now = now or timezone.now()
    # V17-MED-6: под-этапы обслуживания не должны блокировать claim очереди —
    # каждый изолирован своим try/except, ошибка логируется, тик продолжается.
    for step in (
        _ops._touch_scheduler_heartbeat,
        _maybe_recalculate_uploads_size,
        _maybe_run_daily_scheduler_maintenance,
    ):
        try:
            step(now)
        except Exception:
            logger.exception("Scheduler maintenance step %s failed; continuing to claim", step.__name__)
    claimed = _ops.claim_scheduler_operation(now)
    if not claimed:
        return {"claimed": False}

    if claimed["kind"] == "backup":
        run = BackupRun.objects.get(pk=claimed["id"])
        try:
            _ops._run_with_scheduler_heartbeat(
                lambda: _ops.create_backup(
                    run.backup_type,
                    initiated_by=run.initiated_by,
                    run=run,
                    comment=run.comment,
                )
            )
            return {"claimed": True, "kind": "backup", "id": run.pk, "status": BackupRun.STATUS_SUCCESS}
        except Exception as exc:
            return {"claimed": True, "kind": "backup", "id": run.pk, "status": BackupRun.STATUS_ERROR, "error": str(exc)}

    run = RestoreRun.objects.get(pk=claimed["id"])
    try:
        _ops._run_with_scheduler_heartbeat(lambda: _ops.restore_backup(run))
        return {"claimed": True, "kind": "restore", "id": run.pk, "status": RestoreRun.STATUS_SUCCESS}
    except Exception as exc:
        return {"claimed": True, "kind": "restore", "id": run.pk, "status": RestoreRun.STATUS_ERROR, "error": str(exc)}
