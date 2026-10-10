import uuid

from django.db import models


class Principal(models.Model):
    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "active"
        DISABLED = "disabled", "disabled"
        CLOSING = "closing", "closing"
        CLOSED = "closed", "closed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    account_status = models.CharField(max_length=16, choices=AccountStatus.choices)
    auth_epoch = models.BigIntegerField(default=0)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."principal'


class PrincipalIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    principal = models.ForeignKey(Principal, on_delete=models.RESTRICT, db_column="principal_id", related_name="identities")
    provider = models.TextField()
    provider_subject = models.TextField()
    verified_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."principal_identity'
        constraints = [
            models.UniqueConstraint(fields=["provider", "provider_subject"], name="principal_identity_provider_subject_uniq"),
        ]


class PrincipalCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    principal = models.OneToOneField(Principal, on_delete=models.CASCADE, db_column="principal_id", related_name="+")
    username = models.TextField(unique=True)
    password_hash = models.TextField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."principal_credential'


class Patient(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "active"
        RESTRICTED = "restricted", "restricted"
        DISPOSED = "disposed", "disposed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    display_id = models.TextField(unique=True)
    data_epoch = models.BigIntegerField(default=0)
    verification_epoch = models.BigIntegerField(default=0)
    evaluation_sequence = models.BigIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices)
    created_at = models.DateTimeField()
    current_profile_id = models.UUIDField(null=True)

    class Meta:
        db_table = 'sjia"."patient'
