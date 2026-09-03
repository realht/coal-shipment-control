from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Alias for seed_field_config (backward compatibility). Use seed_field_config directly."

    def handle(self, *args, **options):
        call_command("seed_field_config")
