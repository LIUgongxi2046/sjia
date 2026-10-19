import uuid

from django.db import models

from core.fields import Sha256HexField


class RetentionPolicy(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "draft"
        APPROVED = "approved", "approved"
        ACTIVE = "active", "active"
        DISABLED = "disabled", "disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    policy_key = models.TextField()
    version = models.PositiveBigIntegerField()
    data_class = models.TextField()
    rules = models.JSONField()
    schema_version = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices)
    approved_by = models.ForeignKey("identity.Principal", on_delete=models.SET_NULL, null=True, related_name="+")
    approved_at = models.DateTimeField(null=True)
    evidence_ref = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."retention_policy'
        constraints = [
            models.UniqueConstraint(fields=["policy_key", "version"], name="retention_policy_key_version_uniq"),
        ]


class SimulatedProviderCopy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    purpose_copy = models.ForeignKey("archives.PurposeCopy", on_delete=models.RESTRICT,
        db_column="purpose_copy_id", related_name="+")
    purpose_copy_version = models.BigIntegerField()
    provider_id = models.TextField()
    object_id = models.UUIDField()
    object_version = models.TextField()
    checksum = Sha256HexField()
    size_bytes = models.BigIntegerField()
    environment = models.TextField()
    status = models.TextField()
    deletion_request_id = models.UUIDField(null=True)
    receipt_ref = models.TextField(null=True)
    receipt_sha256 = Sha256HexField(null=True)
    created_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT,
        db_column="created_by", related_name="+")
    created_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True)
    processing_evidence = models.JSONField()

    class Meta:
        db_table = 'sjia"."simulated_provider_copy'
        constraints = [
            models.UniqueConstraint(fields=["provider_id", "object_id", "object_version"],
                name="sim_provider_object_version_uniq"),
            models.UniqueConstraint(fields=["purpose_copy", "purpose_copy_version", "provider_id"],
                name="sim_provider_purpose_copy_uniq"),
        ]


class DispositionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        APPROVED = "approved", "approved"
        RUNNING = "running", "running"
        BLOCKED = "blocked", "blocked"
        COMPLETED = "completed", "completed"
        REJECTED = "rejected", "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    requested_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="requested_by", related_name="+")
    policy = models.ForeignKey(RetentionPolicy, on_delete=models.RESTRICT, db_column="policy_id", related_name="+")
    evidence_ref = models.TextField()
    scope = models.JSONField()
    status = models.CharField(max_length=16, choices=Status.choices)
    approved_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="approved_by", related_name="+")
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."disposition_request'


class DispositionItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "pending"
        BLOCKED = "blocked", "blocked"
        RETAINED = "retained", "retained"
        DELETED = "deleted", "deleted"
        FAILED = "failed", "failed"
        AWAITING_PROVIDER = "awaiting_provider", "awaiting_provider"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    request = models.ForeignKey(DispositionRequest, on_delete=models.RESTRICT, db_column="request_id", related_name="+")
    target_type = models.TextField()
    target_id = models.TextField()
    target_version = models.TextField()
    status = models.CharField(max_length=32, choices=Status.choices)
    reason = models.TextField(null=True)
    retention_evidence_ref = models.TextField(null=True)
    recheck_at = models.DateTimeField(null=True)
    executed_by = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, null=True, db_column="executed_by", related_name="+")
    executed_at = models.DateTimeField(null=True)
    receipt_ref = models.TextField(null=True)
    receipt_sha256 = Sha256HexField(null=True)

    class Meta:
        db_table = 'sjia"."disposition_item'
        constraints = [
            models.UniqueConstraint(fields=["request_id", "target_type", "target_id", "target_version"], name="disposition_item_target_version_uniq"),
        ]


class ContentDispositionPolicy(models.Model):
    """An explicitly sourced policy. No rows/default policy are installed."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    version = models.PositiveBigIntegerField()
    environment = models.TextField()
    rules = models.JSONField()
    checksum = Sha256HexField()
    source_revision = models.ForeignKey('sources.SourceRevision', on_delete=models.RESTRICT, related_name='+')
    approved_by = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, null=True, related_name='+')
    approved_at = models.DateTimeField(null=True)
    enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."content_disposition_policy'


class ContentDispositionPlan(models.Model):
    family_request = models.OneToOneField('ContentDispositionFamilyRequest',on_delete=models.RESTRICT,null=True,related_name='+')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey('identity.Patient', on_delete=models.RESTRICT, related_name='+')
    policy = models.ForeignKey(ContentDispositionPolicy, on_delete=models.RESTRICT, related_name='+')
    requested_by = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, related_name='+')
    manifest = models.JSONField()
    manifest_sha256 = Sha256HexField()
    status = models.TextField(default='preview')
    created_at = models.DateTimeField()
    approved_by = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, null=True, related_name='+')
    approved_at = models.DateTimeField(null=True)
    approval = models.JSONField(null=True)
    completed_at = models.DateTimeField(null=True)
    journal_sequence = models.PositiveBigIntegerField(null=True)
    receipt = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."content_disposition_plan'


class ContentErasure(models.Model):
    """Permanent replay identity; never contains original clinical payload."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    plan_id = models.UUIDField()
    patient_id = models.UUIDField()
    adapter = models.TextField()
    object_id = models.UUIDField()
    object_version = models.TextField()
    generation = models.PositiveBigIntegerField()
    erased_at = models.DateTimeField()
    proof_sha256 = Sha256HexField()

    class Meta:
        db_table = 'sjia"."content_erasure'
        constraints = [models.UniqueConstraint(fields=['adapter', 'object_id'], name='content_erasure_object_once')]


class ContentDispositionHold(models.Model):
    """Only explicit current reviewed absence of holds permits erasure."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey('identity.Patient', on_delete=models.RESTRICT, related_name='+')
    source_revision = models.ForeignKey('sources.SourceRevision', on_delete=models.RESTRICT, related_name='+')
    state = models.TextField()  # clear, held, unresolved; absence is unresolved
    reviewed_by = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, related_name='+')
    reviewed_at = models.DateTimeField()
    valid_until = models.DateTimeField()
    version = models.PositiveBigIntegerField()

    class Meta:
        db_table = 'sjia"."content_disposition_hold'


class ContentRestoreState(models.Model):
    """A restored DB cannot hide its admission requirement by omitting a mount."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    singleton = models.BooleanField(default=True, unique=True)
    database_name = models.TextField()
    cluster_id = models.TextField()
    object_root = models.TextField()
    backup_manifest_sha256 = Sha256HexField()
    content_checkpoint = models.JSONField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."content_restore_state'
        constraints = [models.CheckConstraint(condition=models.Q(singleton=True),
            name='content_restore_singleton_true')]


class S3ContentRestoreState(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    generation = models.PositiveBigIntegerField(default=1, unique=True)
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, related_name='+')
    parent_mapping_sha256 = Sha256HexField(null=True)
    ancestor_sha256 = Sha256HexField(null=True)
    database_name = models.TextField()
    cluster_id = models.TextField()
    restore_root = models.TextField()
    source_binding_sha256 = Sha256HexField()
    target_binding_sha256 = Sha256HexField()
    source_snapshot_sha256 = Sha256HexField()
    backup_manifest_sha256 = Sha256HexField()
    content_checkpoint = models.JSONField()
    mapping_sha256 = Sha256HexField(null=True)
    state = models.CharField(max_length=16, default='preparing')
    created_at = models.DateTimeField()
    mapped_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."s3_content_restore_state'
        constraints = [
            models.CheckConstraint(condition=models.Q(generation__gte=1), name='s3_restore_generation_positive'),
            models.CheckConstraint(condition=models.Q(generation=1, parent__isnull=True,
                    parent_mapping_sha256__isnull=True, ancestor_sha256__isnull=True)
                | models.Q(generation__gt=1, parent__isnull=False, parent_mapping_sha256__isnull=False,
                    ancestor_sha256__isnull=False), name='s3_restore_parent_shape'),
            models.UniqueConstraint(fields=['target_binding_sha256'], name='s3_restore_target_binding_once'),
            models.CheckConstraint(condition=~models.Q(source_binding_sha256=models.F('target_binding_sha256')),
                name='s3_restore_distinct_binding'),
            models.CheckConstraint(condition=models.Q(state='preparing', mapping_sha256__isnull=True, mapped_at__isnull=True)
                | models.Q(state='mapped', mapping_sha256__isnull=False, mapped_at__isnull=False,
                    mapped_at__gte=models.F('created_at')), name='s3_restore_mapping_state'),
        ]


class ContentAccessRevocation(models.Model):
    """Read/production fence without altering or relabeling retained payload."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    plan_id = models.UUIDField()
    patient_id = models.UUIDField()
    adapter = models.TextField()
    object_id = models.UUIDField()
    object_version = models.TextField()
    revoked_at = models.DateTimeField()
    proof_sha256 = Sha256HexField()

    class Meta:
        db_table = 'sjia"."content_access_revocation'
        constraints = [models.UniqueConstraint(fields=['adapter', 'object_id'], name='content_access_object_once')]


class ContentDispositionReview(models.Model):
    """Approved, versioned continued-retention/recheck facts; no clinical text."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    plan = models.ForeignKey(ContentDispositionPlan, on_delete=models.RESTRICT, related_name='+')
    version = models.PositiveBigIntegerField()
    reviewed_by = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, related_name='+')
    reviewed_at = models.DateTimeField()
    next_recheck_at = models.DateTimeField()
    source_revision = models.ForeignKey('sources.SourceRevision', on_delete=models.RESTRICT, related_name='+')
    attestation = models.JSONField()

    class Meta:
        db_table = 'sjia"."content_disposition_review'
        constraints = [models.UniqueConstraint(fields=['plan','version'], name='content_review_plan_version_once')]


class ContentDispositionFamilyRequest(models.Model):
    """A person's bounded application, separate from staff approval/execution."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey('identity.Patient',on_delete=models.RESTRICT,related_name='+')
    requested_by = models.ForeignKey('identity.Principal',on_delete=models.RESTRICT,related_name='+')
    requested_auth_epoch = models.PositiveBigIntegerField()
    client_request_id = models.UUIDField()
    policy = models.ForeignKey(ContentDispositionPolicy,on_delete=models.RESTRICT,related_name='+')
    policy_version = models.PositiveBigIntegerField()
    policy_sha256 = Sha256HexField()
    targets = models.JSONField()
    request_sha256 = Sha256HexField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."content_disposition_family_request'
        constraints = [models.UniqueConstraint(fields=['requested_by','client_request_id'], name='content_family_request_once')]
