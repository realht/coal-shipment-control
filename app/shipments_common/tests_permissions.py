"""Consolidated permission tests for AutoShipment and RailShipment.

The auto/rail permission suites были почти полными клонами (по 387 строк).
Здесь тела тестов написаны один раз и параметризуются по entity-конфигу
через параметризованную фикстуру ``entity`` (ids: ``auto`` / ``rail``),
поэтому каждый тест выполняется дважды — по одному кейсу на сущность.
"""
import pytest
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission

from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment


# --- entity configs -------------------------------------------------------

ENTITY_CONFIGS = {
    "auto": {
        "ns": "auto",
        "model": AutoShipment,
        "prefix": "autoshipment",
        "app_label": "shipments_auto",
        "url_prefix": "/auto/export/",
        "filter_field": "coal_grade",
        "shipment_fields": {
            "shipment_date": "2026-03-01",
            "customer_object": "Тест-объект",
            "coal_grade": "ДГ",
            "quantity": "100",
        },
        "deleted_fields": {
            "shipment_date": "2026-04-01",
            "customer_object": "Тест удалённый",
            "coal_grade": "ДГ",
            "quantity": "10",
        },
    },
    "rail": {
        "ns": "rail",
        "model": RailShipment,
        "prefix": "railshipment",
        "app_label": "shipments_rail",
        "url_prefix": "/rail/export/",
        "filter_field": "cargo",
        "shipment_fields": {
            "departure_date": "2026-03-01",
            "wagon_number": "11223344",
            "cargo": "Уголь ДГ",
            "receiver": "Тест-получатель",
            "volume": "500",
        },
        "deleted_fields": {
            "departure_date": "2026-04-01",
            "wagon_number": "77777777",
            "cargo": "Уголь ДГ",
            "volume": "100",
        },
    },
}


@pytest.fixture(params=["auto", "rail"])
def entity(request):
    return ENTITY_CONFIGS[request.param]


# --- helpers / users ------------------------------------------------------

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


def _u(entity, role):
    """Единообразный username по роли и сущности."""
    return f"perm_{role}_{entity['ns']}"


@pytest.fixture
def no_perm_user(django_user_model, entity):
    return django_user_model.objects.create_user(
        username=f"noperm_{entity['ns']}", password="pass"
    )


@pytest.fixture
def viewer_user(django_user_model, entity):
    prefix, app = entity["prefix"], entity["app_label"]
    return _make_user(django_user_model, _u(entity, "viewer"), [
        (f"view_{prefix}", app),
    ])


@pytest.fixture
def excel_user(django_user_model, entity):
    # исторически excel_user получает view+export для обоих приложений
    return _make_user(django_user_model, _u(entity, "excel"), [
        ("view_autoshipment", "shipments_auto"),
        ("export_excel", "shipments_auto"),
        ("view_railshipment", "shipments_rail"),
        ("export_excel", "shipments_rail"),
    ])


@pytest.fixture
def operator_user(django_user_model, entity):
    prefix, app = entity["prefix"], entity["app_label"]
    return _make_user(django_user_model, _u(entity, "operator"), [
        (f"view_{prefix}", app),
        (f"add_{prefix}", app),
        (f"change_{prefix}", app),
        (f"delete_{prefix}", app),
    ])


@pytest.fixture
def documents_user(django_user_model, entity):
    prefix, app = entity["prefix"], entity["app_label"]
    return _make_user(django_user_model, _u(entity, "docs"), [
        (f"view_{prefix}", app),
        ("add_shipmentdocument", "documents"),
    ])


@pytest.fixture
def admin_user(django_user_model, entity):
    prefix, app = entity["prefix"], entity["app_label"]
    return _make_user(django_user_model, _u(entity, "admin"), [
        (f"view_{prefix}", app),
        (f"add_{prefix}", app),
        (f"change_{prefix}", app),
        (f"delete_{prefix}", app),
        ("export_excel", app),
        ("add_shipmentdocument", "documents"),
    ])


@pytest.fixture
def shipment(operator_user, entity):
    return entity["model"].objects.create(
        created_by=operator_user,
        updated_by=operator_user,
        **entity["shipment_fields"],
    )


@pytest.fixture
def deleted_shipment(operator_user, entity):
    return entity["model"].all_objects.create(
        is_deleted=True,
        created_by=operator_user,
        updated_by=operator_user,
        **entity["deleted_fields"],
    )


def _login(client, entity, role):
    client.login(username=_u(entity, role), password="pass")


# --- List -----------------------------------------------------------------

@pytest.mark.django_db
class TestListPermissions:
    def test_anonymous_redirects(self, client, entity):
        r = client.get(reverse(f"{entity['ns']}:list"))
        assert r.status_code == 302
        assert "/accounts/login/" in r["Location"]

    def test_no_perm_gets_403(self, client, entity, no_perm_user):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:list"))
        assert r.status_code == 403

    def test_no_perm_filter_values_gets_403(self, client, entity, no_perm_user):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:filter_values",
                               kwargs={"field": entity["filter_field"]}))
        assert r.status_code == 403

    def test_viewer_gets_200(self, client, entity, viewer_user):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:list"))
        assert r.status_code == 200

    def test_viewer_without_export_controls_hidden(self, client, entity, viewer_user):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:list"))
        content = r.content.decode()
        assert "Частичный экспорт" not in content
        assert f'href="{entity["url_prefix"]}' not in content

    def test_excel_user_export_controls_visible(self, client, entity, excel_user, shipment):
        _login(client, entity, "excel")
        r = client.get(reverse(f"{entity['ns']}:list"))
        content = r.content.decode()
        assert "Частичный экспорт" in content
        assert f'href="{entity["url_prefix"]}' in content
        assert 'id="full-export-link"' in content
        assert 'id="export-selected-btn" disabled' in content
        assert 'class="hidden h-10 items-center' in content
        assert 'data-partial-selection' in content

    def test_operator_without_export_controls_hidden(self, client, entity, operator_user):
        _login(client, entity, "operator")
        r = client.get(reverse(f"{entity['ns']}:list"))
        content = r.content.decode()
        assert "Частичный экспорт" not in content
        assert f'href="{entity["url_prefix"]}' not in content

    def test_documents_user_without_export_controls_hidden(self, client, entity, documents_user):
        _login(client, entity, "docs")
        r = client.get(reverse(f"{entity['ns']}:list"))
        content = r.content.decode()
        assert "Частичный экспорт" not in content
        assert f'href="{entity["url_prefix"]}' not in content

    def test_admin_export_controls_visible(self, client, entity, admin_user, shipment):
        _login(client, entity, "admin")
        r = client.get(reverse(f"{entity['ns']}:list"))
        content = r.content.decode()
        assert "Частичный экспорт" in content
        assert f'href="{entity["url_prefix"]}' in content
        assert 'id="full-export-link"' in content
        assert 'id="export-selected-btn" disabled' in content
        assert 'class="hidden h-10 items-center' in content

    def test_operator_gets_200(self, client, entity, operator_user):
        _login(client, entity, "operator")
        r = client.get(reverse(f"{entity['ns']}:list"))
        assert r.status_code == 200

    def test_documents_user_gets_200(self, client, entity, documents_user):
        _login(client, entity, "docs")
        r = client.get(reverse(f"{entity['ns']}:list"))
        assert r.status_code == 200

    def test_admin_gets_200(self, client, entity, admin_user):
        _login(client, entity, "admin")
        r = client.get(reverse(f"{entity['ns']}:list"))
        assert r.status_code == 200


# --- Detail ---------------------------------------------------------------

@pytest.mark.django_db
class TestDetailPermissions:
    def test_anonymous_redirects(self, client, entity, shipment):
        r = client.get(reverse(f"{entity['ns']}:detail", kwargs={"pk": shipment.pk}))
        assert r.status_code == 302

    def test_no_perm_gets_403(self, client, entity, no_perm_user, shipment):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:detail", kwargs={"pk": shipment.pk}))
        assert r.status_code == 403

    def test_viewer_gets_200(self, client, entity, viewer_user, shipment):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:detail", kwargs={"pk": shipment.pk}))
        assert r.status_code == 200

    def test_operator_gets_200(self, client, entity, operator_user, shipment):
        _login(client, entity, "operator")
        r = client.get(reverse(f"{entity['ns']}:detail", kwargs={"pk": shipment.pk}))
        assert r.status_code == 200


# --- Create ---------------------------------------------------------------

@pytest.mark.django_db
class TestCreatePermissions:
    def test_anonymous_redirects(self, client, entity):
        r = client.get(reverse(f"{entity['ns']}:create"))
        assert r.status_code == 302

    def test_no_perm_gets_403(self, client, entity, no_perm_user):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:create"))
        assert r.status_code == 403

    def test_viewer_gets_403(self, client, entity, viewer_user):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:create"))
        assert r.status_code == 403

    def test_operator_gets_200(self, client, entity, operator_user):
        _login(client, entity, "operator")
        r = client.get(reverse(f"{entity['ns']}:create"))
        assert r.status_code == 200

    def test_documents_user_gets_403(self, client, entity, documents_user):
        _login(client, entity, "docs")
        r = client.get(reverse(f"{entity['ns']}:create"))
        assert r.status_code == 403

    def test_admin_gets_200(self, client, entity, admin_user):
        _login(client, entity, "admin")
        r = client.get(reverse(f"{entity['ns']}:create"))
        assert r.status_code == 200


# --- Update ---------------------------------------------------------------

@pytest.mark.django_db
class TestUpdatePermissions:
    def test_anonymous_redirects(self, client, entity, shipment):
        r = client.get(reverse(f"{entity['ns']}:update", kwargs={"pk": shipment.pk}))
        assert r.status_code == 302

    def test_no_perm_gets_403(self, client, entity, no_perm_user, shipment):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:update", kwargs={"pk": shipment.pk}))
        assert r.status_code == 403

    def test_viewer_gets_403(self, client, entity, viewer_user, shipment):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:update", kwargs={"pk": shipment.pk}))
        assert r.status_code == 403

    def test_operator_gets_200(self, client, entity, operator_user, shipment):
        _login(client, entity, "operator")
        r = client.get(reverse(f"{entity['ns']}:update", kwargs={"pk": shipment.pk}))
        assert r.status_code == 200

    def test_admin_gets_200(self, client, entity, admin_user, shipment):
        _login(client, entity, "admin")
        r = client.get(reverse(f"{entity['ns']}:update", kwargs={"pk": shipment.pk}))
        assert r.status_code == 200


# --- Delete ---------------------------------------------------------------

@pytest.mark.django_db
class TestDeletePermissions:
    def test_anonymous_redirects(self, client, entity, shipment):
        r = client.post(reverse(f"{entity['ns']}:delete", kwargs={"pk": shipment.pk}))
        assert r.status_code == 302

    def test_no_perm_gets_403(self, client, entity, no_perm_user, shipment):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.post(reverse(f"{entity['ns']}:delete", kwargs={"pk": shipment.pk}))
        assert r.status_code == 403

    def test_viewer_gets_403(self, client, entity, viewer_user, shipment):
        _login(client, entity, "viewer")
        r = client.post(reverse(f"{entity['ns']}:delete", kwargs={"pk": shipment.pk}))
        assert r.status_code == 403

    def test_documents_user_gets_403(self, client, entity, documents_user, shipment):
        _login(client, entity, "docs")
        r = client.post(reverse(f"{entity['ns']}:delete", kwargs={"pk": shipment.pk}))
        assert r.status_code == 403

    def test_operator_can_delete(self, client, entity, operator_user, shipment):
        _login(client, entity, "operator")
        r = client.post(reverse(f"{entity['ns']}:delete", kwargs={"pk": shipment.pk}))
        assert r.status_code == 302
        shipment.refresh_from_db()
        assert shipment.is_deleted is True

    def test_admin_can_delete(self, client, entity, admin_user, shipment):
        _login(client, entity, "admin")
        r = client.post(reverse(f"{entity['ns']}:delete", kwargs={"pk": shipment.pk}))
        assert r.status_code == 302


# --- Export ---------------------------------------------------------------

@pytest.mark.django_db
class TestExportPermissions:
    def test_anonymous_redirects(self, client, entity):
        r = client.get(reverse(f"{entity['ns']}:export"))
        assert r.status_code == 302

    def test_no_perm_gets_403(self, client, entity, no_perm_user):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:export"))
        assert r.status_code == 403

    def test_viewer_without_export_perm_gets_403(self, client, entity, viewer_user):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:export"))
        assert r.status_code == 403

    def test_excel_user_gets_200(self, client, entity, excel_user):
        _login(client, entity, "excel")
        r = client.get(reverse(f"{entity['ns']}:export"))
        assert r.status_code == 200

    def test_admin_gets_200(self, client, entity, admin_user):
        _login(client, entity, "admin")
        r = client.get(reverse(f"{entity['ns']}:export"))
        assert r.status_code == 200


# --- Deleted list ---------------------------------------------------------

@pytest.mark.django_db
class TestDeletedListPermissions:
    def test_anonymous_redirects(self, client, entity):
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 302

    def test_no_perm_gets_403(self, client, entity, no_perm_user):
        client.login(username=f"noperm_{entity['ns']}", password="pass")
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 403

    def test_viewer_gets_403(self, client, entity, viewer_user):
        _login(client, entity, "viewer")
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 403

    def test_excel_user_gets_403(self, client, entity, excel_user):
        _login(client, entity, "excel")
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 403

    def test_documents_user_gets_403(self, client, entity, documents_user):
        _login(client, entity, "docs")
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 403

    def test_operator_gets_200(self, client, entity, operator_user):
        _login(client, entity, "operator")
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 200

    def test_admin_gets_200(self, client, entity, admin_user):
        _login(client, entity, "admin")
        r = client.get(reverse(f"{entity['ns']}:deleted"))
        assert r.status_code == 200


# --- Restore --------------------------------------------------------------

@pytest.mark.django_db
class TestRestorePermissions:
    def test_anonymous_redirects(self, client, entity, deleted_shipment):
        r = client.post(reverse(f"{entity['ns']}:restore", kwargs={"pk": deleted_shipment.pk}))
        assert r.status_code == 302

    def test_viewer_gets_403(self, client, entity, viewer_user, deleted_shipment):
        _login(client, entity, "viewer")
        r = client.post(reverse(f"{entity['ns']}:restore", kwargs={"pk": deleted_shipment.pk}))
        assert r.status_code == 403

    def test_documents_user_gets_403(self, client, entity, documents_user, deleted_shipment):
        _login(client, entity, "docs")
        r = client.post(reverse(f"{entity['ns']}:restore", kwargs={"pk": deleted_shipment.pk}))
        assert r.status_code == 403

    def test_operator_can_restore(self, client, entity, operator_user, deleted_shipment):
        _login(client, entity, "operator")
        r = client.post(reverse(f"{entity['ns']}:restore", kwargs={"pk": deleted_shipment.pk}))
        assert r.status_code == 302
        deleted_shipment.refresh_from_db()
        assert deleted_shipment.is_deleted is False

    def test_admin_can_restore(self, client, entity, admin_user, deleted_shipment):
        _login(client, entity, "admin")
        r = client.post(reverse(f"{entity['ns']}:restore", kwargs={"pk": deleted_shipment.pk}))
        assert r.status_code == 302
