from django.db import migrations, models


SQL = (
    'ALTER TABLE sjia.archive_page ADD COLUMN ordinal integer NOT NULL; '
    'ALTER TABLE sjia.archive_page ADD CONSTRAINT archive_page_ordinal_positive CHECK (ordinal > 0); '
    'ALTER TABLE sjia.archive_page ADD CONSTRAINT archive_page_batch_ordinal_unique UNIQUE (batch_id, ordinal)'
)


class Migration(migrations.Migration):
    dependencies = [('archives', '0003_review_unknown')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(SQL)],
        state_operations=[migrations.AddField(model_name='archivepage', name='ordinal', field=models.IntegerField())],
    )]
