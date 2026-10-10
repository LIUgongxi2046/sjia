import uuid

from django.db import models

from core.fields import Sha256HexField


class ConfigVersion(models.Model):
    class Kind(models.TextChoices):
        DICTIONARY = "dictionary", "dictionary"
        MAPPING = "mapping", "mapping"
        VERIFICATION = "verification", "verification"
        WINDOW = "window", "window"
        RULE = "rule", "rule"
        VISIBILITY = "visibility", "visibility"
        CONTENT = "content", "content"
        SUMMARY = "summary", "summary"
        NOTIFICATION = "notification", "notification"
        PROVIDER = "provider", "provider"
        VALIDATION = "validation", "validation"

    class Status(models.TextChoices):
        DRAFT = "draft", "draft"
        PENDING = "pending", "pending"
        APPROVED = "approved", "approved"
        ENABLED = "enabled", "enabled"
        DISABLED = "disabled", "disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    config_key = models.TextField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    version = models.PositiveBigIntegerField()
    schema_version = models.TextField()
    payload = models.JSONField()
    checksum = Sha256HexField()
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.SET_NULL, null=True, db_column="source_revision_id", related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices)
    created_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, related_name="+")
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."config_version'
        constraints = [
            models.UniqueConstraint(fields=["config_key", "version"], name="config_version_key_version_uniq"),
        ]


class ConfigApproval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    config = models.ForeignKey(ConfigVersion, on_delete=models.RESTRICT, db_column="config_id", related_name="approvals")
    approved_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, related_name="+")
    approval_role = models.TextField()
    approved_at = models.DateTimeField()
    evidence_ref = models.TextField()
    effective_scope = models.JSONField()
    valid_until = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    revocation_reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."config_approval'


class ConfigValidation(models.Model):
    class Outcome(models.TextChoices):
        PASS = "pass", "pass"
        FAIL = "fail", "fail"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    config = models.ForeignKey(ConfigVersion, on_delete=models.RESTRICT, db_column="config_id", related_name="validations")
    config_checksum = Sha256HexField()
    environment = models.ForeignKey("family_content.EnvironmentGate", on_delete=models.RESTRICT, db_column="environment", to_field="environment", related_name="+")
    artifact_sha256 = Sha256HexField()
    dataset_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="dataset_revision_id", related_name="+")
    evidence_ref = models.TextField()
    evidence_sha256 = Sha256HexField()
    outcome = models.CharField(max_length=8, choices=Outcome.choices)
    executed_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, related_name="+")
    executed_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    revocation_reason = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."config_validation'
        indexes = [
            models.Index(fields=["config_id", "environment", "executed_at"], name="config_validation_lookup"),
        ]
