import uuid
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from audit.models import AuditLog
from core.ip_utils import get_client_ip
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment
from .forms import EditDocumentForm, UploadDocumentForm
from .models import ShipmentDocument


def _get_shipment(shipment_type, pk):
    if shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO:
        return get_object_or_404(AutoShipment, pk=pk, is_deleted=False)
    if shipment_type == ShipmentDocument.SHIPMENT_TYPE_RAIL:
        return get_object_or_404(RailShipment, pk=pk, is_deleted=False)
    raise Http404


def _detail_url(shipment_type, pk):
    if shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO:
        return reverse("auto:detail", kwargs={"pk": pk})
    return reverse("rail:detail", kwargs={"pk": pk})


class DocumentUploadView(LoginRequiredMixin, View):
    template_name = "documents/upload.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.shipment_type = kwargs["shipment_type"]
        self.shipment_pk = kwargs["pk"]

    def _required_perm(self):
        if self.shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO:
            return "documents.upload_autoshipment_documents"
        return "documents.upload_railshipment_documents"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.shipment_type not in (ShipmentDocument.SHIPMENT_TYPE_AUTO, ShipmentDocument.SHIPMENT_TYPE_RAIL):
            raise Http404
        if not request.user.has_perm(self._required_perm()):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_shipment(self):
        return _get_shipment(self.shipment_type, self.shipment_pk)

    def get(self, request, *args, **kwargs):
        form = UploadDocumentForm()
        return render(request, self.template_name, self._ctx(form))

    def post(self, request, *args, **kwargs):
        form = UploadDocumentForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, self._ctx(form))

        self.get_shipment()
        uploaded = form.cleaned_data["file"]
        doc_type = form.cleaned_data["document_type"]

        ext = uploaded.name.rsplit(".", 1)[-1].lower()
        stored_name = f"{uuid.uuid4().hex}.{ext}"

        today = date.today()
        rel_dir = Path(self.shipment_type) / str(today.year) / f"{today.month:02d}" / f"shipment_{self.shipment_pk}"
        abs_dir = Path(settings.MEDIA_ROOT) / rel_dir
        dest = abs_dir / stored_name
        try:
            abs_dir.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as fh:
                for chunk in uploaded.chunks():
                    fh.write(chunk)
        except OSError:
            dest.unlink(missing_ok=True)
            form.add_error(None, "Не удалось сохранить файл. Проверьте доступность хранилища.")
            return render(request, self.template_name, self._ctx(form))

        try:
            with transaction.atomic():
                mime = getattr(uploaded, "detected_mime_type", "")
                doc = ShipmentDocument.objects.create(
                    shipment_type=self.shipment_type,
                    shipment_id=self.shipment_pk,
                    document_type=doc_type,
                    original_file_name=uploaded.name,
                    stored_file_name=stored_name,
                    file_path=str(rel_dir / stored_name),
                    mime_type=mime or "",
                    file_size=uploaded.size,
                    uploaded_by=request.user,
                )
                AuditLog.objects.create(
                    entity_type=AuditLog.ENTITY_AUTO if self.shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO else AuditLog.ENTITY_RAIL,
                    entity_id=self.shipment_pk,
                    action=AuditLog.ACTION_UPLOAD,
                    new_values={"document_id": doc.pk, "file_name": uploaded.name, "document_type": doc_type},
                    user=request.user,
                    ip_address=get_client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
                )
        except Exception:
            dest.unlink(missing_ok=True)
            raise

        return redirect(_detail_url(self.shipment_type, self.shipment_pk))

    def _ctx(self, form):
        return {
            "form": form,
            "shipment": self.get_shipment(),
            "shipment_type": self.shipment_type,
            "back_url": _detail_url(self.shipment_type, self.shipment_pk),
        }


class DocumentEditView(LoginRequiredMixin, View):
    template_name = "documents/edit.html"

    def _required_perm(self, shipment_type):
        if shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO:
            return "documents.change_autoshipment_documents"
        return "documents.change_railshipment_documents"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        doc = get_object_or_404(ShipmentDocument, pk=pk, is_deleted=False)
        _get_shipment(doc.shipment_type, doc.shipment_id)  # raises 404 if parent deleted
        if not request.user.has_perm(self._required_perm(doc.shipment_type)):
            raise PermissionDenied
        form = EditDocumentForm(initial={"document_type": doc.document_type})
        return render(request, self.template_name, {
            "form": form,
            "doc": doc,
            "back_url": _detail_url(doc.shipment_type, doc.shipment_id),
        })

    def post(self, request, pk):
        doc = get_object_or_404(ShipmentDocument, pk=pk, is_deleted=False)
        _get_shipment(doc.shipment_type, doc.shipment_id)  # raises 404 if parent deleted
        if not request.user.has_perm(self._required_perm(doc.shipment_type)):
            raise PermissionDenied
        form = EditDocumentForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {
                "form": form,
                "doc": doc,
                "back_url": _detail_url(doc.shipment_type, doc.shipment_id),
            })

        old_type = doc.document_type
        doc.document_type = form.cleaned_data["document_type"]

        uploaded = form.cleaned_data.get("file")
        old_file_abs = None
        if uploaded:
            old_file_abs = Path(settings.MEDIA_ROOT) / doc.file_path
            ext = uploaded.name.rsplit(".", 1)[-1].lower()
            stored_name = f"{uuid.uuid4().hex}.{ext}"
            today = date.today()
            rel_dir = Path(doc.shipment_type) / str(today.year) / f"{today.month:02d}" / f"shipment_{doc.shipment_id}"
            abs_dir = Path(settings.MEDIA_ROOT) / rel_dir
            dest = abs_dir / stored_name
            try:
                abs_dir.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as fh:
                    for chunk in uploaded.chunks():
                        fh.write(chunk)
            except OSError:
                dest.unlink(missing_ok=True)
                form.add_error(None, "Не удалось сохранить файл. Проверьте доступность хранилища.")
                return render(request, self.template_name, {
                    "form": form,
                    "doc": doc,
                    "back_url": _detail_url(doc.shipment_type, doc.shipment_id),
                })
            doc.original_file_name = uploaded.name
            doc.stored_file_name = stored_name
            doc.file_path = str(rel_dir / stored_name)
            doc.mime_type = getattr(uploaded, "detected_mime_type", "") or ""
            doc.file_size = uploaded.size

        try:
            with transaction.atomic():
                if uploaded:
                    doc.save()
                else:
                    doc.save(update_fields=["document_type"])
                AuditLog.objects.create(
                    entity_type=AuditLog.ENTITY_AUTO if doc.shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO else AuditLog.ENTITY_RAIL,
                    entity_id=doc.shipment_id,
                    action=AuditLog.ACTION_EDIT_DOCUMENT,
                    old_values={"document_type": old_type},
                    new_values={"document_id": doc.pk, "document_type": doc.document_type, "file_replaced": bool(uploaded)},
                    user=request.user,
                    ip_address=get_client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
                )
                if old_file_abs:
                    transaction.on_commit(lambda p=old_file_abs: p.unlink(missing_ok=True))
        except Exception:
            if uploaded:
                dest.unlink(missing_ok=True)
            raise

        return redirect(_detail_url(doc.shipment_type, doc.shipment_id))


class DocumentDeleteView(LoginRequiredMixin, View):
    template_name = "documents/confirm_delete.html"

    def _required_perm(self, shipment_type):
        if shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO:
            return "documents.delete_autoshipment_documents"
        return "documents.delete_railshipment_documents"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        doc = get_object_or_404(ShipmentDocument, pk=pk, is_deleted=False)
        _get_shipment(doc.shipment_type, doc.shipment_id)  # raises 404 if parent deleted
        if not request.user.has_perm(self._required_perm(doc.shipment_type)):
            raise PermissionDenied
        return render(request, self.template_name, {
            "doc": doc,
            "back_url": _detail_url(doc.shipment_type, doc.shipment_id),
        })

    def post(self, request, pk):
        doc = get_object_or_404(ShipmentDocument, pk=pk, is_deleted=False)
        _get_shipment(doc.shipment_type, doc.shipment_id)  # raises 404 if parent deleted
        if not request.user.has_perm(self._required_perm(doc.shipment_type)):
            raise PermissionDenied
        back_url = _detail_url(doc.shipment_type, doc.shipment_id)
        with transaction.atomic():
            doc.is_deleted = True
            doc.deleted_at = timezone.now()
            doc.save(update_fields=["is_deleted", "deleted_at"])
            AuditLog.objects.create(
                entity_type=AuditLog.ENTITY_AUTO if doc.shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO else AuditLog.ENTITY_RAIL,
                entity_id=doc.shipment_id,
                action=AuditLog.ACTION_DELETE_DOCUMENT,
                old_values={"document_id": doc.pk, "file_name": doc.original_file_name, "document_type": doc.document_type},
                user=request.user,
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            )
        return redirect(back_url)


# Только MIME, которые реально проходят загрузку (см. ALLOWED_MIME_TYPES в forms.py)
# и распознаются _detect_mime по магии. webp/gif не поддержаны на загрузке,
# поэтому в превью не добавляются (V12-23). Инвариант проверяется тестом.
PREVIEW_SAFE_MIMES = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/png",
})


class DocumentServeView(LoginRequiredMixin, View):
    def get(self, request, pk, mode=None):
        doc = get_object_or_404(ShipmentDocument, pk=pk, is_deleted=False)
        _get_shipment(doc.shipment_type, doc.shipment_id)  # raises 404 if parent deleted
        if not request.user.has_perm("documents.view_shipmentdocument"):
            raise PermissionDenied
        if doc.shipment_type == ShipmentDocument.SHIPMENT_TYPE_AUTO:
            if not request.user.has_perm("shipments_auto.view_autoshipment"):
                raise PermissionDenied
        else:
            if not request.user.has_perm("shipments_rail.view_railshipment"):
                raise PermissionDenied
        abs_path = Path(settings.MEDIA_ROOT) / doc.file_path
        media_root = Path(settings.MEDIA_ROOT).resolve()
        try:
            abs_path.resolve().relative_to(media_root)
        except ValueError:
            raise Http404
        if not abs_path.exists():
            raise Http404

        mime = doc.mime_type or "application/octet-stream"
        if mode == "view" and mime in PREVIEW_SAFE_MIMES:
            as_attachment = False
        else:
            as_attachment = True

        response = FileResponse(
            open(abs_path, "rb"),
            as_attachment=as_attachment,
            filename=doc.original_file_name,
            content_type=mime,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
