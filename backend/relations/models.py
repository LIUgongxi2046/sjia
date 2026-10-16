import uuid

from django.db import models


class RelationApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        APPROVED = "approved", "approved"
        REJECTED = "rejected", "rejected"
        WITHDRAWN = "withdrawn", "withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    version = models.PositiveBigIntegerField()
    applicant = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="applicant_id", related_name="+")
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    evidence_ref = models.TextField()
    # Immutable self-report, never a Patient display name or verified relation.
    applicant_declaration = models.JSONField(null=True)
    note = models.TextField(null=True)
    requested_access = models.CharField(max_length=24, null=True, choices=[('read', 'read'), ('read_write', 'read_write'), ('review_decides', 'review_decides')])
    status = models.CharField(max_length=16, choices=Status.choices)
    checked_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="checked_by", related_name="+")
    checked_at = models.DateTimeField(null=True)
    reason = models.TextField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."relation_application'
        constraints = [models.CheckConstraint(condition=models.Q(requested_access__isnull=True)
            | models.Q(requested_access__in=['read', 'read_write', 'review_decides']), name='application_requested_access_valid')]


class Relation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "active"
        REVOKED = "revoked", "revoked"
        EXPIRED = "expired", "expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    guardian = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="guardian_id", related_name="+")
    application = models.ForeignKey(RelationApplication, on_delete=models.RESTRICT, db_column="application_id", related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices)
    version = models.PositiveBigIntegerField()
    auth_epoch = models.BigIntegerField(default=0)
    checked_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="checked_by", related_name="+")
    checked_at = models.DateTimeField()
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."relation'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="relation_id_patient_uniq"),
            models.UniqueConstraint(fields=["patient_id", "guardian_id"], condition=models.Q(status="active"), name="relation_one_active"),
        ]
