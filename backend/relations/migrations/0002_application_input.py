from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('relations', '0001_initial')]

    operations = [
        migrations.AddField(model_name='relationapplication', name='note', field=models.TextField(null=True)),
        migrations.AddField(model_name='relationapplication', name='requested_access',
            field=models.CharField(max_length=24, null=True, choices=[('read', 'read'), ('read_write', 'read_write'), ('review_decides', 'review_decides')])),
        migrations.AddConstraint(model_name='relationapplication', constraint=models.CheckConstraint(
            condition=models.Q(requested_access__isnull=True) | models.Q(requested_access__in=['read', 'read_write', 'review_decides']),
            name='application_requested_access_valid')),
    ]
