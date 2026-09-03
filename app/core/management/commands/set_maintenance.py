from django.core.management.base import BaseCommand

from core.models import SystemState
from audit.models import AuditLog
from core.system_ops import set_system_mode


class Command(BaseCommand):
    help = "Switch system maintenance mode."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            required=True,
            choices=[SystemState.MODE_NORMAL, SystemState.MODE_ADMIN_ONLY],
        )
        parser.add_argument("--reason", default="")

    def handle(self, *args, **options):
        state = set_system_mode(
            options["mode"],
            reason=options["reason"],
            source=AuditLog.SOURCE_SCRIPT,
        )
        self.stdout.write(self.style.SUCCESS(f"System mode: {state.mode}"))
