import uuid

from django.db import models


class ContactVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "draft"
        APPROVED = "approved", "approved"
        ACTIVE = "active", "active"
        DISABLED = "disabled", "disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    contact_id = models.UUIDField()
    version = models.PositiveBigIntegerField()
    contact_name = models.TextField()
    purpose = models.TextField()
    channel = models.TextField()
    channel_value = models.TextField()
    owner = models.ForeignKey("identity.Principal", on_delete=models.RESTRICT, db_column="owner_id", related_name="+")
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")
    approval_config = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="approval_config_id", related_name="+")
    verified_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField()
    valid_until = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices)

    class Meta:
        db_table = 'sjia"."contact_version'
        constraints = [
            models.UniqueConstraint(fields=["contact_id", "version"], name="contact_version_contact_id_version_uniq"),
            models.CheckConstraint(condition=~models.Q(status__in=['approved', 'active'])
                | models.Q(verified_at__isnull=False), name='contact_verified_state_requires_time'),
        ]
