from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('relations', '0002_application_input')]

    operations = [
        # No backfill: legacy evidence must never become an invented declaration.
        migrations.AddField(model_name='relationapplication', name='applicant_declaration',
            field=models.JSONField(null=True)),
    ]
