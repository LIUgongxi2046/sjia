import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models


class AccessGrant(models.Model):
    class RecordScope(models.TextChoices):
        OWN = "own", "own"
        ALL = "all", "all"
        ASSIGNED = "assigned", "assigned"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    principal = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="principal_id", related_name="+")
    # relation_id 参与复合外键 (relation_id, patient_id)，约束由原生 SQL 迁移建立
    relation_id = models.UUIDField(null=True)
    action = models.TextField()
    field_codes = ArrayField(models.TextField())
    record_scope = models.CharField(max_length=16, choices=RecordScope.choices)
    # assigned_record_id 参与复合外键 (assigned_record_id, patient_id)，约束由原生 SQL 迁移建立
    assigned_record_id = models.UUIDField(null=True)
    purpose = models.TextField()
    granted_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="granted_by", related_name="+")
    evidence_ref = models.TextField()
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."access_grant'
        indexes = [
            models.Index(fields=["principal_id", "patient_id", "action"], name="access_lookup"),
        ]


class ServiceRoleGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    principal = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="principal_id", related_name="+")
    environment = models.ForeignKey("family_content.EnvironmentGate", on_delete=models.RESTRICT, db_column="environment", to_field="environment", related_name="+")
    action = models.CharField(max_length=64)
    granted_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="granted_by", related_name="+")
    evidence_ref = models.TextField()
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    revoked_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="revoked_by", related_name="+")
    revocation_reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."service_role_grant'
        indexes = [
            models.Index(fields=["principal_id", "environment", "action"], name="service_role_lookup"),
        ]
