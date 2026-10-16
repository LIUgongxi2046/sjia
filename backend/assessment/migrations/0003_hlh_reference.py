"""Add independent nullable HLH reference output; no backfill or activation."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('assessment', '0002_initial')]
    operations = [
        migrations.AlterField(model_name='assessment', name='kind',
            field=models.CharField(choices=[('classification', 'classification'), ('ms', 'ms'), ('hlh', 'hlh')], max_length=16)),
        migrations.AddField(model_name='assessmentoutput', name='hlh_result', field=models.JSONField(null=True)),
        migrations.AddConstraint(model_name='assessment', constraint=models.CheckConstraint(
            condition=models.Q(kind__in=['classification', 'ms', 'hlh']), name='assessment_reference_kind_valid')),
        migrations.AddConstraint(model_name='assessmentoutput', constraint=models.CheckConstraint(
            condition=models.Q(hlh_result__isnull=True) | (models.Q(classification_conditions__isnull=True)
                & models.Q(classification_result__isnull=True) & models.Q(ms_contributions__isnull=True)
                & models.Q(ms_total__isnull=True) & models.Q(ms_threshold_result__isnull=True)),
            name='assessment_hlh_output_separate')),
    ]
