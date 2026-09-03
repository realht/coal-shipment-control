from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import RestoreRun, SystemState
from core.system_ops import has_active_operation, restore_backup


class Command(BaseCommand):
    help = "Restore database and uploads from a manifest-backed backup."

    def add_arguments(self, parser):
        parser.add_argument("--restore-run-id", type=int, required=True)

    def handle(self, *args, **options):
        with transaction.atomic():
            SystemState.objects.select_for_update().get_or_create(singleton_key=1)
            try:
                run = RestoreRun.objects.select_for_update().get(pk=options["restore_run_id"])
            except RestoreRun.DoesNotExist as exc:
                raise CommandError(f"RestoreRun #{options['restore_run_id']} not found") from exc

            if has_active_operation(exclude_restore_run=run):
                raise CommandError("Another backup or restore operation is already active.")

            if run.status != RestoreRun.STATUS_QUEUED:
                raise CommandError(
                    f"RestoreRun #{run.pk} is not queued (status={run.status})."
                )

            run.status = RestoreRun.STATUS_RUNNING
            run.save(update_fields=["status"])

        try:
            restore_backup(run)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Restore #{run.pk} finished: {run.status}"))
