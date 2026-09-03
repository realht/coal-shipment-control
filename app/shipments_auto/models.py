from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.models import SoftDeleteModel


class AutoShipment(SoftDeleteModel):
    shipment_date = models.DateField()
    source_month_text = models.CharField(max_length=50, blank=True, default="")
    source_day_number = models.IntegerField(null=True, blank=True)
    customer_object = models.CharField(max_length=255)
    sub_object = models.CharField(max_length=255, blank=True, default="")
    vehicle_number = models.CharField(max_length=50, blank=True, default="")
    driver_name = models.CharField(max_length=255, blank=True, default="")
    ttn_number = models.CharField(max_length=100, blank=True, default="")
    coal_grade = models.CharField(max_length=100)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    base_code = models.CharField(max_length=50, blank=True, default="")
    upd_number = models.CharField(max_length=100, blank=True, default="")
    carrier = models.CharField(max_length=255, blank=True, default="")
    balance_note = models.CharField(max_length=255, blank=True, default="")
    comment = models.TextField(blank=True, default="")

    class Meta:
        db_table = "auto_shipments"
        verbose_name = "Автоотгрузка"
        verbose_name_plural = "Автоотгрузки"
        indexes = [
            models.Index(fields=["shipment_date"], name="idx_auto_date"),
            models.Index(fields=["ttn_number"], name="idx_auto_ttn"),
            models.Index(fields=["customer_object"], name="idx_auto_object"),
            models.Index(fields=["vehicle_number"], name="idx_auto_vehicle"),
            models.Index(fields=["coal_grade"], name="idx_auto_coal_grade"),
            models.Index(fields=["coal_grade", "shipment_date"], name="idx_auto_coal_grade_date"),
            models.Index(fields=["customer_object", "shipment_date"], name="idx_auto_object_date"),
            models.Index(
                fields=["is_deleted", "shipment_date", "customer_object", "coal_grade", "quantity"],
                name="idx_auto_dup_check",
            ),
        ]
        ordering = ["-shipment_date"]
        permissions = [
            ("export_excel", "Экспортировать в Excel"),
        ]

    def __str__(self):
        return f"{self.shipment_date} | {self.customer_object} | {self.coal_grade} | {self.quantity}"
