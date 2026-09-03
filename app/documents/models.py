from django.db import models
from django.conf import settings


class ShipmentDocument(models.Model):
    SHIPMENT_TYPE_AUTO = "auto"
    SHIPMENT_TYPE_RAIL = "rail"
    SHIPMENT_TYPE_CHOICES = [
        (SHIPMENT_TYPE_AUTO, "Авто"),
        (SHIPMENT_TYPE_RAIL, "ЖД"),
    ]

    DOCUMENT_TYPE_TTN = "ttn"
    DOCUMENT_TYPE_UPD = "upd"
    DOCUMENT_TYPE_INVOICE = "invoice"
    DOCUMENT_TYPE_PHOTO = "photo"
    DOCUMENT_TYPE_SCAN = "scan"
    DOCUMENT_TYPE_OTHER = "other"
    DOCUMENT_TYPE_CHOICES = [
        (DOCUMENT_TYPE_TTN, "ТТН"),
        (DOCUMENT_TYPE_UPD, "УПД"),
        (DOCUMENT_TYPE_INVOICE, "Накладная"),
        (DOCUMENT_TYPE_PHOTO, "Фото"),
        (DOCUMENT_TYPE_SCAN, "Скан"),
        (DOCUMENT_TYPE_OTHER, "Прочее"),
    ]

    shipment_type = models.CharField(max_length=10, choices=SHIPMENT_TYPE_CHOICES)
    shipment_id = models.BigIntegerField()
    document_type = models.CharField(max_length=50, choices=DOCUMENT_TYPE_CHOICES, default=DOCUMENT_TYPE_OTHER)
    original_file_name = models.CharField(max_length=255)
    stored_file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    mime_type = models.CharField(max_length=100, blank=True, default="")
    file_size = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_documents",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True, default=None)
    file_deleted_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "shipment_documents"
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        indexes = [
            models.Index(fields=["shipment_type", "shipment_id"], name="idx_docs_shipment"),
            models.Index(fields=["uploaded_at"], name="idx_docs_uploaded_at"),
            models.Index(fields=["is_deleted", "deleted_at"], name="idx_docs_deleted_at"),
        ]
        permissions = [
            ("upload_autoshipment_documents", "Может прикреплять документы к автоотгрузкам"),
            ("upload_railshipment_documents", "Может прикреплять документы к ЖД-отгрузкам"),
            ("change_autoshipment_documents", "Может изменять документы автоотгрузок"),
            ("change_railshipment_documents", "Может изменять документы ЖД-отгрузок"),
            ("delete_autoshipment_documents", "Может удалять документы автоотгрузок"),
            ("delete_railshipment_documents", "Может удалять документы ЖД-отгрузок"),
        ]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.original_file_name}"
