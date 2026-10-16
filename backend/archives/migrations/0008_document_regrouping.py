import uuid

from django.db import migrations, models
from core.fields import Sha256HexField


SQL = '''
CREATE TABLE sjia.archive_regrouping (
    id uuid PRIMARY KEY, patient_id uuid NOT NULL, batch_id uuid NOT NULL,
    actor_id uuid NOT NULL REFERENCES sjia.principal(id), request_id uuid NOT NULL,
    input_sha256 char(64) NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    source_refs jsonb NOT NULL CHECK (jsonb_typeof(source_refs) = 'array'),
    result_refs jsonb NOT NULL CHECK (jsonb_typeof(result_refs) = 'object'),
    result_sha256 char(64) NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL,
    UNIQUE (id, patient_id),
    CONSTRAINT archive_regrouping_request_unique UNIQUE (actor_id, patient_id, request_id),
    FOREIGN KEY (batch_id, patient_id) REFERENCES sjia.archive_batch(id, patient_id)
);
ALTER TABLE sjia.archive_document
    ADD COLUMN retired_at timestamptz,
    ADD COLUMN retired_by uuid REFERENCES sjia.principal(id),
    ADD COLUMN retirement_id uuid,
    ADD COLUMN retirement_reason text,
    ADD CONSTRAINT archive_document_retirement_fk FOREIGN KEY (retirement_id, patient_id)
        REFERENCES sjia.archive_regrouping(id, patient_id) DEFERRABLE INITIALLY DEFERRED,
    ADD CONSTRAINT archive_document_retirement_complete CHECK (
        (retired_at IS NULL AND retired_by IS NULL AND retirement_id IS NULL AND retirement_reason IS NULL)
        OR (retired_at IS NOT NULL AND retired_by IS NOT NULL AND retirement_id IS NOT NULL
            AND retirement_reason IS NOT NULL AND length(btrim(retirement_reason)) > 0)
    );
CREATE INDEX archive_document_active ON sjia.archive_document(patient_id, id) WHERE retired_at IS NULL;
'''


class Migration(migrations.Migration):
    dependencies = [('archives', '0007_archive_search_indexes')]
    operations = [migrations.SeparateDatabaseAndState(database_operations=[migrations.RunSQL(SQL)], state_operations=[
        migrations.AddField('archivedocument', 'retired_at', models.DateTimeField(null=True)),
        migrations.AddField('archivedocument', 'retired_by', models.UUIDField(null=True)),
        migrations.AddField('archivedocument', 'retirement_id', models.UUIDField(null=True)),
        migrations.AddField('archivedocument', 'retirement_reason', models.TextField(null=True)),
        migrations.CreateModel(name='ArchiveRegrouping', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False)),
            ('patient_id', models.UUIDField()), ('batch_id', models.UUIDField()), ('actor_id', models.UUIDField()),
            ('request_id', models.UUIDField()), ('input_sha256', Sha256HexField()),
            ('source_refs', models.JSONField()), ('result_refs', models.JSONField()),
            ('result_sha256', Sha256HexField()), ('created_at', models.DateTimeField()),
        ], options={'db_table': 'sjia"."archive_regrouping', 'constraints': [models.UniqueConstraint(
            fields=['actor_id', 'patient_id', 'request_id'], name='archive_regrouping_request_unique')]})])]
