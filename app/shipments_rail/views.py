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
from .models import RailShipment
from .forms import RailShipmentForm

_SEARCH_FIELDS = ["wagon_number", "document_number", "cargo", "destination_station", "receiver", "sender"]

_XLSX_CONFIG = {
    "sheet_title": "ЖД-отгрузки",
    "headers": [
        "Дата отправки", "Номер вагона", "Номер документа", "Марка угля",
        "Регион отправки", "Станция отправки", "Отправитель",
        "Регион назначения", "Станция назначения", "Получатель",
        "Объём (т)", "Комментарий",
    ],
    "row_getter": lambda s: [
        s.departure_date, s.wagon_number, s.document_number, s.cargo,
        s.origin_region, s.origin_station, s.sender,
        s.destination_region, s.destination_station, s.receiver,
        s.volume, s.comment,
    ],
}


class RailShipmentListView(ShipmentListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = RailShipment
    template_name = "shipments_rail/list.html"
    permission_required = "shipments_rail.view_railshipment"
    entity_name = "rail_shipment"
    url_namespace = "rail"
    date_field = "departure_date"
    doc_type = ShipmentDocument.SHIPMENT_TYPE_RAIL
    search_fields = _SEARCH_FIELDS
    perm_add = "shipments_rail.add_railshipment"
    perm_change = "shipments_rail.change_railshipment"
    perm_delete = "shipments_rail.delete_railshipment"
    perm_export = "shipments_rail.export_excel"


class RailShipmentDetailView(ShipmentDetailMixin, LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = RailShipment
    template_name = "shipments_rail/detail.html"
    permission_required = "shipments_rail.view_railshipment"
    entity_name = "rail_shipment"
    doc_type = ShipmentDocument.SHIPMENT_TYPE_RAIL
    audit_entity = AuditLog.ENTITY_RAIL
    perm_change = "shipments_rail.change_railshipment"
    perm_delete = "shipments_rail.delete_railshipment"
    perm_upload = "documents.upload_railshipment_documents"
    perm_change_doc = "documents.change_railshipment_documents"
    perm_delete_doc = "documents.delete_railshipment_documents"


class RailShipmentCreateView(ShipmentCreateMixin, LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = RailShipment
    form_class = RailShipmentForm
    template_name = "shipments_rail/form.html"
    permission_required = "shipments_rail.add_railshipment"
    namespace = "rail"
    audit_entity = AuditLog.ENTITY_RAIL
    create_title = "Новая ЖД-отгрузка"


class RailShipmentUpdateView(ShipmentUpdateMixin, LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = RailShipment
    form_class = RailShipmentForm
    template_name = "shipments_rail/form.html"
    permission_required = "shipments_rail.change_railshipment"
    namespace = "rail"
    audit_entity = AuditLog.ENTITY_RAIL
    update_title = "Редактировать ЖД-отгрузку"


class RailShipmentDeleteView(ShipmentDeleteMixin, LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = RailShipment
    template_name = "shipments_rail/confirm_delete.html"
    success_url = reverse_lazy("rail:list")
    permission_required = "shipments_rail.delete_railshipment"
    audit_entity = AuditLog.ENTITY_RAIL
    doc_type = ShipmentDocument.SHIPMENT_TYPE_RAIL


class RailShipmentExportSelectedView(ShipmentExportSelectedMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    model = RailShipment
    permission_required = "shipments_rail.export_excel"
    selected_order_by = ("-departure_date", "-id")
    selected_filename = "rail_shipments_selected.xlsx"
    list_url_name = "rail:list"
    xlsx_config = _XLSX_CONFIG


class RailShipmentExportView(ShipmentExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    model = RailShipment
    permission_required = "shipments_rail.export_excel"
    entity_name = "rail_shipment"
    date_field = "departure_date"
    search_fields = _SEARCH_FIELDS
    export_filename = "rail_shipments.xlsx"
    list_url_name = "rail:list"
    xlsx_config = _XLSX_CONFIG


class RailShipmentDeletedListView(ShipmentDeletedListMixin, LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = RailShipment
    template_name = "shipments_rail/deleted.html"
    delete_perm = "shipments_rail.delete_railshipment"


class RailShipmentRestoreView(ShipmentRestoreViewMixin, LoginRequiredMixin, UserPassesTestMixin, View):
    model = RailShipment
    delete_perm = "shipments_rail.delete_railshipment"
    namespace = "rail"
    audit_entity = AuditLog.ENTITY_RAIL
    restore_msg_prefix = "ЖД-отгрузка"


class RailFilterValuesView(FilterValuesView):
    entity_name = "rail_shipment"
    model = RailShipment
    permission_required = "shipments_rail.view_railshipment"
