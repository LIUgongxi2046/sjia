import uuid
from django.db import migrations, models
from core.fields import Sha256HexField

class Migration(migrations.Migration):
    dependencies = [('retention','0004_content_payload_guards')]
    operations = [migrations.CreateModel(name='ContentAccessRevocation', fields=[
        ('id',models.UUIDField(primary_key=True,default=uuid.uuid4,serialize=False)),
        ('plan_id',models.UUIDField()),('patient_id',models.UUIDField()),
        ('adapter',models.TextField()),('object_id',models.UUIDField()),
        ('object_version',models.TextField()),('revoked_at',models.DateTimeField()),
        ('proof_sha256',Sha256HexField())],options={'db_table':'sjia"."content_access_revocation',
            'constraints':[models.UniqueConstraint(fields=['adapter','object_id'],name='content_access_object_once')]})]
