import uuid

from django.db import migrations, models
import django.db.models.deletion

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('archives', '0007_archive_search_indexes'),
        ('identity', '0001_initial'),
        ('retention', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SimulatedProviderCopy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('patient_id', models.UUIDField()),
                ('purpose_copy_version', models.BigIntegerField()),
                ('provider_id', models.TextField()),
                ('object_id', models.UUIDField()),
                ('object_version', models.TextField()),
                ('checksum', core.fields.Sha256HexField(max_length=64)),
                ('size_bytes', models.BigIntegerField()),
                ('environment', models.TextField()),
                ('status', models.TextField()),
                ('deletion_request_id', models.UUIDField(null=True)),
                ('receipt_ref', models.TextField(null=True)),
                ('receipt_sha256', core.fields.Sha256HexField(max_length=64, null=True)),
                ('created_at', models.DateTimeField()),
                ('deleted_at', models.DateTimeField(null=True)),
                ('processing_evidence', models.JSONField()),
                ('created_by', models.ForeignKey(db_column='created_by', on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='identity.principal')),
                ('purpose_copy', models.ForeignKey(db_column='purpose_copy_id', on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='archives.purposecopy')),
            ],
            options={'db_table': 'sjia"."simulated_provider_copy'},
        ),
        migrations.AddConstraint(
            model_name='simulatedprovidercopy',
            constraint=models.UniqueConstraint(fields=('provider_id', 'object_id', 'object_version'), name='sim_provider_object_version_uniq'),
        ),
        migrations.AddConstraint(
            model_name='simulatedprovidercopy',
            constraint=models.UniqueConstraint(fields=('purpose_copy', 'purpose_copy_version', 'provider_id'), name='sim_provider_purpose_copy_uniq'),
        ),
    ]
