from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.utils import timezone
from django.views.generic import ListView, DetailView
from core.table_filters import parse_top_level_date_bound
from .models import AuditLog


def _parse_user_id(value):
    if not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class AuditPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "audit.view_auditlog"


class AuditLogListView(AuditPermissionMixin, ListView):
    model = AuditLog
    template_name = "audit/list.html"
    context_object_name = "logs"
    paginate_by = 50

    def get_queryset(self):
        qs = AuditLog.objects.select_related("user")
        entity_type = self.request.GET.get("entity_type", "").strip()
        action = self.request.GET.get("action", "").strip()
        source = self.request.GET.get("source", "").strip()
        user_id = self.request.GET.get("user_id", "").strip()
        date_from = self.request.GET.get("date_from", "").strip()
        date_to = self.request.GET.get("date_to", "").strip()
        if entity_type:
            qs = qs.filter(entity_type=entity_type)
        if action:
            qs = qs.filter(action=action)
        if source:
            qs = qs.filter(source=source)
        parsed_user_id = _parse_user_id(user_id)
        if parsed_user_id is not None:
            qs = qs.filter(user_id=parsed_user_id)

        # Фильтр по дате диапазоном aware-datetime, а не created_at__date:
        # на MariaDB __date генерирует CONVERT_TZ (пустые tz-таблицы → 0 записей),
        # не использует индекс idx_audit_created и падает в 500 при невалидной дате.
        tz = timezone.get_current_timezone()
        d_from = parse_top_level_date_bound(date_from)
        d_to = parse_top_level_date_bound(date_to)
        self._date_from_invalid = bool(date_from) and d_from is None
        self._date_to_invalid = bool(date_to) and d_to is None
        if d_from is not None:
            lower = timezone.make_aware(datetime.combine(d_from, time.min), tz)
            qs = qs.filter(created_at__gte=lower)
        if d_to is not None:
            upper = timezone.make_aware(datetime.combine(d_to + timedelta(days=1), time.min), tz)
            qs = qs.filter(created_at__lt=upper)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["entity_type"] = self.request.GET.get("entity_type", "")
        ctx["action"] = self.request.GET.get("action", "")
        ctx["source"] = self.request.GET.get("source", "")
        ctx["user_id"] = self.request.GET.get("user_id", "")
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        ctx["date_from_invalid"] = getattr(self, "_date_from_invalid", False)
        ctx["date_to_invalid"] = getattr(self, "_date_to_invalid", False)
        ctx["entity_choices"] = AuditLog.ENTITY_CHOICES
        ctx["action_choices"] = AuditLog.ACTION_CHOICES
        ctx["source_choices"] = AuditLog.SOURCE_CHOICES
        ctx["users"] = get_user_model().objects.order_by("username")
        return ctx


class AuditLogDetailView(AuditPermissionMixin, DetailView):
    model = AuditLog
    template_name = "audit/detail.html"
    context_object_name = "log"
