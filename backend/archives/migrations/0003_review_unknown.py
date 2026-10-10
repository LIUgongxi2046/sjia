from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('archives', '0002_legacy_facts')]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE sjia.purpose_copy_review DROP CONSTRAINT purpose_copy_review_clinical_result_check; "
            "ALTER TABLE sjia.purpose_copy_review ADD CONSTRAINT purpose_copy_review_clinical_result_check "
            "CHECK (clinical_result IN ('passed','failed','unknown'))",
        ),
    ]
