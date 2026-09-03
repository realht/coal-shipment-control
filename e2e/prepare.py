"""Create a deterministic, disposable acceptance database and upload fixtures."""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "e2e.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import Permission  # noqa: E402
from django.core.management import call_command  # noqa: E402
from openpyxl import Workbook  # noqa: E402

from shipments_auto.models import AutoShipment  # noqa: E402


def main():
    database = Path(settings.DATABASES["default"]["NAME"])
    database.unlink(missing_ok=True)
    call_command("migrate", interactive=False, verbosity=0)
    call_command("seed_field_config", verbosity=0)

    user_model = get_user_model()
    admin = user_model.objects.create_superuser("e2e-admin", "e2e@example.test", "Acceptance-Admin-2026")
    viewer = user_model.objects.create_user("e2e-viewer", password="Acceptance-Viewer-2026")
    viewer.user_permissions.add(Permission.objects.get(codename="view_autoshipment"))

    start = date(2026, 1, 1)
    for index in range(35):
        AutoShipment.objects.create(
            shipment_date=start + timedelta(days=index),
            customer_object=f"E2E объект {index:02d}",
            vehicle_number=f"А{index:03d}АА",
            ttn_number=f"E2E-TTN-{index:03d}",
            coal_grade="ДР" if index % 2 else "ДПК",
            quantity="10.500",
            base_code="E2E-База",
        )

    fixtures = ROOT / "e2e" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    # Отдельная фикстура на каждый browser-project: оба прогоняются в одном `playwright test`
    # против общей БД, поэтому импортируемая строка должна быть уникальной, иначе второй проект
    # увидит её как дубль («0 готово к импорту») и кнопка импорта не появится.
    for browser in ("chromium", "firefox"):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "ам"
        sheet.append(["месяц", "число", "объект", "марка", "кол-во"])
        sheet.append(["февраль", 7, f"E2E импортированный объект {browser}", "ДР", 17.25])
        workbook.save(fixtures / f"auto-import-{browser}.xlsx")
        workbook.close()

    # A valid 1x1 PNG used by the document upload flow.
    (fixtures / "document.png").write_bytes(
        bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
                      "0000000D49444154789C6360F8CFC00000040101005FE2C1200000000049454E44AE426082")
    )
    print(f"E2E database ready: {database}; admin={admin.username}; viewer={viewer.username}")


if __name__ == "__main__":
    main()
