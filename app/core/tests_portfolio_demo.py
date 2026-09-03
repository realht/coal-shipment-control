from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from accounts.models import User
from audit.models import AuditLog
from documents.models import ShipmentDocument
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment


@pytest.mark.django_db
def test_seed_portfolio_demo_creates_only_synthetic_showcase_records(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path / "uploads"):
        call_command("seed_portfolio_demo")

        user = User.objects.get(username="portfolio_admin")
        assert user.check_password("portfolio-demo")
        assert user.groups.filter(name="admin").exists()
        assert AutoShipment.objects.filter(ttn_number__startswith="DEMO-").count() == 3
        assert RailShipment.objects.filter(document_number__startswith="DEMO-").count() == 2
        document = ShipmentDocument.objects.get(original_file_name="demo-waybill.pdf")
        assert (Path(settings.MEDIA_ROOT) / document.file_path).is_file()
        assert AuditLog.objects.filter(source=AuditLog.SOURCE_SCRIPT, user=user).count() == 2
