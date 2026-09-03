from django.contrib import admin
from .models import ImportLog


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = ["shipment_type", "filename", "status", "total_rows", "imported_rows", "error_rows", "duplicate_rows", "created_by", "created_at"]
    list_filter = ["shipment_type", "status"]
    readonly_fields = ["shipment_type", "filename", "status", "total_rows", "imported_rows", "error_rows", "duplicate_rows", "created_by", "created_at"]
