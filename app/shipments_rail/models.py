from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.models import SoftDeleteModel


class RailShipment(SoftDeleteModel):
    departure_date = models.DateField()
    wagon_number = models.CharField(max_length=50)
    document_number = models.CharField(max_length=100, blank=True, default="")
    cargo = models.CharField(max_length=255)
    origin_region = models.CharField(max_length=255, blank=True, default="")
    origin_station = models.CharField(max_length=255, blank=True, default="")
    sender = models.CharField(max_length=255, blank=True, default="")
    destination_region = models.CharField(max_length=255, blank=True, default="")
    destination_station = models.CharField(max_length=255, blank=True, default="")
    receiver = models.CharField(max_length=255)
    volume = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    comment = models.TextField(blank=True, default="")

    class Meta:
        db_table = "rail_shipments"
        verbose_name = "ЖД-отгрузка"
        verbose_name_plural = "ЖД-отгрузки"
        indexes = [
            models.Index(fields=["departure_date"], name="idx_rail_date"),
            models.Index(fields=["wagon_number"], name="idx_rail_wagon"),
            models.Index(fields=["document_number"], name="idx_rail_document"),
            models.Index(fields=["receiver"], name="idx_rail_receiver"),
            models.Index(fields=["destination_station"], name="idx_rail_dest_station"),
            models.Index(fields=["departure_date", "destination_station"], name="idx_rail_date_dest_station"),
            models.Index(fields=["cargo", "departure_date"], name="idx_rail_cargo_date"),
            models.Index(
                fields=["is_deleted", "departure_date", "wagon_number", "receiver", "volume"],
                name="idx_rail_dup_check",
            ),
        ]
        ordering = ["-departure_date"]
        permissions = [
            ("export_excel", "Экспортировать в Excel"),
        ]

    def __str__(self):
        return f"{self.departure_date} | {self.wagon_number} | {self.receiver} | {self.volume}"
