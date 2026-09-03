from django.dispatch import receiver

from axes.signals import user_locked_out

from .models import AuditLog
from .services import write_audit_log


@receiver(user_locked_out)
def log_axes_lockout(sender, request=None, username=None, ip_address=None, **kwargs):
    """Фиксируем срабатывание axes-блокировки в аудите, чтобы админ видел
    факт брутфорса/DoS по логину. write_audit_log безопасен (try/except)."""
    write_audit_log(
        entity_type=AuditLog.ENTITY_USER,
        entity_id=0,
        action=AuditLog.ACTION_AUTH_LOCKOUT,
        request=request,
        new_values={"username": username, "ip_address": ip_address},
    )
