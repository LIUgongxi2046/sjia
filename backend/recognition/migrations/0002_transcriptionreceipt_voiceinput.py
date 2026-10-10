import uuid

import core.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('archives', '0007_archive_search_indexes'), ('configurations', '0002_initial'),
        ('files', '0002_initial'), ('identity', '0002_principalcredential'),
        ('recognition', '0001_initial'), ('records', '0003_initial'),
        ('retention', '0002_simulated_provider_copy'), ('sources', '0001_initial'),
        ('tasks', '0004_purpose_copy'),
    ]

    operations = [
        migrations.CreateModel(
            name='TranscriptionReceipt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('auth_epoch', models.PositiveBigIntegerField()),
                ('audio_sha256', core.fields.Sha256HexField(max_length=64)),
                ('audio_media_type', models.CharField(max_length=128)),
                ('audio_size_bytes', models.PositiveBigIntegerField()),
                ('duration_seconds', core.fields.FiniteDecimalField()),
                ('inference_duration_ms', models.PositiveBigIntegerField()),
                ('model_name', models.CharField(max_length=256)),
                ('implementation_version', models.CharField(max_length=256)),
                ('transcript', models.TextField()),
                ('transcript_sha256', core.fields.Sha256HexField(max_length=64)),
                ('confirmed_text', models.TextField(null=True)),
                ('confirmed_text_sha256', core.fields.Sha256HexField(max_length=64, null=True)),
                ('status', models.CharField(max_length=32)),
                ('status_version', models.PositiveBigIntegerField(default=1)),
                ('created_at', models.DateTimeField()), ('confirmed_at', models.DateTimeField(null=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='identity.principal')),
                ('confirmed_original_file', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='files.fileobject')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='identity.patient')),
                ('retention_policy', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='retention.retentionpolicy')),
                ('use_authorization', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='sources.datauseauthorization')),
            ],
            options={'db_table': 'sjia"."transcription_receipt', 'constraints': [
                models.UniqueConstraint(fields=('id', 'patient'), name='transcription_receipt_patient_uniq'),
                models.CheckConstraint(condition=models.Q(status__in=['completed', 'confirmed', 'revoked']),
                    name='transcription_receipt_status_check'),
            ]},
        ),
        migrations.CreateModel(
            name='VoiceInput',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('request_id', models.UUIDField()), ('transcript', models.TextField()),
                ('transcript_sha256', core.fields.Sha256HexField(max_length=64)),
                ('transcription_evidence', models.JSONField()), ('input_mode', models.CharField(max_length=32)),
                ('status', models.CharField(max_length=32)), ('status_version', models.PositiveBigIntegerField(default=1)),
                ('candidates', models.JSONField(null=True)), ('decisions', models.JSONField(null=True)),
                ('confirmed_at', models.DateTimeField()), ('created_at', models.DateTimeField()),
                ('decided_at', models.DateTimeField(null=True)), ('cancelled_at', models.DateTimeField(null=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='identity.principal')),
                ('field_config', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='configurations.configversion')),
                ('original_file', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='files.fileobject')),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='identity.patient')),
                ('provider_config', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='configurations.configversion')),
                ('purpose_copy', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='archives.purposecopy')),
                ('raw_response_file', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='files.fileobject')),
                ('saved_record_version', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='records.recordversion')),
                ('task', models.ForeignKey(null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='tasks.task')),
                ('use_authorization', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='sources.datauseauthorization')),
            ],
            options={'db_table': 'sjia"."voice_input', 'constraints': [
                models.UniqueConstraint(fields=('actor', 'request_id'), name='voice_input_actor_request_uniq'),
                models.UniqueConstraint(fields=('id', 'patient'), name='voice_input_id_patient_uniq'),
                models.CheckConstraint(condition=models.Q(input_mode__in=['LOCAL_AUDIO_CONFIRMED_TEXT', 'SIMULATION_TEXT_ONLY']),
                    name='voice_input_mode_check'),
                models.CheckConstraint(condition=models.Q(status__in=['awaiting_review', 'ready', 'running',
                    'awaiting_confirmation', 'saved', 'failed', 'uncertain', 'cancelled', 'revoked']),
                    name='voice_input_status_check'),
            ]},
        ),
    ]
