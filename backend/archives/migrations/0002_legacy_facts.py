from django.db import migrations


LEGACY_SQL = '''
INSERT INTO sjia.archive (id, patient_id, created_at)
SELECT p.id, p.id, CURRENT_TIMESTAMP FROM sjia.patient p
WHERE NOT EXISTS (SELECT 1 FROM sjia.archive a WHERE a.patient_id = p.id);

INSERT INTO sjia.archive_fact (id, patient_id, current_version, created_at)
SELECT f.id, f.patient_id, 1, CURRENT_TIMESTAMP FROM sjia.record_field f
WHERE NOT EXISTS (SELECT 1 FROM sjia.archive_fact_version v WHERE v.field_id = f.id);

INSERT INTO sjia.archive_fact_version (
    id, patient_id, fact_id, version, record_version_id, field_id,
    document_version_id, page_id, source_anchor_id, context_type, assertion_state,
    episode_id, medical_time_id, raw_time_text, time_origin, documented_time_id,
    available_at, availability_status, confirmation_id, created_at, supersedes_version, reason
)
SELECT f.id, f.patient_id, f.id, 1, f.record_version_id, f.id,
    NULL, NULL, f.anchor_id, 'unknown', 'unknown', NULL, f.medical_time_id,
    mt.raw_text, 'legacy', NULL, NULL, 'unknown', f.confirmation_id,
    CURRENT_TIMESTAMP, NULL, NULL
FROM sjia.record_field f
LEFT JOIN sjia.medical_time mt ON (mt.id, mt.patient_id) = (f.medical_time_id, f.patient_id)
WHERE NOT EXISTS (SELECT 1 FROM sjia.archive_fact_version v WHERE v.field_id = f.id);

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM sjia.record_field f
        LEFT JOIN sjia.archive_fact_version v ON v.field_id = f.id
        WHERE v.id IS NULL OR v.patient_id <> f.patient_id
          OR v.record_version_id <> f.record_version_id
          OR v.medical_time_id IS DISTINCT FROM f.medical_time_id
          OR v.source_anchor_id IS DISTINCT FROM f.anchor_id
          OR v.confirmation_id IS DISTINCT FROM f.confirmation_id
          OR v.time_origin <> 'legacy' OR v.context_type <> 'unknown'
          OR v.assertion_state <> 'unknown' OR v.available_at IS NOT NULL
          OR v.availability_status <> 'unknown'
    ) THEN
        RAISE EXCEPTION '旧字段与新增历史事实引用不一致';
    END IF;
END $$;
'''


class Migration(migrations.Migration):
    dependencies = [('archives', '0001_initial')]
    operations = [migrations.RunSQL(LEGACY_SQL)]
