from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0005_set_default_presets"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("backup_type", models.CharField(choices=[("full", "Полный"), ("incremental", "Инкрементальный"), ("pre_restore", "Перед восстановлением")], max_length=20)),
                ("status", models.CharField(choices=[("queued", "В очереди"), ("running", "Выполняется"), ("success", "Успешно"), ("error", "Ошибка")], default="queued", max_length=20)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("db_path", models.CharField(blank=True, default="", max_length=500)),
                ("uploads_path", models.CharField(blank=True, default="", max_length=500)),
                ("manifest_path", models.CharField(blank=True, default="", max_length=500)),
                ("total_size", models.BigIntegerField(default=0)),
                ("manifest", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("initiated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="backup_runs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "backup_runs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="SystemState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("singleton_key", models.PositiveSmallIntegerField(default=1, editable=False, unique=True)),
                ("mode", models.CharField(choices=[("normal", "Обычный режим"), ("admin_only", "Профилактика"), ("restore_running", "Восстановление")], default="normal", max_length=30)),
                ("reason", models.CharField(blank=True, default="", max_length=500)),
                ("changed_at", models.DateTimeField(auto_now=True)),
                ("changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="system_state_changes", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "system_state",
            },
        ),
        migrations.CreateModel(
            name="RestoreRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "В очереди"), ("running", "Выполняется"), ("success", "Успешно"), ("error", "Ошибка")], default="queued", max_length=20)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("full_manifest_path", models.CharField(max_length=500)),
                ("incremental_manifest_path", models.CharField(blank=True, default="", max_length=500)),
                ("selected_manifest", models.JSONField(blank=True, default=dict)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("initiated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="restore_runs", to=settings.AUTH_USER_MODEL)),
                ("pre_restore_backup", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="restore_runs", to="core.backuprun")),
            ],
            options={
                "db_table": "restore_runs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="backuprun",
            index=models.Index(fields=["status", "backup_type"], name="idx_backup_status_type"),
        ),
        migrations.AddIndex(
            model_name="backuprun",
            index=models.Index(fields=["started_at"], name="idx_backup_started"),
        ),
        migrations.AddIndex(
            model_name="restorerun",
            index=models.Index(fields=["status"], name="idx_restore_status"),
        ),
        migrations.AddIndex(
            model_name="restorerun",
            index=models.Index(fields=["started_at"], name="idx_restore_started"),
        ),
    ]
