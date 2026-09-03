import gzip
import hashlib
import io
import json
import logging
import shutil
import sqlite3
import tarfile
from datetime import datetime, time, timedelta
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from shipments_auto.models import AutoShipment

from .models import BackupRun, BackupSchedule, RestoreRun, SystemState
from .system_ops import (
    _apply_retention,
    _claim_next_queued_operation,
    _create_uploads_archive,
    _dump_database,
    _restore_database,
    _restore_sqlite_database,
    _restore_version_preflight,
    _safe_extract_tar,
    _sha256_file,
    _touch_scheduler_heartbeat,
    _uploads_inventory,
    _write_system_audit,
    can_view_system_status,
    create_backup,
    get_system_state_readonly,
    has_active_operation,
    recover_interrupted_restore,
    recover_stale_running_operations_on_scheduler_start,
    restore_backup,
    run_scheduler_tick,
    scan_backup_manifests,
    set_system_mode,
    _swap_staging_to_media,
)
from imports.models import ImportLog, ImportRowResult
from documents.models import ShipmentDocument


class ButtonStateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons = []
        self._current_attrs = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self._current_attrs = dict(attrs)
            self._current_text = []

    def handle_data(self, data):
        if self._current_attrs is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag == "button" and self._current_attrs is not None:
            self.buttons.append(("".join(self._current_text).strip(), self._current_attrs))
            self._current_attrs = None
            self._current_text = []


def button_is_disabled(html, text):
    parser = ButtonStateParser()
    parser.feed(html)
    for label, attrs in parser.buttons:
        if label == text:
            return "disabled" in attrs
    raise AssertionError(f"Button not found: {text}")


class TestSafeExtractTar:
    def _write_archive(self, path, members):
        with tarfile.open(path, "w:gz") as tar:
            for member, data in members:
                tar.addfile(member, io.BytesIO(data) if data is not None else None)

    def _file_member(self, name, data):
        member = tarfile.TarInfo(name)
        member.size = len(data)
        return member, data

    def _dir_member(self, name):
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE
        return member, None

    def test_safe_extract_tar_extracts_regular_files_and_directories(self, tmp_path):
        archive = tmp_path / "uploads.tar.gz"
        target = tmp_path / "media"
        self._write_archive(
            archive,
            [
                self._dir_member("docs"),
                self._file_member("docs/report.txt", b"ok"),
            ],
        )

        _safe_extract_tar(archive, target)

        assert (target / "docs").is_dir()
        assert (target / "docs" / "report.txt").read_bytes() == b"ok"

    def test_safe_extract_tar_rejects_path_traversal(self, tmp_path):
        archive = tmp_path / "uploads.tar.gz"
        target = tmp_path / "media"
        outside = tmp_path / "outside.txt"
        self._write_archive(archive, [self._file_member("../outside.txt", b"owned")])

        with pytest.raises(RuntimeError, match="Unsafe path in archive"):
            _safe_extract_tar(archive, target)

        assert not outside.exists()

    def test_safe_extract_tar_rejects_symlink_members_before_writing_through_them(self, tmp_path):
        archive = tmp_path / "uploads.tar.gz"
        target = tmp_path / "media"
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        self._write_archive(
            archive,
            [
                (link, None),
                self._file_member("link/owned.txt", b"owned"),
            ],
        )

        with pytest.raises(RuntimeError, match="Unsafe tar member type"):
            _safe_extract_tar(archive, target)

        assert not (outside_dir / "owned.txt").exists()

    def test_safe_extract_tar_rejects_hardlink_members(self, tmp_path):
        archive = tmp_path / "uploads.tar.gz"
        target = tmp_path / "media"
        hardlink = tarfile.TarInfo("hardlink")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "docs/report.txt"
        self._write_archive(archive, [(hardlink, None)])

        with pytest.raises(RuntimeError, match="Unsafe tar member type"):
            _safe_extract_tar(archive, target)

    @pytest.mark.parametrize("member_type", [tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE])
    def test_safe_extract_tar_rejects_special_file_members(self, tmp_path, member_type):
        archive = tmp_path / "uploads.tar.gz"
        target = tmp_path / "media"
        member = tarfile.TarInfo("special")
        member.type = member_type
        self._write_archive(archive, [(member, None)])

        with pytest.raises(RuntimeError, match="Unsafe tar member type"):
            _safe_extract_tar(archive, target)


@pytest.fixture
def admin_user(django_user_model):
    user = django_user_model.objects.create_user(username="system_admin", password="pass", is_staff=True)
    group, _ = Group.objects.get_or_create(name="system_admin_perms")
    for codename in (
        "view_system_status",
        "change_system_mode",
        "recover_system_operations",
        "run_backup",
        "run_restore",
    ):
        group.permissions.add(Permission.objects.get(codename=codename, content_type__app_label="core"))
    user.groups.add(group)
    return user


@pytest.fixture
def viewer_user(django_user_model):
    user = django_user_model.objects.create_user(username="system_viewer", password="pass")
    group, _ = Group.objects.get_or_create(name="viewer")
    perm = Permission.objects.get(codename="view_autoshipment", content_type__app_label="shipments_auto")
    group.permissions.add(perm)
    user.groups.add(group)
    return user


@pytest.fixture
def backup_settings(settings, tmp_path):
    uploads = tmp_path / "uploads"
    backups = tmp_path / "backups"
    uploads.mkdir()
    backups.mkdir()
    settings.MEDIA_ROOT = str(uploads)
    settings.BACKUP_DIR = str(backups)
    settings.APP_VERSION = "1.2.3"
    return uploads, backups


@pytest.mark.django_db
class TestCanViewSystemStatus:
    def test_user_with_permission_returns_true(self, admin_user):
        assert can_view_system_status(admin_user) is True

    def test_user_without_permission_returns_false(self, django_user_model):
        user = django_user_model.objects.create_user(username="no_perm", password="pass")
        assert can_view_system_status(user) is False

    def test_none_returns_false(self):
        assert can_view_system_status(None) is False


@pytest.mark.django_db
class TestSystemAccess:
    def test_anonymous_redirects(self, client):
        response = client.get(reverse("core:system_status"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def test_non_admin_gets_403(self, client, viewer_user):
        client.login(username="system_viewer", password="pass")
        response = client.get(reverse("core:system_status"))
        assert response.status_code == 403

    def test_staff_without_permissions_gets_403(self, client, django_user_model):
        staff = django_user_model.objects.create_user(
            username="system_staff", password="pass", is_staff=True
        )
        client.login(username="system_staff", password="pass")
        response = client.get(reverse("core:system_status"))
        assert response.status_code == 403

    def test_admin_can_open_system_page(self, client, admin_user):
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert response.status_code == 200
        assert "Система и резервные копии" in response.content.decode()

    def test_system_page_shows_embedded_build_identity(self, client, admin_user, settings):
        settings.APP_VERSION = "1.2.3"
        settings.APP_BUILD_ID = "abc123build"
        settings.APP_GIT_COMMIT = "0123456789abcdef0123456789abcdef01234567"
        settings.APP_BUILT_AT = "2026-07-10T09:30:00Z"
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))
        html = response.content.decode()

        assert "Версия: 1.2.3" in html
        assert "Build ID: abc123build" in html
        assert "Commit: 0123456789ab" in html
        assert "Сборка: 2026-07-10T09:30:00Z" in html

    def test_system_audit_write_failure_is_logged(self):
        class ListHandler(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.ERROR)
                self.records = []

            def emit(self, record):
                self.records.append(record)

        logger = logging.getLogger("core.system_ops")
        handler = ListHandler()
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        with patch("core.system_ops.write_audit_log", side_effect=RuntimeError("audit down")):
            try:
                _write_system_audit(
                    entity_type=AuditLog.ENTITY_SYSTEM,
                    entity_id=1,
                    action=AuditLog.ACTION_SYSTEM_MODE_CHANGE,
                    source=AuditLog.SOURCE_UI,
                )
            finally:
                logger.removeHandler(handler)
                logger.setLevel(old_level)

        assert any(record.getMessage() == "Failed to write system audit log" for record in handler.records)
        assert any(
            record.exc_info and str(record.exc_info[1]) == "audit down"
            for record in handler.records
        )

    def test_system_page_shows_recent_operations_chronologically(self, client, admin_user):
        older = timezone.now() - timedelta(minutes=10)
        newer = timezone.now()
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            finished_at=older,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path="/app/backups/full.manifest.json",
            finished_at=newer,
        )
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))
        html = response.content.decode()

        assert html.index(f"Restore #{restore.pk}") < html.index(f"Backup #{backup.pk}")
        assert timezone.localtime(newer).strftime("%Y-%m-%d %H:%M:%S") in html

    def test_operation_runs_limited_to_ten(self, client, admin_user):
        now = timezone.now()
        for i in range(12):
            BackupRun.objects.create(
                backup_type=BackupRun.TYPE_FULL,
                status=BackupRun.STATUS_SUCCESS,
                initiated_by=admin_user,
                finished_at=now - timedelta(minutes=i),
            )
        for i in range(3):
            RestoreRun.objects.create(
                status=RestoreRun.STATUS_SUCCESS,
                initiated_by=admin_user,
                full_manifest_path="/fake/path.manifest.json",
                finished_at=now - timedelta(minutes=20 + i),
            )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        html = response.content.decode()
        assert html.count("Backup #") + html.count("Restore #") == 10

    def test_operation_runs_show_newest_first(self, client, admin_user):
        t_oldest = timezone.now() - timedelta(minutes=30)
        t_middle = timezone.now() - timedelta(minutes=20)
        t_newest = timezone.now() - timedelta(minutes=10)
        b1 = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            finished_at=t_oldest,
        )
        r1 = RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path="/x.manifest.json",
            finished_at=t_newest,
        )
        b2 = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            finished_at=t_middle,
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        html = response.content.decode()
        assert html.index(f"Restore #{r1.pk}") < html.index(f"Backup #{b2.pk}") < html.index(f"Backup #{b1.pk}")

    def test_backup_shows_initiator(self, client, admin_user):
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            finished_at=timezone.now(),
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert admin_user.username in response.content.decode()

    def test_backup_shows_start_and_finish_times(self, client, admin_user):
        started = timezone.now() - timedelta(minutes=5)
        finished = timezone.now()
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            started_at=started,
            finished_at=finished,
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        html = response.content.decode()
        assert timezone.localtime(started).strftime("%Y-%m-%d %H:%M:%S") in html
        assert timezone.localtime(finished).strftime("%Y-%m-%d %H:%M:%S") in html

    def test_restore_shows_source_backup_comment(self, client, admin_user, tmp_path):
        manifest_path = tmp_path / "full.manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(manifest_path),
            comment="Перед важным импортом",
            finished_at=timezone.now() - timedelta(minutes=10),
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path=str(manifest_path),
            finished_at=timezone.now(),
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert "Перед важным импортом" in response.content.decode()

    def test_restore_shows_source_backup_pk(self, client, admin_user, tmp_path):
        manifest_path = tmp_path / "full.manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(manifest_path),
            finished_at=timezone.now() - timedelta(minutes=10),
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path=str(manifest_path),
            finished_at=timezone.now(),
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert f"#{backup.pk}" in response.content.decode()

    def test_restore_shows_deleted_files_warning_for_source_backup(self, client, admin_user, tmp_path):
        manifest_path = tmp_path / "full.manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(manifest_path),
            finished_at=timezone.now() - timedelta(minutes=10),
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path=str(manifest_path),
            finished_at=timezone.now(),
        )
        manifest_path.unlink()
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert "файлы удалены" in response.content.decode()

    def test_restore_shows_incremental_source_backup(self, client, admin_user, tmp_path):
        full_path = tmp_path / "full.manifest.json"
        incr_path = tmp_path / "incr.manifest.json"
        full_path.write_text("{}", encoding="utf-8")
        incr_path.write_text("{}", encoding="utf-8")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(full_path),
            finished_at=timezone.now() - timedelta(minutes=20),
        )
        incr_backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_INCREMENTAL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(incr_path),
            comment="Инкремент утром",
            finished_at=timezone.now() - timedelta(minutes=10),
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path=str(full_path),
            incremental_manifest_path=str(incr_path),
            finished_at=timezone.now(),
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        html = response.content.decode()
        assert f"#{incr_backup.pk}" in html
        assert "Инкремент утром" in html

    def test_restore_shows_pre_restore_backup(self, client, admin_user, tmp_path):
        manifest_path = tmp_path / "full.manifest.json"
        manifest_path.write_text("{}", encoding="utf-8")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(manifest_path),
            finished_at=timezone.now() - timedelta(minutes=10),
        )
        pre_restore = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_PRE_RESTORE,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            finished_at=timezone.now() - timedelta(minutes=2),
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            full_manifest_path=str(manifest_path),
            pre_restore_backup=pre_restore,
            finished_at=timezone.now(),
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert f"#{pre_restore.pk}" in response.content.decode()

    def test_maintenance_blocks_non_admin(self, client, viewer_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, reason="test")
        client.login(username="system_viewer", password="pass")
        response = client.get(reverse("index"))
        assert response.status_code == 503
        assert "Система на обслуживании" in response.content.decode()

    def test_anonymous_can_open_login_during_maintenance(self, client):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, reason="test")

        response = client.get(reverse("login"))

        assert response.status_code == 200
        assert "Войдите в систему" in response.content.decode()

    def test_non_admin_login_page_is_blocked_during_maintenance(self, client, viewer_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, reason="test")
        client.login(username="system_viewer", password="pass")

        response = client.get(reverse("login"))
        html = response.content.decode()

        assert response.status_code == 503
        assert "Система на обслуживании" in html
        assert "нет прав администратора" in html
        assert "Авто" not in html
        assert "ЖД" not in html

    def test_non_admin_can_logout_during_maintenance(self, client, viewer_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, reason="test")
        client.login(username="system_viewer", password="pass")

        response = client.post(reverse("logout"))

        assert response.status_code == 302

    def test_admin_can_open_system_page_during_maintenance(self, client, admin_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "test")
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))

        assert response.status_code == 200
        assert "Система и резервные копии" in response.content.decode()

    def test_restore_running_blocks_app_writes(self, client, admin_user):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        client.login(username="system_admin", password="pass")
        response = client.post(reverse("auto:create"), {})
        assert response.status_code == 503

    def test_healthz_available_during_maintenance(self, client):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, reason="restore")
        response = client.get(reverse("core:healthz"))
        assert response.status_code == 200

    def test_readyz_available_during_maintenance(self, client, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path / "uploads")
        Path(settings.MEDIA_ROOT).mkdir()
        settings.BACKUP_DIR = str(tmp_path / "backups")
        Path(settings.BACKUP_DIR).mkdir()
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, reason="restore")

        response = client.get(reverse("core:readyz"))
        data = json.loads(response.content)

        assert response.status_code == 200
        assert data["status"] == "degraded"
        assert data["checks"]["system_mode"] == {"ok": False, "mode": "restore_running"}

    def test_admin_can_recover_interrupted_restore(self, client, admin_user):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        restore_run = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            initiated_by=admin_user,
            full_manifest_path="backup.manifest.json",
        )
        backup_run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_PRE_RESTORE,
            status=BackupRun.STATUS_RUNNING,
            initiated_by=admin_user,
        )
        client.login(username="system_admin", password="pass")

        response = client.post(reverse("core:recover_restore"))

        assert response.status_code == 302
        assert response["Location"] == reverse("core:system_status")
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_ADMIN_ONLY
        restore_run.refresh_from_db()
        backup_run.refresh_from_db()
        assert restore_run.status == RestoreRun.STATUS_ERROR
        assert backup_run.status == BackupRun.STATUS_ERROR
        assert "interrupted" in restore_run.error_message

    def test_admin_can_recover_interrupted_backup_in_maintenance(self, client, admin_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        backup_run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            initiated_by=admin_user,
        )
        client.login(username="system_admin", password="pass")

        response = client.post(reverse("core:recover_restore"))

        assert response.status_code == 302
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_ADMIN_ONLY
        backup_run.refresh_from_db()
        assert backup_run.status == BackupRun.STATUS_ERROR
        assert "interrupted" in backup_run.error_message

    def test_active_operation_disables_buttons_in_maintenance_mode(self, client, admin_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            initiated_by=admin_user,
        )
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))
        html = response.content.decode()

        assert "Сбросить зависшие операции" in html
        assert button_is_disabled(html, "Full backup")
        assert button_is_disabled(html, "Incremental backup")
        assert button_is_disabled(html, "Запустить restore")

    def test_operation_buttons_disabled_in_normal_mode(self, client, admin_user):
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))
        html = response.content.decode()

        assert button_is_disabled(html, "Full backup")
        assert button_is_disabled(html, "Incremental backup")
        assert button_is_disabled(html, "Запустить restore")

    def test_operation_buttons_enabled_in_maintenance_mode(self, client, admin_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))
        html = response.content.decode()

        assert not button_is_disabled(html, "Full backup")
        assert not button_is_disabled(html, "Incremental backup")
        assert not button_is_disabled(html, "Запустить restore")

    def test_enable_maintenance_requires_reason(self, client, admin_user):
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:system_maintenance"),
            {"mode": SystemState.MODE_ADMIN_ONLY, "reason": ""},
        )

        assert response.status_code == 302
        assert not SystemState.objects.filter(mode=SystemState.MODE_ADMIN_ONLY).exists()

    def test_enable_maintenance_with_reason(self, client, admin_user):
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:system_maintenance"),
            {"mode": SystemState.MODE_ADMIN_ONLY, "reason": "Проверка backup"},
        )

        assert response.status_code == 302
        state = SystemState.objects.get(singleton_key=1)
        assert state.mode == SystemState.MODE_ADMIN_ONLY
        assert state.reason == "Проверка backup"

    def test_return_normal_does_not_require_reason(self, client, admin_user):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:system_maintenance"),
            {"mode": SystemState.MODE_NORMAL, "reason": ""},
        )

        assert response.status_code == 302
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_NORMAL


@pytest.mark.django_db
class TestBackupSchedule:
    def test_default_schedules_exist_with_next_run(self):
        full = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_FULL)
        incremental = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_INCREMENTAL)

        assert full.enabled
        assert full.weekdays == "6"
        assert full.run_time == time(2, 0)
        assert full.next_run_at is not None
        assert incremental.enabled
        assert incremental.weekdays == "0,1,2,3,4,5"
        assert incremental.run_time == time(2, 0)
        assert incremental.next_run_at is not None

    def test_update_is_atomic_when_one_schedule_invalid(self, client, admin_user):
        """V12-24: невалидное второе расписание не должно сохранять первое."""
        client.login(username="system_admin", password="pass")
        full = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_FULL)
        incremental = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_INCREMENTAL)
        full_before = (full.enabled, full.weekdays, full.run_time)

        response = client.post(
            reverse("core:update_backup_schedule"),
            {
                # full: валидное изменение (другой день)
                f"enabled_{full.pk}": "on",
                f"weekdays_{full.pk}": ["5"],
                f"run_time_{full.pk}": "03:30",
                # incremental: включён, но без дней недели → ошибка валидации
                f"enabled_{incremental.pk}": "on",
                f"run_time_{incremental.pk}": "04:00",
            },
        )

        assert response.status_code == 302
        full.refresh_from_db()
        assert (full.enabled, full.weekdays, full.run_time) == full_before
        assert not AuditLog.objects.filter(
            action=AuditLog.ACTION_BACKUP_SCHEDULE_UPDATED
        ).exists()

    def test_update_persists_all_schedules_when_valid(self, client, admin_user):
        client.login(username="system_admin", password="pass")
        full = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_FULL)
        incremental = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_INCREMENTAL)

        response = client.post(
            reverse("core:update_backup_schedule"),
            {
                f"enabled_{full.pk}": "on",
                f"weekdays_{full.pk}": ["5"],
                f"run_time_{full.pk}": "03:30",
                f"enabled_{incremental.pk}": "on",
                f"weekdays_{incremental.pk}": ["0", "1"],
                f"run_time_{incremental.pk}": "04:00",
            },
        )

        assert response.status_code == 302
        full.refresh_from_db()
        incremental.refresh_from_db()
        assert full.weekdays == "5"
        assert full.run_time == time(3, 30)
        assert incremental.weekdays == "0,1"
        assert incremental.run_time == time(4, 0)

    def test_schedule_calculates_next_run_from_weekdays(self):
        schedule = BackupSchedule(
            backup_type=BackupRun.TYPE_FULL,
            enabled=True,
            weekdays="6",
            run_time=time(2, 0),
        )
        after = timezone.make_aware(datetime(2026, 5, 29, 1, 0))

        next_run = timezone.localtime(schedule.calculate_next_run(after))

        assert next_run.weekday() == 6
        assert next_run.hour == 2
        assert next_run.minute == 0

    def test_scheduler_enqueues_and_executes_due_backup(self, backup_settings):
        SystemState.objects.get_or_create(singleton_key=1)
        schedule = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_FULL)
        schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=["next_run_at"])

        def fake_create_backup(backup_type, initiated_by=None, run=None, comment="", source=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            return run

        with patch("core.system_ops.create_backup", side_effect=fake_create_backup) as mocked_backup:
            result = run_scheduler_tick()

        assert result["claimed"] is True
        assert result["kind"] == "backup"
        mocked_backup.assert_called_once()
        run = BackupRun.objects.get(schedule=schedule)
        assert run.source == BackupRun.SOURCE_SCHEDULER
        assert run.status == BackupRun.STATUS_SUCCESS
        schedule.refresh_from_db()
        assert schedule.next_run_at > timezone.now()

    def test_scheduler_tick_claims_backup_even_when_maintenance_fails(self, backup_settings):
        """V17-MED-6: сбой daily maintenance не мешает забрать queued backup."""
        SystemState.objects.get_or_create(singleton_key=1)
        schedule = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_FULL)
        schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=["next_run_at"])

        def fake_create_backup(backup_type, initiated_by=None, run=None, comment="", source=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            return run

        with (
            patch(
                "core.system_ops._run_daily_scheduler_maintenance",
                side_effect=OSError("cleanup boom"),
            ) as mocked_maint,
            patch("core.system_ops.create_backup", side_effect=fake_create_backup) as mocked_backup,
        ):
            result = run_scheduler_tick()

        mocked_maint.assert_called_once()
        assert result["claimed"] is True
        assert result["kind"] == "backup"
        mocked_backup.assert_called_once()

    def test_scheduler_tick_records_attempt_marker_when_maintenance_fails(self):
        """V17-MED-6: при сбое maintenance метка попытки ставится → нет retry каждый тик."""
        SystemState.objects.get_or_create(singleton_key=1)
        with patch(
            "core.system_ops._run_daily_scheduler_maintenance",
            side_effect=OSError("cleanup boom"),
        ) as mocked_maint:
            run_scheduler_tick()
            state = SystemState.objects.get(singleton_key=1)
            assert state.daily_cleanup_last_run_at is not None
            # Повторный тик того же дня не должен снова запускать maintenance.
            run_scheduler_tick()

        assert mocked_maint.call_count == 1

    def test_scheduler_does_not_enqueue_when_operation_is_active(self):
        schedule = BackupSchedule.objects.get(backup_type=BackupRun.TYPE_FULL)
        schedule.next_run_at = timezone.now() - timedelta(minutes=1)
        schedule.save(update_fields=["next_run_at"])
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_INCREMENTAL,
            status=BackupRun.STATUS_RUNNING,
        )

        result = run_scheduler_tick()

        assert result == {"claimed": False}
        assert BackupRun.objects.count() == 1

    def test_scheduler_executes_queued_restore(self):
        SystemState.objects.get_or_create(singleton_key=1)
        run = RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            full_manifest_path="backup.manifest.json",
        )

        with patch("core.system_ops.restore_backup") as mocked_restore:
            result = run_scheduler_tick()

        assert result["claimed"] is True
        assert result["kind"] == "restore"
        mocked_restore.assert_called_once()
        run.refresh_from_db()
        assert run.status == RestoreRun.STATUS_RUNNING

    @pytest.mark.django_db(transaction=True)
    def test_scheduler_start_recovers_stale_running_backup_and_executes_queued_restore(self):
        from django.core.management import call_command

        stale_backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            error_message="",
        )
        queued_restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            full_manifest_path="backup.manifest.json",
        )

        with patch("core.system_ops.restore_backup") as mocked_restore:
            call_command("run_scheduler", "--once")

        stale_backup.refresh_from_db()
        queued_restore.refresh_from_db()
        assert stale_backup.status == BackupRun.STATUS_ERROR
        assert "scheduler/container restart" in stale_backup.error_message
        assert stale_backup.finished_at is not None
        mocked_restore.assert_called_once()
        assert queued_restore.status == RestoreRun.STATUS_RUNNING

    @pytest.mark.django_db(transaction=True)
    def test_scheduler_start_recovers_stale_running_restore_and_enters_admin_only(self, admin_user):
        from django.core.management import call_command

        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "Restore is running")
        restore_run = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            initiated_by=admin_user,
            full_manifest_path="backup.manifest.json",
            error_message="",
        )

        call_command("run_scheduler", "--once")

        restore_run.refresh_from_db()
        state = SystemState.objects.get(singleton_key=1)
        assert restore_run.status == RestoreRun.STATUS_ERROR
        assert "scheduler/container restart" in restore_run.error_message
        assert restore_run.finished_at is not None
        assert state.mode == SystemState.MODE_ADMIN_ONLY
        assert "Restore interrupted" in state.reason

    def test_scheduler_start_does_not_mark_queued_operations_stale(self):
        from core.system_ops import recover_stale_running_operations_on_scheduler_start

        queued_backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
        )
        queued_restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            full_manifest_path="backup.manifest.json",
        )

        result = recover_stale_running_operations_on_scheduler_start()

        queued_backup.refresh_from_db()
        queued_restore.refresh_from_db()
        assert result["backup_count"] == 0
        assert result["restore_count"] == 0
        assert queued_backup.status == BackupRun.STATUS_QUEUED
        assert queued_restore.status == RestoreRun.STATUS_QUEUED

    def test_scheduler_start_recovery_writes_scheduler_audit(self):
        from core.system_ops import recover_stale_running_operations_on_scheduler_start

        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path="backup.manifest.json",
        )

        result = recover_stale_running_operations_on_scheduler_start()

        audit = AuditLog.objects.get(action=AuditLog.ACTION_OPERATION_RECOVERED)
        assert result["backup_count"] == 1
        assert result["restore_count"] == 1
        assert audit.entity_type == AuditLog.ENTITY_SYSTEM
        assert audit.source == AuditLog.SOURCE_SCHEDULER
        assert audit.new_values == {"backup_count": 1, "restore_count": 1}

    def test_scheduler_command_cleans_up_connections(self):
        from unittest.mock import MagicMock
        from django.core.management import call_command
        from core.management.commands import run_scheduler

        # Тест проверяет очистку соединений после тика (reset_queries +
        # close_if_unusable_or_obsolete), а не взятие лока. Мок connections
        # ломал бы mysql-ветку acquire_scheduler_lock, поэтому стабим сам лок —
        # реальный lock-путь покрыт test_scheduler_command_exits_when_advisory_lock_unavailable
        # и test_sqlite_scheduler_lock_is_noop.
        mock_conn = MagicMock()
        with (
            patch(
                "core.management.commands.run_scheduler.acquire_scheduler_lock",
                return_value=run_scheduler.SchedulerLock(acquired=True),
            ),
            patch("core.management.commands.run_scheduler.reset_queries") as mock_reset,
            patch("core.management.commands.run_scheduler.connections") as mock_connections,
        ):
            mock_connections.all.return_value = [mock_conn]
            call_command("run_scheduler", "--once")

        mock_reset.assert_called_once()
        mock_conn.close_if_unusable_or_obsolete.assert_called_once()

    def test_scheduler_command_exits_when_advisory_lock_unavailable(self):
        from django.core.management import call_command
        from core.management.commands import run_scheduler

        lock = run_scheduler.SchedulerLock(acquired=False)
        with (
            patch("core.management.commands.run_scheduler.acquire_scheduler_lock", return_value=lock),
            patch("core.management.commands.run_scheduler.run_scheduler_tick") as tick,
        ):
            call_command("run_scheduler", "--once")

        tick.assert_not_called()

    def test_sqlite_scheduler_lock_is_noop(self):
        from core.management.commands import run_scheduler

        lock = run_scheduler.acquire_scheduler_lock(vendor="sqlite")

        assert lock.acquired is True
        lock.release()

    def test_mysql_scheduler_lock_reports_owned_when_connection_matches_holder(self):
        from core.management.commands import run_scheduler

        lock_connection = MagicMock()
        cursor = lock_connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (123, 123)
        lock = run_scheduler.SchedulerLock(acquired=True, lock_connection=lock_connection)

        assert lock.is_owned() is True
        cursor.execute.assert_called_once_with(
            "SELECT IS_USED_LOCK(%s), CONNECTION_ID()",
            ["coal_shipments_scheduler"],
        )

    def test_mysql_scheduler_lock_reports_lost_when_connection_differs_from_holder(self):
        from core.management.commands import run_scheduler

        lock_connection = MagicMock()
        cursor = lock_connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (456, 123)
        lock = run_scheduler.SchedulerLock(acquired=True, lock_connection=lock_connection)

        assert lock.is_owned() is False

    def test_scheduler_command_exits_when_advisory_lock_lost_before_tick(self):
        from core.management.commands import run_scheduler

        lock = Mock(acquired=True)
        lock.is_owned.return_value = False
        with (
            patch("core.management.commands.run_scheduler.acquire_scheduler_lock", return_value=lock),
            patch("core.management.commands.run_scheduler.run_scheduler_tick") as tick,
        ):
            call_command("run_scheduler", "--once")

        tick.assert_not_called()
        lock.release.assert_called_once()

    def test_scheduler_lock_release_swallows_operational_error_and_closes_connection(self):
        from core.management.commands import run_scheduler

        lock_connection = Mock()
        lock_connection.cursor.side_effect = OperationalError("connection lost")
        lock = run_scheduler.SchedulerLock(acquired=True, lock_connection=lock_connection)

        lock.release()

        lock_connection.close.assert_called_once()


class TestUploadsInventory:
    """Unit-тесты кеша sha256 по size+mtime_ns в _uploads_inventory."""

    def _make_file(self, root, name, content=b"data"):
        f = root / name
        f.write_bytes(content)
        return f

    def test_reuses_sha256_when_stat_matches(self, tmp_path, settings):
        root = tmp_path / "media"
        root.mkdir()
        settings.MEDIA_ROOT = str(root)
        f = self._make_file(root, "doc.pdf")
        stat = f.stat()
        previous = {
            "doc.pdf": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": "cached"}
        }
        with patch("core.system_ops._sha256_file") as mock_sha:
            result = _uploads_inventory(previous=previous)
        mock_sha.assert_not_called()
        assert result["doc.pdf"]["sha256"] == "cached"

    def test_recomputes_sha256_when_size_differs(self, tmp_path, settings):
        root = tmp_path / "media"
        root.mkdir()
        settings.MEDIA_ROOT = str(root)
        f = self._make_file(root, "doc.pdf")
        stat = f.stat()
        previous = {
            "doc.pdf": {"size": stat.st_size + 1, "mtime_ns": stat.st_mtime_ns, "sha256": "cached"}
        }
        with patch("core.system_ops._sha256_file", return_value="computed") as mock_sha:
            result = _uploads_inventory(previous=previous)
        mock_sha.assert_called_once()
        assert result["doc.pdf"]["sha256"] == "computed"

    def test_recomputes_sha256_when_mtime_differs(self, tmp_path, settings):
        root = tmp_path / "media"
        root.mkdir()
        settings.MEDIA_ROOT = str(root)
        f = self._make_file(root, "doc.pdf")
        stat = f.stat()
        previous = {
            "doc.pdf": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns + 1, "sha256": "cached"}
        }
        with patch("core.system_ops._sha256_file", return_value="computed") as mock_sha:
            result = _uploads_inventory(previous=previous)
        mock_sha.assert_called_once()
        assert result["doc.pdf"]["sha256"] == "computed"

    def test_recomputes_sha256_when_no_previous(self, tmp_path, settings):
        root = tmp_path / "media"
        root.mkdir()
        settings.MEDIA_ROOT = str(root)
        self._make_file(root, "doc.pdf")
        with patch("core.system_ops._sha256_file", return_value="computed") as mock_sha:
            result = _uploads_inventory(previous=None)
        mock_sha.assert_called_once()
        assert result["doc.pdf"]["sha256"] == "computed"

    def test_skips_file_that_vanishes_before_sha256(self, tmp_path, settings):
        """V17-MED-3: файл, удалённый между stat() и чтением, исключается из inventory."""
        root = tmp_path / "media"
        root.mkdir()
        settings.MEDIA_ROOT = str(root)
        self._make_file(root, "keep.pdf", b"keep")
        self._make_file(root, "vanish.pdf", b"vanish")

        def flaky_sha(path):
            if Path(path).name == "vanish.pdf":
                raise FileNotFoundError(path)
            return "ok"

        with patch("core.system_ops._sha256_file", side_effect=flaky_sha):
            result = _uploads_inventory(previous=None)

        assert "keep.pdf" in result
        assert "vanish.pdf" not in result


@pytest.mark.django_db
class TestBackupCreation:
    def test_backup_requires_maintenance(self, client, admin_user):
        client.login(username="system_admin", password="pass")

        response = client.post(reverse("core:start_backup"), {"backup_type": BackupRun.TYPE_FULL})

        assert response.status_code == 302
        assert BackupRun.objects.count() == 0

    def test_full_backup_creates_manifest(self, backup_settings, admin_user, settings):
        uploads, backups = backup_settings
        settings.APP_VERSION = "1.2.3"
        settings.BUILD_INFO = {
            "build_id": "build-123",
            "git_commit": "a" * 40,
            "built_at": "2026-07-10T08:00:00Z",
        }
        (uploads / "doc.txt").write_text("hello", encoding="utf-8")

        run = create_backup(
            BackupRun.TYPE_FULL,
            initiated_by=admin_user,
            comment="Перед импортом майских отгрузок",
        )

        assert run.status == BackupRun.STATUS_SUCCESS
        assert run.comment == "Перед импортом майских отгрузок"
        assert Path(run.manifest_path).exists()
        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        assert manifest["backup_type"] == BackupRun.TYPE_FULL
        assert manifest["app_version"] == "1.2.3"
        assert manifest["app_build_id"] == "build-123"
        assert manifest["app_git_commit"] == "a" * 40
        assert manifest["app_built_at"] == "2026-07-10T08:00:00Z"
        assert manifest["comment"] == "Перед импортом майских отгрузок"
        assert "doc.txt" in manifest["uploads"]["included_files"]
        audit = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_BACKUP,
            entity_id=run.pk,
            action=AuditLog.ACTION_BACKUP_SUCCESS,
        )
        assert audit.new_values["app_version"] == "1.2.3"
        assert audit.new_values["app_build_id"] == "build-123"

    def test_full_backup_excludes_file_vanished_before_archiving(
        self, backup_settings, admin_user
    ):
        """V17-MED-3: файл, исчезнувший до архивации, исключается из манифеста и архива
        (не abort), манифест и tar остаются согласованными (принцип V15-H1)."""
        uploads, backups = backup_settings
        (uploads / "present.txt").write_text("hi", encoding="utf-8")
        real_inventory = _uploads_inventory()

        def fake_inventory(previous=None, root=None):
            inv = dict(real_inventory)
            inv["ghost.txt"] = {"size": 5, "mtime_ns": 2, "sha256": "deadbeef"}
            return inv

        with patch("core.system_ops._uploads_inventory", side_effect=fake_inventory):
            run = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        assert run.status == BackupRun.STATUS_SUCCESS
        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        assert "ghost.txt" not in manifest["uploads"]["included_files"]
        assert "ghost.txt" not in manifest["uploads"]["files"]
        assert "present.txt" in manifest["uploads"]["included_files"]
        assert "present.txt" in manifest["uploads"]["files"]
        # Манифест ↔ архив согласованы: набор файлов в tar == uploads.files.
        with tarfile.open(manifest["uploads"]["archive"], "r:gz") as tar:
            archived = {m.name for m in tar.getmembers() if m.isfile()}
        assert archived == set(manifest["uploads"]["files"])

    def test_backup_consistency_flags_db_reference_missing_from_uploads(
        self, backup_settings, admin_user
    ):
        """V18-MED-3: активный ShipmentDocument, чей файл отсутствует в срезе uploads
        (замена/исчезновение между dump и архивом), фиксируется в
        manifest.uploads.consistency.missing_referenced; backup не падает."""
        uploads, _ = backup_settings
        (uploads / "present.pdf").write_text("hi", encoding="utf-8")
        ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=1,
            original_file_name="present.pdf",
            stored_file_name="present.pdf",
            file_path="present.pdf",
        )
        ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=2,
            original_file_name="ghost.pdf",
            stored_file_name="ghost.pdf",
            file_path="ghost/ghost.pdf",
        )

        run = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        assert run.status == BackupRun.STATUS_SUCCESS
        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        consistency = manifest["uploads"]["consistency"]
        assert "ghost/ghost.pdf" in consistency["missing_referenced"]
        assert "present.pdf" not in consistency["missing_referenced"]

    def test_backup_consistency_empty_when_all_references_present(
        self, backup_settings, admin_user
    ):
        uploads, _ = backup_settings
        (uploads / "doc.pdf").write_text("hi", encoding="utf-8")
        ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=1,
            original_file_name="doc.pdf",
            stored_file_name="doc.pdf",
            file_path="doc.pdf",
        )

        run = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        assert manifest["uploads"]["consistency"]["missing_referenced"] == []

    def test_backup_consistency_ignores_deleted_documents(self, backup_settings, admin_user):
        ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=1,
            original_file_name="gone.pdf",
            stored_file_name="gone.pdf",
            file_path="gone/gone.pdf",
            is_deleted=True,
            deleted_at=timezone.now(),
        )

        run = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        assert manifest["uploads"]["consistency"]["missing_referenced"] == []

    def test_backup_from_ui_is_queued_with_comment(self, client, admin_user, backup_settings):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:start_backup"),
            {"backup_type": BackupRun.TYPE_FULL, "comment": "Перед restore"},
        )

        assert response.status_code == 302
        run = BackupRun.objects.get()
        assert run.status == BackupRun.STATUS_QUEUED
        assert run.comment == "Перед restore"

        response = client.get(reverse("core:system_status"))
        assert "Перед restore" in response.content.decode()

    def test_backup_from_ui_is_blocked_by_queued_restore(self, client, admin_user, backup_settings):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            initiated_by=admin_user,
            full_manifest_path="backup.manifest.json",
        )
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:start_backup"),
            {"backup_type": BackupRun.TYPE_FULL},
        )

        assert response.status_code == 302
        assert BackupRun.objects.count() == 0

    def test_backup_from_ui_locks_system_state_before_enqueue(self, client, admin_user, backup_settings):
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "Проверка backup")
        client.login(username="system_admin", password="pass")

        with patch.object(
            SystemState.objects,
            "select_for_update",
            wraps=SystemState.objects.select_for_update,
        ) as select_for_update:
            response = client.post(
                reverse("core:start_backup"),
                {"backup_type": BackupRun.TYPE_FULL},
            )

        assert response.status_code == 302
        assert BackupRun.objects.count() == 1
        select_for_update.assert_called()

    def test_incremental_without_baseline_creates_full(self, backup_settings, admin_user):
        run = create_backup(BackupRun.TYPE_INCREMENTAL, initiated_by=admin_user)

        assert run.status == BackupRun.STATUS_SUCCESS
        assert run.backup_type == BackupRun.TYPE_FULL
        assert run.manifest["requested_type"] == BackupRun.TYPE_INCREMENTAL

    def test_incremental_with_baseline_tracks_changes_and_deletes(self, backup_settings, admin_user):
        uploads, _ = backup_settings
        keep = uploads / "keep.txt"
        remove = uploads / "remove.txt"
        keep.write_text("v1", encoding="utf-8")
        remove.write_text("remove", encoding="utf-8")
        full = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        keep.write_text("v2", encoding="utf-8")
        remove.unlink()
        added = uploads / "added.txt"
        added.write_text("added", encoding="utf-8")
        incremental = create_backup(BackupRun.TYPE_INCREMENTAL, initiated_by=admin_user)

        assert incremental.status == BackupRun.STATUS_SUCCESS
        assert incremental.backup_type == BackupRun.TYPE_INCREMENTAL
        assert incremental.manifest["uploads"]["baseline_manifest"] == full.manifest_path
        assert "keep.txt" in incremental.manifest["uploads"]["included_files"]
        assert "added.txt" in incremental.manifest["uploads"]["included_files"]
        assert "remove.txt" in incremental.manifest["uploads"]["deleted_files"]

    def test_backup_cleanup_on_dump_database_error(self, backup_settings, admin_user):
        uploads, backups = backup_settings

        with patch("core.system_ops._dump_database", side_effect=OSError("ENOSPC")):
            with pytest.raises(OSError, match="ENOSPC"):
                create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        run = BackupRun.objects.get()
        assert run.status == BackupRun.STATUS_ERROR
        assert "ENOSPC" in run.error_message
        leftover = list(backups.glob("db_*.sql.gz"))
        assert leftover == [], f"Ожидали пустой список, нашли: {leftover}"

    def test_backup_cleanup_on_archive_error(self, backup_settings, admin_user):
        uploads, backups = backup_settings
        (uploads / "doc.txt").write_text("data", encoding="utf-8")

        def fake_dump(path):
            Path(path).write_bytes(b"fake-gzip-data")
            return {"engine": "sqlite", "path": str(path), "size": 14}

        with (
            patch("core.system_ops._dump_database", side_effect=fake_dump),
            patch("core.system_ops._create_uploads_archive", side_effect=OSError("ENOSPC")),
        ):
            with pytest.raises(OSError, match="ENOSPC"):
                create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        run = BackupRun.objects.get()
        assert run.status == BackupRun.STATUS_ERROR
        leftover_db = list(backups.glob("db_*.sql.gz"))
        leftover_up = list(backups.glob("uploads_*.tar.gz"))
        assert leftover_db == [], f"db файл не удалён: {leftover_db}"
        assert leftover_up == [], f"uploads файл не удалён: {leftover_up}"

    def test_incremental_skips_sha256_for_unchanged_files(self, backup_settings, admin_user):
        uploads, _ = backup_settings
        (uploads / "doc.txt").write_text("content", encoding="utf-8")

        sha256_calls = []
        orig = _sha256_file

        def tracking_sha256(path):
            sha256_calls.append(Path(path).name)
            return orig(path)

        with patch("core.system_ops._sha256_file", side_effect=tracking_sha256):
            create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)
            doc_calls_after_full = sha256_calls.count("doc.txt")
            create_backup(BackupRun.TYPE_INCREMENTAL, initiated_by=admin_user)
            doc_calls_after_incremental = sha256_calls.count("doc.txt")

        assert doc_calls_after_full == 1, "full backup должен вычислить sha256 один раз"
        assert doc_calls_after_incremental == doc_calls_after_full, (
            "incremental backup не должен вычислять sha256 для неизменённых файлов"
        )

    def test_archive_excludes_disappeared_file_instead_of_abort(self, backup_settings):
        """V17-MED-3: исчезнувший файл исключается из архива и возвращается в
        `missing`, а не роняет бэкап (пересмотр прежнего V15-H1 abort)."""
        uploads, backups = backup_settings
        (uploads / "keep.txt").write_text("data", encoding="utf-8")
        (uploads / "ghost.txt").write_text("data", encoding="utf-8")
        (uploads / "ghost.txt").unlink()
        archive_path = backups / "out.tar.gz"

        info = _create_uploads_archive(archive_path, ["keep.txt", "ghost.txt"])

        assert info["missing"] == ["ghost.txt"]
        with tarfile.open(archive_path, "r:gz") as tar:
            names = {m.name for m in tar.getmembers() if m.isfile()}
        assert names == {"keep.txt"}

    def test_backup_run_succeeds_when_file_vanishes_mid_archiving(self, backup_settings, admin_user):
        from core.system_ops import _create_uploads_archive as _orig

        uploads, backups = backup_settings
        doc = uploads / "doc.txt"
        keep = uploads / "keep.txt"
        doc.write_text("hello", encoding="utf-8")
        keep.write_text("keep", encoding="utf-8")

        def racer(path, included_files):
            doc.unlink()
            return _orig(path, included_files)

        with patch("core.system_ops._create_uploads_archive", side_effect=racer):
            run = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        assert run.status == BackupRun.STATUS_SUCCESS
        manifest = json.loads(Path(run.manifest_path).read_text(encoding="utf-8"))
        assert "doc.txt" not in manifest["uploads"]["files"]
        assert "doc.txt" not in manifest["uploads"]["included_files"]
        assert "keep.txt" in manifest["uploads"]["files"]
        with tarfile.open(manifest["uploads"]["archive"], "r:gz") as tar:
            archived = {m.name for m in tar.getmembers() if m.isfile()}
        assert archived == set(manifest["uploads"]["files"])


@pytest.mark.django_db
class TestCreateBackupCommand:
    @pytest.mark.parametrize("status", [RestoreRun.STATUS_QUEUED, RestoreRun.STATUS_RUNNING])
    def test_create_backup_command_blocks_when_restore_is_active(self, status, backup_settings):
        RestoreRun.objects.create(status=status, full_manifest_path="backup.manifest.json")

        with patch("core.management.commands.create_backup.create_backup") as mocked_backup:
            with pytest.raises(CommandError, match="Another backup or restore operation is already active"):
                call_command("create_backup", "--type", BackupRun.TYPE_FULL)

        mocked_backup.assert_not_called()
        assert BackupRun.objects.count() == 0

    def test_create_backup_command_active_operation_guard_can_exclude_current_backup_run(self):
        run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
        )

        assert has_active_operation() is True
        assert has_active_operation(exclude_backup_run=run) is False

    def test_create_backup_command_run_id_does_not_block_on_same_queued_backup(self):
        run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
            source=BackupRun.SOURCE_SCRIPT,
        )

        with patch("core.management.commands.create_backup.create_backup", return_value=run) as mocked_backup:
            call_command("create_backup", "--type", BackupRun.TYPE_FULL, "--run-id", str(run.pk))

        mocked_backup.assert_called_once_with(
            BackupRun.TYPE_FULL,
            run=run,
            comment="",
            source=BackupRun.SOURCE_SCRIPT,
        )
        assert BackupRun.objects.count() == 1

    def test_create_backup_command_run_id_rejects_non_queued_backup(self):
        run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            source=BackupRun.SOURCE_SCRIPT,
        )

        with patch("core.management.commands.create_backup.create_backup") as mocked_backup:
            with pytest.raises(CommandError, match="not queued"):
                call_command("create_backup", "--type", BackupRun.TYPE_FULL, "--run-id", str(run.pk))

        mocked_backup.assert_not_called()

    def test_create_backup_command_run_id_rejects_type_mismatch(self):
        run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
            source=BackupRun.SOURCE_SCRIPT,
        )

        with patch("core.management.commands.create_backup.create_backup") as mocked_backup:
            with pytest.raises(CommandError, match="type"):
                call_command(
                    "create_backup", "--type", BackupRun.TYPE_INCREMENTAL, "--run-id", str(run.pk)
                )

        mocked_backup.assert_not_called()

    def test_create_backup_command_marks_new_run_running_before_service_call(self):
        captured = {}

        def side_effect(backup_type, run=None, comment="", source=None):
            run.refresh_from_db()
            captured["status"] = run.status
            captured["started_at"] = run.started_at
            captured["claim"] = _claim_next_queued_operation()
            return run

        with patch(
            "core.management.commands.create_backup.create_backup", side_effect=side_effect
        ) as mocked_backup:
            call_command("create_backup", "--type", BackupRun.TYPE_FULL)

        mocked_backup.assert_called_once()
        assert captured["status"] == BackupRun.STATUS_RUNNING
        assert captured["started_at"] is not None
        assert captured["claim"] is None

    def test_create_backup_command_marks_existing_run_running_before_service_call(self):
        run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
            source=BackupRun.SOURCE_SCRIPT,
        )
        captured = {}

        def side_effect(backup_type, run=None, comment="", source=None):
            run.refresh_from_db()
            captured["status"] = run.status
            captured["started_at"] = run.started_at
            captured["claim"] = _claim_next_queued_operation()
            return run

        with patch(
            "core.management.commands.create_backup.create_backup", side_effect=side_effect
        ) as mocked_backup:
            call_command("create_backup", "--type", BackupRun.TYPE_FULL, "--run-id", str(run.pk))

        mocked_backup.assert_called_once()
        assert captured["status"] == BackupRun.STATUS_RUNNING
        assert captured["started_at"] is not None
        assert captured["claim"] is None


@pytest.mark.django_db
class TestScanBackupManifests:
    def test_scan_backup_manifests_uses_stored_manifest_without_parsing_db_backed_file(self, backup_settings):
        _, backups = backup_settings
        manifest_path = backups / "db-backed.manifest.json"
        manifest_path.write_text("{not-json", encoding="utf-8")
        manifest = {
            "version": 1,
            "backup_type": BackupRun.TYPE_FULL,
            "created_at": "2026-06-13T10:00:00+00:00",
            "comment": "stored manifest",
            "database": {"path": str(backups / "db.sql.gz"), "size": 1},
            "uploads": {
                "archive": str(backups / "uploads.tar.gz"),
                "baseline_manifest": "",
            },
            "total_size": 2,
        }
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            manifest_path=str(manifest_path),
            manifest=manifest,
        )

        with patch("core.system_ops._load_json", side_effect=AssertionError("manifest file was parsed")):
            entries = scan_backup_manifests()

        assert len(entries) == 1
        assert entries[0]["key"] == manifest_path.name
        assert entries[0]["comment"] == "stored manifest"

    def test_scan_backup_manifests_still_includes_orphan_manifest_files(self, backup_settings):
        _, backups = backup_settings
        db_path = backups / "orphan.sql.gz"
        uploads_path = backups / "orphan.tar.gz"
        db_path.write_bytes(b"db")
        uploads_path.write_bytes(b"uploads")
        manifest = {
            "version": 1,
            "backup_type": BackupRun.TYPE_INCREMENTAL,
            "created_at": "2026-06-13T11:00:00+00:00",
            "comment": "orphan manifest",
            "database": {"path": str(db_path), "size": db_path.stat().st_size},
            "uploads": {
                "archive": str(uploads_path),
                "baseline_manifest": str(backups / "full.manifest.json"),
            },
            "total_size": db_path.stat().st_size + uploads_path.stat().st_size,
        }
        manifest_path = backups / "orphan.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        entries = scan_backup_manifests()

        assert len(entries) == 1
        assert entries[0]["key"] == manifest_path.name
        assert entries[0]["backup_type"] == BackupRun.TYPE_INCREMENTAL
        assert entries[0]["comment"] == "orphan manifest"


@pytest.mark.django_db
class TestRestoreUi:
    def _write_manifest(self, backups, name, backup_type="full", baseline=""):
        db_path = backups / f"{name}.sql.gz"
        uploads_path = backups / f"{name}.tar.gz"
        db_path.write_bytes(b"db")
        uploads_path.write_bytes(b"uploads")
        manifest = {
            "version": 1,
            "backup_type": backup_type,
            "created_at": timezone.now().isoformat(),
            "database": {"engine": "sqlite3", "path": str(db_path), "size": db_path.stat().st_size},
            "uploads": {
                "archive": str(uploads_path),
                "mode": "incremental" if backup_type == BackupRun.TYPE_INCREMENTAL else "full",
                "files": {},
                "included_files": [],
                "deleted_files": [],
                "baseline_manifest": baseline,
            },
            "total_size": db_path.stat().st_size + uploads_path.stat().st_size,
        }
        path = backups / f"{name}.manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path, manifest

    def test_restore_requires_confirmation(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        path, manifest = self._write_manifest(backups, "full")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(path),
            manifest=manifest,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        response = client.post(reverse("core:start_restore"), {"full_backup": path.name})

        assert response.status_code == 302
        assert RestoreRun.objects.count() == 0

    def test_restore_requires_maintenance(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        path, manifest = self._write_manifest(backups, "full")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(path),
            manifest=manifest,
        )
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:start_restore"),
            {"full_backup": path.name, "confirm_text": "ВОССТАНОВИТЬ"},
        )

        assert response.status_code == 302
        assert RestoreRun.objects.count() == 0

    def test_restore_rejects_unrelated_incremental(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        incremental_path, incremental_manifest = self._write_manifest(
            backups,
            "incremental",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(backups / "other.manifest.json"),
        )
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(full_path),
            manifest=full_manifest,
        )
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_INCREMENTAL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(incremental_path),
            manifest=incremental_manifest,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:start_restore"),
            {
                "full_backup": full_path.name,
                "incremental_backup": incremental_path.name,
                "confirm_text": "ВОССТАНОВИТЬ",
            },
        )

        assert response.status_code == 302
        assert RestoreRun.objects.count() == 0

    def test_restore_page_exposes_incremental_filtering_data(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        incremental_path, incremental_manifest = self._write_manifest(
            backups,
            "incremental",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(full_path),
        )
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(full_path),
            manifest=full_manifest,
        )
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_INCREMENTAL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(incremental_path),
            manifest=incremental_manifest,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))
        html = response.content.decode()
        payload = html.split('<script id="restore-incremental-entries" type="application/json">', 1)[1].split(
            "</script>",
            1,
        )[0]
        incremental_entries = json.loads(payload)

        assert f'value="{full_path.name}" data-manifest-path="{full_path}"' in html
        assert incremental_entries == [
            {
                "key": incremental_path.name,
                "created_at": incremental_manifest["created_at"],
                "baseline_manifest": str(full_path),
            }
        ]

    def test_restore_view_creates_restore_run(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        path, manifest = self._write_manifest(backups, "full")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(path),
            manifest=manifest,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:start_restore"),
            {"full_backup": path.name, "confirm_text": "ВОССТАНОВИТЬ"},
        )

        assert response.status_code == 302
        assert RestoreRun.objects.count() == 1
        assert RestoreRun.objects.get().status == RestoreRun.STATUS_QUEUED

    def test_restore_from_ui_is_blocked_by_queued_backup(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        path, manifest = self._write_manifest(backups, "full")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(path),
            manifest=manifest,
        )
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_INCREMENTAL,
            status=BackupRun.STATUS_QUEUED,
            initiated_by=admin_user,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:start_restore"),
            {"full_backup": path.name, "confirm_text": "ВОССТАНОВИТЬ"},
        )

        assert response.status_code == 302
        assert RestoreRun.objects.count() == 0
        assert BackupRun.objects.filter(status=BackupRun.STATUS_QUEUED).count() == 1

    def test_restore_from_ui_locks_system_state_before_enqueue(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        path, manifest = self._write_manifest(backups, "full")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(path),
            manifest=manifest,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        with patch.object(
            SystemState.objects,
            "select_for_update",
            wraps=SystemState.objects.select_for_update,
        ) as select_for_update:
            response = client.post(
                reverse("core:start_restore"),
                {"full_backup": path.name, "confirm_text": "ВОССТАНОВИТЬ"},
            )

        assert response.status_code == 302
        assert RestoreRun.objects.count() == 1
        select_for_update.assert_called()


@pytest.mark.django_db
class TestBackupDeleteUi:
    def _write_manifest(self, backups, name, backup_type="full", baseline=""):
        return TestRestoreUi()._write_manifest(backups, name, backup_type, baseline)

    def _create_run(self, admin_user, path, manifest, backup_type):
        return BackupRun.objects.create(
            backup_type=backup_type,
            status=BackupRun.STATUS_SUCCESS,
            initiated_by=admin_user,
            manifest_path=str(path),
            db_path=manifest["database"]["path"],
            uploads_path=manifest["uploads"]["archive"],
            total_size=manifest["total_size"],
            manifest=manifest,
        )

    def test_delete_requires_run_backup_permission(self, client, viewer_user, backup_settings):
        _, backups = backup_settings
        path, manifest = self._write_manifest(backups, "full")
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_SUCCESS,
            manifest_path=str(path),
            manifest=manifest,
        )
        client.login(username="system_viewer", password="pass")

        response = client.get(reverse("core:delete_backup", kwargs={"key": path.name}))

        assert response.status_code == 403

    def test_delete_preview_shows_related_incremental_and_files(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        incremental_path, incremental_manifest = self._write_manifest(
            backups,
            "incremental",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(full_path),
        )
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, incremental_path, incremental_manifest, BackupRun.TYPE_INCREMENTAL)
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:delete_backup", kwargs={"key": full_path.name}))
        html = response.content.decode()

        assert response.status_code == 200
        assert full_path.name in html
        assert incremental_path.name in html
        assert full_manifest["database"]["path"] in html
        assert incremental_manifest["uploads"]["archive"] in html

    def test_delete_requires_exact_confirmation(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        extra_path, extra_manifest = self._write_manifest(backups, "extra")
        self._create_run(admin_user, extra_path, extra_manifest, BackupRun.TYPE_FULL)
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": full_path.name}),
            {"confirm_text": "delete"},
        )

        assert response.status_code == 302
        assert Path(full_manifest["database"]["path"]).exists()
        assert Path(full_manifest["uploads"]["archive"]).exists()
        assert full_path.exists()
        assert not AuditLog.objects.filter(entity_type=AuditLog.ENTITY_BACKUP, action=AuditLog.ACTION_DELETE).exists()

    def test_delete_incremental_removes_files_writes_audit_and_keeps_run(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        incremental_path, incremental_manifest = self._write_manifest(
            backups,
            "incremental",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(full_path),
        )
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        incremental_run = self._create_run(
            admin_user,
            incremental_path,
            incremental_manifest,
            BackupRun.TYPE_INCREMENTAL,
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": incremental_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )

        assert response.status_code == 302
        assert not incremental_path.exists()
        assert not Path(incremental_manifest["database"]["path"]).exists()
        assert not Path(incremental_manifest["uploads"]["archive"]).exists()
        assert full_path.exists()
        assert Path(full_manifest["database"]["path"]).exists()
        assert BackupRun.objects.filter(pk=incremental_run.pk).exists()
        audit = AuditLog.objects.get(entity_type=AuditLog.ENTITY_BACKUP, action=AuditLog.ACTION_DELETE)
        assert audit.entity_id == incremental_run.pk
        assert incremental_path.name in json.dumps(audit.old_values)

    def test_delete_full_removes_related_incremental_but_not_unrelated(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        related_path, related_manifest = self._write_manifest(
            backups,
            "related",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(full_path),
        )
        other_full_path, other_full_manifest = self._write_manifest(backups, "other_full")
        unrelated_path, unrelated_manifest = self._write_manifest(
            backups,
            "unrelated",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(other_full_path),
        )
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, related_path, related_manifest, BackupRun.TYPE_INCREMENTAL)
        self._create_run(admin_user, other_full_path, other_full_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, unrelated_path, unrelated_manifest, BackupRun.TYPE_INCREMENTAL)
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": full_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )

        assert response.status_code == 302
        assert not full_path.exists()
        assert not related_path.exists()
        assert other_full_path.exists()
        assert unrelated_path.exists()

    def test_delete_last_full_like_backup_is_blocked(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": full_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )

        assert response.status_code == 302
        assert full_path.exists()
        assert Path(full_manifest["database"]["path"]).exists()

    def test_delete_latest_full_backup_is_blocked_even_when_older_full_exists(
        self,
        client,
        admin_user,
        backup_settings,
    ):
        _, backups = backup_settings
        old_path, old_manifest = self._write_manifest(backups, "old_full")
        latest_path, latest_manifest = self._write_manifest(backups, "latest_full")
        latest_manifest["created_at"] = (timezone.now() + timedelta(minutes=1)).isoformat()
        latest_path.write_text(json.dumps(latest_manifest), encoding="utf-8")
        self._create_run(admin_user, old_path, old_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, latest_path, latest_manifest, BackupRun.TYPE_FULL)
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": latest_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )

        assert response.status_code == 302
        assert latest_path.exists()
        assert Path(latest_manifest["database"]["path"]).exists()
        assert old_path.exists()

    def test_delete_backup_used_by_active_restore_is_blocked(self, client, admin_user, backup_settings):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        related_path, related_manifest = self._write_manifest(
            backups,
            "related",
            backup_type=BackupRun.TYPE_INCREMENTAL,
            baseline=str(full_path),
        )
        other_path, other_manifest = self._write_manifest(backups, "other")
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, related_path, related_manifest, BackupRun.TYPE_INCREMENTAL)
        self._create_run(admin_user, other_path, other_manifest, BackupRun.TYPE_FULL)
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            initiated_by=admin_user,
            full_manifest_path=str(other_path),
            incremental_manifest_path=str(related_path),
        )
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": full_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )

        assert response.status_code == 302
        assert full_path.exists()
        assert related_path.exists()

    def test_delete_unsafe_path_is_blocked(self, client, admin_user, backup_settings, tmp_path):
        _, backups = backup_settings
        full_path, full_manifest = self._write_manifest(backups, "full")
        unsafe_path = tmp_path / "outside.sql.gz"
        unsafe_path.write_bytes(b"outside")
        full_manifest["database"]["path"] = str(unsafe_path)
        full_path.write_text(json.dumps(full_manifest), encoding="utf-8")
        other_path, other_manifest = self._write_manifest(backups, "other")
        self._create_run(admin_user, full_path, full_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, other_path, other_manifest, BackupRun.TYPE_FULL)
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        response = client.post(
            reverse("core:delete_backup", kwargs={"key": full_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )

        assert response.status_code == 302
        assert full_path.exists()
        assert unsafe_path.exists()

    def test_deleted_backup_disappears_from_restore_options_but_run_remains(
        self,
        client,
        admin_user,
        backup_settings,
    ):
        _, backups = backup_settings
        deleted_path, deleted_manifest = self._write_manifest(backups, "deleted_full")
        kept_path, kept_manifest = self._write_manifest(backups, "kept_full")
        deleted_run = self._create_run(admin_user, deleted_path, deleted_manifest, BackupRun.TYPE_FULL)
        self._create_run(admin_user, kept_path, kept_manifest, BackupRun.TYPE_FULL)
        set_system_mode(SystemState.MODE_ADMIN_ONLY, admin_user, "delete backup")
        client.login(username="system_admin", password="pass")

        client.post(
            reverse("core:delete_backup", kwargs={"key": deleted_path.name}),
            {"confirm_text": "УДАЛИТЬ"},
        )
        response = client.get(reverse("core:system_status"))
        html = response.content.decode()

        assert BackupRun.objects.filter(pk=deleted_run.pk).exists()
        assert deleted_path.name not in html
        assert kept_path.name in html
        assert "файлы удалены" in html


@pytest.mark.django_db
class TestRestoreService:
    def test_sqlite_restore_replaces_existing_database(self, tmp_path):
        source_db = tmp_path / "source.sqlite3"
        target_db = tmp_path / "target.sqlite3"
        dump_path = tmp_path / "db.sql.gz"

        with closing(sqlite3.connect(source_db)) as db:
            db.execute("CREATE TABLE audit_log (id integer primary key, message text)")
            db.execute("INSERT INTO audit_log (message) VALUES ('from backup')")
            db.commit()
            dump_lines = list(db.iterdump())

        with closing(sqlite3.connect(target_db)) as db:
            db.execute("CREATE TABLE audit_log (id integer primary key, message text)")
            db.execute("INSERT INTO audit_log (message) VALUES ('current data')")
            db.commit()

        with gzip.open(dump_path, "wt", encoding="utf-8") as dump:
            dump.write("\n".join(dump_lines))

        _restore_sqlite_database(dump_path, target_db)

        with closing(sqlite3.connect(target_db)) as db:
            rows = db.execute("SELECT message FROM audit_log").fetchall()

        assert rows == [("from backup",)]

    @pytest.mark.django_db(transaction=True)
    def test_restore_creates_pre_restore_and_leaves_admin_only(self, admin_user, backup_settings):
        _, backups = backup_settings
        full_archive = backups / "full.tar.gz"
        with patch("tarfile.open"):
            full_archive.write_bytes(b"fake")
        manifest = {
            "app_version": "1.2.3",
            "database": {"engine": "sqlite3", "path": str(backups / "db.sql.gz")},
            "uploads": {"archive": str(full_archive)},
        }
        manifest_path = backups / "full.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))

        def fake_create_backup(backup_type, initiated_by=None, run=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.save()
            return run

        with (
            patch("core.system_ops.create_backup", side_effect=fake_create_backup) as mocked_backup,
            patch("core.system_ops._restore_database"),
            patch("core.system_ops._clear_media_root"),
            patch("core.system_ops._safe_extract_tar"),
        ):
            restore_backup(run)

        run.refresh_from_db()
        assert run.status == RestoreRun.STATUS_SUCCESS
        assert run.pre_restore_backup is not None
        assert mocked_backup.called
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_ADMIN_ONLY

    @pytest.mark.django_db(transaction=True)
    def test_restore_finalizes_when_restore_run_is_missing_after_database_restore(
        self,
        admin_user,
        backup_settings,
    ):
        _, backups = backup_settings
        full_archive = backups / "full.tar.gz"
        with patch("tarfile.open"):
            full_archive.write_bytes(b"fake")
        manifest = {
            "app_version": "1.2.3",
            "database": {"engine": "sqlite3", "path": str(backups / "db.sql.gz")},
            "uploads": {"archive": str(full_archive)},
        }
        manifest_path = backups / "full.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))
        run_pk = run.pk
        pre_restore_pk = {}
        stale_backup_pk = run_pk + 100

        def fake_create_backup(backup_type, initiated_by=None, run=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.save()
            pre_restore_pk["pk"] = run.pk
            return run

        def fake_restore_database(path, engine):
            RestoreRun.objects.filter(pk=run_pk).delete()
            BackupRun.objects.filter(backup_type=BackupRun.TYPE_PRE_RESTORE).delete()
            BackupRun.objects.create(
                pk=stale_backup_pk,
                backup_type=BackupRun.TYPE_FULL,
                status=BackupRun.STATUS_RUNNING,
                initiated_by=admin_user,
            )

        with (
            patch("core.system_ops.create_backup", side_effect=fake_create_backup),
            patch("core.system_ops._restore_database", side_effect=fake_restore_database),
            patch("core.system_ops._clear_media_root"),
            patch("core.system_ops._safe_extract_tar"),
        ):
            restored_run = restore_backup(run)

        assert restored_run.pk == run_pk
        assert restored_run.status == RestoreRun.STATUS_SUCCESS
        assert RestoreRun.objects.get(pk=run_pk).status == RestoreRun.STATUS_SUCCESS
        assert restored_run.pre_restore_backup is not None
        assert restored_run.pre_restore_backup_id == pre_restore_pk["pk"]
        assert BackupRun.objects.get(pk=stale_backup_pk).status == BackupRun.STATUS_ERROR
        assert not BackupRun.objects.filter(status__in=[BackupRun.STATUS_QUEUED, BackupRun.STATUS_RUNNING]).exists()
        assert not RestoreRun.objects.filter(status__in=[RestoreRun.STATUS_QUEUED, RestoreRun.STATUS_RUNNING]).exists()
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_ADMIN_ONLY


@pytest.mark.django_db
class TestRestoreManifestSecurity:
    def _write_manifest_file(self, backups, manifest_data, name="test"):
        path = backups / f"{name}.manifest.json"
        path.write_text(json.dumps(manifest_data), encoding="utf-8")
        return path

    def _fake_create_backup(self, backup_type, initiated_by=None, run=None):
        run.status = BackupRun.STATUS_SUCCESS
        run.save()
        return run

    def test_missing_database_key_raises_before_clear(self, admin_user, backup_settings):
        _, backups = backup_settings
        path = self._write_manifest_file(
            backups, {"uploads": {"archive": str(backups / "x.tar.gz")}}
        )
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(path))
        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._clear_media_root") as mock_clear,
        ):
            with pytest.raises(RuntimeError, match="Manifest missing section: 'database'"):
                restore_backup(run)
        mock_clear.assert_not_called()

    def test_missing_uploads_archive_raises_before_clear(self, admin_user, backup_settings):
        _, backups = backup_settings
        path = self._write_manifest_file(
            backups,
            {"database": {"path": str(backups / "db.sql.gz")}, "uploads": {}},
        )
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(path))
        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._clear_media_root") as mock_clear,
        ):
            with pytest.raises(RuntimeError, match="Manifest missing key: 'uploads'"):
                restore_backup(run)
        mock_clear.assert_not_called()

    def test_db_path_outside_backup_dir_raises_before_clear(self, admin_user, backup_settings, tmp_path):
        _, backups = backup_settings
        outside = tmp_path / "evil.sql.gz"
        outside.write_bytes(b"x")
        path = self._write_manifest_file(
            backups,
            {
                "app_version": "1.2.3",
                "database": {"path": str(outside)},
                "uploads": {"archive": str(backups / "x.tar.gz")},
            },
        )
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(path))
        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._clear_media_root") as mock_clear,
        ):
            with pytest.raises(RuntimeError, match="outside backup directory"):
                restore_backup(run)
        mock_clear.assert_not_called()

    def test_archive_outside_backup_dir_raises_before_clear(self, admin_user, backup_settings, tmp_path):
        _, backups = backup_settings
        outside = tmp_path / "evil.tar.gz"
        outside.write_bytes(b"x")
        path = self._write_manifest_file(
            backups,
            {
                "app_version": "1.2.3",
                "database": {"path": str(backups / "db.sql.gz")},
                "uploads": {"archive": str(outside)},
            },
        )
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(path))
        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._clear_media_root") as mock_clear,
        ):
            with pytest.raises(RuntimeError, match="outside backup directory"):
                restore_backup(run)
        mock_clear.assert_not_called()

    def test_dump_database_closes_stdout(self, backup_settings, settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        settings.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "HOST": "localhost",
                "PORT": 3306,
                "USER": "testuser",
                "NAME": "testdb",
                "PASSWORD": "",
            }
        }
        stdout_buf = io.BytesIO(b"")
        mock_process = Mock()
        mock_process.stdout = stdout_buf
        mock_process.stderr = Mock()
        mock_process.stderr.read.return_value = b""
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process):
            _dump_database(db_path)

        assert stdout_buf.closed

    def test_restore_database_closes_connection_before_mysql_subprocess(self, settings, tmp_path):
        dump_path = tmp_path / "db.sql"
        dump_path.write_bytes(b"-- dump\n")
        settings.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "HOST": "localhost",
                "PORT": 3306,
                "USER": "testuser",
                "NAME": "testdb",
                "PASSWORD": "",
            }
        }
        mock_process = Mock()
        mock_process.stdin = io.BytesIO()
        mock_process.stderr = Mock()
        mock_process.stderr.read.return_value = b""
        mock_process.wait.return_value = 0

        call_order = []
        with (
            patch("core.system_ops.connection.vendor", "mysql"),
            patch("core.system_ops.connection.close", side_effect=lambda: call_order.append("close")),
            patch("subprocess.Popen", side_effect=lambda *a, **kw: (call_order.append("popen"), mock_process)[1]) as mock_popen,
        ):
            _restore_database(dump_path, "mysql")

        assert call_order == ["close", "popen"], (
            "connection.close() must run before the mysql restore subprocess starts, "
            "otherwise Django's stale connection breaks with 'MySQL server has gone away' "
            "after the external mysql CLI recreates the database (S-208)"
        )
        mock_popen.assert_called_once()


@pytest.mark.django_db
class TestRestoreVersionCompatibility:
    def _run(self, admin_user, full_version, incremental_version=None, *, full_build="full-build"):
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path="full.manifest.json")
        full = {
            "app_version": full_version,
            "app_build_id": full_build,
            "database": {"path": "db.sql.gz"},
            "uploads": {"archive": "uploads.tar.gz"},
        }
        incremental = None
        if incremental_version is not None:
            incremental = {
                "app_version": incremental_version,
                "app_build_id": "incremental-build",
                "database": {"path": "incremental.sql.gz"},
                "uploads": {"archive": "incremental.tar.gz"},
            }
        return run, full, incremental

    def test_exact_version_allows_restore_and_normalizes_leading_v(self, admin_user, settings):
        settings.APP_VERSION = "1.2.3"
        run, full, incremental = self._run(admin_user, "v1.2.3")

        assert _restore_version_preflight(run, full, incremental) == "ALLOW"

    def test_older_same_major_warns_and_audits_versions_and_build_ids(self, admin_user, settings):
        settings.APP_VERSION = "1.4.0"
        settings.BUILD_INFO = {"build_id": "current-build"}
        run, full, incremental = self._run(admin_user, "1.3.9")

        assert _restore_version_preflight(run, full, incremental) == "WARN"

        audit = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_RESTORE,
            entity_id=run.pk,
            new_values__restore_version_decision="WARN",
        )
        assert audit.new_values["current_app_version"] == "1.4.0"
        assert audit.new_values["current_app_build_id"] == "current-build"
        assert audit.new_values["backups"] == [
            {
                "manifest": "full",
                "backup_app_version": "1.3.9",
                "backup_app_build_id": "full-build",
            }
        ]

    @pytest.mark.parametrize(
        ("current_version", "full_version", "incremental_version", "message"),
        [
            ("", "1.2.3", None, "current application app_version is missing"),
            ("development", "1.2.3", None, "current application app_version is not valid SemVer"),
            ("1.2.3", "1.2.4", None, "newer application version"),
            ("1.2.3", "2.0.0", None, "different major versions"),
            ("1.2.3", None, None, "app_version is missing"),
            ("1.2.3", "1.2", None, "not valid SemVer"),
            ("1.2.3", "1.2.3", "1.2.2", "do not match"),
        ],
    )
    def test_incompatible_version_blocks_before_any_restore_mutation(
        self,
        admin_user,
        backup_settings,
        settings,
        current_version,
        full_version,
        incremental_version,
        message,
    ):
        _, backups = backup_settings
        settings.APP_VERSION = current_version
        full_path = backups / "full.manifest.json"
        full_manifest = {
            "app_build_id": "full-build",
            "database": {"path": str(backups / "db.sql.gz")},
            "uploads": {"archive": str(backups / "uploads.tar.gz")},
        }
        if full_version is not None:
            full_manifest["app_version"] = full_version
        full_path.write_text(json.dumps(full_manifest), encoding="utf-8")

        incremental_path = ""
        if incremental_version is not None:
            incremental_path = backups / "incremental.manifest.json"
            incremental_path.write_text(
                json.dumps(
                    {
                        "app_version": incremental_version,
                        "app_build_id": "incremental-build",
                        "database": {"path": str(backups / "incremental.sql.gz")},
                        "uploads": {"archive": str(backups / "incremental.tar.gz")},
                    }
                ),
                encoding="utf-8",
            )

        run = RestoreRun.objects.create(
            initiated_by=admin_user,
            full_manifest_path=str(full_path),
            incremental_manifest_path=str(incremental_path) if incremental_path else "",
        )
        with (
            patch("core.system_ops.create_backup") as mock_create_backup,
            patch("core.system_ops._extract_uploads_to_staging") as mock_extract,
            patch("core.system_ops._restore_database") as mock_restore_database,
            patch("core.system_ops._swap_staging_to_media") as mock_media_swap,
        ):
            with pytest.raises(RuntimeError, match=message):
                restore_backup(run)

        mock_create_backup.assert_not_called()
        mock_extract.assert_not_called()
        mock_restore_database.assert_not_called()
        mock_media_swap.assert_not_called()
        assert not BackupRun.objects.filter(backup_type=BackupRun.TYPE_PRE_RESTORE).exists()
        audit = AuditLog.objects.get(
            entity_type=AuditLog.ENTITY_RESTORE,
            entity_id=run.pk,
            new_values__restore_version_decision="BLOCK",
        )
        assert audit.new_values["current_app_version"] == current_version
        assert audit.new_values["current_app_build_id"] == ""
        assert audit.new_values["backups"][0]["backup_app_build_id"] == "full-build"
        if incremental_version is not None:
            assert audit.new_values["backups"][1] == {
                "manifest": "incremental",
                "backup_app_version": incremental_version,
                "backup_app_build_id": "incremental-build",
            }


@pytest.mark.django_db
class TestRestoreArtifactVerification:
    """V16-H4: restore must verify backup artifacts against the manifest
    (size/sha256) and abort before touching DB/media on mismatch."""

    def _make_run(self, admin_user, backups, manifest, name="full"):
        manifest.setdefault("app_version", "1.2.3")
        manifest_path = backups / f"{name}.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))

    def _fake_create_backup(self, backup_type, initiated_by=None, run=None):
        run.status = BackupRun.STATUS_SUCCESS
        run.save()
        return run

    def _build_real_uploads_archive(self, path, files):
        with tarfile.open(path, "w:gz") as tar:
            for rel, content in files.items():
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=rel)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    def test_db_dump_size_mismatch_aborts_before_database_restore(self, admin_user, backup_settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        db_path.write_bytes(b"x" * 10)
        uploads_path = backups / "uploads.tar.gz"
        uploads_path.write_bytes(b"fake")
        manifest = {
            "database": {"engine": "sqlite3", "path": str(db_path), "size": 999},
            "uploads": {"archive": str(uploads_path)},
        }
        run = self._make_run(admin_user, backups, manifest)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
            patch("core.system_ops._safe_extract_tar") as mock_extract,
        ):
            with pytest.raises(RuntimeError, match="database dump size mismatch"):
                restore_backup(run)

        mock_restore_db.assert_not_called()
        mock_extract.assert_not_called()

    def test_db_dump_sha256_mismatch_aborts_before_database_restore(self, admin_user, backup_settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        db_path.write_bytes(b"real-content")
        uploads_path = backups / "uploads.tar.gz"
        uploads_path.write_bytes(b"fake")
        manifest = {
            "database": {
                "engine": "sqlite3",
                "path": str(db_path),
                "size": db_path.stat().st_size,
                "sha256": "0" * 64,
            },
            "uploads": {"archive": str(uploads_path)},
        }
        run = self._make_run(admin_user, backups, manifest)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
        ):
            with pytest.raises(RuntimeError, match="database dump checksum mismatch"):
                restore_backup(run)

        mock_restore_db.assert_not_called()

    def test_uploads_archive_size_mismatch_aborts_before_database_restore(self, admin_user, backup_settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        db_path.write_bytes(b"db")
        uploads_path = backups / "uploads.tar.gz"
        uploads_path.write_bytes(b"fake")
        manifest = {
            "database": {"engine": "sqlite3", "path": str(db_path), "size": db_path.stat().st_size},
            "uploads": {"archive": str(uploads_path), "size": 999},
        }
        run = self._make_run(admin_user, backups, manifest)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
        ):
            with pytest.raises(RuntimeError, match="uploads archive size mismatch"):
                restore_backup(run)

        mock_restore_db.assert_not_called()

    def test_uploads_missing_file_after_extraction_aborts_before_database_restore(self, admin_user, backup_settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        db_path.write_bytes(b"db")
        uploads_path = backups / "uploads.tar.gz"
        self._build_real_uploads_archive(uploads_path, {"present.txt": "hello"})
        manifest = {
            "database": {"engine": "sqlite3", "path": str(db_path)},
            "uploads": {
                "archive": str(uploads_path),
                "files": {
                    "present.txt": {"size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()},
                    "missing.txt": {"size": 3, "sha256": "deadbeef"},
                },
            },
        }
        run = self._make_run(admin_user, backups, manifest)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
        ):
            with pytest.raises(RuntimeError, match="missing 1 file"):
                restore_backup(run)

        mock_restore_db.assert_not_called()

    def test_uploads_extra_file_in_archive_aborts_before_database_restore(self, admin_user, backup_settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        db_path.write_bytes(b"db")
        uploads_path = backups / "uploads.tar.gz"
        self._build_real_uploads_archive(uploads_path, {"present.txt": "hello", "extra.txt": "surprise"})
        manifest = {
            "database": {"engine": "sqlite3", "path": str(db_path)},
            "uploads": {
                "archive": str(uploads_path),
                "files": {
                    "present.txt": {"size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()},
                },
            },
        }
        run = self._make_run(admin_user, backups, manifest)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
        ):
            with pytest.raises(RuntimeError, match="extra 1 file"):
                restore_backup(run)

        mock_restore_db.assert_not_called()

    def test_uploads_checksum_mismatch_aborts_before_database_restore(self, admin_user, backup_settings):
        _, backups = backup_settings
        db_path = backups / "db.sql.gz"
        db_path.write_bytes(b"db")
        uploads_path = backups / "uploads.tar.gz"
        self._build_real_uploads_archive(uploads_path, {"present.txt": "tampered"})
        manifest = {
            "database": {"engine": "sqlite3", "path": str(db_path)},
            "uploads": {
                "archive": str(uploads_path),
                "files": {
                    "present.txt": {"size": 8, "sha256": hashlib.sha256(b"original").hexdigest()},
                },
            },
        }
        run = self._make_run(admin_user, backups, manifest)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
        ):
            with pytest.raises(RuntimeError, match="checksum mismatch"):
                restore_backup(run)

        mock_restore_db.assert_not_called()

    @pytest.mark.django_db(transaction=True)
    def test_real_backup_passes_verification_on_restore(self, admin_user, backup_settings, settings):
        uploads, backups = backup_settings
        (uploads / "doc.txt").write_text("hello", encoding="utf-8")
        full = create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=full.manifest_path)

        with (
            patch("core.system_ops.create_backup", side_effect=self._fake_create_backup),
            patch("core.system_ops._restore_database") as mock_restore_db,
        ):
            restore_backup(run)

        mock_restore_db.assert_called_once()
        run.refresh_from_db()
        assert run.status == RestoreRun.STATUS_SUCCESS


@pytest.mark.django_db
class TestRetention:
    """Tests for _apply_retention() manifest-based chain-aware logic."""

    def _make_backup(self, backups, name, backup_type, created_at, baseline=""):
        db_path = backups / f"{name}.sql.gz"
        uploads_path = backups / f"{name}.tar.gz"
        db_path.write_bytes(b"db")
        uploads_path.write_bytes(b"uploads")
        manifest = {
            "version": 1,
            "backup_type": backup_type,
            "created_at": created_at.isoformat(),
            "database": {"engine": "sqlite3", "path": str(db_path), "size": 2},
            "uploads": {
                "archive": str(uploads_path),
                "mode": "incremental" if backup_type == BackupRun.TYPE_INCREMENTAL else "full",
                "files": {},
                "included_files": [],
                "deleted_files": [],
                "baseline_manifest": baseline,
            },
            "total_size": 4,
        }
        manifest_path = backups / f"{name}.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        run = BackupRun.objects.create(
            backup_type=backup_type,
            status=BackupRun.STATUS_SUCCESS,
            manifest_path=str(manifest_path),
            manifest=manifest,
        )
        BackupRun.objects.filter(pk=run.pk).update(created_at=created_at)
        run.refresh_from_db()
        return manifest_path, db_path, uploads_path, run

    def test_retention_deletes_expired_incremental(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 14
        old = timezone.now() - timedelta(days=20)
        full_path, _, _, _ = self._make_backup(backups, "full", BackupRun.TYPE_FULL, old)
        incr_path, incr_db, incr_up, _ = self._make_backup(
            backups, "incr", BackupRun.TYPE_INCREMENTAL, old, baseline=str(full_path)
        )

        _apply_retention()

        assert not incr_path.exists()
        assert not incr_db.exists()
        assert not incr_up.exists()
        # Full is protected as the last full backup
        assert full_path.exists()

    def test_retention_keeps_incremental_within_cutoff(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 14
        old = timezone.now() - timedelta(days=40)
        fresh = timezone.now() - timedelta(days=5)
        full_path, full_db, full_up, _ = self._make_backup(
            backups, "full_old", BackupRun.TYPE_FULL, old
        )
        # A second, newer full so the old one is not "the last"
        new_full_path, _, _, _ = self._make_backup(
            backups, "full_new", BackupRun.TYPE_FULL, fresh
        )
        incr_path, _, _, _ = self._make_backup(
            backups, "incr", BackupRun.TYPE_INCREMENTAL, fresh, baseline=str(full_path)
        )

        _apply_retention()

        # Incremental is fresh — must survive
        assert incr_path.exists()
        # Old full is protected because its incremental is still alive
        assert full_path.exists()

    def test_retention_protects_full_with_active_incremental(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 1
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 14
        old_full = timezone.now() - timedelta(days=5)
        fresh_incr = timezone.now() - timedelta(days=2)
        full_path, full_db, full_up, _ = self._make_backup(
            backups, "full", BackupRun.TYPE_FULL, old_full
        )
        # Newer full so the old one is eligible for deletion checks
        new_full_path, _, _, _ = self._make_backup(
            backups, "full_new", BackupRun.TYPE_FULL, timezone.now()
        )
        incr_path, _, _, _ = self._make_backup(
            backups, "incr", BackupRun.TYPE_INCREMENTAL, fresh_incr, baseline=str(full_path)
        )

        _apply_retention()

        assert full_path.exists()
        assert incr_path.exists()

    def test_retention_deletes_full_when_all_dependents_expired(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 7
        old = timezone.now() - timedelta(days=40)
        full_path, full_db, full_up, _ = self._make_backup(
            backups, "full_old", BackupRun.TYPE_FULL, old
        )
        new_full_path, _, _, _ = self._make_backup(
            backups, "full_new", BackupRun.TYPE_FULL, timezone.now()
        )
        incr_path, incr_db, incr_up, _ = self._make_backup(
            backups, "incr", BackupRun.TYPE_INCREMENTAL, old, baseline=str(full_path)
        )

        _apply_retention()

        assert not full_path.exists()
        assert not full_db.exists()
        assert not full_up.exists()
        assert not incr_path.exists()
        assert not incr_db.exists()
        assert not incr_up.exists()

    def test_retention_never_deletes_last_full(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 0
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 0
        old = timezone.now() - timedelta(days=999)
        full_path, full_db, full_up, _ = self._make_backup(
            backups, "full", BackupRun.TYPE_FULL, old
        )

        _apply_retention()

        assert full_path.exists()
        assert full_db.exists()
        assert full_up.exists()

    def test_retention_skips_active_restore(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 7
        old = timezone.now() - timedelta(days=40)
        full_path, full_db, full_up, _ = self._make_backup(
            backups, "full_old", BackupRun.TYPE_FULL, old
        )
        new_full_path, _, _, _ = self._make_backup(
            backups, "full_new", BackupRun.TYPE_FULL, timezone.now()
        )
        incr_path, _, _, _ = self._make_backup(
            backups, "incr", BackupRun.TYPE_INCREMENTAL, old, baseline=str(full_path)
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path=str(full_path),
        )

        _apply_retention()

        assert full_path.exists()
        assert incr_path.exists()

    def test_retention_uses_backup_run_created_at_not_manifest_string(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        settings.BACKUP_INCREMENTAL_KEEP_DAYS = 7
        old = timezone.now() - timedelta(days=40)
        old_path, old_db, old_up, run = self._make_backup(
            backups,
            "db_old_manifest_future",
            BackupRun.TYPE_INCREMENTAL,
            timezone.now() + timedelta(days=365),
        )
        BackupRun.objects.filter(pk=run.pk).update(created_at=old)

        _apply_retention(now=timezone.now())

        assert not old_path.exists()
        assert not old_db.exists()
        assert not old_up.exists()

    def test_retention_deletes_expired_pre_restore_but_keeps_newest(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_PRE_RESTORE_KEEP_DAYS = 30
        old = timezone.now() - timedelta(days=40)
        newest = timezone.now() - timedelta(days=35)
        old_path, old_db, old_up, old_run = self._make_backup(
            backups,
            "pre_restore_old",
            BackupRun.TYPE_PRE_RESTORE,
            old,
        )
        newest_path, newest_db, newest_up, newest_run = self._make_backup(
            backups,
            "pre_restore_newest",
            BackupRun.TYPE_PRE_RESTORE,
            newest,
        )
        BackupRun.objects.filter(pk=old_run.pk).update(created_at=old)
        BackupRun.objects.filter(pk=newest_run.pk).update(created_at=newest)

        _apply_retention(now=timezone.now())

        assert not old_path.exists()
        assert not old_db.exists()
        assert not old_up.exists()
        assert newest_path.exists()
        assert newest_db.exists()
        assert newest_up.exists()

    def test_retention_skips_pre_restore_used_by_active_restore(self, backup_settings, settings):
        _, backups = backup_settings
        settings.BACKUP_PRE_RESTORE_KEEP_DAYS = 30
        old = timezone.now() - timedelta(days=40)
        path, db_path, uploads_path, run = self._make_backup(
            backups,
            "pre_restore_active",
            BackupRun.TYPE_PRE_RESTORE,
            old,
        )
        BackupRun.objects.filter(pk=run.pk).update(created_at=old)
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path=str(path),
        )

        _apply_retention(now=timezone.now())

        assert path.exists()
        assert db_path.exists()
        assert uploads_path.exists()

    def _make_orphan_manifest(self, backups, name, backup_type, created_at, baseline=""):
        """Manifest + файлы на диске БЕЗ BackupRun (V17-LOW-2: осиротевший бэкап)."""
        db_path = backups / f"{name}.sql.gz"
        uploads_path = backups / f"{name}.tar.gz"
        db_path.write_bytes(b"db")
        uploads_path.write_bytes(b"uploads")
        manifest = {
            "version": 2,
            "backup_type": backup_type,
            "created_at": created_at.isoformat(),
            "database": {"engine": "sqlite3", "path": str(db_path), "size": 2},
            "uploads": {
                "archive": str(uploads_path),
                "mode": "incremental" if backup_type == BackupRun.TYPE_INCREMENTAL else "full",
                "files": {},
                "included_files": [],
                "deleted_files": [],
                "baseline_manifest": baseline,
            },
            "total_size": 4,
        }
        manifest_path = backups / f"{name}.manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path, db_path, uploads_path

    def test_retention_deletes_orphan_manifest_without_backup_run(self, backup_settings, settings):
        """V17-LOW-2: старый манифест на диске без BackupRun удаляется retention."""
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        old = timezone.now() - timedelta(days=40)
        # Новейший full защищён своим BackupRun, орфан — не новейший.
        self._make_backup(backups, "full_new", BackupRun.TYPE_FULL, timezone.now())
        orphan_path, orphan_db, orphan_up = self._make_orphan_manifest(
            backups, "orphan_old", BackupRun.TYPE_FULL, old
        )

        _apply_retention(now=timezone.now())

        assert not orphan_path.exists()
        assert not orphan_db.exists()
        assert not orphan_up.exists()

    def test_retention_protects_orphan_that_is_newest_full(self, backup_settings, settings):
        """V17-LOW-2: орфан, оказавшийся новейшим full, защищён (не удаляется)."""
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 0
        old = timezone.now() - timedelta(days=999)
        orphan_path, orphan_db, orphan_up = self._make_orphan_manifest(
            backups, "orphan_only", BackupRun.TYPE_FULL, old
        )

        _apply_retention(now=timezone.now())

        assert orphan_path.exists()
        assert orphan_db.exists()
        assert orphan_up.exists()

    def test_retention_skips_orphan_under_active_restore(self, backup_settings, settings):
        """V17-LOW-2: орфан под активным restore защищён."""
        _, backups = backup_settings
        settings.BACKUP_FULL_KEEP_DAYS = 30
        old = timezone.now() - timedelta(days=40)
        self._make_backup(backups, "full_new", BackupRun.TYPE_FULL, timezone.now())
        orphan_path, orphan_db, orphan_up = self._make_orphan_manifest(
            backups, "orphan_active", BackupRun.TYPE_FULL, old
        )
        RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path=str(orphan_path),
        )

        _apply_retention(now=timezone.now())

        assert orphan_path.exists()
        assert orphan_db.exists()
        assert orphan_up.exists()

    def test_create_backup_does_not_apply_retention_synchronously(self, backup_settings, admin_user):
        uploads, _ = backup_settings
        (uploads / "doc.txt").write_text("hello", encoding="utf-8")

        with patch("core.system_ops._apply_retention") as mocked_retention:
            create_backup(BackupRun.TYPE_FULL, initiated_by=admin_user)

        mocked_retention.assert_not_called()


@pytest.mark.django_db
class TestDailySchedulerMaintenance:
    def test_daily_maintenance_runs_clearsessions_import_cleanup_documents_and_retention(
        self,
        backup_settings,
        admin_user,
        settings,
    ):
        settings.IMPORT_ROW_RESULTS_KEEP_DAYS = 180
        settings.DELETED_DOCUMENT_FILE_KEEP_DAYS = 30
        uploads, _ = backup_settings
        old_log = ImportLog.objects.create(
            shipment_type=ImportLog.SHIPMENT_TYPE_AUTO,
            filename="old.xlsx",
            status=ImportLog.STATUS_SUCCESS,
            total_rows=1,
            imported_rows=1,
            created_by=admin_user,
        )
        ImportLog.objects.filter(pk=old_log.pk).update(
            created_at=timezone.now() - timedelta(days=200)
        )
        row = ImportRowResult.objects.create(
            import_log=old_log,
            row_num=2,
            status=ImportRowResult.STATUS_CREATED,
            source_data={"customer_object": "old"},
        )
        rel_path = "auto/2026/02/shipment_1/deleted.pdf"
        abs_path = uploads / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF-1.4 deleted")
        shipment = AutoShipment.objects.create(
            shipment_date="2026-02-01",
            customer_object="Cleanup",
            coal_grade="ДГ",
            quantity="100",
            created_by=admin_user,
            updated_by=admin_user,
        )
        doc = ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="deleted.pdf",
            stored_file_name="deleted.pdf",
            file_path=rel_path,
            file_size=17,
            uploaded_by=admin_user,
            is_deleted=True,
            deleted_at=timezone.now() - timedelta(days=31),
        )

        from core.system_ops import _run_daily_scheduler_maintenance
        SystemState.objects.get_or_create(singleton_key=1)
        with (
            patch("core.system_ops.call_command") as mocked_call,
            patch("core.system_ops._apply_retention", return_value={"deleted_entries": 0}) as mocked_retention,
        ):
            result = _run_daily_scheduler_maintenance(now=timezone.now())

        mocked_call.assert_called_once_with("clearsessions")
        mocked_retention.assert_called_once()
        assert not ImportRowResult.objects.filter(pk=row.pk).exists()
        assert ImportLog.objects.filter(pk=old_log.pk).exists()
        doc.refresh_from_db()
        assert not abs_path.exists()
        assert doc.file_deleted_at is not None
        assert result["import_row_results_deleted"] == 1
        assert result["document_files_deleted"] == 1
        state = SystemState.objects.get(singleton_key=1)
        assert state.daily_cleanup_last_run_at is not None
        assert state.daily_cleanup_last_result["document_files_deleted"] == 1

    def test_daily_maintenance_skips_legacy_deleted_documents_without_deleted_at(
        self,
        backup_settings,
        admin_user,
        settings,
    ):
        settings.DELETED_DOCUMENT_FILE_KEEP_DAYS = 30
        uploads, _ = backup_settings
        rel_path = "auto/2026/02/shipment_1/legacy.pdf"
        abs_path = uploads / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(b"%PDF-1.4 legacy")
        shipment = AutoShipment.objects.create(
            shipment_date="2026-02-01",
            customer_object="Cleanup",
            coal_grade="ДГ",
            quantity="100",
            created_by=admin_user,
            updated_by=admin_user,
        )
        ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=shipment.pk,
            document_type=ShipmentDocument.DOCUMENT_TYPE_TTN,
            original_file_name="legacy.pdf",
            stored_file_name="legacy.pdf",
            file_path=rel_path,
            file_size=17,
            uploaded_by=admin_user,
            is_deleted=True,
            deleted_at=None,
        )

        from core.system_ops import _run_daily_scheduler_maintenance
        result = _run_daily_scheduler_maintenance(now=timezone.now())

        assert abs_path.exists()
        assert result["legacy_deleted_documents_skipped"] == 1

@pytest.mark.django_db
class TestSchedulerHeartbeat:
    def test_run_scheduler_tick_updates_heartbeat(self):
        # V18-MED-1: heartbeat update-only. Реальный scheduler создаёт синглтон
        # (get_or_create) на старте до цикла тиков — воспроизводим это условие.
        SystemState.objects.get_or_create(singleton_key=1)
        before = timezone.now()
        run_scheduler_tick()
        state = SystemState.objects.get(singleton_key=1)
        assert state.scheduler_heartbeat_at is not None
        assert state.scheduler_heartbeat_at >= before

    def test_system_page_shows_heartbeat(self, client, admin_user):
        SystemState.objects.update_or_create(
            singleton_key=1,
            defaults={"scheduler_heartbeat_at": timezone.now()},
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert response.status_code == 200
        assert response.context["scheduler_heartbeat_at"] is not None

    def test_scheduler_is_stale_when_old_heartbeat(self, client, admin_user):
        old_heartbeat = timezone.now() - timedelta(minutes=10)
        SystemState.objects.update_or_create(
            singleton_key=1,
            defaults={"scheduler_heartbeat_at": old_heartbeat},
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert response.context["scheduler_is_stale"] is True

    def test_scheduler_is_not_stale_when_recent_heartbeat(self, client, admin_user, settings):
        settings.SCHEDULER_WARN_SECONDS = 180
        SystemState.objects.update_or_create(
            singleton_key=1,
            defaults={"scheduler_heartbeat_at": timezone.now()},
        )
        client.login(username="system_admin", password="pass")
        response = client.get(reverse("core:system_status"))
        assert response.context["scheduler_is_stale"] is False

    def test_run_scheduler_tick_wraps_long_operation_with_heartbeat(self):
        SystemState.objects.get_or_create(singleton_key=1)
        BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
        )
        with (
            patch("core.system_ops._run_with_scheduler_heartbeat", side_effect=lambda fn: fn()) as wrapped,
            patch("core.system_ops.create_backup"),
        ):
            run_scheduler_tick()

        assert wrapped.called

    def test_system_page_shows_active_running_operation_label(self, client, admin_user):
        run = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=timezone.now(),
        )
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))

        assert response.context["active_running_operation_label"] == f"Backup #{run.pk}"
        assert f"выполняется Backup #{run.pk}" in response.content.decode()

    def test_schedule_with_null_next_run_enqueues_first_due_slot(self):
        SystemState.objects.get_or_create(singleton_key=1)
        # V-fix B-952: якорим тест на фиксированный полдень, чтобы run_time (полдень − 5 мин)
        # не пересекал полночь и слот гарантированно был due независимо от времени запуска.
        now = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        BackupSchedule.objects.filter(backup_type=BackupRun.TYPE_FULL).update(
            enabled=True,
            weekdays=str(now.weekday()),
            run_time=(now - timedelta(minutes=5)).time(),
            next_run_at=None,
        )

        result = run_scheduler_tick(now=now)

        assert result["claimed"] is True
        assert BackupRun.objects.filter(
            backup_type=BackupRun.TYPE_FULL,
            source=BackupRun.SOURCE_SCHEDULER,
        ).exists()

    def test_scheduler_daily_maintenance_runs_once_per_day(self):
        now = timezone.now()
        SystemState.objects.update_or_create(
            singleton_key=1,
            defaults={"daily_cleanup_last_run_at": now},
        )

        with patch("core.system_ops._run_daily_scheduler_maintenance") as mocked:
            run_scheduler_tick(now=now + timedelta(hours=1))

        mocked.assert_not_called()

        with patch("core.system_ops._run_daily_scheduler_maintenance") as mocked:
            run_scheduler_tick(now=now + timedelta(days=1, minutes=1))

        mocked.assert_called_once()


@pytest.mark.django_db
class TestSystemStateWriters:
    """V18-MED-1: сторонние писатели не должны INSERT-ить строку-синглтон
    SystemState — иначе в окне DROP/CREATE→INSERT mysql-дампа их вставка
    конфликтует с INSERT из дампа и валит restore."""

    def test_touch_heartbeat_does_not_insert_when_singleton_absent(self):
        SystemState.objects.all().delete()
        _touch_scheduler_heartbeat()
        assert SystemState.objects.count() == 0

    def test_touch_heartbeat_updates_existing_singleton(self):
        SystemState.objects.update_or_create(singleton_key=1, defaults={"scheduler_heartbeat_at": None})
        before = timezone.now()
        _touch_scheduler_heartbeat()
        state = SystemState.objects.get(singleton_key=1)
        assert state.scheduler_heartbeat_at is not None
        assert state.scheduler_heartbeat_at >= before

    def test_get_system_state_readonly_returns_none_without_insert(self):
        SystemState.objects.all().delete()
        assert get_system_state_readonly() is None
        assert SystemState.objects.count() == 0

    def test_middleware_passes_through_without_singleton_and_no_insert(self, rf):
        from django.contrib.auth.models import AnonymousUser

        from .middleware import MaintenanceModeMiddleware

        SystemState.objects.all().delete()
        sentinel = object()
        middleware = MaintenanceModeMiddleware(lambda request: sentinel)
        request = rf.get("/some/protected/path/")
        request.user = AnonymousUser()

        assert middleware(request) is sentinel
        assert SystemState.objects.count() == 0

    def test_readyz_without_singleton_does_not_insert(self, client):
        SystemState.objects.all().delete()

        response = client.get(reverse("core:readyz"))

        assert response.status_code in (200, 503)
        assert SystemState.objects.count() == 0

    def test_readyz_database_error_does_not_read_or_insert_singleton(self, client):
        SystemState.objects.all().delete()

        with patch("core.system_ops.database_health", side_effect=OperationalError("db down")):
            response = client.get(reverse("core:readyz"))

        assert response.status_code == 503
        assert response.json()["status"] == "error"
        assert SystemState.objects.count() == 0

    def test_system_page_without_singleton_does_not_insert(self, client, admin_user):
        SystemState.objects.all().delete()
        client.login(username="system_admin", password="pass")

        response = client.get(reverse("core:system_status"))

        assert response.status_code == 200
        assert SystemState.objects.count() == 0

    def test_recalculate_uploads_size_without_singleton_does_not_insert(self):
        from core.system_ops import recalculate_uploads_size

        SystemState.objects.all().delete()

        assert isinstance(recalculate_uploads_size(), int)
        assert SystemState.objects.count() == 0

    def test_scheduler_maintenance_without_singleton_does_not_insert(self):
        from core.system_ops import _maybe_run_daily_scheduler_maintenance

        SystemState.objects.all().delete()

        with patch("core.system_ops._run_daily_scheduler_maintenance") as maintenance:
            _maybe_run_daily_scheduler_maintenance(timezone.now())

        maintenance.assert_not_called()
        assert SystemState.objects.count() == 0

    def test_scheduler_claim_without_singleton_does_not_insert(self):
        from core.system_ops import claim_scheduler_operation

        SystemState.objects.all().delete()
        BackupRun.objects.create(backup_type=BackupRun.TYPE_FULL, status=BackupRun.STATUS_QUEUED)

        assert claim_scheduler_operation() is None
        assert SystemState.objects.count() == 0


@pytest.mark.django_db
class TestStaleAgeRecovery:
    def test_fresh_running_operation_not_cancelled(self, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=timezone.now(),
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path="x.manifest.json",
            started_at=timezone.now(),
        )
        result = recover_stale_running_operations_on_scheduler_start()
        backup.refresh_from_db()
        restore.refresh_from_db()
        assert result["backup_count"] == 0
        assert result["restore_count"] == 0
        assert backup.status == BackupRun.STATUS_RUNNING
        assert restore.status == RestoreRun.STATUS_RUNNING

    def test_stale_running_operation_cancelled(self, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        stale_time = timezone.now() - timedelta(minutes=20)
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=stale_time,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path="x.manifest.json",
            started_at=stale_time,
        )
        result = recover_stale_running_operations_on_scheduler_start()
        backup.refresh_from_db()
        restore.refresh_from_db()
        assert result["backup_count"] == 1
        assert result["restore_count"] == 1
        assert backup.status == BackupRun.STATUS_ERROR
        assert restore.status == RestoreRun.STATUS_ERROR

    def test_null_started_at_is_stale(self, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=None,
        )
        result = recover_stale_running_operations_on_scheduler_start()
        backup.refresh_from_db()
        assert result["backup_count"] == 1
        assert backup.status == BackupRun.STATUS_ERROR

    def test_scheduler_start_does_not_cancel_stale_running_when_heartbeat_fresh(self, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        settings.SCHEDULER_WARN_SECONDS = 180
        stale_time = timezone.now() - timedelta(minutes=20)
        SystemState.objects.update_or_create(
            singleton_key=1,
            defaults={
                "mode": SystemState.MODE_RESTORE_RUNNING,
                "scheduler_heartbeat_at": timezone.now(),
            },
        )
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=stale_time,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path="x.manifest.json",
            started_at=stale_time,
        )

        result = recover_stale_running_operations_on_scheduler_start()

        backup.refresh_from_db()
        restore.refresh_from_db()
        assert result["backup_count"] == 0
        assert result["restore_count"] == 0
        assert result["scheduler_alive"] is True
        assert result["recovery_refused"] is True
        assert backup.status == BackupRun.STATUS_RUNNING
        assert restore.status == RestoreRun.STATUS_RUNNING
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_RESTORE_RUNNING


@pytest.mark.django_db
class TestRecoverAffectedIds:
    def test_recover_interrupted_restore_locks_singleton_row(self, admin_user):
        """V18-LOW-1: recover_interrupted_restore берёт SystemState под
        select_for_update (по образцу sibling recover_stale_...)."""
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        with patch.object(
            SystemState.objects,
            "select_for_update",
            wraps=SystemState.objects.select_for_update,
        ) as locked:
            recover_interrupted_restore(admin_user)
        assert locked.called

    def test_recover_includes_affected_ids_in_result(self, admin_user):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            initiated_by=admin_user,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            full_manifest_path="x.manifest.json",
            initiated_by=admin_user,
        )
        from core.system_ops import recover_interrupted_restore
        result = recover_interrupted_restore(admin_user)
        assert backup.pk in result["affected_backup_ids"]
        assert restore.pk in result["affected_restore_ids"]

    def test_recover_view_message_contains_ids(self, client, admin_user):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            initiated_by=admin_user,
        )
        client.login(username="system_admin", password="pass")
        response = client.post(reverse("core:recover_restore"), follow=True)
        content = response.content.decode()
        assert f"backup #{backup.pk}" in content

    def test_recover_does_not_cancel_fresh_running_operations(self, admin_user, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=timezone.now(),
            initiated_by=admin_user,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            started_at=timezone.now(),
            full_manifest_path="x.manifest.json",
            initiated_by=admin_user,
        )

        result = recover_interrupted_restore(admin_user)

        backup.refresh_from_db()
        restore.refresh_from_db()
        assert result["backup_count"] == 0
        assert result["restore_count"] == 0
        assert result["affected_backup_ids"] == []
        assert result["affected_restore_ids"] == []
        assert backup.status == BackupRun.STATUS_RUNNING
        assert restore.status == RestoreRun.STATUS_RUNNING
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_RESTORE_RUNNING

    def test_recover_cancels_stale_running_operations(self, admin_user, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        stale_time = timezone.now() - timedelta(minutes=20)
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=stale_time,
            initiated_by=admin_user,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_RUNNING,
            started_at=stale_time,
            full_manifest_path="x.manifest.json",
            initiated_by=admin_user,
        )

        result = recover_interrupted_restore(admin_user)

        backup.refresh_from_db()
        restore.refresh_from_db()
        assert result["backup_count"] == 1
        assert result["restore_count"] == 1
        assert backup.pk in result["affected_backup_ids"]
        assert restore.pk in result["affected_restore_ids"]
        assert backup.status == BackupRun.STATUS_ERROR
        assert restore.status == RestoreRun.STATUS_ERROR

    def test_recover_cancels_queued_operations_immediately(self, admin_user, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_QUEUED,
            initiated_by=admin_user,
        )
        restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            started_at=timezone.now(),
            full_manifest_path="x.manifest.json",
            initiated_by=admin_user,
        )

        result = recover_interrupted_restore(admin_user)

        backup.refresh_from_db()
        restore.refresh_from_db()
        assert result["backup_count"] == 1
        assert result["restore_count"] == 1
        assert backup.pk in result["affected_backup_ids"]
        assert restore.pk in result["affected_restore_ids"]
        assert backup.status == BackupRun.STATUS_ERROR
        assert restore.status == RestoreRun.STATUS_ERROR

    def test_recover_view_reports_fresh_running_operations_are_not_stale(
        self,
        client,
        admin_user,
        settings,
    ):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=timezone.now(),
            initiated_by=admin_user,
        )
        client.login(username="system_admin", password="pass")

        response = client.post(reverse("core:recover_restore"), follow=True)

        backup.refresh_from_db()
        content = response.content.decode()
        assert backup.status == BackupRun.STATUS_RUNNING
        assert "ещё не считаются зависшими" in content

    def test_recover_forces_admin_only_when_mode_stuck_without_records(self, admin_user):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")

        result = recover_interrupted_restore(admin_user)

        assert result["backup_count"] == 0
        assert result["restore_count"] == 0
        assert result["mode_was_stuck"] is True
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_ADMIN_ONLY

    def test_recover_view_handles_stuck_mode_without_records(self, client, admin_user):
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        client.login(username="system_admin", password="pass")

        response = client.post(reverse("core:recover_restore"), follow=True)

        assert response.status_code == 200
        content = response.content.decode()
        assert "не считаются зависшими" not in content
        assert "профилактику" in content
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_ADMIN_ONLY

    def test_recover_refuses_without_force_when_scheduler_heartbeat_fresh(
        self,
        admin_user,
        settings,
    ):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        settings.SCHEDULER_WARN_SECONDS = 180
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        SystemState.objects.filter(singleton_key=1).update(scheduler_heartbeat_at=timezone.now())
        stale_time = timezone.now() - timedelta(minutes=20)
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=stale_time,
            initiated_by=admin_user,
        )

        result = recover_interrupted_restore(admin_user)

        backup.refresh_from_db()
        assert result["backup_count"] == 0
        assert result["scheduler_alive"] is True
        assert result["recovery_refused"] is True
        assert backup.status == BackupRun.STATUS_RUNNING
        assert SystemState.objects.get(singleton_key=1).mode == SystemState.MODE_RESTORE_RUNNING

    def test_recover_force_cancels_even_when_scheduler_heartbeat_fresh(self, admin_user, settings):
        settings.SCHEDULER_STALE_AGE_MINUTES = 15
        settings.SCHEDULER_WARN_SECONDS = 180
        set_system_mode(SystemState.MODE_RESTORE_RUNNING, admin_user, "restore")
        SystemState.objects.filter(singleton_key=1).update(scheduler_heartbeat_at=timezone.now())
        stale_time = timezone.now() - timedelta(minutes=20)
        backup = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_FULL,
            status=BackupRun.STATUS_RUNNING,
            started_at=stale_time,
            initiated_by=admin_user,
        )

        result = recover_interrupted_restore(admin_user, force=True)

        backup.refresh_from_db()
        assert result["backup_count"] == 1
        assert result["scheduler_alive"] is True
        assert result["recovery_refused"] is False
        assert backup.status == BackupRun.STATUS_ERROR


@pytest.mark.django_db
class TestAtomicRestore:
    def _make_manifest(self, backups, archive):
        import json as _json
        manifest = {
            "app_version": "1.2.3",
            "database": {"engine": "sqlite3", "path": str(backups / "db.sql.gz")},
            "uploads": {"archive": str(archive)},
        }
        path = backups / "full.manifest.json"
        path.write_text(_json.dumps(manifest), encoding="utf-8")
        return path

    def test_swap_staging_to_media_replaces_content_and_removes_holder(self, backup_settings, tmp_path):
        uploads, _ = backup_settings
        (uploads / "old.txt").write_text("old", encoding="utf-8")
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "new.txt").write_text("new", encoding="utf-8")

        _swap_staging_to_media(staging)

        assert not (uploads / "old.txt").exists()
        assert (uploads / "new.txt").read_text(encoding="utf-8") == "new"
        assert list(uploads.glob(".restore_old.*")) == []

    def test_swap_staging_to_media_rolls_back_on_failure(self, backup_settings, tmp_path):
        uploads, _ = backup_settings
        (uploads / "old.txt").write_text("old", encoding="utf-8")
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "new.txt").write_text("new", encoding="utf-8")
        real_move = shutil.move

        def fail_incoming_move(src, dst):
            if Path(src).parent == staging:
                raise RuntimeError("simulated move failure")
            return real_move(src, dst)

        with patch("core.system_ops.shutil.move", side_effect=fail_incoming_move):
            with pytest.raises(RuntimeError, match="move failure"):
                _swap_staging_to_media(staging)

        assert uploads.exists()
        assert (uploads / "old.txt").read_text(encoding="utf-8") == "old"
        assert not (uploads / "new.txt").exists()
        assert list(uploads.glob(".restore_old.*")) == []

    def test_swap_never_moves_or_removes_media_root_directory(self, backup_settings):
        """Prod MEDIA_ROOT is a bind-mount point: moving or rmdir'ing the
        directory itself fails EBUSY. The swap must only touch its *contents*
        (V17-HIGH-1)."""
        import tempfile as _tempfile

        uploads, _ = backup_settings
        (uploads / "old.txt").write_text("old", encoding="utf-8")
        staging = Path(_tempfile.mkdtemp(prefix=".restore_staging_", dir=str(uploads)))
        (staging / "new.txt").write_text("new", encoding="utf-8")
        real_move = shutil.move
        real_rmtree = shutil.rmtree

        def guard_move(src, dst):
            if Path(src) == uploads:
                raise OSError("EBUSY: cannot move mount point")
            return real_move(src, dst)

        def guard_rmtree(path, *args, **kwargs):
            if Path(path) == uploads:
                raise OSError("EBUSY: cannot remove mount point")
            return real_rmtree(path, *args, **kwargs)

        with (
            patch("core.system_ops.shutil.move", side_effect=guard_move),
            patch("core.system_ops.shutil.rmtree", side_effect=guard_rmtree),
        ):
            _swap_staging_to_media(staging)

        assert not (uploads / "old.txt").exists()
        assert (uploads / "new.txt").read_text(encoding="utf-8") == "new"
        assert list(uploads.glob(".restore_old.*")) == []

    def test_extract_uploads_to_staging_places_staging_inside_media_root(self, backup_settings, tmp_path):
        from core.system_ops import _extract_uploads_to_staging

        uploads, backups = backup_settings
        archive = backups / "full.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            data = b"content"
            info = tarfile.TarInfo(name="doc.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        manifest = {"uploads": {"archive": str(archive)}}

        staging = _extract_uploads_to_staging(manifest)
        try:
            assert staging.parent == uploads
            assert staging.name.startswith(".restore_staging_")
            assert (staging / "doc.txt").read_bytes() == b"content"
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def test_mysql_drop_preamble_lists_all_tables_quoted(self):
        from core.system_ops import _mysql_drop_all_preamble

        sql = _mysql_drop_all_preamble(["auth_user", "we`ird"]).decode("utf-8")
        assert "SET FOREIGN_KEY_CHECKS=0;" in sql
        assert "SET FOREIGN_KEY_CHECKS=1;" in sql
        assert "DROP TABLE IF EXISTS `auth_user`, `we``ird`;" in sql

    def test_mysql_drop_preamble_empty_when_no_tables(self):
        from core.system_ops import _mysql_drop_all_preamble

        assert _mysql_drop_all_preamble([]) == b""

    def test_restore_database_rejects_engine_vendor_mismatch(self, tmp_path):
        dump = tmp_path / "db.sql.gz"
        dump.write_bytes(b"")
        called = []
        with (
            patch("core.system_ops.connection.vendor", "sqlite"),
            patch("core.system_ops._restore_sqlite_database", side_effect=lambda *a, **k: called.append("sqlite")),
            patch("subprocess.Popen", side_effect=lambda *a, **k: called.append("popen")),
        ):
            with pytest.raises(RuntimeError, match="does not match database vendor"):
                _restore_database(dump, "mysql")
        assert called == [], "no restore path may run on engine/vendor mismatch"

    def test_restore_database_pauses_heartbeat_during_mysql_load(self, settings, tmp_path):
        from core.system_ops import _scheduler_heartbeat_paused

        dump_path = tmp_path / "db.sql"
        dump_path.write_bytes(b"-- dump\n")
        settings.DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "HOST": "localhost",
                "PORT": 3306,
                "USER": "u",
                "NAME": "db",
                "PASSWORD": "",
            }
        }
        mock_process = Mock()
        mock_process.stdin = io.BytesIO()
        mock_process.stderr = Mock()
        mock_process.stderr.read.return_value = b""
        mock_process.wait.return_value = 0

        observed = {}

        def observe_copy(src, dst, length=None):
            observed["paused_during_load"] = _scheduler_heartbeat_paused.is_set()

        assert not _scheduler_heartbeat_paused.is_set()
        with (
            patch("core.system_ops.connection.vendor", "mysql"),
            patch("core.system_ops.connection.close"),
            patch("subprocess.Popen", return_value=mock_process),
            patch("core.system_ops.shutil.copyfileobj", side_effect=observe_copy),
        ):
            _restore_database(dump_path, "mysql")

        assert observed["paused_during_load"] is True
        assert not _scheduler_heartbeat_paused.is_set(), "heartbeat pause must clear after load"

    def test_staging_failure_does_not_touch_db(self, admin_user, backup_settings):
        _, backups = backup_settings
        full_archive = backups / "full.tar.gz"
        full_archive.write_bytes(b"fake")
        manifest_path = self._make_manifest(backups, full_archive)
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))

        def fake_create_backup(backup_type, initiated_by=None, run=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.save()
            return run

        db_called = []

        def fake_extract_tar(archive, target):
            raise RuntimeError("Simulated tar failure")

        with (
            patch("core.system_ops.create_backup", side_effect=fake_create_backup),
            patch("core.system_ops._restore_database", side_effect=lambda *a, **kw: db_called.append(True)),
            patch("core.system_ops._safe_extract_tar", side_effect=fake_extract_tar),
        ):
            with pytest.raises(RuntimeError, match="tar failure"):
                restore_backup(run)

        assert not db_called, "DB must not be touched when staging extraction fails"

    @pytest.mark.django_db(transaction=True)
    def test_restore_staging_cleanup_on_success(self, admin_user, backup_settings):
        import tempfile as _tempfile
        from pathlib import Path as _Path
        _, backups = backup_settings
        full_archive = backups / "full.tar.gz"
        full_archive.write_bytes(b"fake")
        manifest_path = self._make_manifest(backups, full_archive)
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))

        staging_dirs = []
        real_mkdtemp = _tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            staging_dirs.append(d)
            return d

        def fake_create_backup(backup_type, initiated_by=None, run=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.save()
            return run

        with (
            patch("core.system_ops.create_backup", side_effect=fake_create_backup),
            patch("core.system_ops._restore_database"),
            patch("core.system_ops._safe_extract_tar"),
            patch("core.system_ops._clear_media_root"),
            patch("core.system_ops.tempfile.mkdtemp", side_effect=tracking_mkdtemp),
        ):
            restore_backup(run)

        assert staging_dirs, "mkdtemp should have been called"
        for d in staging_dirs:
            assert not _Path(d).exists(), f"Staging dir {d} was not cleaned up"

    @pytest.mark.django_db(transaction=True)
    def test_restore_reasserts_restore_mode_before_media_swap(self, admin_user, backup_settings):
        _, backups = backup_settings
        full_archive = backups / "full.tar.gz"
        full_archive.write_bytes(b"fake")
        manifest_path = self._make_manifest(backups, full_archive)
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))

        def fake_create_backup(backup_type, initiated_by=None, run=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.save()
            return run

        def fake_restore_database(path, engine):
            SystemState.objects.update_or_create(
                singleton_key=1,
                defaults={"mode": SystemState.MODE_NORMAL, "reason": "from dump"},
            )

        def assert_restore_mode_before_swap(staging):
            state = SystemState.objects.get(singleton_key=1)
            assert state.mode == SystemState.MODE_RESTORE_RUNNING

        with (
            patch("core.system_ops.create_backup", side_effect=fake_create_backup),
            patch("core.system_ops._restore_database", side_effect=fake_restore_database),
            patch("core.system_ops._safe_extract_tar"),
            patch("core.system_ops.call_command", create=True),
            patch("core.system_ops._swap_staging_to_media", side_effect=assert_restore_mode_before_swap),
        ):
            restore_backup(run)

    @pytest.mark.django_db(transaction=True)
    def test_restore_runs_post_restore_commands_before_media_swap(self, admin_user, backup_settings):
        _, backups = backup_settings
        full_archive = backups / "full.tar.gz"
        full_archive.write_bytes(b"fake")
        manifest_path = self._make_manifest(backups, full_archive)
        run = RestoreRun.objects.create(initiated_by=admin_user, full_manifest_path=str(manifest_path))
        events = []

        def fake_create_backup(backup_type, initiated_by=None, run=None):
            run.status = BackupRun.STATUS_SUCCESS
            run.save()
            return run

        def fake_restore_database(path, engine):
            events.append("restore_database")

        def fake_call_command(name, *args, **kwargs):
            events.append(name)

        def fake_swap(staging):
            events.append("swap")

        with (
            patch("core.system_ops.create_backup", side_effect=fake_create_backup),
            patch("core.system_ops._restore_database", side_effect=fake_restore_database),
            patch("core.system_ops._safe_extract_tar"),
            patch("core.system_ops.call_command", side_effect=fake_call_command, create=True),
            patch("core.system_ops._swap_staging_to_media", side_effect=fake_swap),
        ):
            restore_backup(run)

        assert events == [
            "restore_database",
            "migrate",
            "seed_groups",
            "seed_field_config",
            "swap",
        ]


@pytest.mark.django_db
class TestRestoreBackupCommandGuards:
    def test_rejects_when_another_operation_active(self, admin_user, backup_settings):
        run = RestoreRun.objects.create(
            initiated_by=admin_user, status=RestoreRun.STATUS_QUEUED, full_manifest_path="x"
        )
        BackupRun.objects.create(backup_type=BackupRun.TYPE_FULL, status=BackupRun.STATUS_RUNNING)
        with pytest.raises(CommandError, match="already active"):
            call_command("restore_backup", "--restore-run-id", str(run.pk))
        run.refresh_from_db()
        assert run.status == RestoreRun.STATUS_QUEUED

    def test_rejects_non_queued_run(self, admin_user, backup_settings):
        run = RestoreRun.objects.create(
            initiated_by=admin_user, status=RestoreRun.STATUS_RUNNING, full_manifest_path="x"
        )
        with pytest.raises(CommandError, match="not queued"):
            call_command("restore_backup", "--restore-run-id", str(run.pk))

    def test_missing_run_raises(self):
        with pytest.raises(CommandError, match="not found"):
            call_command("restore_backup", "--restore-run-id", "999999")

    def test_claims_queued_run_and_invokes_restore(self, admin_user, backup_settings):
        run = RestoreRun.objects.create(
            initiated_by=admin_user, status=RestoreRun.STATUS_QUEUED, full_manifest_path="x"
        )
        with patch("core.management.commands.restore_backup.restore_backup") as mock_restore:
            call_command("restore_backup", "--restore-run-id", str(run.pk))
        mock_restore.assert_called_once()
        run.refresh_from_db()
        assert run.status == RestoreRun.STATUS_RUNNING
