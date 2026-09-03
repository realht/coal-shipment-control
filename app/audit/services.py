import logging

from .models import AuditLog
from core.ip_utils import get_client_ip


logger = logging.getLogger(__name__)


def write_audit_log(
    *,
    entity_type,
    entity_id,
    action,
    user=None,
    request=None,
    old_values=None,
    new_values=None,
    source=AuditLog.SOURCE_UI,
    ip_address=None,
    user_agent="",
):
    try:
        if request is not None:
            user = getattr(request, "user", user)
            ip_address = get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]

        if user is not None and not getattr(user, "is_authenticated", False):
            user = None

        return AuditLog.objects.create(
            entity_type=entity_type,
            entity_id=entity_id or 0,
            action=action,
            old_values=old_values,
            new_values=new_values,
            source=source,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent[:500],
        )
    except Exception:
        logger.exception(
            "Failed to write audit log",
            extra={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action": action,
                "source": source,
            },
        )
        return None
