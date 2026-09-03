from datetime import datetime

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog

from core import system_ops as _ops

from ..models import BackupRun, BackupSchedule, RestoreRun, SystemState


def has_active_operation(exclude_backup_run=None, exclude_restore_run=None):
    active_backup = BackupRun.objects.filter(
        status__in=[BackupRun.STATUS_QUEUED, BackupRun.STATUS_RUNNING]
    )
    if exclude_backup_run is not None and getattr(exclude_backup_run, "pk", None):
        active_backup = active_backup.exclude(pk=exclude_backup_run.pk)
    active_backup = active_backup.exists()
    active_restore = RestoreRun.objects.filter(
        status__in=[RestoreRun.STATUS_QUEUED, RestoreRun.STATUS_RUNNING]
    )
    if exclude_restore_run is not None and getattr(exclude_restore_run, "pk", None):
        active_restore = active_restore.exclude(pk=exclude_restore_run.pk)
    active_restore = active_restore.exists()
    return active_backup or active_restore


def get_active_operations():
    return {
        "backup_runs": BackupRun.objects.filter(
            status__in=[BackupRun.STATUS_QUEUED, BackupRun.STATUS_RUNNING]
        ).order_by("-started_at", "-created_at"),
        "restore_runs": RestoreRun.objects.filter(
            status__in=[RestoreRun.STATUS_QUEUED, RestoreRun.STATUS_RUNNING]
        ).order_by("-started_at", "-created_at"),
    }


def _mark_stale_active_operations(exclude_backup_id=None, exclude_restore_id=None):
    message = "Operation state was active in restored backup; marked stale after restore"
    now = timezone.now()
    backup_runs = BackupRun.objects.filter(
        status__in=[BackupRun.STATUS_QUEUED, BackupRun.STATUS_RUNNING]
    )
    restore_runs = RestoreRun.objects.filter(
        status__in=[RestoreRun.STATUS_QUEUED, RestoreRun.STATUS_RUNNING]
    )
    if exclude_backup_id is not None:
        backup_runs = backup_runs.exclude(pk=exclude_backup_id)
    if exclude_restore_id is not None:
        restore_runs = restore_runs.exclude(pk=exclude_restore_id)
    backup_runs.update(status=BackupRun.STATUS_ERROR, finished_at=now, error_message=message)
    restore_runs.update(status=RestoreRun.STATUS_ERROR, finished_at=now, error_message=message)


def _schedule_due(schedule, now):
    if not schedule.enabled:
        return False
    if schedule.next_run_at is None:
        local_now = timezone.localtime(now)
        current_tz = timezone.get_current_timezone()
        candidate = timezone.make_aware(
            datetime.combine(local_now.date(), schedule.run_time),
            current_tz,
        )
        if local_now.weekday() in schedule.weekday_numbers() and candidate <= now:
            return True
        schedule.next_run_at = schedule.calculate_next_run(now)
        schedule.save(update_fields=["next_run_at"])
        return False
    return schedule.next_run_at <= now


def _enqueue_due_scheduled_backup(now):
    if has_active_operation():
        return None
    for schedule in BackupSchedule.objects.select_for_update().filter(enabled=True).order_by("next_run_at", "backup_type"):
        if not _schedule_due(schedule, now):
            continue
        run = BackupRun.objects.create(
            backup_type=schedule.backup_type,
            status=BackupRun.STATUS_QUEUED,
            source=BackupRun.SOURCE_SCHEDULER,
            schedule=schedule,
            comment="Автоматический backup по расписанию",
        )
        schedule.next_run_at = schedule.calculate_next_run(now)
        schedule.save(update_fields=["next_run_at"])
        _ops._audit_backup_run(run, AuditLog.ACTION_BACKUP_QUEUED)
        return run
    return None


def _claim_next_queued_operation():
    running_backup = BackupRun.objects.filter(status=BackupRun.STATUS_RUNNING).exists()
    running_restore = RestoreRun.objects.filter(status=RestoreRun.STATUS_RUNNING).exists()
    if running_backup or running_restore:
        return None

    restore_run = (
        RestoreRun.objects.select_for_update()
        .filter(status=RestoreRun.STATUS_QUEUED)
        .order_by("created_at")
        .first()
    )
    if restore_run:
        restore_run.status = RestoreRun.STATUS_RUNNING
        restore_run.started_at = timezone.now()
        restore_run.save(update_fields=["status", "started_at"])
        return {"kind": "restore", "id": restore_run.pk}

    backup_run = (
        BackupRun.objects.select_for_update()
        .filter(status=BackupRun.STATUS_QUEUED)
        .order_by("created_at")
        .first()
    )
    if backup_run:
        backup_run.status = BackupRun.STATUS_RUNNING
        backup_run.started_at = timezone.now()
        backup_run.save(update_fields=["status", "started_at"])
        return {"kind": "backup", "id": backup_run.pk}
    return None


def claim_scheduler_operation(now=None):
    now = now or timezone.now()
    with transaction.atomic():
        state = SystemState.objects.select_for_update().filter(singleton_key=1).first()
        if state is None:
            return None
        _enqueue_due_scheduled_backup(now)
        return _claim_next_queued_operation()
