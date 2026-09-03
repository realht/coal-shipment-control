import io
from unittest import mock
import pytest
from pathlib import Path
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import ShipmentDocument
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def viewer_user(django_user_model):
    user = django_user_model.objects.create_user(username="viewer_docs", password="pass")
    group, _ = Group.objects.get_or_create(name="viewer")
    perm = Permission.objects.get(codename="view_shipmentdocument", content_type__app_label="documents")
    group.permissions.add(perm)
    for codename in ("view_autoshipment", "view_railshipment"):
        app = codename.split("_")[1] + "s_" + codename.split("_")[0]
        # проще получить напрямую
        pass
    for codename, app_label in [
        ("view_autoshipment", "shipments_auto"),
        ("view_railshipment", "shipments_rail"),
    ]:
        p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        group.permissions.add(p)
    user.groups.add(group)
    return user


@pytest.fixture
def docs_user(django_user_model):
    user = django_user_model.objects.create_user(username="docs_user", password="pass")
    group, _ = Group.objects.get_or_create(name="docs_group")
    for codename, app_label in [
        ("view_autoshipment", "shipments_auto"),
        ("view_railshipment", "shipments_rail"),
        ("view_shipmentdocument", "documents"),
        ("upload_autoshipment_documents", "documents"),
        ("upload_railshipment_documents", "documents"),
    ]:
        p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        group.permissions.add(p)
    user.groups.add(group)
    return user


@pytest.fixture
def auto_shipment(docs_user):
    return AutoShipment.objects.create(
        shipment_date="2026-02-01",
        customer_object="Объект для документов",
        coal_grade="ДГ",
        quantity="100",
        created_by=docs_user,
        updated_by=docs_user,
    )


@pytest.fixture
def rail_shipment(docs_user):
    return RailShipment.objects.create(
        departure_date="2026-02-01",
        wagon_number="11111111",
        cargo="Уголь",
        receiver="Получатель",
        volume="500",
        created_by=docs_user,
        updated_by=docs_user,
    )


def _pdf_file(name="test.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test content", content_type="application/pdf")


@pytest.mark.django_db
class TestDocumentUploadPermissions:
    def test_anonymous_redirects(self, client, auto_shipment):
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_cannot_upload(self, client, viewer_user, auto_shipment):
        client.login(username="viewer_docs", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_docs_user_can_get_form(self, client, docs_user, auto_shipment):
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        response = client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestDocumentUploadAuto:
    def test_upload_valid_pdf(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
            "file": _pdf_file("накладная.pdf"),
        })
        assert response.status_code == 302
        doc = ShipmentDocument.objects.get(
            shipment_type="auto", shipment_id=auto_shipment.pk
        )
        assert doc.original_file_name == "накладная.pdf"
        assert doc.file_size > 0

    def test_upload_creates_audit_log(self, client, docs_user, auto_shipment, tmp_path, settings):
        from audit.models import AuditLog
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
            "file": _pdf_file("doc.pdf"),
        })
        assert AuditLog.objects.filter(
            entity_type=AuditLog.ENTITY_AUTO,
            entity_id=auto_shipment.pk,
            action=AuditLog.ACTION_UPLOAD,
        ).exists()

    def test_upload_file_saved_on_disk(self, client, docs_user, auto_shipment, tmp_path, settings):
        from pathlib import Path
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_SCAN,
            "file": _pdf_file("скан.pdf"),
        })
        doc = ShipmentDocument.objects.get(shipment_type="auto", shipment_id=auto_shipment.pk)
        assert (Path(tmp_path) / doc.file_path).exists()

    def test_upload_invalid_extension_rejected(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        bad_file = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
            "file": bad_file,
        })
        assert response.status_code == 200
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=auto_shipment.pk).exists()

    def test_upload_oversized_file_rejected(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        big = SimpleUploadedFile("big.pdf", b"x" * (25 * 1024 * 1024 + 1), content_type="application/pdf")
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
            "file": big,
        })
        assert response.status_code == 200
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=auto_shipment.pk).exists()

    def test_upload_uses_configured_max_upload_size(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        settings.MAX_UPLOAD_SIZE_MB = 1
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        big = SimpleUploadedFile(
            "big.pdf",
            b"%PDF-1.4" + b"x" * (1024 * 1024 + 1),
            content_type="application/pdf",
        )
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
            "file": big,
        })
        assert response.status_code == 200
        assert "1 МБ" in response.content.decode()
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=auto_shipment.pk).exists()

    def test_upload_redirects_to_detail(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
            "file": _pdf_file(),
        })
        assert response.status_code == 302
        assert f"/auto/{auto_shipment.pk}/" in response["Location"]

    def test_valid_upload_to_missing_shipment_returns_404_without_side_effects(self, client, docs_user, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        missing_pk = 999999
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": missing_pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
            "file": _pdf_file("missing.pdf"),
        })
        assert response.status_code == 404
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=missing_pk).exists()
        assert list(tmp_path.rglob("*")) == []

    def test_valid_upload_to_deleted_shipment_returns_404_without_side_effects(
        self, client, docs_user, auto_shipment, tmp_path, settings
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        auto_shipment.delete()
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
            "file": _pdf_file("deleted.pdf"),
        })
        assert response.status_code == 404
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=auto_shipment.pk).exists()
        assert list(tmp_path.rglob("*")) == []


@pytest.mark.django_db
class TestDocumentUploadRail:
    def test_upload_valid_pdf_rail(self, client, docs_user, rail_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "rail", "pk": rail_shipment.pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_INVOICE,
            "file": _pdf_file("накладная_жд.pdf"),
        })
        assert response.status_code == 302
        doc = ShipmentDocument.objects.get(shipment_type="rail", shipment_id=rail_shipment.pk)
        assert doc.original_file_name == "накладная_жд.pdf"

    def test_upload_creates_audit_log_rail(self, client, docs_user, rail_shipment, tmp_path, settings):
        from audit.models import AuditLog
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "rail", "pk": rail_shipment.pk})
        client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
            "file": _pdf_file("жд_doc.pdf"),
        })
        assert AuditLog.objects.filter(
            entity_type=AuditLog.ENTITY_RAIL,
            entity_id=rail_shipment.pk,
            action=AuditLog.ACTION_UPLOAD,
        ).exists()

    def test_valid_upload_to_missing_shipment_returns_404_without_side_effects(self, client, docs_user, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        missing_pk = 999999
        url = reverse("documents:upload", kwargs={"shipment_type": "rail", "pk": missing_pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_INVOICE,
            "file": _pdf_file("missing_rail.pdf"),
        })
        assert response.status_code == 404
        assert not ShipmentDocument.objects.filter(shipment_type="rail", shipment_id=missing_pk).exists()
        assert list(tmp_path.rglob("*")) == []

    def test_valid_upload_to_deleted_shipment_returns_404_without_side_effects(
        self, client, docs_user, rail_shipment, tmp_path, settings
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        rail_shipment.delete()
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "rail", "pk": rail_shipment.pk})
        response = client.post(url, {
            "document_type": ShipmentDocument.DOCUMENT_TYPE_INVOICE,
            "file": _pdf_file("deleted_rail.pdf"),
        })
        assert response.status_code == 404
        assert not ShipmentDocument.objects.filter(shipment_type="rail", shipment_id=rail_shipment.pk).exists()
        assert list(tmp_path.rglob("*")) == []


@pytest.mark.django_db
class TestDocumentServeAuth:
    def test_serve_existing_document(self, client, docs_user, auto_shipment, tmp_path, settings):
        from pathlib import Path
        settings.MEDIA_ROOT = str(tmp_path)
        rel = "auto/2026/02/shipment_1/test.pdf"
        abs_path = Path(tmp_path) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF test")
        doc = ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="test.pdf",
            stored_file_name="test.pdf",
            file_path=rel,
            file_size=9,
            uploaded_by=docs_user,
        )
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 200

    def test_serve_missing_file_returns_404(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="ghost.pdf",
            stored_file_name="ghost.pdf",
            file_path="auto/2026/02/shipment_1/ghost.pdf",
            file_size=0,
            uploaded_by=docs_user,
        )
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 404


@pytest.mark.django_db
class TestUploadSplitPermissions:
    """P1-02: раздельные права загрузки авто/ЖД; P1-03: неизвестный тип → 404."""

    @pytest.fixture
    def auto_only_user(self, django_user_model):
        user = django_user_model.objects.create_user(username="uploader_auto", password="pass")
        group, _ = Group.objects.get_or_create(name="_test_uploader_auto")
        for codename, app_label in [
            ("view_autoshipment", "shipments_auto"),
            ("view_railshipment", "shipments_rail"),
            ("upload_autoshipment_documents", "documents"),
        ]:
            p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            group.permissions.add(p)
        user.groups.add(group)
        return user

    @pytest.fixture
    def rail_only_user(self, django_user_model):
        user = django_user_model.objects.create_user(username="uploader_rail", password="pass")
        group, _ = Group.objects.get_or_create(name="_test_uploader_rail")
        for codename, app_label in [
            ("view_autoshipment", "shipments_auto"),
            ("view_railshipment", "shipments_rail"),
            ("upload_railshipment_documents", "documents"),
        ]:
            p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            group.permissions.add(p)
        user.groups.add(group)
        return user

    @pytest.fixture
    def auto_shipment_obj(self):
        return AutoShipment.objects.create(
            shipment_date="2026-04-01", customer_object="Объект",
            coal_grade="ДГ", quantity="100",
        )

    @pytest.fixture
    def rail_shipment_obj(self):
        return RailShipment.objects.create(
            departure_date="2026-04-01", wagon_number="99887766",
            cargo="Уголь", receiver="Получатель", volume="500",
        )

    def test_auto_user_can_upload_auto(self, client, auto_only_user, auto_shipment_obj):
        client.login(username="uploader_auto", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment_obj.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_auto_user_cannot_upload_rail(self, client, auto_only_user, rail_shipment_obj):
        client.login(username="uploader_auto", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "rail", "pk": rail_shipment_obj.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_rail_user_can_upload_rail(self, client, rail_only_user, rail_shipment_obj):
        client.login(username="uploader_rail", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "rail", "pk": rail_shipment_obj.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_rail_user_cannot_upload_auto(self, client, rail_only_user, auto_shipment_obj):
        client.login(username="uploader_rail", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment_obj.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_unknown_shipment_type_returns_404(self, client, auto_only_user, auto_shipment_obj):
        client.login(username="uploader_auto", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment_obj.pk})
        url = url.replace("/auto/", "/truck/")
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestDocumentServe:
    def test_serve_anonymous_redirects(self, client, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="x.pdf",
            stored_file_name="x.pdf",
            file_path="auto/2026/02/shipment_1/x.pdf",
            file_size=0,
        )
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]


@pytest.mark.django_db
class TestDocumentInDetail:
    def test_document_shown_in_auto_detail(self, client, docs_user, auto_shipment, tmp_path, settings):
        from pathlib import Path
        settings.MEDIA_ROOT = str(tmp_path)
        rel = "auto/2026/02/shipment_1/shown.pdf"
        Path(tmp_path, rel).parent.mkdir(parents=True, exist_ok=True)
        ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="shown.pdf",
            stored_file_name="shown.pdf",
            file_path=rel,
            file_size=10,
            uploaded_by=docs_user,
        )
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("auto:detail", kwargs={"pk": auto_shipment.pk}))
        assert "shown.pdf" in response.content.decode()

    def test_upload_link_shown_for_docs_user(self, client, docs_user, auto_shipment):
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("auto:detail", kwargs={"pk": auto_shipment.pk}))
        assert "/documents/auto/" in response.content.decode()

    def test_upload_link_hidden_for_viewer(self, client, viewer_user, auto_shipment):
        client.login(username="viewer_docs", password="pass")
        response = client.get(reverse("auto:detail", kwargs={"pk": auto_shipment.pk}))
        assert "Прикрепить документ" not in response.content.decode()


@pytest.mark.django_db
class TestDocumentMimeValidation:
    """B-S05: проверка MIME по содержимому файла."""

    def test_pdf_with_pdf_magic_accepted(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        f = SimpleUploadedFile("real.pdf", b"%PDF-1.4 content", content_type="application/pdf")
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER, "file": f})
        assert response.status_code == 302

    def test_exe_renamed_to_pdf_rejected(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        f = SimpleUploadedFile("virus.pdf", b"MZx\x00fake exe content", content_type="application/pdf")
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER, "file": f})
        assert response.status_code == 200
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=auto_shipment.pk).exists()

    def test_jpeg_magic_accepted(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        f = SimpleUploadedFile("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 100, content_type="image/jpeg")
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_SCAN, "file": f})
        assert response.status_code == 302

    def test_stores_detected_mime_instead_of_filename_guess(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        f = SimpleUploadedFile("renamed.xlsx", b"%PDF-1.4 content", content_type="application/pdf")
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER, "file": f})
        assert response.status_code == 302
        doc = ShipmentDocument.objects.get(shipment_type="auto", shipment_id=auto_shipment.pk)
        assert doc.mime_type == "application/pdf"


def _build_zip(entries):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


class TestDetectOfficeZip:
    """V18-LOW-7: OOXML определяется по [Content_Types].xml + canonical part."""

    def test_xl_prefix_without_content_types_is_plain_zip(self):
        from documents.forms import _detect_office_zip

        buf = _build_zip({"xl/styles.xml": "<styles/>"})
        assert _detect_office_zip(buf) == "application/zip"

    def test_content_types_without_workbook_is_plain_zip(self):
        from documents.forms import _detect_office_zip

        buf = _build_zip({"[Content_Types].xml": "<Types/>", "xl/styles.xml": "<styles/>"})
        assert _detect_office_zip(buf) == "application/zip"

    def test_minimal_xlsx_detected(self):
        from documents.forms import _detect_office_zip

        buf = _build_zip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>"})
        assert _detect_office_zip(buf) == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_minimal_docx_detected(self):
        from documents.forms import _detect_office_zip

        buf = _build_zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "<document/>"})
        assert _detect_office_zip(buf) == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )


@pytest.mark.django_db
class TestOfficeZipUpload:
    """V18-LOW-7: форма отклоняет ZIP, замаскированный под OOXML."""

    def test_fake_xlsx_without_content_types_rejected(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        buf = _build_zip({"xl/styles.xml": "<styles/>"})
        f = SimpleUploadedFile(
            "fake.xlsx",
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER, "file": f})
        assert response.status_code == 200
        assert not ShipmentDocument.objects.filter(shipment_type="auto", shipment_id=auto_shipment.pk).exists()

    def test_real_xlsx_accepted(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        buf = _build_zip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>"})
        f = SimpleUploadedFile(
            "real.xlsx",
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER, "file": f})
        assert response.status_code == 302
        doc = ShipmentDocument.objects.get(shipment_type="auto", shipment_id=auto_shipment.pk)
        assert doc.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.django_db
class TestDocumentEditView:
    @pytest.fixture
    def editor_user(self, django_user_model):
        user = django_user_model.objects.create_user(username="editor_doc", password="pass")
        group, _ = Group.objects.get_or_create(name="_test_editor_doc")
        for codename, app_label in [
            ("view_autoshipment", "shipments_auto"),
            ("view_railshipment", "shipments_rail"),
            ("view_shipmentdocument", "documents"),
            ("change_autoshipment_documents", "documents"),
        ]:
            p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            group.permissions.add(p)
        user.groups.add(group)
        return user

    @pytest.fixture
    def rail_editor_user(self, django_user_model, rail_shipment):
        user = django_user_model.objects.create_user(username="rail_editor_doc", password="pass")
        group, _ = Group.objects.get_or_create(name="_test_rail_editor_doc")
        for codename, app_label in [
            ("view_autoshipment", "shipments_auto"),
            ("view_railshipment", "shipments_rail"),
            ("view_shipmentdocument", "documents"),
            ("change_railshipment_documents", "documents"),
        ]:
            p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            group.permissions.add(p)
        user.groups.add(group)
        return user

    @pytest.fixture
    def rail_doc(self, docs_user, rail_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        rel = f"rail/2026/02/shipment_{rail_shipment.pk}/rail_orig.pdf"
        from pathlib import Path
        abs_path = Path(tmp_path) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF-1.4 rail original")
        return ShipmentDocument.objects.create(
            shipment_type="rail",
            shipment_id=rail_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="rail_orig.pdf",
            stored_file_name="rail_orig.pdf",
            file_path=rel,
            mime_type="application/pdf",
            file_size=22,
            uploaded_by=docs_user,
        )

    @pytest.fixture
    def existing_doc(self, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        rel = "auto/2026/02/shipment_1/orig.pdf"
        from pathlib import Path
        abs_path = Path(tmp_path) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF-1.4 original")
        return ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="orig.pdf",
            stored_file_name="orig.pdf",
            file_path=rel,
            mime_type="application/pdf",
            file_size=17,
            uploaded_by=docs_user,
        )

    def test_anonymous_redirects(self, client, existing_doc):
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_without_change_perm_gets_403(self, client, viewer_user, existing_doc):
        client.login(username="viewer_docs", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_editor_gets_form_with_current_type(self, client, editor_user, existing_doc):
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert ShipmentDocument.DOCUMENT_TYPE_TTN in response.content.decode()

    def test_post_changes_document_type_only(self, client, editor_user, existing_doc, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        old_path = existing_doc.file_path
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_UPD})
        assert response.status_code == 302
        existing_doc.refresh_from_db()
        assert existing_doc.document_type == ShipmentDocument.DOCUMENT_TYPE_UPD
        assert existing_doc.file_path == old_path

    def test_post_with_new_file_replaces_file_fields(self, client, editor_user, existing_doc, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        new_file = SimpleUploadedFile("new.pdf", b"%PDF-1.4 new content", content_type="application/pdf")
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_SCAN, "file": new_file})
        assert response.status_code == 302
        existing_doc.refresh_from_db()
        assert existing_doc.original_file_name == "new.pdf"
        assert existing_doc.document_type == ShipmentDocument.DOCUMENT_TYPE_SCAN

    def test_post_creates_audit_log(self, client, editor_user, existing_doc, tmp_path, settings):
        from audit.models import AuditLog
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER})
        assert AuditLog.objects.filter(
            action=AuditLog.ACTION_EDIT_DOCUMENT,
            entity_id=existing_doc.shipment_id,
        ).exists()

    def test_nonexistent_pk_returns_404(self, client, editor_user):
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": 999999})
        response = client.get(url)
        assert response.status_code == 404

    def test_deleted_doc_returns_404(self, client, editor_user, existing_doc):
        existing_doc.is_deleted = True
        existing_doc.save()
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 404

    def test_auto_user_cannot_edit_rail_doc(self, client, editor_user, rail_doc):
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": rail_doc.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_rail_user_can_edit_rail_doc(self, client, rail_editor_user, rail_doc):
        client.login(username="rail_editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": rail_doc.pk})
        response = client.get(url)
        assert response.status_code == 200

    @pytest.mark.django_db(transaction=True)
    def test_post_with_new_file_deletes_old_file(self, client, editor_user, existing_doc, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        old_abs = tmp_path / existing_doc.file_path
        assert old_abs.exists()
        client.login(username="editor_doc", password="pass")
        url = reverse("documents:edit", kwargs={"pk": existing_doc.pk})
        new_file = SimpleUploadedFile("replaced.pdf", b"%PDF-1.4 replaced", content_type="application/pdf")
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_UPD, "file": new_file})
        assert response.status_code == 302
        assert not old_abs.exists()

    def test_upload_db_failure_no_orphan_file(self, client, docs_user, auto_shipment, tmp_path, settings):
        from unittest.mock import patch
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        new_file = SimpleUploadedFile("boom.pdf", b"%PDF-1.4 boom", content_type="application/pdf")
        with patch("documents.models.ShipmentDocument.objects.create", side_effect=Exception("DB error")):
            try:
                client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER, "file": new_file})
            except Exception:
                pass
        uploaded_files = list(tmp_path.rglob("*.pdf"))
        assert uploaded_files == [], f"Orphan files found: {uploaded_files}"


@pytest.mark.django_db
class TestDocumentDeleteView:
    @pytest.fixture
    def deleter_user(self, django_user_model):
        user = django_user_model.objects.create_user(username="deleter_doc", password="pass")
        group, _ = Group.objects.get_or_create(name="_test_deleter_doc")
        for codename, app_label in [
            ("view_autoshipment", "shipments_auto"),
            ("view_railshipment", "shipments_rail"),
            ("view_shipmentdocument", "documents"),
            ("delete_autoshipment_documents", "documents"),
        ]:
            p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            group.permissions.add(p)
        user.groups.add(group)
        return user

    @pytest.fixture
    def rail_deleter_user(self, django_user_model):
        user = django_user_model.objects.create_user(username="rail_deleter_doc", password="pass")
        group, _ = Group.objects.get_or_create(name="_test_rail_deleter_doc")
        for codename, app_label in [
            ("view_autoshipment", "shipments_auto"),
            ("view_railshipment", "shipments_rail"),
            ("view_shipmentdocument", "documents"),
            ("delete_railshipment_documents", "documents"),
        ]:
            p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
            group.permissions.add(p)
        user.groups.add(group)
        return user

    @pytest.fixture
    def rail_doc_for_delete(self, docs_user, rail_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        rel = f"rail/2026/02/shipment_{rail_shipment.pk}/del_rail.pdf"
        from pathlib import Path
        abs_path = Path(tmp_path) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF-1.4 rail todelete")
        return ShipmentDocument.objects.create(
            shipment_type="rail",
            shipment_id=rail_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="del_rail.pdf",
            stored_file_name="del_rail.pdf",
            file_path=rel,
            mime_type="application/pdf",
            file_size=22,
            uploaded_by=docs_user,
        )

    @pytest.fixture
    def existing_doc(self, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        rel = "auto/2026/02/shipment_1/del.pdf"
        from pathlib import Path
        abs_path = Path(tmp_path) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF-1.4 todelete")
        return ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="del.pdf",
            stored_file_name="del.pdf",
            file_path=rel,
            mime_type="application/pdf",
            file_size=17,
            uploaded_by=docs_user,
        )

    def test_anonymous_redirects(self, client, existing_doc):
        url = reverse("documents:delete", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_without_delete_perm_gets_403(self, client, viewer_user, existing_doc):
        client.login(username="viewer_docs", password="pass")
        url = reverse("documents:delete", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_get_shows_confirmation_page(self, client, deleter_user, existing_doc):
        client.login(username="deleter_doc", password="pass")
        url = reverse("documents:delete", kwargs={"pk": existing_doc.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert "del.pdf" in response.content.decode()

    def test_post_soft_deletes_document(self, client, deleter_user, existing_doc, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="deleter_doc", password="pass")
        url = reverse("documents:delete", kwargs={"pk": existing_doc.pk})
        response = client.post(url)
        assert response.status_code == 302
        existing_doc.refresh_from_db()
        assert existing_doc.is_deleted is True
        assert existing_doc.deleted_at is not None

    def test_deleted_doc_not_in_shipment_detail(self, client, deleter_user, docs_user, existing_doc, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="deleter_doc", password="pass")
        client.post(reverse("documents:delete", kwargs={"pk": existing_doc.pk}))
        # нужны права просмотра отгрузки, пересоздаём клиент под docs_user
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("auto:detail", kwargs={"pk": auto_shipment.pk}))
        assert "del.pdf" not in response.content.decode()

    def test_serve_deleted_doc_returns_404(self, client, deleter_user, docs_user, existing_doc, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="deleter_doc", password="pass")
        client.post(reverse("documents:delete", kwargs={"pk": existing_doc.pk}))
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": existing_doc.pk}))
        assert response.status_code == 404

    def test_post_creates_audit_log(self, client, deleter_user, existing_doc, tmp_path, settings):
        from audit.models import AuditLog
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="deleter_doc", password="pass")
        client.post(reverse("documents:delete", kwargs={"pk": existing_doc.pk}))
        assert AuditLog.objects.filter(
            action=AuditLog.ACTION_DELETE_DOCUMENT,
            entity_id=existing_doc.shipment_id,
        ).exists()

    def test_nonexistent_pk_returns_404(self, client, deleter_user):
        client.login(username="deleter_doc", password="pass")
        response = client.post(reverse("documents:delete", kwargs={"pk": 999999}))
        assert response.status_code == 404

    def test_auto_user_cannot_delete_rail_doc(self, client, deleter_user, rail_doc_for_delete):
        client.login(username="deleter_doc", password="pass")
        url = reverse("documents:delete", kwargs={"pk": rail_doc_for_delete.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_rail_user_can_delete_rail_doc(self, client, rail_deleter_user, rail_doc_for_delete):
        client.login(username="rail_deleter_doc", password="pass")
        url = reverse("documents:delete", kwargs={"pk": rail_doc_for_delete.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_post_soft_delete_and_audit_log_atomic(self, client, deleter_user, existing_doc, auto_shipment, tmp_path, settings):
        """V13-L6: soft-delete + AuditLog выполняются в одной транзакции."""
        from audit.models import AuditLog
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="deleter_doc", password="pass")
        url = reverse("documents:delete", kwargs={"pk": existing_doc.pk})
        response = client.post(url)
        assert response.status_code == 302
        existing_doc.refresh_from_db()
        assert existing_doc.is_deleted is True
        assert AuditLog.objects.filter(
            action=AuditLog.ACTION_DELETE_DOCUMENT,
            entity_type=AuditLog.ENTITY_AUTO,
            entity_id=existing_doc.shipment_id,
        ).exists()


@pytest.mark.django_db
class TestDocumentServeSecure:
    """B-S06: Path Traversal защита; B-S12: X-Content-Type-Options."""

    def _make_doc(self, auto_shipment, docs_user, tmp_path, rel, content=b"%PDF test"):
        from pathlib import Path
        abs_path = Path(tmp_path) / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        return ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="test.pdf",
            stored_file_name="test.pdf",
            file_path=rel,
            file_size=len(content),
            uploaded_by=docs_user,
        )

    def test_x_content_type_options_header(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = self._make_doc(auto_shipment, docs_user, tmp_path, "auto/2026/02/shipment_1/hdr.pdf")
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 200
        assert response["X-Content-Type-Options"] == "nosniff"

    def test_serve_as_attachment(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = self._make_doc(auto_shipment, docs_user, tmp_path, "auto/2026/02/shipment_1/attach.pdf")
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 200
        assert "attachment" in response.get("Content-Disposition", "")

    def test_path_traversal_rejected(self, client, docs_user, auto_shipment, tmp_path, settings):
        import os
        from pathlib import Path
        settings.MEDIA_ROOT = str(tmp_path)
        outside = Path(tmp_path).parent / "secret.pdf"
        outside.write_bytes(b"%PDF secret")
        rel = os.path.join("..", "secret.pdf")
        doc = ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="secret.pdf",
            stored_file_name="secret.pdf",
            file_path=rel,
            file_size=11,
            uploaded_by=docs_user,
        )
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 404

    def test_valid_file_served_200(self, client, docs_user, auto_shipment, tmp_path, settings):
        """V14-L11: валидный файл внутри MEDIA_ROOT → 200."""
        settings.MEDIA_ROOT = str(tmp_path)
        doc = self._make_doc(auto_shipment, docs_user, tmp_path, "auto/2026/06/shipment_1/v14l11.pdf")
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 200

    def test_missing_file_inside_media_root_returns_404(self, client, docs_user, auto_shipment, tmp_path, settings):
        """V14-L11: путь внутри MEDIA_ROOT, файл не существует → 404."""
        settings.MEDIA_ROOT = str(tmp_path)
        doc = ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="missing.pdf",
            stored_file_name="missing.pdf",
            file_path="auto/2026/06/shipment_1/missing.pdf",
            file_size=0,
            uploaded_by=docs_user,
        )
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 404

    def test_path_traversal_nonexistent_file_returns_404(self, client, docs_user, auto_shipment, tmp_path, settings):
        """V14-L11: path traversal без существующего файла → 404 (traversal-check до exists-check)."""
        import os
        settings.MEDIA_ROOT = str(tmp_path)
        # файл вне MEDIA_ROOT не создаём — проверяем, что resolve().relative_to() срабатывает первым
        rel = os.path.join("..", "nonexistent_secret.pdf")
        doc = ShipmentDocument.objects.create(
            shipment_type="auto",
            shipment_id=auto_shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
            original_file_name="nonexistent_secret.pdf",
            stored_file_name="nonexistent_secret.pdf",
            file_path=rel,
            file_size=0,
            uploaded_by=docs_user,
        )
        client.login(username="docs_user", password="pass")
        response = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert response.status_code == 404


@pytest.fixture
def viewer_no_doc_perm(django_user_model):
    """User: has view_autoshipment but NOT view_shipmentdocument."""
    user = django_user_model.objects.create_user(username="viewer_nodoc", password="pass")
    p = Permission.objects.get(codename="view_autoshipment", content_type__app_label="shipments_auto")
    user.user_permissions.add(p)
    return user


@pytest.fixture
def doc_on_auto(docs_user, auto_shipment, tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    file_path = Path(tmp_path) / "auto" / "2026" / "01" / f"shipment_{auto_shipment.pk}"
    file_path.mkdir(parents=True, exist_ok=True)
    (file_path / "test.pdf").write_bytes(b"%PDF-1.4 test")
    return ShipmentDocument.objects.create(
        shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
        shipment_id=auto_shipment.pk,
        document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
        original_file_name="test.pdf",
        stored_file_name="test.pdf",
        file_path=f"auto/2026/01/shipment_{auto_shipment.pk}/test.pdf",
        mime_type="application/pdf",
        file_size=13,
        uploaded_by=docs_user,
    )


@pytest.mark.django_db
class TestViewDocumentPermission:
    def test_serve_without_view_doc_perm_is_403(self, client, viewer_no_doc_perm, doc_on_auto):
        client.login(username="viewer_nodoc", password="pass")
        url = reverse("documents:serve", kwargs={"pk": doc_on_auto.pk})
        response = client.get(url)
        assert response.status_code == 403

    def test_serve_with_view_doc_perm_is_200(self, client, viewer_user, doc_on_auto):
        client.login(username="viewer_docs", password="pass")
        url = reverse("documents:serve", kwargs={"pk": doc_on_auto.pk})
        response = client.get(url)
        assert response.status_code == 200


@pytest.fixture
def deleted_auto_shipment(docs_user):
    from shipments_auto.models import AutoShipment
    s = AutoShipment.objects.create(
        shipment_date="2026-02-01",
        customer_object="Удалённый объект",
        coal_grade="ДГ",
        quantity="50",
        is_deleted=True,
        created_by=docs_user,
        updated_by=docs_user,
    )
    return s


@pytest.fixture
def doc_on_deleted_auto(docs_user, deleted_auto_shipment):
    return ShipmentDocument.objects.create(
        shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
        shipment_id=deleted_auto_shipment.pk,
        document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
        original_file_name="old.pdf",
        stored_file_name="old.pdf",
        file_path="auto/2026/01/old.pdf",
        mime_type="application/pdf",
        file_size=10,
        uploaded_by=docs_user,
    )


@pytest.fixture
def edit_delete_user(django_user_model):
    user = django_user_model.objects.create_user(username="edit_doc_user", password="pass")
    for codename, app_label in [
        ("view_autoshipment", "shipments_auto"),
        ("view_shipmentdocument", "documents"),
        ("change_autoshipment_documents", "documents"),
        ("delete_autoshipment_documents", "documents"),
    ]:
        p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        user.user_permissions.add(p)
    return user


@pytest.mark.django_db
class TestDocumentParentActiveCheck:
    def test_edit_get_deleted_parent_is_404(self, client, edit_delete_user, doc_on_deleted_auto):
        client.login(username="edit_doc_user", password="pass")
        url = reverse("documents:edit", kwargs={"pk": doc_on_deleted_auto.pk})
        response = client.get(url)
        assert response.status_code == 404

    def test_edit_post_deleted_parent_is_404(self, client, edit_delete_user, doc_on_deleted_auto):
        client.login(username="edit_doc_user", password="pass")
        url = reverse("documents:edit", kwargs={"pk": doc_on_deleted_auto.pk})
        response = client.post(url, {"document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER})
        assert response.status_code == 404

    def test_delete_get_deleted_parent_is_404(self, client, edit_delete_user, doc_on_deleted_auto):
        client.login(username="edit_doc_user", password="pass")
        url = reverse("documents:delete", kwargs={"pk": doc_on_deleted_auto.pk})
        response = client.get(url)
        assert response.status_code == 404

    def test_delete_post_deleted_parent_is_404(self, client, edit_delete_user, doc_on_deleted_auto):
        client.login(username="edit_doc_user", password="pass")
        url = reverse("documents:delete", kwargs={"pk": doc_on_deleted_auto.pk})
        response = client.post(url)
        assert response.status_code == 404

    def test_serve_deleted_parent_is_404(self, client, viewer_user, doc_on_deleted_auto, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="viewer_docs", password="pass")
        url = reverse("documents:serve", kwargs={"pk": doc_on_deleted_auto.pk})
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestDocumentDeleteConfirmationText:
    def test_confirm_shows_soft_delete_text(self, client, edit_delete_user, doc_on_auto):
        client.login(username="edit_doc_user", password="pass")
        url = reverse("documents:delete", kwargs={"pk": doc_on_auto.pk})
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "нельзя отменить" not in content
        assert "скрыт" in content


@pytest.fixture
def admin_user(django_user_model):
    return django_user_model.objects.create_superuser(
        username="admin_v11", password="pass", email="a@a.com"
    )


@pytest.mark.django_db
class TestShipmentDeleteShowsDocCount:
    def test_delete_confirmation_shows_document_count(
        self, client, admin_user, auto_shipment, doc_on_auto
    ):
        client.login(username="admin_v11", password="pass")
        url = reverse("auto:delete", kwargs={"pk": auto_shipment.pk})
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Прикреплённых документов" in content

    def test_delete_confirmation_no_doc_count_when_none(
        self, client, admin_user, auto_shipment
    ):
        client.login(username="admin_v11", password="pass")
        url = reverse("auto:delete", kwargs={"pk": auto_shipment.pk})
        response = client.get(url)
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "Прикреплённых документов" not in content


@pytest.mark.django_db
class TestDocumentStorageFailure:
    def test_upload_mkdir_failure_shows_error(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        with mock.patch("pathlib.Path.mkdir", side_effect=OSError("нет места на диске")):
            response = client.post(url, {
                "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
                "file": _pdf_file("test.pdf"),
            })
        assert response.status_code == 200
        assert "сохранить файл" in response.content.decode("utf-8")
        assert not ShipmentDocument.objects.filter(
            shipment_type="auto", shipment_id=auto_shipment.pk
        ).exists()

    def test_upload_write_failure_shows_error(self, client, docs_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="docs_user", password="pass")
        url = reverse("documents:upload", kwargs={"shipment_type": "auto", "pk": auto_shipment.pk})
        with mock.patch("documents.views.open", side_effect=OSError("permission denied")):
            response = client.post(url, {
                "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
                "file": _pdf_file("test.pdf"),
            })
        assert response.status_code == 200
        assert "сохранить файл" in response.content.decode("utf-8")
        assert not ShipmentDocument.objects.filter(
            shipment_type="auto", shipment_id=auto_shipment.pk
        ).exists()


def test_preview_safe_mimes_subset_of_allowed():
    """PREVIEW_SAFE_MIMES не должен содержать MIME, которые не проходят загрузку.

    Три списка (ALLOWED_EXTENSIONS / ALLOWED_MIME_TYPES / PREVIEW_SAFE_MIMES) должны
    быть согласованы: превью разрешено только для типов, которые вообще можно
    загрузить. Иначе появляются мёртвые записи вроде webp/gif (V12-23).
    """
    from documents.views import PREVIEW_SAFE_MIMES
    from documents.forms import ALLOWED_MIME_TYPES

    assert PREVIEW_SAFE_MIMES <= ALLOWED_MIME_TYPES, (
        f"PREVIEW_SAFE_MIMES содержит незагружаемые типы: "
        f"{PREVIEW_SAFE_MIMES - ALLOWED_MIME_TYPES}"
    )
