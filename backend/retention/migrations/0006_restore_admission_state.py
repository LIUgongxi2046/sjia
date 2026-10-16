"""Empty by default; populated only by an explicitly authorized isolated restore."""
import uuid
import core.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('retention', '0005_contentaccessrevocation')]
    operations = [migrations.CreateModel(name='ContentRestoreState', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
        ('singleton', models.BooleanField(default=True, unique=True)),
        ('database_name', models.TextField()),
        ('cluster_id', models.TextField()),
        ('object_root', models.TextField()),
        ('backup_manifest_sha256', core.fields.Sha256HexField(max_length=64)),
        ('content_checkpoint', models.JSONField(null=True)),
        ('created_at', models.DateTimeField()),
    ], options={'db_table': 'sjia"."content_restore_state',
        'constraints': [models.CheckConstraint(condition=models.Q(singleton=True),
            name='content_restore_singleton_true')]})]
