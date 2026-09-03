from django.db import models
from django.conf import settings


class ImportLog(models.Model):
    SHIPMENT_TYPE_AUTO = "auto"
    SHIPMENT_TYPE_RAIL = "rail"
    SHIPMENT_TYPE_CHOICES = [
        (SHIPMENT_TYPE_AUTO, "Автоотгрузки"),
        (SHIPMENT_TYPE_RAIL, "ЖД-отгрузки"),
    ]

    STATUS_SUCCESS = "success"
    STATUS_PARTIAL = "partial"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_SUCCESS, "Успешно"),
        (STATUS_PARTIAL, "Частично"),
        (STATUS_ERROR, "Ошибка"),
    ]

    shipment_type = models.CharField(max_length=10, choices=SHIPMENT_TYPE_CHOICES)
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    total_rows = models.IntegerField(default=0)
    imported_rows = models.IntegerField(default=0)
    updated_rows = models.IntegerField(default=0)
    skipped_rows = models.IntegerField(default=0)
    error_rows = models.IntegerField(default=0)
    duplicate_rows = models.IntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="import_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "import_logs"
        verbose_name = "Журнал импорта"
        verbose_name_plural = "Журнал импортов"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"], name="idx_import_log_created"),
        ]
        permissions = [
            ("import_shipments", "Может импортировать отгрузки из Excel"),
        ]

    def __str__(self):
        return f"{self.get_shipment_type_display()} | {self.filename} | {self.created_at:%Y-%m-%d %H:%M}"


class ImportRowResult(models.Model):
    STATUS_CREATED = "created"
    STATUS_UPDATED = "updated"
    STATUS_SKIPPED = "skipped"
    STATUS_DUPLICATE = "duplicate"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Создано"),
        (STATUS_UPDATED, "Обновлено"),
        (STATUS_SKIPPED, "Пропущено"),
        (STATUS_DUPLICATE, "Дубль"),
        (STATUS_ERROR, "Ошибка"),
    ]

    import_log = models.ForeignKey(
        ImportLog,
        on_delete=models.CASCADE,
        related_name="row_results",
    )
    row_num = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    messages = models.JSONField(default=list, blank=True)
    source_data = models.JSONField(default=dict, blank=True)
    created_object_id = models.BigIntegerField(null=True, blank=True)
    created_object_label = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "import_row_results"
        verbose_name = "Результат строки импорта"
        verbose_name_plural = "Результаты строк импорта"
        ordering = ["row_num"]
        indexes = [
            models.Index(fields=["import_log", "status"], name="idx_import_row_log_status"),
        ]

    def __str__(self):
        return f"{self.import_log_id} | row {self.row_num} | {self.status}"
