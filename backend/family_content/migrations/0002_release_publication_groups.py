import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('family_content', '0001_initial')]
    operations = [migrations.CreateModel(name='ReleasePublicationGroup', fields=[
        ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
        ('release', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='family_content.releasebundle')),
        *[(kind, models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='+', to='configurations.configversion'))
            for kind in ('rule', 'window', 'verification', 'mapping', 'content', 'validation')],
    ], options={'db_table': 'sjia"."release_publication_group', 'constraints': [
        models.UniqueConstraint(fields=('release', 'rule'), name='release_publication_rule_unique')]} )]
