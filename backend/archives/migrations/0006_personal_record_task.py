import hashlib
import uuid
from pathlib import Path

from django.db import migrations, models
import django.db.models.deletion


SQL_PATH = Path(__file__).resolve().parents[3] / 'data-design/personal-record-task.sql'
SQL_BYTES = SQL_PATH.read_bytes()
if hashlib.sha256(SQL_BYTES).hexdigest() != 'e2e6309929f516199df8631223579b7e84d6750b815a7c50eb98f0c76da7c11d':
    raise ValueError('本人记录任务的 SQL migration 版本不匹配。')


class Migration(migrations.Migration):
    dependencies = [('archives', '0005_workflow_evaluation')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(SQL_BYTES.decode())],
        state_operations=[migrations.CreateModel(name='PersonalRecordTask', fields=[
            ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, serialize=False)),
            ('patient_id', models.UUIDField()), ('owner_id', models.UUIDField()),
            ('version', models.BigIntegerField()), ('title', models.TextField()),
            ('description', models.TextField(null=True)), ('status', models.TextField()),
            ('created_at', models.DateTimeField()), ('updated_at', models.DateTimeField()),
            ('finished_at', models.DateTimeField(null=True)),
            ('retention_policy', models.ForeignKey('retention.RetentionPolicy',
                db_column='retention_policy_id', on_delete=django.db.models.deletion.RESTRICT, related_name='+')),
        ], options={'db_table': 'sjia"."personal_record_task'})])]
