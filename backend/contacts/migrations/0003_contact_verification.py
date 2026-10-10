from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('contacts', '0002_initial')]

    operations = [
        migrations.AlterField(model_name='contactversion', name='verified_at', field=models.DateTimeField(null=True)),
        migrations.AddConstraint(model_name='contactversion', constraint=models.CheckConstraint(
            condition=~models.Q(status__in=['approved', 'active']) | models.Q(verified_at__isnull=False),
            name='contact_verified_state_requires_time')),
    ]
