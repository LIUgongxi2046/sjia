from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('requests', '0002_initial')]

    operations = [
        migrations.AddField(
            model_name='writeintent', name='authorization_context', field=models.JSONField(null=True),
        ),
    ]
