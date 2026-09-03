import pytest
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def viewer_user(django_user_model):
    user = django_user_model.objects.create_user(username="viewer", password="pass")
    group, _ = Group.objects.get_or_create(name="viewer")
    user.groups.add(group)
    return user


@pytest.fixture
def operator_user(django_user_model):
    user = django_user_model.objects.create_user(username="operator", password="pass")
    group, _ = Group.objects.get_or_create(name="operator")
    for codename in ("view_autoshipment", "add_autoshipment"):
        perm = Permission.objects.get(
            codename=codename,
            content_type__app_label="shipments_auto",
        )
        group.permissions.add(perm)
    user.groups.add(group)
    return user


@pytest.mark.django_db
class TestAnonymousAccess:
    def test_index_redirects_to_login(self, client):
        response = client.get(reverse("index"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_login_page_accessible(self, client):
        response = client.get(reverse("login"))
        assert response.status_code == 200

    def test_admin_redirects_to_login(self, client):
        response = client.get("/admin/")
        assert response.status_code == 302


@pytest.mark.django_db
class TestViewerPermissions:
    def test_viewer_can_access_index(self, client, viewer_user):
        client.login(username="viewer", password="pass")
        response = client.get(reverse("index"))
        assert response.status_code == 200

    def test_viewer_has_no_add_permission(self, viewer_user):
        assert not viewer_user.has_perm("shipments_auto.add_autoshipment")

    def test_viewer_has_no_change_permission(self, viewer_user):
        assert not viewer_user.has_perm("shipments_auto.change_autoshipment")

    def test_viewer_has_no_export_permission(self, viewer_user):
        assert not viewer_user.has_perm("shipments_auto.export_excel")
        assert not viewer_user.has_perm("shipments_rail.export_excel")

    def test_viewer_index_no_add_link(self, client, viewer_user):
        client.login(username="viewer", password="pass")
        response = client.get(reverse("index"))
        assert "Добавить отгрузку" not in response.content.decode()

    def test_viewer_index_no_export_link(self, client, viewer_user):
        client.login(username="viewer", password="pass")
        response = client.get(reverse("index"))
        assert "Экспорт в Excel" not in response.content.decode()


@pytest.mark.django_db
class TestOperatorPermissions:
    def test_operator_has_add_permission(self, operator_user):
        assert operator_user.has_perm("shipments_auto.add_autoshipment")

    def test_operator_index_shows_add_link(self, client, operator_user):
        client.login(username="operator", password="pass")
        response = client.get(reverse("auto:list"))
        assert "/auto/new/" in response.content.decode()


@pytest.mark.django_db
class TestSeedGroups:
    def test_seed_groups_creates_groups(self, django_user_model):
        from django.core.management import call_command
        call_command("seed_groups", verbosity=0)
        assert Group.objects.filter(name="viewer").exists()
        assert Group.objects.filter(name="excel").exists()
        assert Group.objects.filter(name="operator").exists()
        assert Group.objects.filter(name="documents").exists()
        assert Group.objects.filter(name="admin").exists()

    def test_seed_groups_is_idempotent(self, django_user_model):
        from django.core.management import call_command
        call_command("seed_groups", verbosity=0)
        call_command("seed_groups", verbosity=0)
        assert Group.objects.filter(name="viewer").count() == 1
        assert Group.objects.filter(name="excel").count() == 1
        assert Group.objects.filter(name="operator").count() == 1

    def test_seed_groups_preserves_manual_extra_permissions(self, django_user_model):
        from django.core.management import call_command
        call_command("seed_groups", verbosity=0)
        group = Group.objects.get(name="viewer")
        extra_perm = Permission.objects.get(
            codename="export_excel",
            content_type__app_label="shipments_auto",
        )
        group.permissions.add(extra_perm)

        call_command("seed_groups", verbosity=0)

        assert group.permissions.filter(pk=extra_perm.pk).exists()

    def test_seed_groups_assigns_permissions(self, django_user_model):
        from django.core.management import call_command
        call_command("seed_groups", verbosity=0)

        viewer_group = Group.objects.get(name="viewer")
        viewer_perms = set(viewer_group.permissions.values_list("content_type__app_label", "codename"))
        assert ("shipments_auto", "view_autoshipment") in viewer_perms
        assert ("shipments_rail", "view_railshipment") in viewer_perms
        assert ("shipments_auto", "export_excel") not in viewer_perms
        assert ("shipments_rail", "export_excel") not in viewer_perms
        assert ("shipments_auto", "add_autoshipment") not in viewer_perms

        excel_group = Group.objects.get(name="excel")
        excel_perms = set(excel_group.permissions.values_list("content_type__app_label", "codename"))
        assert ("shipments_auto", "view_autoshipment") in excel_perms
        assert ("shipments_auto", "export_excel") in excel_perms
        assert ("shipments_rail", "view_railshipment") in excel_perms
        assert ("shipments_rail", "export_excel") in excel_perms

        operator_group = Group.objects.get(name="operator")
        operator_perms = set(operator_group.permissions.values_list("content_type__app_label", "codename"))
        assert ("shipments_auto", "view_autoshipment") in operator_perms
        assert ("shipments_auto", "add_autoshipment") in operator_perms
        assert ("shipments_auto", "change_autoshipment") in operator_perms
        assert ("shipments_auto", "delete_autoshipment") in operator_perms
        assert ("shipments_rail", "view_railshipment") in operator_perms
        assert ("shipments_rail", "add_railshipment") in operator_perms
        assert ("shipments_rail", "change_railshipment") in operator_perms
        assert ("shipments_rail", "delete_railshipment") in operator_perms
        assert ("shipments_auto", "export_excel") not in operator_perms
        assert ("shipments_rail", "export_excel") not in operator_perms

        documents_group = Group.objects.get(name="documents")
        documents_perms = set(documents_group.permissions.values_list("content_type__app_label", "codename"))
        assert ("shipments_auto", "view_autoshipment") in documents_perms
        assert ("shipments_rail", "view_railshipment") in documents_perms
        assert ("documents", "add_shipmentdocument") in documents_perms
        assert ("documents", "upload_autoshipment_documents") in documents_perms
        assert ("documents", "upload_railshipment_documents") in documents_perms
        assert ("documents", "change_autoshipment_documents") in documents_perms
        assert ("documents", "change_railshipment_documents") in documents_perms
        assert ("documents", "delete_autoshipment_documents") in documents_perms
        assert ("documents", "delete_railshipment_documents") in documents_perms
        assert ("shipments_auto", "export_excel") not in documents_perms
        assert ("shipments_rail", "export_excel") not in documents_perms

        admin_group = Group.objects.get(name="admin")
        admin_perms = set(admin_group.permissions.values_list("content_type__app_label", "codename"))
        assert ("shipments_auto", "export_excel") in admin_perms
        assert ("shipments_rail", "export_excel") in admin_perms
        assert ("accounts", "add_user") in admin_perms
        assert ("accounts", "change_user") in admin_perms
        assert ("audit", "view_auditlog") in admin_perms
        assert ("imports", "import_shipments") in admin_perms
        assert ("imports", "view_importlog") in admin_perms
        assert ("catalogs", "view_catalogvalue") in admin_perms
        assert ("catalogs", "change_catalogvalue") in admin_perms
        assert ("core", "view_fieldsettings") in admin_perms
        assert ("core", "change_fieldsettings") in admin_perms
        assert ("core", "view_system_status") in admin_perms
        assert ("core", "change_system_mode") in admin_perms
        assert ("core", "recover_system_operations") in admin_perms
        assert ("core", "run_backup") in admin_perms
        assert ("core", "run_restore") in admin_perms

        for group_name in ("viewer", "excel", "operator", "documents"):
            perms = set(
                Group.objects.get(name=group_name).permissions.values_list(
                    "content_type__app_label", "codename"
                )
            )
            assert ("core", "view_system_status") not in perms
            assert ("imports", "import_shipments") not in perms
            assert ("audit", "view_auditlog") not in perms


@pytest.mark.django_db
def test_upload_documents_permission_removed():
    from django.contrib.auth.models import Permission
    assert Permission.objects.filter(codename="export_excel").exists()
    assert not Permission.objects.filter(codename="upload_documents").exists()
