import pytest
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission

from audit.models import AuditLog
from .forms import UserCreateForm
from .models import User
from .permissions import GROUP_DESCRIPTIONS


def create_role_groups():
    for group_name in GROUP_DESCRIPTIONS:
        Group.objects.get_or_create(name=group_name)


def add_perms(user, perms):
    group, _ = Group.objects.get_or_create(name=f"{user.username}_perms")
    for app_label, codename in perms:
        group.permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
    user.groups.add(group)


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_user(username="mgmt_admin", password="pass")
    add_perms(user, [
        ("accounts", "view_user"),
        ("accounts", "add_user"),
        ("accounts", "change_user"),
    ])
    return user


@pytest.fixture
def viewer_user(django_user_model):
    user = django_user_model.objects.create_user(username="mgmt_viewer", password="pass")
    group, _ = Group.objects.get_or_create(name="viewer")
    user.groups.add(group)
    return user


@pytest.fixture
def target_user(django_user_model):
    user = django_user_model.objects.create_user(
        username="target_user", password="oldpass", first_name="Иван", last_name="Иванов"
    )
    return user


@pytest.mark.django_db
class TestUserList:
    def test_anonymous_redirects(self, client):
        response = client.get(reverse("accounts:list"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_gets_403(self, client, viewer_user):
        client.login(username="mgmt_viewer", password="pass")
        response = client.get(reverse("accounts:list"))
        assert response.status_code == 403

    def test_admin_can_view_list(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:list"))
        assert response.status_code == 200

    def test_list_shows_users(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:list"))
        assert "target_user" in response.content.decode()

    def test_list_hides_password_action_for_superuser(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="root_user",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")

        response = client.get(reverse("accounts:list"))

        content = response.content.decode()
        assert reverse("accounts:password", kwargs={"pk": protected.pk}) not in content

    def test_list_hides_edit_and_deactivate_actions_for_superuser(self, client, admin_user, django_user_model):
        """V17-LOW-7: у superuser не должно быть кнопок edit/deactivate (view отдаёт 403)."""
        protected = django_user_model.objects.create_superuser(
            username="root_user2",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")

        response = client.get(reverse("accounts:list"))

        content = response.content.decode()
        assert reverse("accounts:update", kwargs={"pk": protected.pk}) not in content
        assert reverse("accounts:deactivate", kwargs={"pk": protected.pk}) not in content
        assert reverse("accounts:activate", kwargs={"pk": protected.pk}) not in content

    def test_staff_without_permissions_gets_403(self, client, django_user_model):
        staff = django_user_model.objects.create_user(
            username="staff_mgmt", password="pass", is_staff=True
        )
        client.login(username="staff_mgmt", password="pass")
        response = client.get(reverse("accounts:list"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestUserCreate:
    def test_viewer_gets_403(self, client, viewer_user):
        client.login(username="mgmt_viewer", password="pass")
        response = client.get(reverse("accounts:create"))
        assert response.status_code == 403

    def test_admin_can_get_form(self, client, admin_user):
        create_role_groups()
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:create"))
        assert response.status_code == 200

    def test_create_form_shows_role_help(self, client, admin_user):
        create_role_groups()
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:create"))
        content = response.content.decode()

        for group_name, description in GROUP_DESCRIPTIONS.items():
            assert group_name in content
            assert description in content

    def test_admin_can_create_user(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:create"), {
            "username": "newuser",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "groups": [],
        })
        assert response.status_code == 302
        assert User.objects.filter(username="newuser").exists()

    def test_create_writes_audit_log_without_password(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")

        client.post(reverse("accounts:create"), {
            "username": "audit_newuser",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "groups": [],
        })

        created = User.objects.get(username="audit_newuser")
        entry = AuditLog.objects.get(entity_type=AuditLog.ENTITY_USER, entity_id=created.pk)
        assert entry.action == AuditLog.ACTION_CREATE
        assert entry.user == admin_user
        assert entry.new_values["username"] == "audit_newuser"
        assert "password" not in entry.new_values
        assert "StrongPass123!" not in str(entry.new_values)

    def test_create_with_group(self, client, admin_user):
        group, _ = Group.objects.get_or_create(name="viewer")
        client.login(username="mgmt_admin", password="pass")
        client.post(reverse("accounts:create"), {
            "username": "grouped_user",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "groups": [group.pk],
        })
        user = User.objects.get(username="grouped_user")
        assert user.groups.filter(name="viewer").exists()

    def test_password_mismatch_shows_error(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:create"), {
            "username": "badpw",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "WrongPass456!",
            "groups": [],
        })
        assert response.status_code == 200
        assert not User.objects.filter(username="badpw").exists()

    def test_create_without_group_shows_warning(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:create"), {
            "username": "nogroup_user",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "groups": [],
        }, follow=True)
        content = response.content.decode()
        assert "не состоит ни в одной группе" in content

    def test_create_with_group_no_warning(self, client, admin_user):
        group, _ = Group.objects.get_or_create(name="viewer")
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:create"), {
            "username": "withgroup_user",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "groups": [group.pk],
        }, follow=True)
        content = response.content.decode()
        assert "не состоит ни в одной группе" not in content

    def test_created_user_can_login_with_set_password(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")
        client.post(reverse("accounts:create"), {
            "username": "logintest",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "StrongPass123!",
            "password2": "StrongPass123!",
            "groups": [],
        })
        client2 = Client()
        logged_in = client2.login(username="logintest", password="StrongPass123!")
        assert logged_in

    def test_password_similar_to_username_shows_error(self, client, admin_user):
        """V19-MED-5: UserAttributeSimilarityValidator должен реально срабатывать."""
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:create"), {
            "username": "ivanov.petrov",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "password1": "Ivanov.Petrov2026",
            "password2": "Ivanov.Petrov2026",
            "groups": [],
        })
        assert response.status_code == 200
        assert not User.objects.filter(username="ivanov.petrov").exists()
        form = response.context["form"]
        assert not form.is_valid()
        assert "password1" in form.errors


@pytest.mark.django_db
class TestUserUpdate:
    def test_viewer_gets_403(self, client, viewer_user, target_user):
        client.login(username="mgmt_viewer", password="pass")
        response = client.get(reverse("accounts:update", kwargs={"pk": target_user.pk}))
        assert response.status_code == 403

    def test_admin_can_get_form(self, client, admin_user, target_user):
        create_role_groups()
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:update", kwargs={"pk": target_user.pk}))
        assert response.status_code == 200

    def test_update_form_shows_role_help(self, client, admin_user, target_user):
        create_role_groups()
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:update", kwargs={"pk": target_user.pk}))
        content = response.content.decode()

        for group_name, description in GROUP_DESCRIPTIONS.items():
            assert group_name in content
            assert description in content

    def test_admin_can_update_user(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:update", kwargs={"pk": target_user.pk}), {
            "username": "target_user",
            "first_name": "Пётр",
            "last_name": "Петров",
            "email": "petr@example.com",
            "is_active": True,
            "groups": [],
        })
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.first_name == "Пётр"
        assert target_user.email == "petr@example.com"

    def test_update_writes_audit_log(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")

        client.post(reverse("accounts:update", kwargs={"pk": target_user.pk}), {
            "username": "target_user",
            "first_name": "Пётр",
            "last_name": "Петров",
            "email": "petr@example.com",
            "is_active": True,
            "groups": [],
        })

        entry = AuditLog.objects.get(entity_type=AuditLog.ENTITY_USER, entity_id=target_user.pk)
        assert entry.action == AuditLog.ACTION_UPDATE
        assert entry.old_values["first_name"] == "Иван"
        assert entry.new_values["first_name"] == "Пётр"

    def test_update_changes_groups(self, client, admin_user, target_user):
        group, _ = Group.objects.get_or_create(name="operator")
        client.login(username="mgmt_admin", password="pass")
        client.post(reverse("accounts:update", kwargs={"pk": target_user.pk}), {
            "username": "target_user",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": True,
            "groups": [group.pk],
        })
        target_user.refresh_from_db()
        assert target_user.groups.filter(name="operator").exists()

    def test_cannot_get_edit_form_for_superuser(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="protected_edit_get",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:update", kwargs={"pk": protected.pk}))
        assert response.status_code == 403

    def test_cannot_edit_superuser(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="protected_edit_post",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:update", kwargs={"pk": protected.pk}), {
            "username": "protected_edit_post",
            "first_name": "Попытка",
            "last_name": "",
            "email": "",
            "is_active": True,
            "groups": [],
        })
        assert response.status_code == 403
        protected.refresh_from_db()
        assert protected.first_name == ""

    def test_update_without_groups_shows_warning(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(
            reverse("accounts:update", kwargs={"pk": target_user.pk}),
            {
                "username": "target_user",
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": True,
                "groups": [],
            },
            follow=True,
        )
        assert "не состоит ни в одной группе" in response.content.decode()

    def test_update_with_group_no_warning(self, client, admin_user, target_user):
        group, _ = Group.objects.get_or_create(name="viewer")
        client.login(username="mgmt_admin", password="pass")
        response = client.post(
            reverse("accounts:update", kwargs={"pk": target_user.pk}),
            {
                "username": "target_user",
                "first_name": "",
                "last_name": "",
                "email": "",
                "is_active": True,
                "groups": [group.pk],
            },
            follow=True,
        )
        assert "не состоит ни в одной группе" not in response.content.decode()


@pytest.mark.django_db
class TestUserFormGroups:
    def test_known_groups_are_ordered_before_unknown_groups(self):
        Group.objects.create(name="custom")
        for group_name in reversed(GROUP_DESCRIPTIONS):
            Group.objects.create(name=group_name)

        form = UserCreateForm()
        group_names = [option["group"].name for option in form.group_options]

        assert group_names[:5] == list(GROUP_DESCRIPTIONS)
        assert group_names[5:] == ["custom"]


@pytest.mark.django_db
class TestUserPassword:
    def test_viewer_gets_403(self, client, viewer_user, target_user):
        client.login(username="mgmt_viewer", password="pass")
        response = client.get(reverse("accounts:password", kwargs={"pk": target_user.pk}))
        assert response.status_code == 403

    def test_admin_can_get_form(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.get(reverse("accounts:password", kwargs={"pk": target_user.pk}))
        assert response.status_code == 200

    def test_admin_can_change_password(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:password", kwargs={"pk": target_user.pk}), {
            "password1": "NewStrongPass99!",
            "password2": "NewStrongPass99!",
        })
        assert response.status_code == 302
        client2 = Client()
        assert client2.login(username="target_user", password="NewStrongPass99!")

    def test_password_change_writes_audit_log_without_password(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")

        client.post(reverse("accounts:password", kwargs={"pk": target_user.pk}), {
            "password1": "NewStrongPass99!",
            "password2": "NewStrongPass99!",
        })

        entry = AuditLog.objects.get(entity_type=AuditLog.ENTITY_USER, entity_id=target_user.pk)
        assert entry.action == AuditLog.ACTION_UPDATE
        assert entry.new_values == {"username": "target_user", "password_changed": True}
        assert "NewStrongPass99!" not in str(entry.new_values)

    def test_password_mismatch_shows_error(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:password", kwargs={"pk": target_user.pk}), {
            "password1": "NewStrongPass99!",
            "password2": "WrongPass000!",
        })
        assert response.status_code == 200
        client2 = Client()
        assert not client2.login(username="target_user", password="NewStrongPass99!")

    def test_cannot_get_password_form_for_superuser(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="protected_get",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")

        response = client.get(reverse("accounts:password", kwargs={"pk": protected.pk}))

        assert response.status_code == 403

    def test_cannot_change_superuser_password(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="protected_post",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")

        response = client.post(reverse("accounts:password", kwargs={"pk": protected.pk}), {
            "password1": "NewStrongPass99!",
            "password2": "NewStrongPass99!",
        })

        protected.refresh_from_db()
        assert response.status_code == 403
        assert client.login(username="protected_post", password="RootPass123!")
        assert not client.login(username="protected_post", password="NewStrongPass99!")
        assert not AuditLog.objects.filter(entity_type=AuditLog.ENTITY_USER, entity_id=protected.pk).exists()

    def test_password_similar_to_username_shows_error(self, client, admin_user, target_user):
        """V19-MED-5: смена пароля тоже должна учитывать атрибуты пользователя."""
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:password", kwargs={"pk": target_user.pk}), {
            "password1": "Target_User2026",
            "password2": "Target_User2026",
        })
        assert response.status_code == 200
        form = response.context["form"]
        assert not form.is_valid()
        assert "password1" in form.errors
        client2 = Client()
        assert not client2.login(username="target_user", password="Target_User2026")


@pytest.mark.django_db
class TestUserDeactivate:
    def test_viewer_gets_403(self, client, viewer_user, target_user):
        client.login(username="mgmt_viewer", password="pass")
        response = client.post(reverse("accounts:deactivate", kwargs={"pk": target_user.pk}))
        assert response.status_code == 403

    def test_admin_can_deactivate(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:deactivate", kwargs={"pk": target_user.pk}))
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.is_active is False

    def test_deactivate_writes_audit_log(self, client, admin_user, target_user):
        client.login(username="mgmt_admin", password="pass")

        client.post(reverse("accounts:deactivate", kwargs={"pk": target_user.pk}))

        entry = AuditLog.objects.get(entity_type=AuditLog.ENTITY_USER, entity_id=target_user.pk)
        assert entry.action == AuditLog.ACTION_UPDATE
        assert entry.old_values["is_active"] is True
        assert entry.new_values["is_active"] is False

    def test_deactivated_user_not_deleted(self, client, admin_user, target_user):
        pk = target_user.pk
        client.login(username="mgmt_admin", password="pass")
        client.post(reverse("accounts:deactivate", kwargs={"pk": pk}))
        assert User.objects.filter(pk=pk).exists()

    def test_cannot_deactivate_self(self, client, admin_user):
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:deactivate", kwargs={"pk": admin_user.pk}))
        assert response.status_code == 302
        admin_user.refresh_from_db()
        assert admin_user.is_active is True

    def test_admin_can_activate(self, client, admin_user, target_user):
        target_user.is_active = False
        target_user.save()
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:activate", kwargs={"pk": target_user.pk}))
        assert response.status_code == 302
        target_user.refresh_from_db()
        assert target_user.is_active is True

    def test_activate_writes_audit_log(self, client, admin_user, target_user):
        target_user.is_active = False
        target_user.save()
        client.login(username="mgmt_admin", password="pass")

        client.post(reverse("accounts:activate", kwargs={"pk": target_user.pk}))

        entry = AuditLog.objects.get(entity_type=AuditLog.ENTITY_USER, entity_id=target_user.pk)
        assert entry.action == AuditLog.ACTION_UPDATE
        assert entry.old_values["is_active"] is False
        assert entry.new_values["is_active"] is True

    def test_cannot_deactivate_superuser(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="protected_deactivate",
            password="RootPass123!",
        )
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:deactivate", kwargs={"pk": protected.pk}))
        assert response.status_code == 403
        protected.refresh_from_db()
        assert protected.is_active is True

    def test_cannot_activate_superuser(self, client, admin_user, django_user_model):
        protected = django_user_model.objects.create_superuser(
            username="protected_activate",
            password="RootPass123!",
        )
        protected.is_active = False
        protected.save(update_fields=["is_active"])
        client.login(username="mgmt_admin", password="pass")
        response = client.post(reverse("accounts:activate", kwargs={"pk": protected.pk}))
        assert response.status_code == 403
        protected.refresh_from_db()
        assert protected.is_active is False
