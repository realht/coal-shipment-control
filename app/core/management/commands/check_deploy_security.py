import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.deploy_security import validate_deploy_security


class Command(BaseCommand):
    help = "Validate production security environment policy."

    def handle(self, *args, **options):
        result = validate_deploy_security(os.environ, settings)

        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {warning}"))

        if result.errors:
            message = "\n".join(result.errors)
            raise CommandError(message)

        self.stdout.write(self.style.SUCCESS("Deploy security env check passed."))
