import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission
from pathlib import Path

from .models import ShipmentDocument
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment


@pytest.fixture
def client():
    return Client()


def _make_user(django_user_model, username, perms=()):
    user = django_user_model.objects.create_user(username=username, password="pass")
    group, _ = Group.objects.get_or_create(name=f"_test_{username}")
    for codename, app_label in perms:
        p = Permission.objects.get(codename=codename, content_type__app_label=app_label)
        group.permissions.add(p)
    user.groups.add(group)
    return user


@pytest.fixture
def operator_user(django_user_model):
    return _make_user(django_user_model, "srv_operator", [
        ("view_autoshipment", "shipments_auto"),
        ("view_railshipment", "shipments_rail"),
        ("add_autoshipment", "shipments_auto"),
        ("add_railshipment", "shipments_rail"),
    ])


@pytest.fixture
def viewer_auto(django_user_model):
    return _make_user(django_user_model, "srv_viewer_auto", [
        ("view_autoshipment", "shipments_auto"),
        ("view_shipmentdocument", "documents"),
    ])


@pytest.fixture
def viewer_rail(django_user_model):
    return _make_user(django_user_model, "srv_viewer_rail", [
        ("view_railshipment", "shipments_rail"),
        ("view_shipmentdocument", "documents"),
    ])


@pytest.fixture
def no_perm_user(django_user_model):
    return django_user_model.objects.create_user(username="srv_noperm", password="pass")


@pytest.fixture
def auto_shipment(operator_user):
    return AutoShipment.objects.create(
        shipment_date="2026-03-01",
        customer_object="Объект докS",
        coal_grade="Т",
        quantity="100",
        created_by=operator_user,
        updated_by=operator_user,
    )


@pytest.fixture
def rail_shipment(operator_user):
    return RailShipment.objects.create(
        departure_date="2026-03-01",
        wagon_number="55443322",
        cargo="Уголь",
        receiver="Получатель докS",
        volume="200",
        created_by=operator_user,
        updated_by=operator_user,
    )


def _make_doc(shipment_type, shipment_id, uploader, tmp_path):
    rel = f"{shipment_type}/2026/03/shipment_{shipment_id}/doc.pdf"
    abs_path = Path(tmp_path) / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"%PDF test")
    return ShipmentDocument.objects.create(
        shipment_type=shipment_type,
        shipment_id=shipment_id,
        document_type=ShipmentDocument.DOCUMENT_TYPE_OTHER,
        original_file_name="doc.pdf",
        stored_file_name="doc.pdf",
        file_path=rel,
        file_size=9,
        uploaded_by=uploader,
    )


@pytest.mark.django_db
class TestDocumentServePermissions:
    def test_anonymous_redirects(self, client, operator_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = _make_doc("auto", auto_shipment.pk, operator_user, tmp_path)
        r = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_no_perm_gets_403(self, client, no_perm_user, operator_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = _make_doc("auto", auto_shipment.pk, operator_user, tmp_path)
        client.login(username="srv_noperm", password="pass")
        r = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert r.status_code == 403

    def test_viewer_auto_can_serve_auto_doc(self, client, viewer_auto, operator_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = _make_doc("auto", auto_shipment.pk, operator_user, tmp_path)
        client.login(username="srv_viewer_auto", password="pass")
        r = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert r.status_code == 200

    def test_viewer_auto_cannot_serve_rail_doc(self, client, viewer_auto, operator_user, rail_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = _make_doc("rail", rail_shipment.pk, operator_user, tmp_path)
        client.login(username="srv_viewer_auto", password="pass")
        r = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert r.status_code == 403

    def test_viewer_rail_can_serve_rail_doc(self, client, viewer_rail, operator_user, rail_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = _make_doc("rail", rail_shipment.pk, operator_user, tmp_path)
        client.login(username="srv_viewer_rail", password="pass")
        r = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert r.status_code == 200

    def test_viewer_rail_cannot_serve_auto_doc(self, client, viewer_rail, operator_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        doc = _make_doc("auto", auto_shipment.pk, operator_user, tmp_path)
        client.login(username="srv_viewer_rail", password="pass")
        r = client.get(reverse("documents:serve", kwargs={"pk": doc.pk}))
        assert r.status_code == 403


@pytest.fixture
def uploader_user(django_user_model):
    return _make_user(django_user_model, "srv_uploader", [
        ("upload_autoshipment_documents", "documents"),
        ("view_autoshipment", "shipments_auto"),
    ])


@pytest.mark.django_db
class TestDocumentUploadPermissions:
    def _upload_url(self, shipment_type, pk):
        return reverse("documents:upload", kwargs={"shipment_type": shipment_type, "pk": pk})

    def test_anonymous_get_redirects(self, client, auto_shipment):
        r = client.get(self._upload_url("auto", auto_shipment.pk))
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_anonymous_post_redirects(self, client, auto_shipment):
        r = client.post(self._upload_url("auto", auto_shipment.pk))
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_no_perm_get_returns_403(self, client, no_perm_user, auto_shipment):
        client.login(username="srv_noperm", password="pass")
        r = client.get(self._upload_url("auto", auto_shipment.pk))
        assert r.status_code == 403

    def test_no_perm_post_returns_403_and_no_file(self, client, no_perm_user, auto_shipment, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        client.login(username="srv_noperm", password="pass")
        f = SimpleUploadedFile("test.pdf", b"%PDF test", content_type="application/pdf")
        r = client.post(self._upload_url("auto", auto_shipment.pk), {
            "file": f,
            "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
        })
        assert r.status_code == 403
        assert ShipmentDocument.objects.count() == 0

    def test_with_perm_get_returns_200(self, client, uploader_user, auto_shipment):
        client.login(username="srv_uploader", password="pass")
        r = client.get(self._upload_url("auto", auto_shipment.pk))
        assert r.status_code == 200

    def test_invalid_shipment_type_returns_404(self, client, uploader_user, auto_shipment):
        client.login(username="srv_uploader", password="pass")
        r = client.get(self._upload_url("invalid", auto_shipment.pk))
        assert r.status_code == 404
