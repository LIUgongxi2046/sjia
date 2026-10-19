import uuid

import core.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('retention', '0009_content_family_request_immutable'), ('files', '0003_s3storedobject')]

    operations = [
        migrations.CreateModel(
            name='S3ContentRestoreState',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('singleton', models.BooleanField(default=True, unique=True)),
                ('database_name', models.TextField()),
                ('cluster_id', models.TextField()),
                ('restore_root', models.TextField()),
                ('source_binding_sha256', core.fields.Sha256HexField(max_length=64)),
                ('target_binding_sha256', core.fields.Sha256HexField(max_length=64)),
                ('source_snapshot_sha256', core.fields.Sha256HexField(max_length=64)),
                ('backup_manifest_sha256', core.fields.Sha256HexField(max_length=64)),
                ('content_checkpoint', models.JSONField()),
                ('mapping_sha256', core.fields.Sha256HexField(max_length=64, null=True)),
                ('state', models.CharField(default='preparing', max_length=16)),
                ('created_at', models.DateTimeField()),
                ('mapped_at', models.DateTimeField(null=True)),
            ],
            options={'db_table': 'sjia"."s3_content_restore_state', 'constraints': [
                models.CheckConstraint(condition=models.Q(singleton=True), name='s3_restore_singleton_true'),
                models.CheckConstraint(condition=~models.Q(source_binding_sha256=models.F('target_binding_sha256')),
                    name='s3_restore_distinct_binding'),
                models.CheckConstraint(condition=models.Q(state='preparing', mapping_sha256__isnull=True, mapped_at__isnull=True)
                    | models.Q(state='mapped', mapping_sha256__isnull=False, mapped_at__isnull=False,
                        mapped_at__gte=models.F('created_at')), name='s3_restore_mapping_state'),
            ]}),
        migrations.RunSQL(
            sql='''
CREATE FUNCTION sjia.guard_s3_restore_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'S3_RESTORE_STATE_DELETE_FORBIDDEN';
 END IF;
 IF EXISTS (SELECT 1 FROM sjia.content_restore_state) THEN
  RAISE EXCEPTION 'RESTORE_STORAGE_STATE_CONFLICT';
 END IF;
 IF NEW.database_name <> current_database() THEN
  RAISE EXCEPTION 'S3_RESTORE_DATABASE_IDENTITY_MISMATCH';
 END IF;
 IF TG_OP = 'UPDATE' THEN
  IF ROW(NEW.id, NEW.singleton, NEW.database_name, NEW.cluster_id, NEW.restore_root,
    NEW.source_binding_sha256, NEW.target_binding_sha256, NEW.source_snapshot_sha256,
    NEW.backup_manifest_sha256, NEW.content_checkpoint, NEW.created_at)
   IS DISTINCT FROM ROW(OLD.id, OLD.singleton, OLD.database_name, OLD.cluster_id, OLD.restore_root,
    OLD.source_binding_sha256, OLD.target_binding_sha256, OLD.source_snapshot_sha256,
    OLD.backup_manifest_sha256, OLD.content_checkpoint, OLD.created_at) THEN
   RAISE EXCEPTION 'S3_RESTORE_STATE_IDENTITY_IMMUTABLE';
  END IF;
  IF OLD.state = 'mapped' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
   RAISE EXCEPTION 'S3_RESTORE_MAPPING_IMMUTABLE';
  END IF;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER s3_restore_identity_guard BEFORE INSERT OR UPDATE OR DELETE
ON sjia.s3_content_restore_state FOR EACH ROW EXECUTE FUNCTION sjia.guard_s3_restore_identity();
CREATE FUNCTION sjia.guard_local_restore_storage() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS (SELECT 1 FROM sjia.s3_content_restore_state) THEN
  RAISE EXCEPTION 'RESTORE_STORAGE_STATE_CONFLICT';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER local_restore_storage_guard BEFORE INSERT OR UPDATE
ON sjia.content_restore_state FOR EACH ROW EXECUTE FUNCTION sjia.guard_local_restore_storage();
''',
            reverse_sql='''
DROP TRIGGER local_restore_storage_guard ON sjia.content_restore_state;
DROP FUNCTION sjia.guard_local_restore_storage();
DROP TRIGGER s3_restore_identity_guard ON sjia.s3_content_restore_state;
DROP FUNCTION sjia.guard_s3_restore_identity();
'''),
    ]
