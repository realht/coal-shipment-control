from django.conf import settings
from django.db import models
from django.utils import timezone

from datetime import datetime, timedelta


class FieldSettings(models.Model):
    SECTION_MAIN = "main"
    SECTION_ADVANCED = "advanced"

    entity = models.CharField(max_length=50)
    field_name = models.CharField(max_length=100)
    label = models.CharField(max_length=200, blank=True, default="")
    visible = models.BooleanField(default=True)
    required = models.BooleanField(default=False)
    section = models.CharField(max_length=20, default=SECTION_MAIN)
    is_system = models.BooleanField(default=False)
    show_in_list = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    use_catalog = models.BooleanField(default=False)
    allow_filter = models.BooleanField(default=False)
    allow_sort = models.BooleanField(default=False)
    sticky_col = models.BooleanField(default=False)
    preset_membership = models.CharField(max_length=200, blank=True, default="")
    filter_type = models.CharField(
        max_length=20,
        default="none",
        choices=[
            ("none", "Нет фильтра"),
            ("value", "Список значений"),
            ("date", "Диапазон дат"),
            ("number", "Числовой диапазон"),
            ("text", "Текстовый поиск"),
        ],
    )

    class Meta:
        db_table = "field_settings"
        unique_together = [("entity", "field_name")]
        ordering = ["entity", "sort_order", "field_name"]

    def __str__(self):
        return f"{self.entity}.{self.field_name}"


class BackupRun(models.Model):
    SOURCE_UI = "ui"
    SOURCE_SCHEDULER = "scheduler"
    SOURCE_SCRIPT = "script"
    SOURCE_RESTORE = "restore"
    SOURCE_CHOICES = [
        (SOURCE_UI, "UI"),
        (SOURCE_SCHEDULER, "Scheduler"),
        (SOURCE_SCRIPT, "Script"),
        (SOURCE_RESTORE, "Restore"),
    ]

    TYPE_FULL = "full"
    TYPE_INCREMENTAL = "incremental"
    TYPE_PRE_RESTORE = "pre_restore"
    TYPE_CHOICES = [
        (TYPE_FULL, "Полный"),
        (TYPE_INCREMENTAL, "Инкрементальный"),
        (TYPE_PRE_RESTORE, "Перед восстановлением"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "В очереди"),
        (STATUS_RUNNING, "Выполняется"),
        (STATUS_SUCCESS, "Успешно"),
        (STATUS_ERROR, "Ошибка"),
    ]

    backup_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="backup_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    db_path = models.CharField(max_length=500, blank=True, default="")
    uploads_path = models.CharField(max_length=500, blank=True, default="")
    manifest_path = models.CharField(max_length=500, blank=True, default="")
    total_size = models.BigIntegerField(default=0)
    comment = models.CharField(max_length=500, blank=True, default="")
    manifest = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True, default="")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_UI)
    schedule = models.ForeignKey(
        "BackupSchedule",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="backup_runs",
    )

    class Meta:
        db_table = "backup_runs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "backup_type"], name="idx_backup_status_type"),
            models.Index(fields=["started_at"], name="idx_backup_started"),
            models.Index(fields=["source"], name="idx_backup_source"),
        ]

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.backup_type} | {self.status} | {self.created_at:%Y-%m-%d %H:%M}"


class BackupSchedule(models.Model):
    backup_type = models.CharField(
        max_length=20,
        choices=[
            (BackupRun.TYPE_FULL, "Полный"),
            (BackupRun.TYPE_INCREMENTAL, "Инкрементальный"),
        ],
        unique=True,
    )
    enabled = models.BooleanField(default=True)
    weekdays = models.CharField(
        max_length=20,
        help_text="Comma-separated weekday numbers, where 0 is Monday and 6 is Sunday.",
    )
    run_time = models.TimeField()
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run = models.ForeignKey(
        BackupRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="last_for_schedules",
    )

    class Meta:
        db_table = "backup_schedules"
        ordering = ["backup_type"]

    def __str__(self):
        return f"{self.backup_type} schedule"

    def weekday_numbers(self):
        result = []
        for raw in self.weekdays.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if 0 <= value <= 6:
                result.append(value)
        return sorted(set(result))

    def calculate_next_run(self, after=None):
        weekdays = self.weekday_numbers()
        if not weekdays:
            return None
        after = after or timezone.now()
        local_after = timezone.localtime(after)
        current_tz = timezone.get_current_timezone()
        for offset in range(8):
            candidate_date = local_after.date() + timedelta(days=offset)
            if candidate_date.weekday() not in weekdays:
                continue
            candidate = timezone.make_aware(
                datetime.combine(candidate_date, self.run_time),
                current_tz,
            )
            if candidate > after:
                return candidate
        return None

    def refresh_next_run(self, after=None, save=True):
        self.next_run_at = self.calculate_next_run(after)
        if save:
            self.save(update_fields=["next_run_at"])
        return self.next_run_at


class RestoreRun(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "В очереди"),
        (STATUS_RUNNING, "Выполняется"),
        (STATUS_SUCCESS, "Успешно"),
        (STATUS_ERROR, "Ошибка"),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="restore_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    full_manifest_path = models.CharField(max_length=500)
    incremental_manifest_path = models.CharField(max_length=500, blank=True, default="")
    selected_manifest = models.JSONField(default=dict, blank=True)
    pre_restore_backup = models.ForeignKey(
        BackupRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="restore_runs",
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "restore_runs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="idx_restore_status"),
            models.Index(fields=["started_at"], name="idx_restore_started"),
        ]

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"restore | {self.status} | {self.created_at:%Y-%m-%d %H:%M}"


class SystemState(models.Model):
    MODE_NORMAL = "normal"
    MODE_ADMIN_ONLY = "admin_only"
    MODE_RESTORE_RUNNING = "restore_running"
    MODE_CHOICES = [
        (MODE_NORMAL, "Обычный режим"),
        (MODE_ADMIN_ONLY, "Профилактика"),
        (MODE_RESTORE_RUNNING, "Восстановление"),
    ]

    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    mode = models.CharField(max_length=30, choices=MODE_CHOICES, default=MODE_NORMAL)
    reason = models.CharField(max_length=500, blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="system_state_changes",
    )
    changed_at = models.DateTimeField(auto_now=True)
    uploads_size_bytes = models.BigIntegerField(null=True, blank=True, default=None)
    uploads_size_calculated_at = models.DateTimeField(null=True, blank=True, default=None)
    scheduler_heartbeat_at = models.DateTimeField(null=True, blank=True, default=None)
    daily_cleanup_last_run_at = models.DateTimeField(null=True, blank=True, default=None)
    daily_cleanup_last_result = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "system_state"
        permissions = [
            ("view_system_status", "Может просматривать состояние системы"),
            ("change_system_mode", "Может переключать режим системы"),
            ("recover_system_operations", "Может сбрасывать зависшие системные операции"),
            ("run_backup", "Может запускать backup"),
            ("run_restore", "Может запускать restore"),
        ]

    def __str__(self):
        return self.get_mode_display()


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllObjectsManager(models.Manager):
    pass


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(class)s_updated",
    )

    class Meta:
        abstract = True


class SoftDeleteModel(TimeStampedModel):
    is_deleted = models.BooleanField(default=False, db_index=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.save(update_fields=["is_deleted", "updated_at"])

    def hard_delete(self, using=None, keep_parents=False):
        super().delete(using=using, keep_parents=keep_parents)

    class Meta:
        abstract = True
