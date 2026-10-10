from django.db import migrations

# 病例当前版本视图，来自 case-schema.sql 末尾定义。
CASE_VIEW_SQL = """
CREATE VIEW sjia.current_case_record_version AS
SELECT rv.* FROM sjia.record_version rv
JOIN sjia.record r ON (r.id, r.patient_id, r.current_version) = (rv.record_id, rv.patient_id, rv.version)
JOIN sjia.patient p ON p.id = rv.patient_id
WHERE r.status = 'active' AND p.status = 'active';
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_complex_checks"),
    ]

    operations = [
        migrations.RunSQL(CASE_VIEW_SQL, reverse_sql="DROP VIEW IF EXISTS sjia.current_case_record_version"),
    ]
