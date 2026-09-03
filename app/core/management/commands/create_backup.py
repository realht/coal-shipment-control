from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import BackupRun, SystemState
from core.system_ops import create_backup, has_active_operation


class Command(BaseCommand):
    help = "Create a full, incremental, or pre-restore backup."

    def add_arguments(self, parser):
        parser.add_argument(
            "--type",
            required=True,
            choices=[BackupRun.TYPE_FULL, BackupRun.TYPE_INCREMENTAL, BackupRun.TYPE_PRE_RESTORE],
            dest="backup_type",
        )
        parser.add_argument("--run-id", type=int, default=None)
        parser.add_argument("--comment", default="")

    def handle(self, *args, **options):
        with transaction.atomic():
            SystemState.objects.select_for_update().get_or_create(singleton_key=1)
            run = None
            if options["run_id"]:
                try:
                    run = BackupRun.objects.select_for_update().get(pk=options["run_id"])
                except BackupRun.DoesNotExist as exc:
                    raise CommandError(f"BackupRun #{options['run_id']} not found") from exc

                if run.status != BackupRun.STATUS_QUEUED:
                    raise CommandError(
                        f"BackupRun #{run.pk} is not queued (status={run.status})."
                    )
                if run.backup_type != options["backup_type"]:
                    raise CommandError(
                        f"BackupRun #{run.pk} type mismatch "
                        f"(run={run.backup_type}, --type={options['backup_type']})."
                    )

            if has_active_operation(exclude_backup_run=run):
                raise CommandError("Another backup or restore operation is already active.")

            if run is None:
                run = BackupRun.objects.create(
                    backup_type=options["backup_type"],
                    status=BackupRun.STATUS_RUNNING,
                    started_at=timezone.now(),
                    comment=options["comment"].strip()[:500],
                    source=BackupRun.SOURCE_SCRIPT,
                )
            else:
                run.status = BackupRun.STATUS_RUNNING
                run.started_at = timezone.now()
                run.save(update_fields=["status", "started_at"])

        try:
            run = create_backup(
                options["backup_type"],
                run=run,
                comment=options["comment"],
                source=BackupRun.SOURCE_SCRIPT,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Backup #{run.pk} finished: {run.status}"))
