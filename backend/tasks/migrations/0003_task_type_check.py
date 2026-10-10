from django.db import migrations

# backend-design/schema.sql 将 task_type 扩展为 8 类并重建值域 CHECK。
TASK_TYPE_CHECK_SQL = (
    "ALTER TABLE sjia.task ADD CONSTRAINT task_task_type_check CHECK "
    "(task_type IN ('file_validation','ocr','assessment','alert_evaluation','summary','notification','disposition','reconcile'))"
)


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0002_alter_task_task_type"),
    ]

    operations = [
        migrations.RunSQL(TASK_TYPE_CHECK_SQL, reverse_sql="ALTER TABLE sjia.task DROP CONSTRAINT IF EXISTS task_task_type_check"),
    ]
