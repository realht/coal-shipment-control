from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0007_backuprun_comment"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="systemstate",
            options={
                "db_table": "system_state",
                "permissions": [
                    ("view_system_status", "Может просматривать состояние системы"),
                    ("change_system_mode", "Может переключать режим системы"),
                    ("recover_system_operations", "Может сбрасывать зависшие системные операции"),
                    ("run_backup", "Может запускать backup"),
                    ("run_restore", "Может запускать restore"),
                ],
            },
        ),
    ]
