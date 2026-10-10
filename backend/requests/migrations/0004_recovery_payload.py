from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('requests', '0003_authorization_context')]

    operations = [migrations.AddField(model_name='writeintent', name='recovery_payload', field=models.JSONField(null=True))]
