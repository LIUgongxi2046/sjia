# release_component 的 SQL 主键为 (release_id, component_kind, config_id)。Django 需要单列主键，
# 因此增加代理主键 id，原复合主键改为唯一约束 release_component_pk_uniq。
import uuid

from django.db import models


class EnvironmentGate(models.Model):
    environment = models.CharField(primary_key=True, max_length=64)
    version = models.PositiveBigIntegerField()
    publication_epoch = models.BigIntegerField(default=0)
    enabled = models.BooleanField()
    active_release = models.ForeignKey("family_content.ReleaseBundle", on_delete=models.RESTRICT, null=True, db_column="active_release_id", related_name="+")
    runtime_config = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, null=True, db_column="runtime_config_id", related_name="+")
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."environment_gate'


class ReleaseBundle(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "draft"
        APPROVED = "approved", "approved"
        ENABLED = "enabled", "enabled"
        DISABLED = "disabled", "disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    environment = models.TextField()
    version = models.PositiveBigIntegerField()
    publication_epoch = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices)
    contact_version = models.ForeignKey("contacts.ContactVersion", on_delete=models.RESTRICT, db_column="contact_version_id", related_name="+")
    validation_config = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="validation_config_id", related_name="+")
    activated_at = models.DateTimeField(null=True)
    disabled_at = models.DateTimeField(null=True)
    valid_until = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."release_bundle'
        constraints = [
            models.UniqueConstraint(fields=["environment", "version"], name="release_bundle_environment_version_uniq"),
            models.UniqueConstraint(fields=["environment"], condition=models.Q(status="enabled"), name="release_one_enabled"),
        ]


class ReleaseComponent(models.Model):
    class ComponentKind(models.TextChoices):
        RULE = "rule", "rule"
        WINDOW = "window", "window"
        VERIFICATION = "verification", "verification"
        MAPPING = "mapping", "mapping"
        VISIBILITY = "visibility", "visibility"
        CONTENT = "content", "content"
        NOTIFICATION = "notification", "notification"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    release = models.ForeignKey(ReleaseBundle, on_delete=models.RESTRICT, db_column="release_id", related_name="+")
    component_kind = models.CharField(max_length=16, choices=ComponentKind.choices)
    config = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="config_id", related_name="+")

    class Meta:
        db_table = 'sjia"."release_component'
        constraints = [
            models.UniqueConstraint(fields=["release_id", "component_kind", "config_id"], name="release_component_pk_uniq"),
        ]


class ReleasePublicationGroup(models.Model):
    """Exact per-rule publication binding; shared components remain on the release."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    release = models.ForeignKey(ReleaseBundle, on_delete=models.RESTRICT, related_name='+')
    rule = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    window = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    verification = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    mapping = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    content = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    validation = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')

    class Meta:
        db_table = 'sjia"."release_publication_group'
        constraints = [models.UniqueConstraint(fields=['release', 'rule'], name='release_publication_rule_unique')]
