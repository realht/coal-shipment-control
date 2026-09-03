import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError
from unittest.mock import patch

from .models import CatalogValue
from core.models import FieldSettings
from catalogs.views import _all_fields_with_catalog_info, _shipment_word


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_user(username="cat_admin", password="pass")
    group, _ = Group.objects.get_or_create(name="cat_admin_perms")
    for app_label, codename in [
        ("catalogs", "view_catalogvalue"),
        ("catalogs", "add_catalogvalue"),
        ("catalogs", "change_catalogvalue"),
        ("catalogs", "delete_catalogvalue"),
        ("core", "change_fieldsettings"),
    ]:
        group.permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    user.groups.add(group)
    return user


@pytest.fixture
def viewer_user(django_user_model):
    user = django_user_model.objects.create_user(username="cat_viewer", password="pass")
    group, _ = Group.objects.get_or_create(name="viewer")
    user.groups.add(group)
    return user


@pytest.fixture
def field_setting():
    return FieldSettings.objects.get_or_create(
        entity="auto_shipment",
        field_name="coal_grade",
        defaults={"label": "Марка угля", "use_catalog": True, "sort_order": 1},
    )[0]


@pytest.fixture
def catalog_value(field_setting):
    return CatalogValue.objects.create(
        catalog_type="auto_shipment__coal_grade",
        name="ДГ",
        is_active=True,
    )


@pytest.mark.django_db
def test_all_fields_with_catalog_info_uses_aggregate_counts():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    CatalogValue.objects.create(
        catalog_type="auto_shipment__coal_grade",
        name="ДГ",
        is_active=True,
    )
    CatalogValue.objects.create(
        catalog_type="auto_shipment__coal_grade",
        name="ГЖО",
        is_active=False,
    )
    CatalogValue.objects.create(
        catalog_type="auto_shipment__base_code",
        name="База 1",
        is_active=True,
    )
    CatalogValue.objects.create(
        catalog_type="rail_shipment__cargo",
        name="Rail value",
        is_active=True,
    )

    with CaptureQueriesContext(connection) as captured:
        fields = _all_fields_with_catalog_info("auto_shipment")

    counts = {
        item["catalog_type"]: (item["active"], item["count"])
        for item in fields
    }
    select_queries = [
        query for query in captured.captured_queries
        if query["sql"].lstrip().upper().startswith("SELECT")
    ]

    assert counts["auto_shipment__coal_grade"] == (1, 2)
    assert counts["auto_shipment__base_code"] == (1, 1)
    assert counts["auto_shipment__customer_object"] == (0, 0)
    assert "rail_shipment__cargo" not in counts
    assert len(select_queries) == 2


@pytest.mark.parametrize("count, expected", [
    (1, "отгрузке"),
    (2, "отгрузках"),
    (5, "отгрузках"),
    (11, "отгрузках"),
    (21, "отгрузке"),
    (101, "отгрузке"),
    (111, "отгрузках"),
])
def test_shipment_word_prepositional_case(count, expected):
    assert _shipment_word(count) == expected


@pytest.mark.django_db
class TestCatalogValueEdit:
    def test_anonymous_redirects(self, client, catalog_value):
        response = client.get(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_gets_403(self, client, viewer_user, catalog_value):
        client.login(username="cat_viewer", password="pass")
        response = client.get(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}))
        assert response.status_code == 403

    def test_staff_without_permissions_gets_403(self, client, django_user_model, catalog_value):
        staff = django_user_model.objects.create_user(
            username="cat_staff", password="pass", is_staff=True
        )
        client.login(username="cat_staff", password="pass")
        response = client.get(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}))
        assert response.status_code == 403

    def test_admin_can_get_form(self, client, admin_user, catalog_value):
        client.login(username="cat_admin", password="pass")
        response = client.get(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}))
        assert response.status_code == 200
        assert b"\xd0\x94\xd0\x93" in response.content  # "ДГ" в UTF-8

    def test_get_form_shows_singular_shipment_word(self, client, admin_user, catalog_value, field_setting):
        from shipments_auto.models import AutoShipment
        AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        client.login(username="cat_admin", password="pass")
        response = client.get(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}))
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "<strong>1</strong> отгрузке (" in content

    def test_rename_without_update(self, client, admin_user, catalog_value):
        client.login(username="cat_admin", password="pass")
        response = client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
        })
        assert response.status_code == 302
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГКО"

    def test_rename_with_shipment_update(self, client, admin_user, catalog_value, field_setting):
        from shipments_auto.models import AutoShipment
        AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
            "update_shipments": "1",
        })
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГКО"
        assert AutoShipment.objects.filter(coal_grade="ДГКО").exists()
        assert not AutoShipment.objects.filter(coal_grade="ДГ").exists()

    def test_rename_without_update_does_not_touch_shipments(self, client, admin_user, catalog_value):
        from shipments_auto.models import AutoShipment
        AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
        })
        assert AutoShipment.objects.filter(coal_grade="ДГ").exists()

    def test_duplicate_name_rejected(self, client, admin_user, catalog_value, field_setting):
        CatalogValue.objects.create(
            catalog_type="auto_shipment__coal_grade",
            name="ГЖО",
            is_active=True,
        )
        client.login(username="cat_admin", password="pass")
        response = client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ГЖО",
        })
        assert response.status_code == 302
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГ"

    def test_unknown_catalog_type_redirects_to_list(self, client, admin_user):
        orphan = CatalogValue.objects.create(
            catalog_type="auto_shipment__no_such_field",
            name="Сирота",
            is_active=True,
        )
        client.login(username="cat_admin", password="pass")
        response = client.post(reverse("catalogs:edit", kwargs={"pk": orphan.pk}), {
            "name": "Новое",
        })
        assert response.status_code == 302
        assert response["Location"] == reverse("catalogs:list")
        orphan.refresh_from_db()
        assert orphan.name == "Сирота"
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        assert any("Неизвестный справочник." in message for message in messages)

    def test_integrity_error_on_rename_shows_message(self, client, admin_user, catalog_value):
        client.login(username="cat_admin", password="pass")
        with patch.object(CatalogValue, "save", side_effect=IntegrityError("duplicate")):
            response = client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
                "name": "ДГКО",
            })

        assert response.status_code == 302
        assert response["Location"] == reverse("catalogs:edit", kwargs={"pk": catalog_value.pk})
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГ"
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        assert any("Не удалось переименовать" in message for message in messages)

    def test_shipment_update_failure_rolls_back_rename(self, client, admin_user, catalog_value, field_setting):
        from shipments_auto.models import AutoShipment
        AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )

        client.login(username="cat_admin", password="pass")
        with patch("django.db.models.query.QuerySet.update", side_effect=IntegrityError("update failed")):
            response = client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
                "name": "ДГКО",
                "update_shipments": "1",
            })

        assert response.status_code == 302
        assert response["Location"] == reverse("catalogs:edit", kwargs={"pk": catalog_value.pk})
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГ"
        assert AutoShipment.objects.filter(coal_grade="ДГ").exists()
        assert not AutoShipment.objects.filter(coal_grade="ДГКО").exists()

    def test_empty_name_rejected(self, client, admin_user, catalog_value):
        client.login(username="cat_admin", password="pass")
        response = client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "",
        })
        assert response.status_code == 302
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГ"

    def test_same_name_no_change(self, client, admin_user, catalog_value):
        client.login(username="cat_admin", password="pass")
        response = client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГ",
        })
        assert response.status_code == 302
        catalog_value.refresh_from_db()
        assert catalog_value.name == "ДГ"

    def test_rename_does_not_touch_updated_by(self, client, admin_user, catalog_value, field_setting):
        # V12-D2: нормализация справочника не трогает токены optimistic locking.
        from shipments_auto.models import AutoShipment
        shipment = AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        original_updated_at = shipment.updated_at
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
            "update_shipments": "1",
        })
        shipment.refresh_from_db()
        assert shipment.coal_grade == "ДГКО"
        assert shipment.updated_by is None
        assert shipment.updated_at == original_updated_at

    def test_rename_updates_soft_deleted_shipments(self, client, admin_user, catalog_value, field_setting):
        # V12-08: all_objects — soft-deleted отгрузка тоже переименовывается.
        from shipments_auto.models import AutoShipment
        shipment = AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        shipment.delete()  # soft delete
        assert shipment.is_deleted
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
            "update_shipments": "1",
        })
        shipment.refresh_from_db()
        assert shipment.coal_grade == "ДГКО"

    def test_rename_writes_audit_log(self, client, admin_user, catalog_value, field_setting):
        # V12-08: сводная запись аудита о переименовании справочника.
        from audit.models import AuditLog
        from shipments_auto.models import AutoShipment
        AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
            "update_shipments": "1",
        })
        log = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_CATALOG_RENAME,
        )
        assert log.entity_id == catalog_value.pk
        assert log.user == admin_user
        assert log.old_values["name"] == "ДГ"
        assert log.old_values["catalog_type"] == "auto_shipment__coal_grade"
        assert log.new_values["name"] == "ДГКО"
        assert log.new_values["shipments_updated"] == 1

    def test_rename_without_update_still_writes_audit_log(self, client, admin_user, catalog_value):
        from audit.models import AuditLog
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
            "name": "ДГКО",
        })
        log = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_CATALOG_RENAME,
        )
        assert log.new_values["shipments_updated"] == 0

    def test_rollback_does_not_leave_audit_log(self, client, admin_user, catalog_value, field_setting):
        # Откат транзакции при ошибке update → записи аудита об успехе быть не должно.
        from audit.models import AuditLog
        from shipments_auto.models import AutoShipment
        AutoShipment.objects.create(
            shipment_date="2024-01-01",
            customer_object="Объект 1",
            coal_grade="ДГ",
            quantity="100.000",
        )
        client.login(username="cat_admin", password="pass")
        with patch("django.db.models.query.QuerySet.update", side_effect=IntegrityError("update failed")):
            client.post(reverse("catalogs:edit", kwargs={"pk": catalog_value.pk}), {
                "name": "ДГКО",
                "update_shipments": "1",
            })
        assert not AuditLog.objects.filter(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_CATALOG_RENAME,
        ).exists()


@pytest.mark.django_db
def test_catalog_toggle_rejects_non_text_field(client, admin_user):
    fs = FieldSettings.objects.get(entity="auto_shipment", field_name="quantity")
    fs.use_catalog = False
    fs.save(update_fields=["use_catalog"])

    client.login(username="cat_admin", password="pass")
    response = client.post(reverse("catalogs:list"), {"field_id": fs.pk})

    assert response.status_code == 302
    fs.refresh_from_db()
    assert fs.use_catalog is False


@pytest.fixture
def rail_field_setting():
    return FieldSettings.objects.get_or_create(
        entity="rail_shipment",
        field_name="cargo",
        defaults={"label": "Груз", "use_catalog": False, "sort_order": 1},
    )[0]


@pytest.mark.django_db
class TestCatalogList:
    def test_anonymous_redirects(self, client):
        response = client.get(reverse("catalogs:list"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_can_access(self, client, viewer_user):
        group = viewer_user.groups.first()
        group.permissions.add(
            Permission.objects.get(content_type__app_label="catalogs", codename="view_catalogvalue")
        )
        client.login(username="cat_viewer", password="pass")
        response = client.get(reverse("catalogs:list"))
        assert response.status_code == 200

    def test_defaults_to_auto(self, client, admin_user, field_setting):
        client.login(username="cat_admin", password="pass")
        response = client.get(reverse("catalogs:list"))
        assert response.status_code == 200
        assert response.context["entity"] == "auto_shipment"

    def test_rail_tab(self, client, admin_user, rail_field_setting):
        client.login(username="cat_admin", password="pass")
        response = client.get(reverse("catalogs:list") + "?entity=rail_shipment")
        assert response.status_code == 200
        assert response.context["entity"] == "rail_shipment"

    def test_invalid_entity_falls_back(self, client, admin_user, field_setting):
        client.login(username="cat_admin", password="pass")
        response = client.get(reverse("catalogs:list") + "?entity=unknown")
        assert response.status_code == 200
        assert response.context["entity"] == "auto_shipment"

    def test_filters_by_entity(self, client, admin_user, field_setting, rail_field_setting):
        client.login(username="cat_admin", password="pass")
        response = client.get(reverse("catalogs:list") + "?entity=auto_shipment")
        fields = response.context["fields"]
        entities = {item["fs"].entity for item in fields}
        assert entities == {"auto_shipment"}

    def test_toggle_preserves_entity(self, client, admin_user, rail_field_setting):
        client.login(username="cat_admin", password="pass")
        response = client.post(reverse("catalogs:list"), {
            "entity": "rail_shipment",
            "field_id": rail_field_setting.pk,
        })
        assert response.status_code == 302
        assert "entity=rail_shipment" in response["Location"]


@pytest.mark.django_db
class TestCatalogValueAudit:
    # V17-MED-11: add/toggle/delete пишут аудит; delete остаётся hard delete.

    def test_add_writes_audit_log(self, client, admin_user, field_setting):
        from audit.models import AuditLog
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:add", kwargs={"catalog_type": "auto_shipment__coal_grade"}), {
            "name": "ДГ",
        })
        obj = CatalogValue.objects.get(catalog_type="auto_shipment__coal_grade", name="ДГ")
        log = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_CREATE,
        )
        assert log.entity_id == obj.pk
        assert log.user == admin_user
        assert log.new_values["catalog_type"] == "auto_shipment__coal_grade"
        assert log.new_values["name"] == "ДГ"
        assert log.new_values["is_active"] is True

    def test_add_duplicate_does_not_write_audit_log(self, client, admin_user, catalog_value):
        from audit.models import AuditLog
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:add", kwargs={"catalog_type": catalog_value.catalog_type}), {
            "name": catalog_value.name,
        })
        assert not AuditLog.objects.filter(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_CREATE,
        ).exists()

    def test_toggle_writes_audit_log(self, client, admin_user, catalog_value):
        from audit.models import AuditLog
        assert catalog_value.is_active is True
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:toggle", kwargs={"pk": catalog_value.pk}))
        catalog_value.refresh_from_db()
        assert catalog_value.is_active is False
        log = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_UPDATE,
        )
        assert log.entity_id == catalog_value.pk
        assert log.user == admin_user
        assert log.old_values["is_active"] is True
        assert log.new_values["is_active"] is False
        assert log.new_values["name"] == catalog_value.name

    def test_delete_removes_row_and_writes_audit_log(self, client, admin_user, catalog_value):
        from audit.models import AuditLog
        pk = catalog_value.pk
        client.login(username="cat_admin", password="pass")
        client.post(reverse("catalogs:delete", kwargs={"pk": pk}))
        assert not CatalogValue.objects.filter(pk=pk).exists()  # hard delete
        log = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_CATALOG,
            action=AuditLog.ACTION_DELETE,
        )
        assert log.entity_id == pk
        assert log.user == admin_user
        assert log.old_values["catalog_type"] == "auto_shipment__coal_grade"
        assert log.old_values["name"] == "ДГ"
        assert log.old_values["is_active"] is True


@pytest.mark.django_db
class TestCatalogAdmin:
    # V17-MED-10: legacy-модели сняты, CatalogValue зарегистрирован read-only.

    def test_legacy_models_not_registered(self):
        from django.contrib import admin
        from catalogs.models import AutoBase, AutoCoalGrade, RailCoalGrade
        assert AutoBase not in admin.site._registry
        assert AutoCoalGrade not in admin.site._registry
        assert RailCoalGrade not in admin.site._registry

    def test_catalog_value_registered_read_only(self, rf, admin_user):
        from django.contrib import admin
        assert CatalogValue in admin.site._registry
        model_admin = admin.site._registry[CatalogValue]
        request = rf.get("/admin/")
        request.user = admin_user
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False

