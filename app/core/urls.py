from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("settings/fields/", views.field_settings, name="field_settings"),
    path("settings/system/", views.system_status, name="system_status"),
    path("settings/system/maintenance/", views.system_maintenance, name="system_maintenance"),
    path("settings/system/schedule/", views.update_backup_schedule, name="update_backup_schedule"),
    path("settings/system/backups/start/", views.start_backup, name="start_backup"),
    path("settings/system/backups/<str:key>/delete/", views.delete_backup, name="delete_backup"),
    path("settings/system/restore/start/", views.start_restore, name="start_restore"),
    path("settings/system/restore/recover/", views.recover_restore, name="recover_restore"),
    path("settings/system/uploads-size/recalculate/", views.recalculate_uploads_size_view, name="recalculate_uploads_size"),
    path("healthz/", views.healthz, name="healthz"),
    path("health/", views.health, name="health"),
    path("readyz/", views.readyz, name="readyz"),
]
