from django.db import models
from django.conf import settings


class AuditLog(models.Model):
    ENTITY_AUTO = "auto_shipment"
    ENTITY_RAIL = "rail_shipment"
    ENTITY_DOCUMENT = "document"
    ENTITY_USER = "user"
    ENTITY_SYSTEM = "system"
    ENTITY_BACKUP = "backup"
    ENTITY_RESTORE = "restore"
    ENTITY_CATALOG = "catalog"
    ENTITY_CHOICES = [
        (ENTITY_AUTO, "Автоотгрузка"),
        (ENTITY_RAIL, "ЖД-отгрузка"),
        (ENTITY_DOCUMENT, "Документ"),
        (ENTITY_USER, "Пользователь"),
        (ENTITY_SYSTEM, "Система"),
        (ENTITY_BACKUP, "Backup"),
        (ENTITY_RESTORE, "Restore"),
        (ENTITY_CATALOG, "Справочник"),
    ]

    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_RESTORE = "restore"
    ACTION_UPLOAD = "upload_document"
    ACTION_EDIT_DOCUMENT = "edit_document"
    ACTION_DELETE_DOCUMENT = "delete_document"
    ACTION_SYSTEM_MODE_CHANGE = "system_mode_change"
    ACTION_BACKUP_QUEUED = "backup_queued"
    ACTION_BACKUP_STARTED = "backup_started"
    ACTION_BACKUP_SUCCESS = "backup_success"
    ACTION_BACKUP_ERROR = "backup_error"
    ACTION_RESTORE_QUEUED = "restore_queued"
    ACTION_RESTORE_STARTED = "restore_started"
    ACTION_RESTORE_SUCCESS = "restore_success"
    ACTION_RESTORE_ERROR = "restore_error"
    ACTION_OPERATION_RECOVERED = "operation_recovered"
    ACTION_BACKUP_SCHEDULE_UPDATED = "backup_schedule_updated"
    ACTION_CATALOG_RENAME = "catalog_rename"
    ACTION_AUTH_LOCKOUT = "auth_lockout"
    ACTION_CHOICES = [
        (ACTION_CREATE, "Создание"),
        (ACTION_UPDATE, "Изменение"),
        (ACTION_DELETE, "Удаление"),
        (ACTION_RESTORE, "Восстановление"),
        (ACTION_UPLOAD, "Загрузка документа"),
        (ACTION_EDIT_DOCUMENT, "Изменение документа"),
        (ACTION_DELETE_DOCUMENT, "Удаление документа"),
        (ACTION_SYSTEM_MODE_CHANGE, "Смена режима системы"),
        (ACTION_BACKUP_QUEUED, "Backup поставлен в очередь"),
        (ACTION_BACKUP_STARTED, "Backup запущен"),
        (ACTION_BACKUP_SUCCESS, "Backup выполнен"),
        (ACTION_BACKUP_ERROR, "Ошибка backup"),
        (ACTION_RESTORE_QUEUED, "Restore поставлен в очередь"),
        (ACTION_RESTORE_STARTED, "Restore запущен"),
        (ACTION_RESTORE_SUCCESS, "Restore выполнен"),
        (ACTION_RESTORE_ERROR, "Ошибка restore"),
        (ACTION_OPERATION_RECOVERED, "Сброс системной операции"),
        (ACTION_BACKUP_SCHEDULE_UPDATED, "Расписание backup изменено"),
        (ACTION_CATALOG_RENAME, "Переименование справочника"),
        (ACTION_AUTH_LOCKOUT, "Блокировка входа (axes)"),
    ]

    SOURCE_UI = "ui"
    SOURCE_IMPORT = "import"
    SOURCE_BACKUP = "backup"
    SOURCE_RESTORE = "restore"
    SOURCE_SCRIPT = "script"
    SOURCE_SCHEDULER = "scheduler"
    SOURCE_CHOICES = [
        (SOURCE_UI, "UI"),
        (SOURCE_IMPORT, "Import"),
        (SOURCE_BACKUP, "Backup"),
        (SOURCE_RESTORE, "Restore"),
        (SOURCE_SCRIPT, "Script"),
        (SOURCE_SCHEDULER, "Scheduler"),
    ]

    entity_type = models.CharField(max_length=50, choices=ENTITY_CHOICES)
    entity_id = models.BigIntegerField()
    action = models.CharField(max_length=50, choices=ACTION_CHOICES)
    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_UI)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "audit_log"
        verbose_name = "Запись аудита"
        verbose_name_plural = "Журнал изменений"
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="idx_audit_entity"),
            models.Index(fields=["created_at"], name="idx_audit_created"),
            models.Index(fields=["user"], name="idx_audit_user"),
            models.Index(fields=["source"], name="idx_audit_source"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at} | {self.entity_type}#{self.entity_id} | {self.action}"
