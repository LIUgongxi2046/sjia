from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('tasks', '0004_purpose_copy')]
    operations = [migrations.AddField(model_name='outboxevent', name='traceparent',
        field=models.CharField(max_length=55, blank=True, default=''))]
