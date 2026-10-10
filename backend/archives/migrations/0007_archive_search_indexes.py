from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('archives', '0006_personal_record_task')]
    operations = [
        TrigramExtension(),
        migrations.RunSQL('CREATE INDEX archive_confirmed_text_search ON sjia.archive_document_version '
            'USING gin (lower(confirmed_text) gin_trgm_ops) '
            'WHERE confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL'),
        migrations.RunSQL('CREATE INDEX archive_current_document_order ON sjia.archive_document_version '
            '(patient_id, document_id, version, available_at DESC, id)'),
    ]
