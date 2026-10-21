import uuid

import core.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("files", "0002_initial")]

    operations = [
        migrations.CreateModel(
            name="S3StoredObject",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ("binding_sha256", core.fields.Sha256HexField(max_length=64)),
                ("object_key", models.TextField()),
                ("version_id", models.TextField()),
                ("etag", models.TextField()),
                ("checksum", core.fields.Sha256HexField(max_length=64)),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("status", models.CharField(choices=[("available", "available"), ("disposed", "disposed")], max_length=16)),
                ("created_at", models.DateTimeField()),
                ("disposed_at", models.DateTimeField(null=True)),
            ],
            options={
                "db_table": 'sjia"."s3_stored_object',
                "constraints": [
                    models.UniqueConstraint(fields=("binding_sha256", "object_key"), name="s3_stored_object_binding_key_uniq"),
                    models.CheckConstraint(condition=~models.Q(object_key="") & ~models.Q(object_key__startswith="/")
                        & ~models.Q(object_key__endswith="/") & ~models.Q(object_key__contains="//")
                        & ~models.Q(object_key__regex=r"(^|/)\.{1,2}(/|$)"), name="s3_stored_object_key_check"),
                    models.CheckConstraint(condition=~models.Q(version_id="") & ~models.Q(version_id="null")
                        & ~models.Q(etag=""), name="s3_stored_object_version_check"),
                    models.CheckConstraint(condition=models.Q(status="available", disposed_at__isnull=True)
                        | models.Q(status="disposed", disposed_at__isnull=False, disposed_at__gte=models.F("created_at")),
                        name="s3_stored_object_state_check"),
                ],
            },
        ),
        migrations.RunSQL(
            sql="""
CREATE FUNCTION sjia.guard_s3_stored_object_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'S3_STORED_OBJECT_DELETE_FORBIDDEN';
    END IF;
    IF ROW(NEW.id, NEW.binding_sha256, NEW.object_key, NEW.version_id, NEW.etag,
           NEW.checksum, NEW.size_bytes, NEW.created_at)
       IS DISTINCT FROM ROW(OLD.id, OLD.binding_sha256, OLD.object_key, OLD.version_id, OLD.etag,
           OLD.checksum, OLD.size_bytes, OLD.created_at) THEN
        RAISE EXCEPTION 'S3_STORED_OBJECT_IDENTITY_IMMUTABLE';
    END IF;
    IF OLD.status = 'disposed' AND ROW(NEW.status, NEW.disposed_at)
       IS DISTINCT FROM ROW(OLD.status, OLD.disposed_at) THEN
        RAISE EXCEPTION 'S3_STORED_OBJECT_REACTIVATION_FORBIDDEN';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER s3_stored_object_identity_guard BEFORE UPDATE OR DELETE
ON sjia.s3_stored_object FOR EACH ROW EXECUTE FUNCTION sjia.guard_s3_stored_object_identity();
""",
            reverse_sql="""
DROP TRIGGER s3_stored_object_identity_guard ON sjia.s3_stored_object;
DROP FUNCTION sjia.guard_s3_stored_object_identity();
""",
        ),
    ]
