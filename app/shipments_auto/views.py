from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from audit.models import AuditLog
from documents.models import ShipmentDocument
from core.shipment_views import (
    ShipmentListMixin, ShipmentDetailMixin, ShipmentCreateMixin, ShipmentUpdateMixin,
    ShipmentDeleteMixin, ShipmentExportSelectedMixin, ShipmentExportMixin,
    ShipmentDeletedListMixin, ShipmentRestoreViewMixin, FilterValuesView,
)
from .models import AutoShipment
from .forms import AutoShipmentForm

_SEARCH_FIELDS = ["customer_object", "vehicle_number", "driver_name", "ttn_number", "coal_grade", "carrier"]

_XLSX_CONFIG = {
    "sheet_title": "Автоотгрузки",
    "headers": [
        "Дата", "Месяц (текст)", "№ дня", "Объект", "Подобъект",
        "Номер машины", "Водитель", "ТТН", "Марка угля", "Кол-во (т)",
        "Код базиса", "№ УПД", "Перевозчик", "Остаток (заметка)", "Комментарий",
    ],
    "row_getter": lambda s: [
        s.shipment_date, s.source_month_text, s.source_day_number,
        s.customer_object, s.sub_object, s.vehicle_number, s.driver_name,
        s.ttn_number, s.coal_grade, s.quantity, s.base_code,
        s.upd_number, s.carrier, s.balance_note, s.comment,
    ],
}


class AutoShipmentListView(ShipmentListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = AutoShipment
    template_name = "shipments_auto/list.html"
    permission_required = "shipments_auto.view_autoshipment"
    entity_name = "auto_shipment"
    url_namespace = "auto"
    date_field = "shipment_date"
    doc_type = ShipmentDocument.SHIPMENT_TYPE_AUTO
    search_fields = _SEARCH_FIELDS
    perm_add = "shipments_auto.add_autoshipment"
    perm_change = "shipments_auto.change_autoshipment"
    perm_delete = "shipments_auto.delete_autoshipment"
    perm_export = "shipments_auto.export_excel"


class AutoShipmentDetailView(ShipmentDetailMixin, LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = AutoShipment
    template_name = "shipments_auto/detail.html"
    permission_required = "shipments_auto.view_autoshipment"
    entity_name = "auto_shipment"
    doc_type = ShipmentDocument.SHIPMENT_TYPE_AUTO
    audit_entity = AuditLog.ENTITY_AUTO
    perm_change = "shipments_auto.change_autoshipment"
    perm_delete = "shipments_auto.delete_autoshipment"
    perm_upload = "documents.upload_autoshipment_documents"
    perm_change_doc = "documents.change_autoshipment_documents"
    perm_delete_doc = "documents.delete_autoshipment_documents"


class AutoShipmentCreateView(ShipmentCreateMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = AutoShipment
    form_class = AutoShipmentForm
    template_name = "shipments_auto/form.html"
    permission_required = "shipments_auto.add_autoshipment"
    namespace = "auto"
    audit_entity = AuditLog.ENTITY_AUTO
    create_title = "Новая автоотгрузка"


class AutoShipmentUpdateView(ShipmentUpdateMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = AutoShipment
    form_class = AutoShipmentForm
    template_name = "shipments_auto/form.html"
    permission_required = "shipments_auto.change_autoshipment"
    namespace = "auto"
    audit_entity = AuditLog.ENTITY_AUTO
    update_title = "Редактировать автоотгрузку"


class AutoShipmentDeleteView(ShipmentDeleteMixin, LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = AutoShipment
    template_name = "shipments_auto/confirm_delete.html"
    success_url = reverse_lazy("auto:list")
    permission_required = "shipments_auto.delete_autoshipment"
    audit_entity = AuditLog.ENTITY_AUTO
    doc_type = ShipmentDocument.SHIPMENT_TYPE_AUTO


class AutoShipmentExportSelectedView(ShipmentExportSelectedMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    model = AutoShipment
    permission_required = "shipments_auto.export_excel"
    selected_order_by = ("-shipment_date", "-id")
    selected_filename = "auto_shipments_selected.xlsx"
    list_url_name = "auto:list"
    xlsx_config = _XLSX_CONFIG


class AutoShipmentExportView(ShipmentExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    model = AutoShipment
    permission_required = "shipments_auto.export_excel"
    entity_name = "auto_shipment"
    date_field = "shipment_date"
    search_fields = _SEARCH_FIELDS
    export_filename = "auto_shipments.xlsx"
    list_url_name = "auto:list"
    xlsx_config = _XLSX_CONFIG


class AutoShipmentDeletedListView(ShipmentDeletedListMixin, LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = AutoShipment
    template_name = "shipments_auto/deleted.html"
    delete_perm = "shipments_auto.delete_autoshipment"


class AutoShipmentRestoreView(ShipmentRestoreViewMixin, LoginRequiredMixin, UserPassesTestMixin, View):
    model = AutoShipment
    delete_perm = "shipments_auto.delete_autoshipment"
    namespace = "auto"
    audit_entity = AuditLog.ENTITY_AUTO
    restore_msg_prefix = "Автоотгрузка"


class AutoFilterValuesView(FilterValuesView):
    entity_name = "auto_shipment"
    model = AutoShipment
    permission_required = "shipments_auto.view_autoshipment"
