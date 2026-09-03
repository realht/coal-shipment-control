from django.conf import settings
from django.core.management.base import BaseCommand

from imports.views import _cleanup_import_tmp


class Command(BaseCommand):
    help = "Delete expired import preview JSON temp files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=None,
            help="Delete import temp files older than this many hours.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report matching expired files without deleting them.",
        )

    def handle(self, *args, **options):
        ttl_hours = options["older_than_hours"]
        if ttl_hours is None:
            ttl_hours = settings.IMPORT_TMP_TTL_HOURS
        if ttl_hours < 0:
            self.stderr.write("--older-than-hours must be zero or greater.")
            return

        result = _cleanup_import_tmp(
            ttl_hours=ttl_hours,
            dry_run=options["dry_run"],
        )
        action = "Would delete" if options["dry_run"] else "Deleted"
        self.stdout.write(
            f"{action} {result['deleted']} expired import temp file(s); "
            f"scanned {result['scanned']} file(s)."
        )
