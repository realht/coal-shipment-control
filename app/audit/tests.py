import pytest
from django.test import Client
from django.test import RequestFactory
from django.urls import reverse
from django.contrib.auth.models import Group, Permission

from .models import AuditLog
from .services import write_audit_log


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_user(username="audit_admin", password="pass")
    group, _ = Group.objects.get_or_create(name="audit_admin_perms")
    group.permissions.add(Permission.objects.get(content_type__app_label="audit", codename="view_auditlog"))
    user.groups.add(group)
    return user


@pytest.fixture
def viewer_user(django_user_model):
    user = django_user_model.objects.create_user(username="audit_viewer", password="pass")
    group, _ = Group.objects.get_or_create(name="viewer")
    user.groups.add(group)
    return user


@pytest.fixture
def log_entry(admin_user):
    return AuditLog.objects.create(
        entity_type=AuditLog.ENTITY_AUTO,
        entity_id=42,
        action=AuditLog.ACTION_CREATE,
        new_values={"customer_object": "Объект Х", "coal_grade": "ДГ"},
        user=admin_user,
        ip_address="127.0.0.1",
    )


@pytest.mark.django_db
class TestAuditLogList:
    def test_anonymous_redirects(self, client):
        response = client.get(reverse("audit:list"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_gets_403(self, client, viewer_user):
        client.login(username="audit_viewer", password="pass")
        response = client.get(reverse("audit:list"))
        assert response.status_code == 403

    def test_admin_can_view_list(self, client, admin_user):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list"))
        assert response.status_code == 200

    def test_list_shows_log_entry(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list"))
        assert response.status_code == 200
        assert "audit_admin" in response.content.decode()

    def test_filter_by_entity_type(self, client, admin_user, log_entry):
        AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_RAIL,
            entity_id=7,
            action=AuditLog.ACTION_UPDATE,
            user=admin_user,
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?entity_type=auto_shipment")
        logs = list(response.context["logs"])
        assert all(l.entity_type == AuditLog.ENTITY_AUTO for l in logs)

    def test_filter_by_action(self, client, admin_user, log_entry):
        AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_AUTO,
            entity_id=42,
            action=AuditLog.ACTION_DELETE,
            user=admin_user,
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?action=create")
        logs = list(response.context["logs"])
        assert all(l.action == AuditLog.ACTION_CREATE for l in logs)

    def test_filter_by_source(self, client, admin_user, log_entry):
        AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_RAIL,
            entity_id=7,
            action=AuditLog.ACTION_CREATE,
            source=AuditLog.SOURCE_IMPORT,
            user=admin_user,
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?source=import")

        logs = list(response.context["logs"])
        assert logs
        assert all(l.source == AuditLog.SOURCE_IMPORT for l in logs)

    def test_filter_by_user(self, client, admin_user, viewer_user, log_entry):
        AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_RAIL,
            entity_id=1,
            action=AuditLog.ACTION_UPDATE,
            user=viewer_user,
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + f"?user_id={admin_user.pk}")
        logs = list(response.context["logs"])
        assert all(l.user_id == admin_user.pk for l in logs)

    def test_invalid_user_id_is_ignored_without_500(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?user_id=abc")

        assert response.status_code == 200
        assert response.context["user_id"] == "abc"
        assert log_entry.pk in [l.pk for l in response.context["logs"]]

    def test_filter_by_date(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2099-01-01&date_to=2099-12-31")
        logs = list(response.context["logs"])
        assert len(logs) == 0

    def _set_created_at(self, log, aware_dt):
        # created_at — auto_now_add, обходим через .update()
        AuditLog.objects.filter(pk=log.pk).update(created_at=aware_dt)

    def test_filter_by_date_finds_entry_in_range(self, client, admin_user, log_entry):
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2026, 3, 15, 12, 0), tz))
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2026-03-01&date_to=2026-03-31")
        logs = list(response.context["logs"])
        assert log_entry.pk in [l.pk for l in logs]

    def test_filter_by_date_includes_end_of_day(self, client, admin_user, log_entry):
        # Запись в конце дня (локально) должна попасть в фильтр date_to=<тот же день>.
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2026, 3, 15, 23, 59), tz))
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2026-03-15&date_to=2026-03-15")
        logs = list(response.context["logs"])
        assert log_entry.pk in [l.pk for l in logs]

    def test_filter_by_date_before_range_excluded(self, client, admin_user, log_entry):
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2026, 3, 14, 23, 59), tz))
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2026-03-15&date_to=2026-03-15")
        logs = list(response.context["logs"])
        assert log_entry.pk not in [l.pk for l in logs]

    def test_invalid_date_does_not_500(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2099-13-40")
        assert response.status_code == 200
        assert response.context["date_from_invalid"] is True
        # Невалидная граница игнорируется — запись остаётся видимой.
        assert log_entry.pk in [l.pk for l in response.context["logs"]]

    def test_invalid_date_non_numeric_does_not_500(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_to=abc")
        assert response.status_code == 200
        assert response.context["date_to_invalid"] is True

    def test_user_filter_includes_inactive_users(self, client, admin_user, django_user_model):
        """V17-LOW-11: деактивированный сотрудник доступен в фильтре «Пользователь» с пометкой."""
        inactive = django_user_model.objects.create_user(
            username="fired_worker", password="pass", is_active=False
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list"))
        users = list(response.context["users"])
        assert inactive in users
        content = response.content.decode()
        assert "fired_worker" in content
        assert "(неактивен)" in content

    def test_pagination(self, client, admin_user):
        for i in range(60):
            AuditLog.objects.create(
                entity_type=AuditLog.ENTITY_AUTO,
                entity_id=i,
                action=AuditLog.ACTION_CREATE,
                user=admin_user,
            )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list"))
        assert response.context["is_paginated"]
        assert len(response.context["logs"]) == 50


@pytest.mark.django_db
class TestAuditLogDetail:
    def test_anonymous_redirects(self, client, log_entry):
        response = client.get(reverse("audit:detail", kwargs={"pk": log_entry.pk}))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_viewer_gets_403(self, client, viewer_user, log_entry):
        client.login(username="audit_viewer", password="pass")
        response = client.get(reverse("audit:detail", kwargs={"pk": log_entry.pk}))
        assert response.status_code == 403

    def test_admin_can_view_detail(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:detail", kwargs={"pk": log_entry.pk}))
        assert response.status_code == 200

    def test_detail_shows_new_values(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:detail", kwargs={"pk": log_entry.pk}))
        content = response.content.decode()
        assert "Объект Х" in content
        assert "ДГ" in content

    def test_detail_shows_old_values(self, client, admin_user):
        entry = AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_AUTO,
            entity_id=10,
            action=AuditLog.ACTION_UPDATE,
            old_values={"customer_object": "Старый"},
            new_values={"customer_object": "Новый"},
            user=admin_user,
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:detail", kwargs={"pk": entry.pk}))
        content = response.content.decode()
        assert "Старый" in content
        assert "Новый" in content

    def test_detail_shows_source(self, client, admin_user):
        entry = AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_SYSTEM,
            entity_id=1,
            action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
            source=AuditLog.SOURCE_SCRIPT,
            user=admin_user,
        )
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:detail", kwargs={"pk": entry.pk}))

        assert "Script" in response.content.decode()


@pytest.mark.django_db
class TestAuditLogService:
    def test_system_backup_restore_events_can_be_written(self, admin_user):
        write_audit_log(
            entity_type=AuditLog.ENTITY_BACKUP,
            entity_id=10,
            action=AuditLog.ACTION_BACKUP_SUCCESS,
            user=admin_user,
            source=AuditLog.SOURCE_SCHEDULER,
            new_values={"status": "success"},
        )
        write_audit_log(
            entity_type=AuditLog.ENTITY_RESTORE,
            entity_id=11,
            action=AuditLog.ACTION_RESTORE_ERROR,
            user=admin_user,
            source=AuditLog.SOURCE_RESTORE,
            new_values={"status": "error"},
        )

        assert AuditLog.objects.filter(entity_type=AuditLog.ENTITY_BACKUP, action=AuditLog.ACTION_BACKUP_SUCCESS).exists()
        assert AuditLog.objects.filter(entity_type=AuditLog.ENTITY_RESTORE, action=AuditLog.ACTION_RESTORE_ERROR).exists()

    def test_request_logging_uses_trusted_proxy_ip(self, settings, admin_user):
        settings.TRUSTED_PROXIES = ["10.0.0.1"]
        request = RequestFactory().get(
            "/",
            HTTP_X_FORWARDED_FOR="203.0.113.10, 10.0.0.1",
            REMOTE_ADDR="10.0.0.1",
        )
        request.user = admin_user

        entry = write_audit_log(
            entity_type=AuditLog.ENTITY_SYSTEM,
            entity_id=1,
            action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
            request=request,
            source=AuditLog.SOURCE_UI,
        )

        assert entry.ip_address == "203.0.113.10"

    def test_write_failure_is_logged_and_not_raised(self, monkeypatch, caplog, admin_user):
        def fail_create(*args, **kwargs):
            raise RuntimeError("audit database is unavailable")

        monkeypatch.setattr(AuditLog.objects, "create", fail_create)

        with caplog.at_level("ERROR", logger="audit.services"):
            entry = write_audit_log(
                entity_type=AuditLog.ENTITY_SYSTEM,
                entity_id=1,
                action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
                user=admin_user,
            )

        assert entry is None
        assert "Failed to write audit log" in caplog.text


@pytest.mark.django_db
class TestAuditLogDateFilter:
    """Regression tests for V14-L2: parse_top_level_date_bound replaces _parse_date_bound."""

    def _set_created_at(self, log, aware_dt):
        AuditLog.objects.filter(pk=log.pk).update(created_at=aware_dt)

    def test_date_from_and_date_to_filter_in_range(self, client, admin_user, log_entry):
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2024, 6, 15, 10, 0), tz))
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2024-01-01&date_to=2024-12-31")
        assert response.status_code == 200
        logs = list(response.context["logs"])
        assert log_entry.pk in [l.pk for l in logs]

    def test_date_from_and_date_to_exclude_out_of_range(self, client, admin_user, log_entry):
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2023, 12, 31, 23, 59), tz))
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=2024-01-01&date_to=2024-12-31")
        assert response.status_code == 200
        logs = list(response.context["logs"])
        assert log_entry.pk not in [l.pk for l in logs]

    def test_invalid_date_from_no_500_and_sets_flag(self, client, admin_user, log_entry):
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_from=notadate")
        assert response.status_code == 200
        assert response.context["date_from_invalid"] is True
        # Invalid bound is ignored — existing entries still visible
        assert log_entry.pk in [l.pk for l in response.context["logs"]]

    def test_valid_date_to_only_filters_correctly(self, client, admin_user, log_entry):
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2025, 6, 1, 8, 0), tz))
        client.login(username="audit_admin", password="pass")
        # date_to=2025-06-30 should include the entry set on 2025-06-01
        response = client.get(reverse("audit:list") + "?date_to=2025-06-30")
        assert response.status_code == 200
        logs = list(response.context["logs"])
        assert log_entry.pk in [l.pk for l in logs]

    def test_valid_date_to_only_excludes_after_range(self, client, admin_user, log_entry):
        from datetime import datetime
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        self._set_created_at(log_entry, timezone.make_aware(datetime(2025, 7, 1, 0, 0), tz))
        client.login(username="audit_admin", password="pass")
        response = client.get(reverse("audit:list") + "?date_to=2025-06-30")
        assert response.status_code == 200
        logs = list(response.context["logs"])
        assert log_entry.pk not in [l.pk for l in logs]


@pytest.mark.django_db
class TestAuditLogStaffAccess:
    def test_staff_without_permissions_gets_403_on_list(self, client, django_user_model):
        staff = django_user_model.objects.create_user(
            username="staff_user", password="pass", is_staff=True
        )
        client.login(username="staff_user", password="pass")
        response = client.get(reverse("audit:list"))
        assert response.status_code == 403

    def test_staff_without_permissions_gets_403_on_detail(self, client, django_user_model, admin_user):
        staff = django_user_model.objects.create_user(
            username="staff_user2", password="pass", is_staff=True
        )
        entry = AuditLog.objects.create(
            entity_type=AuditLog.ENTITY_RAIL,
            entity_id=99,
            action=AuditLog.ACTION_DELETE,
            user=admin_user,
        )
        client.login(username="staff_user2", password="pass")
        response = client.get(reverse("audit:detail", kwargs={"pk": entry.pk}))
        assert response.status_code == 403


@pytest.mark.django_db
def test_axes_lockout_signal_writes_audit_log():
    from axes.signals import user_locked_out

    import audit.signals  # noqa: F401  (регистрирует ресивер)

    request = RequestFactory().post("/admin/login/")
    request.META["REMOTE_ADDR"] = "203.0.113.5"

    user_locked_out.send(
        "axes",
        request=request,
        username="victim",
        ip_address="203.0.113.5",
    )

    entry = AuditLog.objects.filter(action=AuditLog.ACTION_AUTH_LOCKOUT).get()
    assert entry.entity_type == AuditLog.ENTITY_USER
    assert entry.new_values == {"username": "victim", "ip_address": "203.0.113.5"}
    assert entry.ip_address == "203.0.113.5"
