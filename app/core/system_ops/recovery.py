from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog

from core import system_ops as _ops

from ..models import BackupRun, RestoreRun, SystemState


def _stale_running_filter(now=None):
    now = now or timezone.now()
    stale_minutes = getattr(settings, "SCHEDULER_STALE_AGE_MINUTES", 15)
    stale_cutoff = now - timedelta(minutes=stale_minutes)
    return Q(started_at__isnull=True) | Q(started_at__lt=stale_cutoff)


def _scheduler_heartbeat_is_fresh(state, now=None):
    if state is None or state.scheduler_heartbeat_at is None:
        return False
    now = now or timezone.now()
    warn_seconds = getattr(settings, "SCHEDULER_WARN_SECONDS", 180)
    return (now - state.scheduler_heartbeat_at).total_seconds() <= warn_seconds


def recover_interrupted_restore(user=None, request=None, force=False):
    message = "Operation was interrupted; admin recovery was requested"
    now = timezone.now()
    stale_filter = _stale_running_filter(now)

    # V18-LOW-1: держим синглтон под select_for_update на всё окно оценки/updates
    # (по образцу recover_stale_running_operations_on_scheduler_start), чтобы
    # решение о stuck-режиме и смена mode были согласованы с конкурентными писателями.
    with transaction.atomic():
        state, _ = SystemState.objects.select_for_update().get_or_create(singleton_key=1)
        scheduler_alive = _scheduler_heartbeat_is_fresh(state, now)

        # Determine stuck-case BEFORE updates: RESTORE_RUNNING + no active records at all = stuck
        any_active_before = _ops.has_active_operation()
        mode_was_stuck = state.mode == SystemState.MODE_RESTORE_RUNNING and not any_active_before
        if scheduler_alive and any_active_before and not force:
            return {
                "backup_count": 0,
                "restore_count": 0,
                "affected_backup_ids": [],
                "affected_restore_ids": [],
                "mode_was_stuck": False,
                "scheduler_alive": True,
                "recovery_refused": True,
            }

        active_backup_qs = BackupRun.objects.filter(
            Q(status=BackupRun.STATUS_QUEUED)
            | (Q(status=BackupRun.STATUS_RUNNING) & stale_filter)
        )
        active_restore_qs = RestoreRun.objects.filter(
            Q(status=RestoreRun.STATUS_QUEUED)
            | (Q(status=RestoreRun.STATUS_RUNNING) & stale_filter)
        )
        affected_backup_ids = list(active_backup_qs.values_list("pk", flat=True))
        affected_restore_ids = list(active_restore_qs.values_list("pk", flat=True))
        backup_count = active_backup_qs.update(status=BackupRun.STATUS_ERROR, finished_at=now, error_message=message)
        restore_count = active_restore_qs.update(status=RestoreRun.STATUS_ERROR, finished_at=now, error_message=message)
        if backup_count or restore_count or mode_was_stuck:
            old_mode = state.mode
            state.mode = SystemState.MODE_ADMIN_ONLY
            state.reason = "Restore interrupted; admin verification required"
            if user is not None and getattr(user, "is_authenticated", False):
                state.changed_by = user
            state.save(update_fields=["mode", "reason", "changed_by", "changed_at"])
            _ops._write_system_audit(
                entity_type=AuditLog.ENTITY_SYSTEM,
                entity_id=state.pk,
                action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
                user=user,
                request=request,
                new_values={"old_mode": old_mode, "mode": state.mode, "reason": state.reason},
            )
            _ops._write_system_audit(
                entity_type=AuditLog.ENTITY_SYSTEM,
                entity_id=state.pk,
                action=AuditLog.ACTION_OPERATION_RECOVERED,
                user=user,
                request=request,
                new_values={
                    "backup_count": backup_count,
                    "restore_count": restore_count,
                    "mode_was_stuck": mode_was_stuck,
                },
            )
    return {
        "backup_count": backup_count,
        "restore_count": restore_count,
        "affected_backup_ids": affected_backup_ids,
        "affected_restore_ids": affected_restore_ids,
        "mode_was_stuck": mode_was_stuck,
        "scheduler_alive": scheduler_alive,
        "recovery_refused": False,
    }


def recover_stale_running_operations_on_scheduler_start():
    message = "Operation was interrupted by scheduler/container restart"
    now = timezone.now()
    stale_filter = _stale_running_filter(now)
    with transaction.atomic():
        state, _ = SystemState.objects.select_for_update().get_or_create(singleton_key=1)
        scheduler_alive = _scheduler_heartbeat_is_fresh(state, now)
        if scheduler_alive and _ops.has_active_operation():
            return {
                "backup_count": 0,
                "restore_count": 0,
                "scheduler_alive": True,
                "recovery_refused": True,
            }
        backup_count = BackupRun.objects.filter(
            status=BackupRun.STATUS_RUNNING,
        ).filter(stale_filter).update(
            status=BackupRun.STATUS_ERROR,
            finished_at=now,
            error_message=message,
        )
        restore_count = RestoreRun.objects.filter(
            status=RestoreRun.STATUS_RUNNING,
        ).filter(stale_filter).update(
            status=RestoreRun.STATUS_ERROR,
            finished_at=now,
            error_message=message,
        )
        restore_interrupted = restore_count > 0 or state.mode == SystemState.MODE_RESTORE_RUNNING
        if restore_interrupted:
            old_mode = state.mode
            state.mode = SystemState.MODE_ADMIN_ONLY
            state.reason = "Restore interrupted; admin verification required"
            state.save(update_fields=["mode", "reason", "changed_at"])
            _ops._write_system_audit(
                entity_type=AuditLog.ENTITY_SYSTEM,
                entity_id=state.pk,
                action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
                source=AuditLog.SOURCE_SCHEDULER,
                new_values={"old_mode": old_mode, "mode": state.mode, "reason": state.reason},
            )

        if backup_count or restore_count or restore_interrupted:
            _ops._write_system_audit(
                entity_type=AuditLog.ENTITY_SYSTEM,
                entity_id=state.pk,
                action=AuditLog.ACTION_OPERATION_RECOVERED,
                source=AuditLog.SOURCE_SCHEDULER,
                new_values={"backup_count": backup_count, "restore_count": restore_count},
            )
    return {
        "backup_count": backup_count,
        "restore_count": restore_count,
        "scheduler_alive": scheduler_alive,
        "recovery_refused": False,
    }
