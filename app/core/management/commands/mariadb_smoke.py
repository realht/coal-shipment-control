import json
import os
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client

from core.models import BackupRun, RestoreRun
from documents.models import ShipmentDocument
from shipments_auto.models import AutoShipment
from core.management.commands.run_scheduler import acquire_scheduler_lock

_SMOKE_PDF = b"%PDF-1.4\n%mariadb-smoke\n%%EOF\n"


class Command(BaseCommand):
    help = (
        "Production-like MariaDB acceptance: least-privilege grants, migrations, "
        "pytest, full/incremental restore, scheduler advisory-lock drill and report."
    )

    def handle(self, *args, **options):
        self.started_at = datetime.now(timezone.utc)
        steps = []

        self._step(steps, "least-privilege database grants", self._check_database_grants)
        self._step(steps, "collectstatic", lambda: call_command("collectstatic", "--noinput", "--clear"))
        self._step(steps, "migrate", lambda: call_command("migrate", "--noinput"))
        self._step(steps, "seed_groups", lambda: call_command("seed_groups"))
        self._step(steps, "seed_field_config", lambda: call_command("seed_field_config"))

        seeded = {}
        self._step(steps, "seed minimal data (user/shipment/document)", lambda: seeded.update(self._seed()))
        self._step(
            steps,
            "pytest suite against MariaDB (import/export, documents, permissions matrix)",
            self._run_pytest,
        )
        self._step(steps, "full and incremental backup/restore drill", lambda: self._run_backup_restore(seeded))
        self._step(steps, "scheduler overlap and lost-lock drill", self._run_scheduler_lock_drill)

        self._print_summary(steps)
        self._write_report(steps)
        if any(step["status"] != "PASS" for step in steps):
            raise CommandError("MariaDB acceptance contour failed")

    def _step(self, steps, name, fn):
        self.stdout.write(f"==> {name}")
        started = time.monotonic()
        try:
            fn()
            steps.append({"name": name, "status": "PASS", "duration_seconds": round(time.monotonic() - started, 3), "error": ""})
        except Exception as exc:
            steps.append({"name": name, "status": "FAIL", "duration_seconds": round(time.monotonic() - started, 3), "error": str(exc)})

    def _check_database_grants(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), CURRENT_USER()")
            self.database_version, self.database_user = cursor.fetchone()
            cursor.execute("SHOW GRANTS FOR CURRENT_USER")
            self.database_grants = [row[0] for row in cursor.fetchall()]

        joined = "\n".join(self.database_grants).upper()
        for schema in ("COAL_SMOKE", "TEST_COAL_SMOKE"):
            if f"`{schema}`.*" not in joined:
                raise RuntimeError(f"missing database-scoped grant for {schema.lower()}")
        non_usage_global = any(" ON *.*" in grant.upper() and not grant.upper().startswith("GRANT USAGE ") for grant in self.database_grants)
        forbidden = ("GRANT OPTION", "ALL PRIVILEGES", "SUPER", "PROCESS")
        if non_usage_global or any(item in joined for item in forbidden):
            raise RuntimeError("acceptance database user has an administrative/global grant")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT User FROM mysql.user LIMIT 1")
        except Exception:
            connection.rollback()
        else:
            raise RuntimeError("acceptance database user can read mysql.user")

    def _seed(self):
        User = get_user_model()
        user = User.objects.create_superuser(username="mariadb_smoke", password="mariadb-smoke-pw-001")

        shipment = AutoShipment.objects.create(
            shipment_date="2026-01-15",
            customer_object="Smoke Object",
            coal_grade="ДГ",
            quantity=Decimal("12.345"),
        )

        client = Client()
        client.force_login(user)
        response = client.post(
            f"/documents/auto/{shipment.pk}/upload/",
            data={
                "document_type": ShipmentDocument.DOCUMENT_TYPE_OTHER,
                "file": SimpleUploadedFile("smoke.pdf", _SMOKE_PDF, content_type="application/pdf"),
            },
        )
        if response.status_code != 302:
            snippet = response.content.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"document upload failed: status={response.status_code} content_snippet={snippet!r}")

        document = ShipmentDocument.objects.get(shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO, shipment_id=shipment.pk)
        return {"shipment_id": shipment.pk, "document_id": document.pk}

    def _run_pytest(self):
        (Path(settings.BASE_DIR) / ".tmp").mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "pytest",
                "--ds=config.settings.test_mariadb",
                "--ignore=core/tests_deployment.py",
                "--ignore=test_project_hygiene.py",
                "--deselect=core/tests.py::test_settings_database_is_sqlite",
            ],
            cwd=settings.BASE_DIR,
            timeout=int(os.environ.get("ACCEPTANCE_PYTEST_TIMEOUT_SECONDS", "1800")),
        )
        if result.returncode != 0:
            raise RuntimeError(f"pytest exited with code {result.returncode}")

    def _run_backup_restore(self, seeded):
        shipment_id = seeded["shipment_id"]
        document_id = seeded["document_id"]

        before_shipment = AutoShipment.objects.get(pk=shipment_id)
        before_document = ShipmentDocument.objects.get(pk=document_id)
        before_file = (Path(settings.MEDIA_ROOT) / before_document.file_path).read_bytes()

        call_command("create_backup", backup_type=BackupRun.TYPE_FULL, comment="MariaDB acceptance full")
        backup_run = BackupRun.objects.filter(status=BackupRun.STATUS_SUCCESS, backup_type=BackupRun.TYPE_FULL).latest("id")

        restore_run = RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            full_manifest_path=backup_run.manifest_path,
        )
        call_command("restore_backup", restore_run_id=restore_run.pk)
        restore_run.refresh_from_db()
        if restore_run.status != RestoreRun.STATUS_SUCCESS:
            raise RuntimeError(f"restore finished with status={restore_run.status}: {restore_run.error_message}")

        after_shipment = AutoShipment.objects.get(pk=shipment_id)
        if after_shipment.coal_grade != before_shipment.coal_grade or after_shipment.quantity != before_shipment.quantity:
            raise RuntimeError("AutoShipment data mismatch after restore")

        after_document = ShipmentDocument.objects.get(pk=document_id)
        after_file = (Path(settings.MEDIA_ROOT) / after_document.file_path).read_bytes()
        if after_file != before_file:
            raise RuntimeError("Document file content mismatch after restore")

        # Change DB and media after the baseline. Incremental restore must apply
        # the changed DB/file and preserve deletion from the uploads inventory.
        deleted_path = Path(settings.MEDIA_ROOT) / after_document.file_path
        deleted_path.unlink()
        after_shipment.coal_grade = "Т"
        after_shipment.quantity = Decimal("98.765")
        after_shipment.save(update_fields=["coal_grade", "quantity"])
        extra = Path(settings.MEDIA_ROOT) / "acceptance" / "incremental.txt"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_bytes(b"incremental acceptance payload\n")

        call_command("create_backup", backup_type=BackupRun.TYPE_INCREMENTAL, comment="MariaDB acceptance incremental")
        incremental = BackupRun.objects.filter(
            status=BackupRun.STATUS_SUCCESS, backup_type=BackupRun.TYPE_INCREMENTAL
        ).latest("id")

        after_shipment.coal_grade = "Mutation after incremental"
        after_shipment.quantity = Decimal("1.000")
        after_shipment.save(update_fields=["coal_grade", "quantity"])
        extra.write_bytes(b"corrupted after backup\n")
        deleted_path.parent.mkdir(parents=True, exist_ok=True)
        deleted_path.write_bytes(b"must be deleted by incremental restore")

        incremental_restore = RestoreRun.objects.create(
            status=RestoreRun.STATUS_QUEUED,
            full_manifest_path=backup_run.manifest_path,
            incremental_manifest_path=incremental.manifest_path,
        )
        call_command("restore_backup", restore_run_id=incremental_restore.pk)
        incremental_restore.refresh_from_db()
        if incremental_restore.status != RestoreRun.STATUS_SUCCESS:
            raise RuntimeError(
                f"incremental restore status={incremental_restore.status}: {incremental_restore.error_message}"
            )
        restored = AutoShipment.objects.get(pk=shipment_id)
        if restored.coal_grade != "Т" or restored.quantity != Decimal("98.765"):
            raise RuntimeError("AutoShipment data mismatch after full+incremental restore")
        if extra.read_bytes() != b"incremental acceptance payload\n":
            raise RuntimeError("incremental upload content mismatch after restore")
        if deleted_path.exists():
            raise RuntimeError("incremental restore did not apply deleted_files inventory")

    def _run_scheduler_lock_drill(self):
        first = acquire_scheduler_lock()
        if not first.acquired:
            raise RuntimeError("first scheduler could not acquire advisory lock")
        second = None
        try:
            second = acquire_scheduler_lock()
            if second.acquired:
                raise RuntimeError("overlapping scheduler acquired an already-held lock")
            # Simulate a network/session loss without granting the acceptance
            # user PROCESS/CONNECTION ADMIN merely for the drill. Django will
            # reopen this alias on the ownership check; the new session must
            # not report ownership of the released advisory lock.
            first._connection.close()
            owned = first.is_owned()
            if owned:
                raise RuntimeError("scheduler still reports ownership after its lock connection was killed")
        finally:
            if second is not None:
                second.release()
            first.release()

    def _identity(self):
        embedded = getattr(settings, "BUILD_INFO", {}) or {}
        identity = {
            "version": os.environ.get("ACCEPTANCE_VERSION", "") or getattr(settings, "APP_VERSION", "") or embedded.get("app_version", ""),
            "build_id": os.environ.get("ACCEPTANCE_BUILD_ID", "") or embedded.get("build_id", ""),
            "commit": os.environ.get("ACCEPTANCE_COMMIT", "") or embedded.get("git_commit", ""),
            "image_id": os.environ.get("ACCEPTANCE_IMAGE_ID", ""),
        }
        supplied = [bool(value) for value in identity.values()]
        if any(supplied) and not all(supplied):
            raise RuntimeError("acceptance identity is partial; version, build_id, commit and image_id are required together")
        if embedded.get("app_version") and identity["version"] != embedded["app_version"]:
            raise RuntimeError("acceptance version does not match embedded BUILD_INFO")
        if embedded.get("build_id") and identity["build_id"] != embedded["build_id"]:
            raise RuntimeError("acceptance build_id does not match embedded BUILD_INFO")
        return identity

    def _write_report(self, steps):
        report_dir = Path(os.environ.get("ACCEPTANCE_REPORT_DIR", str(Path(settings.BASE_DIR) / ".tmp")))
        report_dir.mkdir(parents=True, exist_ok=True)
        identity_error = ""
        try:
            identity = self._identity()
        except Exception as exc:
            identity = {"version": "", "build_id": "", "commit": "", "image_id": ""}
            identity_error = str(exc)
            steps.append({"name": "version-bound identity", "status": "FAIL", "duration_seconds": 0.0, "error": identity_error})
        finished = datetime.now(timezone.utc)
        report = {
            "schema_version": 1,
            "status": "PASS" if all(step["status"] == "PASS" for step in steps) else "FAIL",
            "started_at": self.started_at.isoformat(),
            "finished_at": finished.isoformat(),
            "identity": identity,
            "database": {
                "vendor": connection.vendor,
                "version": getattr(self, "database_version", ""),
                "user": getattr(self, "database_user", ""),
                "grants": getattr(self, "database_grants", []),
            },
            "steps": steps,
        }
        json_path = report_dir / "mariadb-acceptance.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# MariaDB acceptance report", "", f"Status: **{report['status']}**", "", "## Identity", ""]
        lines.extend(f"- {key}: `{value or 'not supplied (source checkout)'}`" for key, value in identity.items())
        lines.extend(["", "## Steps", ""])
        for step in steps:
            suffix = f" — {step['error']}" if step["error"] else ""
            lines.append(f"- **{step['status']}** {step['name']} ({step['duration_seconds']:.3f}s){suffix}")
        (report_dir / "mariadb-acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _print_summary(self, steps):
        self.stdout.write("")
        self.stdout.write("=== MariaDB smoke contour summary ===")
        for step in steps:
            status = self.style.SUCCESS("PASS") if step["status"] == "PASS" else self.style.ERROR("FAIL")
            self.stdout.write(f"[{status}] {step['name']}")
            if step["error"]:
                self.stdout.write(f"        {step['error']}")
