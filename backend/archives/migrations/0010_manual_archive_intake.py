from django.db import migrations, models


SQL = '''
ALTER TABLE sjia.archive_batch
    ADD COLUMN intake_mode varchar(8) NOT NULL DEFAULT 'ocr',
    ALTER COLUMN quota_policy_id DROP NOT NULL,
    ADD CONSTRAINT archive_batch_intake_mode_check CHECK (
        (intake_mode = 'ocr' AND quota_policy_id IS NOT NULL)
        OR (intake_mode = 'manual' AND quota_policy_id IS NULL
            AND budget_status = 'unknown' AND status = 'receiving')
    );
'''


class Migration(migrations.Migration):
    dependencies = [('archives', '0009_alter_archivedocument_managers_and_more')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(SQL)],
        state_operations=[
            migrations.AddField('archivebatch', 'intake_mode',
                models.CharField(max_length=8, default='ocr')),
            migrations.AlterField('archivebatch', 'quota_policy_id', models.UUIDField(null=True)),
        ],
    )]
