import io
import logging
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import FieldDoesNotExist
from django.db import connection, transaction
from django.db.models import CharField, Exists, OuterRef, Q, TextField
from django.db.models.functions import Cast
from django.http import HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from audit.models import AuditLog
from documents.models import ShipmentDocument
from core.exporting import SelectedExportError, selected_export_queryset
from core.field_config import get_filter_config, get_sort_fields, get_table_presets, get_sticky_fields
from core.ip_utils import get_client_ip
from core.table_filters import active_column_filters, apply_column_filters, parse_top_level_date_bound

logger = logging.getLogger(__name__)


def _text_filter_values_search(field, q):
    search_filter = Q(**{f"{field}__icontains": q})
    if connection.vendor == "sqlite":
        # SQLite LIKE is not Unicode-case-insensitive for Cyrillic; production MariaDB uses CI collation.
        for variant in {q.upper(), q.lower(), q.capitalize()} - {q}:
            search_filter |= Q(**{f"{field}__contains": variant})
    return search_filter


def _query_filter_values(model, field, q="", limit=200):
    qs = model.objects.filter(is_deleted=False)
    try:
        f = model._meta.get_field(field)
    except FieldDoesNotExist:
        return [], False
    if isinstance(f, (CharField, TextField)):
        qs = qs.exclude(**{f"{field}__exact": ""})
        if q:
            qs = qs.filter(_text_filter_values_search(field, q))
    elif q:
        qs = qs.annotate(_filter_value_text=Cast(field, output_field=CharField())).filter(
            _filter_value_text__icontains=q
        )
    values = list(qs.values_list(field, flat=True).distinct().order_by(field)[: limit + 1])
    return values[:limit], len(values) > limit


class FilterValuesView(LoginRequiredMixin, PermissionRequiredMixin, View):
    entity_name = None
    model = None
    LIMIT = 200

    def get(self, request, field):
        filter_config = get_filter_config(self.entity_name)
        if filter_config.get(field) != "value":
            return JsonResponse({"values": []}, status=404)
        q = request.GET.get("q", "").strip()
        values, has_more = _query_filter_values(self.model, field, q=q, limit=self.LIMIT)
        return JsonResponse({"values": values, "has_more": has_more})


def shipment_to_dict(obj):
    return {
        f.name: str(getattr(obj, f.name))
        for f in obj._meta.fields
        if f.name not in ("id", "created_at", "updated_at", "created_by", "updated_by", "is_deleted")
    }


def log_shipment(request, entity_type, action, obj, old=None, new=None):
    AuditLog.objects.create(
        entity_type=entity_type,
        entity_id=obj.pk,
        action=action,
        old_values=old,
        new_values=new,
        user=request.user,
        ip_address=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
    )


FORMULA_PREFIXES = ("=", "+", "-", "@")


def _xlsx_safe_cell(ws, value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        cell = WriteOnlyCell(ws, value=value)
        cell.data_type = "s"
        return cell
    return value


def build_shipment_xlsx(qs, filename, sheet_title, headers, row_getter):
    def _generate():
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title=sheet_title)
        bold = Font(bold=True)
        header_cells = [WriteOnlyCell(ws, value=h) for h in headers]
        for c in header_cells:
            c.font = bold
        ws.append(header_cells)
        for s in qs.iterator():
            ws.append([_xlsx_safe_cell(ws, value) for value in row_getter(s)])
        # Псевдо-стриминг: write_only+iterator (V10-MED-8) держат низкий пик при
        # формировании строк, но openpyxl упаковывает xlsx-zip только в save(),
        # поэтому весь файл буферизуется в BytesIO перед единственным yield.
        # Это осознанно: размер ограничен FULL_EXPORT_MAX_ROWS (см. EntityExportMixin),
        # так что буфер не может разрастись неконтролируемо (V12-21).
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        yield buf.read()

    response = StreamingHttpResponse(
        _generate(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class ShipmentListMixin:
    """
    Ожидаемые атрибуты класса:
    entity_name, url_namespace, date_field, doc_type, search_fields,
    perm_add, perm_change, perm_delete, perm_export
    """
    context_object_name = "shipments"
    paginate_by = 25
    url_namespace = None

    def get_queryset(self):
        filter_config = get_filter_config(self.entity_name)
        sort_fields = get_sort_fields(self.entity_name)
        qs = self.model.objects.select_related("created_by").annotate(
            has_docs=Exists(
                ShipmentDocument.objects.filter(
                    shipment_type=self.doc_type,
                    shipment_id=OuterRef("pk"),
                    is_deleted=False,
                )
            )
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            q_filter = Q()
            for field in self.search_fields:
                q_filter |= Q(**{f"{field}__icontains": q})
            qs = qs.filter(q_filter)
        date_from = parse_top_level_date_bound(self.request.GET.get("date_from"))
        date_to = parse_top_level_date_bound(self.request.GET.get("date_to"))
        if date_from:
            qs = qs.filter(**{f"{self.date_field}__gte": date_from})
        if date_to:
            qs = qs.filter(**{f"{self.date_field}__lte": date_to})
        qs = apply_column_filters(qs, self.request.GET, filter_config, self.model)
        sort = self.request.GET.get("sort", "")
        direction = self.request.GET.get("dir", "desc")
        if sort in sort_fields:
            qs = qs.order_by(f"-{sort}" if direction == "desc" else sort, "-id")
        else:
            qs = qs.order_by(f"-{self.date_field}", "-id")
        return qs

    def get_context_data(self, **kwargs):
        from core.models import FieldSettings
        filter_config = get_filter_config(self.entity_name)
        sort_fields = get_sort_fields(self.entity_name)
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        ctx["sort"] = self.request.GET.get("sort", "")
        ctx["dir"] = self.request.GET.get("dir", "desc")
        ctx["can_add"] = self.request.user.has_perm(self.perm_add)
        ctx["can_change"] = self.request.user.has_perm(self.perm_change)
        ctx["can_delete"] = self.request.user.has_perm(self.perm_delete)
        ctx["can_export"] = self.request.user.has_perm(self.perm_export)
        ctx["sortable_fields"] = sort_fields
        ctx["filter_config"] = filter_config
        value_filter_fields = {f for f, t in filter_config.items() if t == "value"}
        active_context = active_column_filters(self.request.GET, filter_config)
        ctx["filter_urls"] = {
            field: reverse(f"{self.url_namespace}:filter_values", kwargs={"field": field})
            for field in value_filter_fields
        }
        ctx["filter_query_safe_limit"] = settings.FILTER_QUERY_SAFE_LIMIT
        ctx.update(active_context)
        ctx["list_columns"] = list(
            FieldSettings.objects.filter(
                entity=self.entity_name,
                show_in_list=True,
                visible=True,
            ).order_by("sort_order", "field_name").values("field_name", "label")
        )
        ctx["table_presets"] = get_table_presets(self.entity_name)
        ctx["table_sticky_fields"] = ",".join(get_sticky_fields(self.entity_name))
        from core.field_config import get_entity_config
        entity_cfg = get_entity_config(self.entity_name)
        ctx["field_label_map"] = {field: attrs["label"] for field, attrs in entity_cfg.items()}
        return ctx


class ShipmentDetailMixin:
    """
    Ожидаемые атрибуты класса:
    entity_name, doc_type, audit_entity,
    perm_change, perm_delete, perm_upload, perm_change_doc, perm_delete_doc
    """
    context_object_name = "shipment"

    def get_context_data(self, **kwargs):
        from core.field_config import get_entity_config
        ctx = super().get_context_data(**kwargs)
        ctx["can_change"] = self.request.user.has_perm(self.perm_change)
        ctx["can_delete"] = self.request.user.has_perm(self.perm_delete)
        ctx["can_upload"] = self.request.user.has_perm(self.perm_upload)
        ctx["can_change_doc"] = self.request.user.has_perm(self.perm_change_doc)
        ctx["can_delete_doc"] = self.request.user.has_perm(self.perm_delete_doc)
        ctx["can_audit"] = self.request.user.has_perm("audit.view_auditlog")
        ctx["can_view_doc"] = self.request.user.has_perm("documents.view_shipmentdocument")
        if ctx["can_view_doc"]:
            ctx["documents"] = ShipmentDocument.objects.filter(
                shipment_type=self.doc_type,
                shipment_id=self.object.pk,
                is_deleted=False,
            ).order_by("-uploaded_at")
        else:
            ctx["documents"] = ShipmentDocument.objects.none()
        ctx["field_config"] = get_entity_config(self.entity_name)
        if ctx["can_audit"]:
            ctx["audit_log"] = AuditLog.objects.filter(
                entity_type=self.audit_entity,
                entity_id=self.object.pk,
            ).order_by("-created_at")[:20]
        return ctx


class ShipmentCreateMixin:
    """Ожидаемые атрибуты класса: namespace, audit_entity, create_title"""

    def get_success_url(self):
        return reverse_lazy(f"{self.namespace}:detail", kwargs={"pk": self.object.pk})

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        log_shipment(self.request, self.audit_entity, AuditLog.ACTION_CREATE, self.object,
                     new=shipment_to_dict(self.object))
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.create_title
        main, advanced = ctx["form"].__class__._build_field_lists()
        ctx["main_fields"] = [ctx["form"][f] for f in main]
        ctx["advanced_fields"] = [ctx["form"][f] for f in advanced]
        return ctx


class ShipmentUpdateMixin:
    """Ожидаемые атрибуты класса: namespace, audit_entity, update_title"""

    def get_success_url(self):
        return reverse_lazy(f"{self.namespace}:detail", kwargs={"pk": self.object.pk})

    def _conflict_response(self, form, current_obj):
        self.object = current_obj
        user = current_obj.updated_by
        who = (user.get_full_name() or user.username) if user else "другим пользователем"
        when = timezone.localtime(current_obj.updated_at).strftime("%d.%m.%Y %H:%M")
        form.add_error(
            None,
            f"Запись изменена пользователем {who} в {when}. "
            "Ваши изменения не сохранены — проверьте данные и нажмите «Сохранить» снова.",
        )
        return self.form_invalid(form)

    def _form_update_values(self, form, updated_at):
        values = {}
        for name in form.fields:
            if name not in form.cleaned_data:
                continue
            try:
                field = self.model._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            if field.many_to_many:
                continue
            values[name] = form.cleaned_data[name]
        values["updated_by"] = self.request.user
        values["updated_at"] = updated_at
        return values

    def form_valid(self, form):
        current_obj = self.get_object()
        token_str = self.request.POST.get("updated_at_token", "")
        if token_str != current_obj.updated_at.isoformat():
            return self._conflict_response(form, current_obj)
        old = shipment_to_dict(current_obj)
        updated_at = timezone.now()
        values = self._form_update_values(form, updated_at)
        with transaction.atomic():
            updated = self.model.objects.filter(
                pk=current_obj.pk,
                updated_at=current_obj.updated_at,
            ).update(**values)
            if updated == 0:
                fresh_obj = self.model.objects.get(pk=current_obj.pk)
                return self._conflict_response(form, fresh_obj)
            self.object = self.model.objects.get(pk=current_obj.pk)
            log_shipment(
                self.request,
                self.audit_entity,
                AuditLog.ACTION_UPDATE,
                self.object,
                old=old,
                new=shipment_to_dict(self.object),
            )
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.update_title
        main, advanced = ctx["form"].__class__._build_field_lists()
        ctx["main_fields"] = [ctx["form"][f] for f in main]
        ctx["advanced_fields"] = [ctx["form"][f] for f in advanced]
        return ctx


class ShipmentDeleteMixin:
    """
    Ожидаемые атрибуты класса: audit_entity, doc_type (optional)
    success_url определяется в конкретном классе через reverse_lazy
    """
    context_object_name = "shipment"
    doc_type = None

    def _safe_next_url(self):
        next_url = self.request.GET.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        return ""

    def get_success_url(self):
        return self._safe_next_url() or str(self.success_url)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["return_url"] = self._safe_next_url()
        if self.doc_type is not None:
            ctx["document_count"] = ShipmentDocument.objects.filter(
                shipment_type=self.doc_type,
                shipment_id=self.object.pk,
                is_deleted=False,
            ).count()
        return ctx

    def form_valid(self, form):
        obj = self.get_object()
        old = shipment_to_dict(obj)
        with transaction.atomic():
            obj.delete()
            log_shipment(self.request, self.audit_entity, AuditLog.ACTION_DELETE, obj, old=old)
        return HttpResponseRedirect(self.get_success_url())


class ShipmentExportBaseMixin:
    list_url_name = None

    def _rejection_redirect_url(self, request):
        url = reverse(self.list_url_name)
        query_string = request.GET.urlencode()
        if query_string:
            return f"{url}?{query_string}"
        return url


class ShipmentExportSelectedMixin(ShipmentExportBaseMixin):
    """Ожидаемые атрибуты класса: model, selected_order_by, selected_filename, xlsx_config, list_url_name"""

    def post(self, request):
        try:
            qs = selected_export_queryset(request, self.model, self.selected_order_by)
        except SelectedExportError as exc:
            messages.error(request, str(exc))
            return HttpResponseRedirect(self._rejection_redirect_url(request))
        return build_shipment_xlsx(qs, self.selected_filename, **self.xlsx_config)


class ShipmentExportMixin(ShipmentExportBaseMixin):
    """Ожидаемые атрибуты класса: model, entity_name, date_field, search_fields, export_filename, xlsx_config, list_url_name"""

    def get(self, request):
        filter_config = get_filter_config(self.entity_name)
        qs = self.model.objects.all()
        q = request.GET.get("q", "").strip()
        if q:
            q_filter = Q()
            for field in self.search_fields:
                q_filter |= Q(**{f"{field}__icontains": q})
            qs = qs.filter(q_filter)
        date_from = parse_top_level_date_bound(request.GET.get("date_from"))
        date_to = parse_top_level_date_bound(request.GET.get("date_to"))
        if date_from:
            qs = qs.filter(**{f"{self.date_field}__gte": date_from})
        if date_to:
            qs = qs.filter(**{f"{self.date_field}__lte": date_to})
        qs = apply_column_filters(qs, request.GET, filter_config, self.model)
        max_rows = settings.FULL_EXPORT_MAX_ROWS
        count = qs.count()
        if count > max_rows:
            logger.warning(
                "Full export rejected: user=%s entity=%s rows=%d limit=%d",
                request.user, self.entity_name, count, max_rows,
            )
            messages.error(
                request,
                f"Слишком много записей для экспорта: {count}. Максимум — {max_rows}. Уточните фильтры.",
            )
            return HttpResponseRedirect(self._rejection_redirect_url(request))
        if count > settings.PARTIAL_EXPORT_MAX_IDS:
            logger.info(
                "Large export: user=%s entity=%s rows=%d",
                request.user, self.entity_name, count,
            )
        return build_shipment_xlsx(qs, self.export_filename, **self.xlsx_config)


class ShipmentDeletedListMixin:
    """Ожидаемые атрибуты класса: model, delete_perm"""
    context_object_name = "shipments"
    paginate_by = 50

    def test_func(self):
        return self.request.user.has_perm(self.delete_perm)

    def get_queryset(self):
        return self.model.all_objects.filter(is_deleted=True).order_by("-updated_at")


class ShipmentRestoreViewMixin:
    """Ожидаемые атрибуты класса: model, delete_perm, namespace, audit_entity, restore_msg_prefix"""

    def test_func(self):
        return self.request.user.has_perm(self.delete_perm)

    def post(self, request, pk):
        try:
            obj = self.model.all_objects.get(pk=pk, is_deleted=True)
        except self.model.DoesNotExist:
            messages.error(request, "Запись не найдена или не удалена.")
            return redirect(f"{self.namespace}:deleted")
        obj.is_deleted = False
        obj.updated_by = request.user
        with transaction.atomic():
            obj.save(update_fields=["is_deleted", "updated_at", "updated_by"])
            log_shipment(request, self.audit_entity, AuditLog.ACTION_RESTORE, obj, new=shipment_to_dict(obj))
        messages.success(request, f"{self.restore_msg_prefix} #{pk} восстановлена.")
        return redirect(f"{self.namespace}:deleted")
