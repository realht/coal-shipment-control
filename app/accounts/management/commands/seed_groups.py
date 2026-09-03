from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from accounts.permissions import GROUPS


class Command(BaseCommand):
    help = "Создаёт группы и назначает права. Idempotent — безопасно запускать повторно."

    def handle(self, *args, **options):
        for group_name, perm_codenames in GROUPS.items():
            group, created = Group.objects.get_or_create(name=group_name)
            action = "создана" if created else "обновлена"

            perms = []
            for full_codename in perm_codenames:
                if "." in full_codename:
                    app_label, codename = full_codename.split(".", 1)
                    qs = Permission.objects.filter(
                        codename=codename,
                        content_type__app_label=app_label,
                    )
                else:
                    qs = Permission.objects.filter(codename=full_codename)

                perm = qs.first()
                if perm:
                    perms.append(perm)
                else:
                    self.stdout.write(self.style.WARNING(
                        f"  Право не найдено: {full_codename} (пропущено)"
                    ))

            group.permissions.add(*perms)
            self.stdout.write(self.style.SUCCESS(
                f"Группа '{group_name}' {action}, прав: {len(perms)}"
            ))
