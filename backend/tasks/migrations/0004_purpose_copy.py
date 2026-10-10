from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tasks', '0003_task_type_check'), ('archives', '0002_legacy_facts')]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE sjia.task DROP CONSTRAINT task_task_type_check; "
            "ALTER TABLE sjia.task ADD CONSTRAINT task_task_type_check CHECK "
            "(task_type IN ('file_validation','purpose_copy','ocr','assessment','alert_evaluation','summary','notification','disposition','reconcile'))",
        ),
        migrations.AlterField(
            model_name='task', name='task_type',
            field=models.CharField(max_length=16, choices=[
                ('file_validation', 'file_validation'), ('purpose_copy', 'purpose_copy'), ('ocr', 'ocr'),
                ('assessment', 'assessment'), ('alert_evaluation', 'alert_evaluation'), ('summary', 'summary'),
                ('notification', 'notification'), ('disposition', 'disposition'), ('reconcile', 'reconcile'),
            ]),
        ),
    ]
