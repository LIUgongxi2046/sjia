from django.db import migrations


SQL = (
    'ALTER TABLE sjia.purpose_copy DROP CONSTRAINT purpose_copy_purpose_check; '
    'ALTER TABLE sjia.purpose_copy ADD CONSTRAINT purpose_copy_purpose_check '
    "CHECK (purpose IN ('external_recognition','format_evaluation','workflow_evaluation',"
    "'clinical_evaluation','competition_display','export'))"
)


class Migration(migrations.Migration):
    dependencies = [('archives', '0004_page_order')]
    operations = [migrations.RunSQL(SQL)]
