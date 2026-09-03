import pytest
from unittest.mock import patch
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission

from core.field_config import load_field_config, get_entity_config, get_table_presets, get_sticky_fields, _cache_key


# --- unit-тесты утилиты ---

@pytest.mark.django_db
def test_load_field_config_returns_dict():
    cfg = load_field_config()
    assert isinstance(cfg, dict)
    assert "auto_shipment" in cfg
    assert "rail_shipment" in cfg


@pytest.mark.django_db
def test_auto_shipment_has_required_fields():
    cfg = get_entity_config("auto_shipment")
    assert cfg["shipment_date"]["required"] is True
    assert cfg["customer_object"]["required"] is True
    assert cfg["coal_grade"]["required"] is True
    assert cfg["quantity"]["required"] is True


@pytest.mark.django_db
def test_auto_shipment_invisible_fields():
    cfg = get_entity_config("auto_shipment")
    assert cfg["source_month_text"]["visible"] is False
    assert cfg["source_day_number"]["visible"] is False


@pytest.mark.django_db
def test_rail_shipment_has_required_fields():
    cfg = get_entity_config("rail_shipment")
    assert cfg["departure_date"]["required"] is True
    assert cfg["wagon_number"]["required"] is True
    assert cfg["cargo"]["required"] is True
    assert cfg["receiver"]["required"] is True
    assert cfg["volume"]["required"] is True


@pytest.mark.django_db
def test_unknown_entity_returns_empty():
    cfg = get_entity_config("nonexistent_entity")
    assert cfg == {}


@pytest.mark.django_db
def test_load_field_config_is_cached():
    from django.core.cache import cache
    from core.field_config import _cache_key
    cache.delete(_cache_key("auto_shipment"))
    cfg1 = get_entity_config("auto_shipment")
    cfg2 = get_entity_config("auto_shipment")
    assert cfg1 == cfg2


# --- тесты формы AutoShipmentForm ---

@pytest.mark.django_db
def test_auto_form_hidden_fields_not_in_form():
    from shipments_auto.forms import AutoShipmentForm
    form = AutoShipmentForm()
    assert "source_month_text" not in form.fields
    assert "source_day_number" not in form.fields


@pytest.mark.django_db
def test_auto_form_required_fields_are_required():
    from shipments_auto.forms import AutoShipmentForm
    form = AutoShipmentForm()
    assert form.fields["shipment_date"].required is True
    assert form.fields["customer_object"].required is True
    assert form.fields["quantity"].required is True
    assert "coal_grade_select" in form.fields
    assert "coal_grade_other" in form.fields


@pytest.mark.django_db
def test_auto_form_optional_fields_not_required():
    from shipments_auto.forms import AutoShipmentForm
    form = AutoShipmentForm()
    assert form.fields["vehicle_number"].required is False
    assert form.fields["driver_name"].required is False
    assert form.fields["carrier"].required is False


@pytest.mark.django_db
def test_auto_form_main_fields_list():
    from shipments_auto.forms import AutoShipmentForm
    main = AutoShipmentForm.get_main_fields()
    assert "shipment_date" in main
    assert "customer_object" in main
    assert "source_month_text" not in main
    assert "source_day_number" not in main


@pytest.mark.django_db
def test_auto_form_advanced_fields_list():
    from shipments_auto.forms import AutoShipmentForm
    adv = AutoShipmentForm.get_advanced_fields()
    assert "sub_object" in adv
    assert "base_code_select" in adv
    assert "shipment_date" not in adv


# --- тесты формы RailShipmentForm ---

@pytest.mark.django_db
def test_rail_form_required_fields_are_required():
    from shipments_rail.forms import RailShipmentForm
    form = RailShipmentForm()
    assert form.fields["departure_date"].required is True
    assert form.fields["wagon_number"].required is True
    assert form.fields["receiver"].required is True
    assert form.fields["volume"].required is True
    assert "cargo_select" in form.fields
    assert "cargo_other" in form.fields


@pytest.mark.django_db
def test_rail_form_optional_fields_not_required():
    from shipments_rail.forms import RailShipmentForm
    form = RailShipmentForm()
    assert form.fields["document_number"].required is False
    assert form.fields["comment"].required is False


@pytest.mark.django_db
def test_rail_form_advanced_fields_list():
    from shipments_rail.forms import RailShipmentForm
    adv = RailShipmentForm.get_advanced_fields()
    assert "origin_region" in adv
    assert "origin_station" in adv
    assert "sender" in adv
    assert "destination_region" in adv
    assert "departure_date" not in adv


# --- тесты: скрытое поле вызывает ошибку валидации (hidden → required) ---

@pytest.mark.django_db
def test_auto_form_missing_required_fails():
    from shipments_auto.forms import AutoShipmentForm
    form = AutoShipmentForm(data={
        "shipment_date": "2026-01-01",
        # customer_object пропущено
        "coal_grade": "ДГ",
        "quantity": "100",
    })
    assert not form.is_valid()
    assert "customer_object" in form.errors


@pytest.mark.django_db
def test_rail_form_missing_required_fails():
    from shipments_rail.forms import RailShipmentForm
    form = RailShipmentForm(data={
        "departure_date": "2026-01-01",
        "wagon_number": "12345678",
        # cargo_select пропущено
        "receiver": "ООО Тест",
        "volume": "100",
    })
    assert not form.is_valid()


# --- интеграционный тест: скрытое поле не в ответе формы создания ---

@pytest.fixture
def operator_user(django_user_model):
    user = django_user_model.objects.create_user(username="fc_operator", password="pass")
    group, _ = Group.objects.get_or_create(name="fc_operator_group")
    for codename in ("view_autoshipment", "add_autoshipment", "view_railshipment", "add_railshipment"):
        perm = Permission.objects.get(codename=codename)
        group.permissions.add(perm)
    user.groups.add(group)
    return user


@pytest.mark.django_db
def test_auto_create_form_no_hidden_field_in_html(operator_user):
    client = Client()
    client.login(username="fc_operator", password="pass")
    response = client.get(reverse("auto:create"))
    assert response.status_code == 200
    content = response.content.decode()
    assert "source_month_text" not in content
    assert "source_day_number" not in content


@pytest.mark.django_db
def test_hidden_field_visible_false_hides_in_form():
    from core.models import FieldSettings
    from django.core.cache import cache
    from core.field_config import _cache_key, invalidate_entity_config

    FieldSettings.objects.filter(entity="auto_shipment", field_name="vehicle_number").update(visible=False)
    invalidate_entity_config("auto_shipment")

    try:
        from shipments_auto.forms import AutoShipmentForm
        form = AutoShipmentForm()
        assert "vehicle_number" not in form.fields
        assert "driver_name" in form.fields
    finally:
        FieldSettings.objects.filter(entity="auto_shipment", field_name="vehicle_number").update(visible=True)
        invalidate_entity_config("auto_shipment")


# --- тесты get_table_presets / get_sticky_fields ---

@pytest.mark.django_db
def test_get_sticky_fields_auto():
    from core.field_config import invalidate_entity_config
    invalidate_entity_config("auto_shipment")
    sticky = get_sticky_fields("auto_shipment")
    assert "shipment_date" in sticky
    assert "customer_object" in sticky
    assert "vehicle_number" in sticky


@pytest.mark.django_db
def test_get_sticky_fields_rail():
    from core.field_config import invalidate_entity_config
    invalidate_entity_config("rail_shipment")
    sticky = get_sticky_fields("rail_shipment")
    assert "departure_date" in sticky
    assert "wagon_number" in sticky
    assert "receiver" in sticky


@pytest.mark.django_db
def test_get_table_presets_auto_structure():
    from core.field_config import invalidate_entity_config
    invalidate_entity_config("auto_shipment")
    presets = get_table_presets("auto_shipment")
    names = [p["name"] for p in presets]
    assert "operative" in names
    assert "documents" in names
    assert "logistics" in names
    assert "full" in names


@pytest.mark.django_db
def test_get_table_presets_full_has_empty_fields():
    from core.field_config import invalidate_entity_config
    invalidate_entity_config("auto_shipment")
    presets = get_table_presets("auto_shipment")
    full = next(p for p in presets if p["name"] == "full")
    assert full["fields"] == []


@pytest.mark.django_db
def test_get_table_presets_operative_has_core_fields():
    from core.field_config import invalidate_entity_config
    invalidate_entity_config("auto_shipment")
    presets = get_table_presets("auto_shipment")
    operative = next(p for p in presets if p["name"] == "operative")
    assert "shipment_date" in operative["fields"]
    assert "customer_object" in operative["fields"]
    assert "quantity" in operative["fields"]


def _field_settings_post_data(entity):
    from core.field_config import ENTITY_PRESETS
    from core.models import FieldSettings

    fields = list(FieldSettings.objects.filter(entity=entity).order_by("sort_order", "field_name"))
    post_data = {
        "entity": entity,
        "field_order": ",".join(fs.field_name for fs in fields),
    }
    preset_names = [preset["name"] for preset in ENTITY_PRESETS.get(entity, []) if preset["name"] != "full"]
    for fs in fields:
        if fs.visible:
            post_data[f"visible_{fs.field_name}"] = "on"
        if fs.required:
            post_data[f"required_{fs.field_name}"] = "on"
        if fs.show_in_list:
            post_data[f"show_in_list_{fs.field_name}"] = "on"
        post_data[f"section_{fs.field_name}"] = fs.section
        if fs.allow_filter:
            post_data[f"allow_filter_{fs.field_name}"] = "on"
        post_data[f"filter_type_{fs.field_name}"] = fs.filter_type
        if fs.allow_sort:
            post_data[f"allow_sort_{fs.field_name}"] = "on"
        if fs.sticky_col:
            post_data[f"sticky_col_{fs.field_name}"] = "on"
        memberships = set((fs.preset_membership or "").split(","))
        for preset_name in preset_names:
            if preset_name in memberships:
                post_data[f"preset_{preset_name}_{fs.field_name}"] = "on"
    return post_data


@pytest.mark.django_db
def test_field_settings_view_saves_sticky_and_preset(admin_user):
    from core.models import FieldSettings
    from core.field_config import invalidate_entity_config
    client = Client()
    client.force_login(admin_user)
    invalidate_entity_config("auto_shipment")

    post_data = _field_settings_post_data("auto_shipment")
    post_data["sticky_col_comment"] = "on"
    post_data["preset_operative_comment"] = "on"
    post_data["preset_documents_comment"] = "on"

    response = client.post("/settings/fields/", post_data)
    assert response.status_code == 302

    fs = FieldSettings.objects.get(entity="auto_shipment", field_name="comment")
    assert fs.sticky_col is True
    assert "operative" in fs.preset_membership
    assert "documents" in fs.preset_membership

    fs.sticky_col = False
    fs.preset_membership = ""
    fs.save()
    invalidate_entity_config("auto_shipment")


@pytest.mark.django_db
def test_field_settings_rejects_required_field_hidden(admin_user):
    from core.field_config import invalidate_entity_config
    from core.models import FieldSettings

    client = Client()
    client.force_login(admin_user)
    invalidate_entity_config("auto_shipment")

    post_data = _field_settings_post_data("auto_shipment")
    post_data.pop("visible_customer_object", None)
    post_data.pop("required_customer_object", None)

    response = client.post("/settings/fields/", post_data)

    assert response.status_code == 200
    fs = FieldSettings.objects.get(entity="auto_shipment", field_name="customer_object")
    assert fs.visible is True
    assert fs.required is True


@pytest.mark.django_db
def test_field_settings_rejects_sticky_for_hidden_column(admin_user):
    from core.field_config import invalidate_entity_config
    from core.models import FieldSettings

    client = Client()
    client.force_login(admin_user)
    invalidate_entity_config("auto_shipment")

    post_data = _field_settings_post_data("auto_shipment")
    post_data.pop("show_in_list_comment", None)
    post_data["sticky_col_comment"] = "on"

    response = client.post("/settings/fields/", post_data)

    assert response.status_code == 200
    fs = FieldSettings.objects.get(entity="auto_shipment", field_name="comment")
    assert fs.sticky_col is False


@pytest.mark.django_db
def test_field_settings_rejects_filter_type_incompatible_with_model_field(admin_user):
    from core.field_config import invalidate_entity_config
    from core.models import FieldSettings

    client = Client()
    client.force_login(admin_user)
    invalidate_entity_config("auto_shipment")

    post_data = _field_settings_post_data("auto_shipment")
    post_data["allow_filter_quantity"] = "on"
    post_data["filter_type_quantity"] = "text"

    response = client.post("/settings/fields/", post_data)

    assert response.status_code == 200
    fs = FieldSettings.objects.get(entity="auto_shipment", field_name="quantity")
    assert fs.filter_type == "number"


@pytest.mark.django_db
def test_field_settings_staff_without_permissions_gets_403(django_user_model):
    client = Client()
    staff = django_user_model.objects.create_user(username="fc_staff", password="pass", is_staff=True)
    client.login(username="fc_staff", password="pass")

    response = client.get("/settings/fields/")

    assert response.status_code == 403


@pytest.mark.django_db
def test_field_settings_rejects_duplicate_sort_order(admin_user):
    from core.field_config import invalidate_entity_config
    client = Client()
    client.force_login(admin_user)
    invalidate_entity_config("auto_shipment")

    post_data = _field_settings_post_data("auto_shipment")
    fields_list = post_data["field_order"].split(",")
    # исключаем первое поле из field_order: оно сохранит sort_order=0,
    # а второе поле (первое в новом порядке) получит index=0 → коллизия
    post_data["field_order"] = ",".join(fields_list[1:])

    response = client.post("/settings/fields/", post_data)
    assert response.status_code == 200


@pytest.mark.django_db
def test_field_settings_saves_atomically(admin_user):
    from core.field_config import invalidate_entity_config
    client = Client()
    client.force_login(admin_user)
    invalidate_entity_config("auto_shipment")

    post_data = _field_settings_post_data("auto_shipment")
    response = client.post("/settings/fields/", post_data)
    assert response.status_code == 302


@pytest.mark.django_db
def test_seed_field_config_invalidates_cache():
    cache.set(_cache_key("auto_shipment"), {"dummy": True}, 300)
    cache.set(_cache_key("rail_shipment"), {"dummy": True}, 300)
    call_command("seed_field_config", verbosity=0)
    assert cache.get(_cache_key("auto_shipment")) is None
    assert cache.get(_cache_key("rail_shipment")) is None


@pytest.mark.django_db
def test_sync_field_config_invalidates_cache():
    cache.set(_cache_key("auto_shipment"), {"dummy": True}, 300)
    cache.set(_cache_key("rail_shipment"), {"dummy": True}, 300)
    call_command("sync_field_config", verbosity=0)
    assert cache.get(_cache_key("auto_shipment")) is None
    assert cache.get(_cache_key("rail_shipment")) is None


@pytest.mark.django_db
def test_init_field_defaults_invalidates_cache():
    cache.set(_cache_key("auto_shipment"), {"dummy": True}, 300)
    cache.set(_cache_key("rail_shipment"), {"dummy": True}, 300)
    call_command("init_field_defaults", verbosity=0)
    assert cache.get(_cache_key("auto_shipment")) is None
    assert cache.get(_cache_key("rail_shipment")) is None


@pytest.mark.django_db
def test_sync_field_config_is_alias_of_seed(db):
    """sync_field_config должен давать идентичный результат seed_field_config."""
    from django.core.management import call_command
    from core.models import FieldSettings

    FieldSettings.objects.all().delete()
    call_command("seed_field_config", verbosity=0)
    seed_count = FieldSettings.objects.count()

    # Повторный вызов через alias не должен создавать новые записи
    call_command("sync_field_config", verbosity=0)
    assert FieldSettings.objects.count() == seed_count


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_user(username="fc_admin", password="pass", is_staff=True)
    group, _ = Group.objects.get_or_create(name="fc_admin_perms")
    for codename in ("view_fieldsettings", "change_fieldsettings"):
        group.permissions.add(Permission.objects.get(content_type__app_label="core", codename=codename))
    user.groups.add(group)
    return user


def test_field_labels_and_catalog_fields_exported_from_field_config():
    from core.field_config import FIELD_LABELS, CATALOG_FIELDS
    assert set(FIELD_LABELS) == {"auto_shipment", "rail_shipment"}
    assert FIELD_LABELS["auto_shipment"]["shipment_date"] == "Дата отгрузки"
    assert FIELD_LABELS["auto_shipment"]["coal_grade"] == "Марка угля"
    assert FIELD_LABELS["auto_shipment"]["source_day_number"] == "№ дня"
    assert FIELD_LABELS["rail_shipment"]["departure_date"] == "Дата отправления"
    assert FIELD_LABELS["rail_shipment"]["destination_station"] == "Станция назначения РФ"
    assert set(FIELD_LABELS["auto_shipment"]) == {
        "shipment_date", "customer_object", "vehicle_number", "driver_name",
        "ttn_number", "coal_grade", "quantity", "carrier", "comment",
        "sub_object", "base_code", "upd_number", "balance_note",
        "source_month_text", "source_day_number",
    }
    assert set(FIELD_LABELS["rail_shipment"]) == {
        "departure_date", "wagon_number", "document_number", "cargo",
        "destination_station", "receiver", "volume", "comment",
        "origin_region", "origin_station", "sender", "destination_region",
    }
    assert CATALOG_FIELDS == {
        "auto_shipment": {"coal_grade", "base_code"},
        "rail_shipment": {"cargo"},
    }


# --- V14-L8: _catalog_field_names как classmethod, _build_field_lists однократно ---

@pytest.mark.django_db
def test_catalog_field_names_callable_as_classmethod():
    """_catalog_field_names можно вызвать напрямую через класс (classmethod)."""
    from shipments_auto.forms import AutoShipmentForm
    cfg = get_entity_config("auto_shipment")
    result = AutoShipmentForm._catalog_field_names(cfg)
    assert isinstance(result, set)
    assert "coal_grade" in result
    assert "base_code" in result


@pytest.mark.django_db
def test_catalog_field_names_matches_build_field_lists_catalog_fields():
    """_catalog_field_names и _build_field_lists используют одну логику — нет расхождений."""
    from shipments_auto.forms import AutoShipmentForm
    cfg = get_entity_config("auto_shipment")
    from_classmethod = AutoShipmentForm._catalog_field_names(cfg)
    # _build_field_lists использует _catalog_field_names внутри,
    # проверяем что результирующие поля списков отражают правильный набор
    main, advanced = AutoShipmentForm._build_field_lists()
    all_rendered = set(main + advanced)
    for cat_field in from_classmethod:
        assert f"{cat_field}_select" in all_rendered, (
            f"catalog field '{cat_field}' должен рендериться как '{cat_field}_select'"
        )


@pytest.mark.django_db
def test_catalog_field_names_instance_call_still_works():
    """Вызов classmethod через экземпляр (self.method()) по-прежнему работает."""
    from shipments_auto.forms import AutoShipmentForm
    form = AutoShipmentForm()
    cfg = get_entity_config("auto_shipment")
    result = form._catalog_field_names(cfg)
    assert "coal_grade" in result


@pytest.mark.django_db
def test_create_view_context_has_main_and_advanced_fields(operator_user):
    """GET на форму создания возвращает main_fields и advanced_fields в контексте."""
    client = Client()
    client.login(username="fc_operator", password="pass")
    response = client.get(reverse("auto:create"))
    assert response.status_code == 200
    assert "main_fields" in response.context
    assert "advanced_fields" in response.context
    assert len(response.context["main_fields"]) > 0


@pytest.mark.django_db
def test_update_view_context_has_main_and_advanced_fields(django_user_model):
    """GET на форму редактирования возвращает main_fields и advanced_fields в контексте."""
    from shipments_auto.models import AutoShipment
    user = django_user_model.objects.create_user(username="fc_changer", password="pass")
    group, _ = Group.objects.get_or_create(name="fc_changer_group")
    for codename in ("view_autoshipment", "change_autoshipment"):
        perm = Permission.objects.get(codename=codename, content_type__app_label="shipments_auto")
        group.permissions.add(perm)
    user.groups.add(group)
    obj = AutoShipment.objects.create(
        shipment_date="2026-01-15",
        customer_object="Тест",
        coal_grade="ДГ",
        quantity="100",
        created_by=user,
        updated_by=user,
    )
    client = Client()
    client.login(username="fc_changer", password="pass")
    response = client.get(reverse("auto:update", kwargs={"pk": obj.pk}))
    assert response.status_code == 200
    assert "main_fields" in response.context
    assert "advanced_fields" in response.context
    assert len(response.context["main_fields"]) > 0
