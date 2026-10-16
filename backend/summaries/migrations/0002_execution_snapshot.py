from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('summaries', '0001_initial')]
    operations = [
        migrations.AddField(model_name='summary', name='execution_snapshot', field=models.JSONField(null=True)),
        migrations.AddField(model_name='summary', name='execution_sha256', field=models.CharField(max_length=64, null=True)),
    ]
