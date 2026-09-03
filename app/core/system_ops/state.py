from django.utils import timezone

from audit.models import AuditLog

from core import system_ops as _ops

from ..models import SystemState
from ._shared import get_dir_size, get_media_root, logger


def can_view_system_status(user):
    return bool(user and user.is_authenticated and user.has_perm("core.view_system_status"))


def _write_system_audit(*, entity_type, entity_id, action, user=None, request=None, new_values=None, source=AuditLog.SOURCE_UI):
    try:
        _ops.write_audit_log(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            user=user,
            request=request,
            new_values=new_values,
            source=source,
        )
    except Exception:
        logger.exception(
            "Failed to write system audit log",
            extra={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "source": source,
            },
        )


def get_system_state():
    state, _ = SystemState.objects.get_or_create(singleton_key=1)
    return state


def get_system_state_readonly():
    """Прочитать строку-синглтон SystemState без её создания.

    V18-MED-1: писатели вне процесса restore (middleware в веб-воркерах) не
    должны делать INSERT синглтона — в окне DROP/CREATE→INSERT mysql-дампа
    такая вставка конфликтует с INSERT из дампа и валит restore.
    """
    return SystemState.objects.filter(singleton_key=1).first()


def _touch_scheduler_heartbeat(now=None):
    # V18-MED-1: update-only, чтобы heartbeat никогда не вставлял строку-синглтон
    # (см. get_system_state_readonly). Если строки ещё нет — no-op; синглтон
    # создаётся при старте scheduler и первым веб-запросом.
    now = now or timezone.now()
    SystemState.objects.filter(singleton_key=1).update(scheduler_heartbeat_at=now)
    return now


def set_system_mode(mode, user=None, reason="", request=None, source=AuditLog.SOURCE_UI):
    state = get_system_state()
    old_mode = state.mode
    state.mode = mode
    state.reason = reason or ""
    if user is not None and getattr(user, "is_authenticated", False):
        state.changed_by = user
    state.save(update_fields=["mode", "reason", "changed_by", "changed_at"])
    _write_system_audit(
        entity_type=AuditLog.ENTITY_SYSTEM,
        entity_id=state.pk,
        action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
        user=user,
        request=request,
        source=source,
        new_values={"old_mode": old_mode, "mode": state.mode, "reason": state.reason},
    )
    return state


def recalculate_uploads_size():
    size = get_dir_size(get_media_root())
    SystemState.objects.filter(singleton_key=1).update(
        uploads_size_bytes=size,
        uploads_size_calculated_at=timezone.now(),
    )
    return size
