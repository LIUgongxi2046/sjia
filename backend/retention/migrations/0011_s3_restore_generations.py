import core.fields
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('retention', '0010_s3contentrestorestate')]

    operations = [
        migrations.AddField(model_name='s3contentrestorestate', name='generation',
            field=models.PositiveBigIntegerField(default=1, unique=True)),
        migrations.AddField(model_name='s3contentrestorestate', name='parent',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='+', to='retention.s3contentrestorestate')),
        migrations.AddField(model_name='s3contentrestorestate', name='parent_mapping_sha256',
            field=core.fields.Sha256HexField(max_length=64, null=True)),
        migrations.AddField(model_name='s3contentrestorestate', name='ancestor_sha256',
            field=core.fields.Sha256HexField(max_length=64, null=True)),
        migrations.RemoveConstraint(model_name='s3contentrestorestate', name='s3_restore_singleton_true'),
        migrations.RunSQL(sql='''
CREATE OR REPLACE FUNCTION sjia.guard_s3_restore_identity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 parent_row sjia.s3_content_restore_state%ROWTYPE;
 actual_cluster text;
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'S3_RESTORE_STATE_DELETE_FORBIDDEN';
 END IF;
 IF EXISTS (SELECT 1 FROM sjia.content_restore_state) THEN
  RAISE EXCEPTION 'RESTORE_STORAGE_STATE_CONFLICT';
 END IF;
 IF TG_OP = 'UPDATE' THEN
  IF ROW(NEW.id, NEW.generation, NEW.parent_id, NEW.parent_mapping_sha256, NEW.ancestor_sha256,
    NEW.database_name, NEW.cluster_id, NEW.restore_root, NEW.source_binding_sha256,
    NEW.target_binding_sha256, NEW.source_snapshot_sha256, NEW.backup_manifest_sha256,
    NEW.content_checkpoint, NEW.created_at)
   IS DISTINCT FROM ROW(OLD.id, OLD.generation, OLD.parent_id, OLD.parent_mapping_sha256, OLD.ancestor_sha256,
    OLD.database_name, OLD.cluster_id, OLD.restore_root, OLD.source_binding_sha256,
    OLD.target_binding_sha256, OLD.source_snapshot_sha256, OLD.backup_manifest_sha256,
    OLD.content_checkpoint, OLD.created_at) THEN
   RAISE EXCEPTION 'S3_RESTORE_STATE_IDENTITY_IMMUTABLE';
  END IF;
  IF OLD.state = 'mapped' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
   RAISE EXCEPTION 'S3_RESTORE_MAPPING_IMMUTABLE';
  END IF;
 END IF;
 IF NEW.database_name <> current_database() THEN
  RAISE EXCEPTION 'S3_RESTORE_DATABASE_IDENTITY_MISMATCH';
 END IF;
 SELECT system_identifier::text INTO actual_cluster FROM pg_control_system();
 IF NEW.cluster_id <> actual_cluster THEN
  RAISE EXCEPTION 'S3_RESTORE_CLUSTER_IDENTITY_MISMATCH';
 END IF;
 IF TG_OP = 'INSERT' THEN
  IF NEW.generation = 1 THEN
   IF EXISTS (SELECT 1 FROM sjia.s3_content_restore_state) THEN
    RAISE EXCEPTION 'S3_RESTORE_ROOT_GENERATION_ALREADY_EXISTS';
   END IF;
  ELSE
   SELECT * INTO parent_row FROM sjia.s3_content_restore_state WHERE id=NEW.parent_id FOR SHARE;
   IF NOT FOUND OR parent_row.state <> 'mapped' OR NEW.generation <> parent_row.generation + 1
    OR NEW.parent_mapping_sha256 IS DISTINCT FROM parent_row.mapping_sha256
    OR NEW.source_binding_sha256 <> parent_row.target_binding_sha256 THEN
    RAISE EXCEPTION 'S3_RESTORE_PARENT_IDENTITY_MISMATCH';
   END IF;
   IF NEW.created_at < parent_row.mapped_at THEN
    RAISE EXCEPTION 'S3_RESTORE_PARENT_TIME_INVALID';
   END IF;
   IF NEW.database_name = parent_row.database_name OR NEW.cluster_id = parent_row.cluster_id THEN
    RAISE EXCEPTION 'S3_RESTORE_DISTINCT_PARENT_TARGET_REQUIRED';
   END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM sjia.s3_content_restore_state
    WHERE source_binding_sha256=NEW.target_binding_sha256 OR target_binding_sha256=NEW.target_binding_sha256) THEN
   RAISE EXCEPTION 'S3_RESTORE_TARGET_BINDING_REUSED';
  END IF;
 END IF;
 RETURN NEW;
END;
$$;
'''),
        migrations.RemoveField(model_name='s3contentrestorestate', name='singleton'),
        migrations.AddConstraint(model_name='s3contentrestorestate', constraint=models.CheckConstraint(
            condition=models.Q(generation__gte=1), name='s3_restore_generation_positive')),
        migrations.AddConstraint(model_name='s3contentrestorestate', constraint=models.CheckConstraint(
            condition=models.Q(generation=1, parent__isnull=True, parent_mapping_sha256__isnull=True, ancestor_sha256__isnull=True)
                | models.Q(generation__gt=1, parent__isnull=False, parent_mapping_sha256__isnull=False, ancestor_sha256__isnull=False),
            name='s3_restore_parent_shape')),
        migrations.AddConstraint(model_name='s3contentrestorestate', constraint=models.UniqueConstraint(
            fields=['target_binding_sha256'], name='s3_restore_target_binding_once')),
    ]
