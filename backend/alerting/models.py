# SQL 中 alert_evaluation_input、alert_evaluation_assessment、alert_signal_result 使用复合主键。
# Django 不支持复合主键，因此增加代理主键 id，原复合主键改由 UniqueConstraint 表达。
import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from retention.erasure_models import ErasureModel

from core.fields import Sha256HexField


class AlertPresetChoice(models.Model):
    """Immutable patient-scoped selection history; a choice grants no access."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey('identity.Patient', on_delete=models.RESTRICT, related_name='+')
    environment = models.TextField()
    purpose = models.TextField()
    version = models.PositiveBigIntegerField()
    assignment = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    schedule = models.ForeignKey('configurations.ConfigVersion', on_delete=models.RESTRICT, related_name='+')
    selected_by = models.ForeignKey('identity.Principal', on_delete=models.RESTRICT, related_name='+')
    selected_at = models.DateTimeField()
    authorization_context = models.JSONField()
    write_request = models.ForeignKey('requests.WriteRequest', on_delete=models.RESTRICT, null=True, related_name='+')

    class Meta:
        db_table = 'sjia"."alert_preset_choice'
        constraints = [
            models.UniqueConstraint(fields=['patient', 'environment', 'purpose', 'version'], name='preset_choice_scope_version_uniq'),
            models.CheckConstraint(condition=models.Q(version__gte=1), name='preset_choice_positive_version'),
        ]


class AlertEvaluation(ErasureModel, models.Model):
    class Mode(models.TextChoices):
        CURRENT = "current", "current"
        REPLAY = "replay", "replay"

    class Status(models.TextChoices):
        CONFIGURATION_PENDING = "configuration_pending", "configuration_pending"
        INSUFFICIENT = "insufficient", "insufficient"
        COMPLETED = "completed", "completed"
        FAILED = "failed", "failed"
        SUPERSEDED = "superseded", "superseded"

    class Decision(models.TextChoices):
        MET = "met", "met"
        NOT_MET = "not_met", "not_met"
        UNKNOWN = "unknown", "unknown"

    class Validity(models.TextChoices):
        VALID = "valid", "valid"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    task_id = models.UUIDField(unique=True)
    environment = models.TextField()
    policy = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, null=True, db_column="policy_id", related_name="+")
    window_policy = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, null=True, db_column="window_policy_id", related_name="+")
    assessment_at = models.DateTimeField(null=True)
    known_at = models.DateTimeField(null=True)
    context_sequence = models.PositiveBigIntegerField()
    mode = models.CharField(max_length=16, choices=Mode.choices)
    status = models.CharField(max_length=24, choices=Status.choices)
    decision = models.CharField(max_length=16, choices=Decision.choices, null=True)
    data_epoch = models.BigIntegerField(default=0)
    verification_epoch = models.BigIntegerField(default=0)
    publication_epoch = models.BigIntegerField(default=0)
    authorization_version = models.PositiveBigIntegerField()
    lease_generation = models.PositiveBigIntegerField()
    use_authorization_id = models.UUIDField()
    input_digest = Sha256HexField()
    missing_items = models.JSONField(null=True)
    computed_at = models.DateTimeField()
    validity = models.CharField(max_length=16, choices=Validity.choices)

    class Meta:
        db_table = 'sjia"."alert_evaluation'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="alert_evaluation_id_patient_uniq"),
            models.UniqueConstraint(fields=["patient_id", "context_sequence"], name="alert_evaluation_patient_id_context_sequence_uniq"),
        ]
        indexes = [
            models.Index(fields=["patient_id", "-assessment_at", "-context_sequence"], condition=models.Q(mode="current"), name="evaluation_current"),
        ]


class AlertEvaluationInput(ErasureModel, models.Model):
    class InputRole(models.TextChoices):
        BASELINE = "baseline", "baseline"
        CURRENT = "current", "current"
        SERIES = "series", "series"
        CONTEXT = "context", "context"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    evaluation_id = models.UUIDField()
    patient_id = models.UUIDField()
    signal_id = models.TextField(null=True)
    position = models.IntegerField()
    variable_code = models.TextField(null=True)
    input_role = models.CharField(max_length=16, choices=InputRole.choices, null=True)
    record_version_id = models.UUIDField()
    field_id = models.UUIDField()
    verification_id = models.UUIDField()
    normalization_id = models.UUIDField(null=True)
    medical_time_id = models.UUIDField(null=True)
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")

    class Meta:
        db_table = 'sjia"."alert_evaluation_input'
        constraints = [
            models.UniqueConstraint(fields=["evaluation_id", "signal_id", "position"], name="alert_evaluation_input_pk_uniq"),
        ]
        indexes = [
            models.Index(fields=["field_id", "evaluation_id"], name="evaluation_input_field"),
        ]


class AlertEvaluationAssessment(ErasureModel, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    evaluation_id = models.UUIDField()
    assessment_id = models.UUIDField()
    patient_id = models.UUIDField()
    role = models.TextField(null=True)

    class Meta:
        db_table = 'sjia"."alert_evaluation_assessment'
        constraints = [
            models.UniqueConstraint(fields=["evaluation_id", "assessment_id", "role"], name="alert_evaluation_assessment_pk_uniq"),
        ]


class AlertSignalResult(ErasureModel, models.Model):
    class State(models.TextChoices):
        MET = "met", "met"
        NOT_MET = "not_met", "not_met"
        UNKNOWN = "unknown", "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    evaluation_id = models.UUIDField()
    patient_id = models.UUIDField()
    signal_id = models.TextField(null=True)
    schema_version = models.TextField()
    state = models.CharField(max_length=16, choices=State.choices, null=True)
    algorithm_version = models.TextField()
    input_field_ids = ArrayField(models.UUIDField())
    reason_codes = ArrayField(models.TextField(), null=True)
    pair_results = models.JSONField(null=True)

    class Meta:
        db_table = 'sjia"."alert_signal_result'
        constraints = [
            models.UniqueConstraint(fields=["evaluation_id", "signal_id"], name="alert_signal_result_pk_uniq"),
        ]


class AlertEpisode(ErasureModel, models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "open"
        CLOSED = "closed", "closed"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    rule_family_key = models.TextField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    opened_at = models.DateTimeField(null=True)
    last_evaluation_id = models.UUIDField()
    last_event_id = models.UUIDField(null=True)
    state_version = models.PositiveBigIntegerField()
    reset_evaluation_id = models.UUIDField(null=True)
    last_assessment_at = models.DateTimeField(null=True)
    last_context_sequence = models.PositiveBigIntegerField()

    class Meta:
        db_table = 'sjia"."alert_episode'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="alert_episode_id_patient_uniq"),
            models.UniqueConstraint(fields=["patient_id", "rule_family_key"], condition=models.Q(status="open"), name="episode_one_open"),
        ]


class AlertDecision(ErasureModel, models.Model):
    class State(models.TextChoices):
        MET = "met", "met"
        NOT_MET = "not_met", "not_met"
        UNKNOWN = "unknown", "unknown"

    class Action(models.TextChoices):
        CREATE_EVENT = "create_event", "create_event"
        CONTINUE_EPISODE = "continue_episode", "continue_episode"
        CLOSE_EPISODE = "close_episode", "close_episode"
        NO_EVENT = "no_event", "no_event"
        BLOCKED = "blocked", "blocked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    evaluation_id = models.UUIDField()
    patient_id = models.UUIDField()
    rule_key = models.TextField(null=True)
    state = models.CharField(max_length=16, choices=State.choices, null=True)
    action = models.CharField(max_length=16, choices=Action.choices, null=True)
    reason_codes = ArrayField(models.TextField(), null=True)
    episode_id = models.UUIDField(null=True)
    sequence = models.PositiveBigIntegerField()
    previous_decision_id = models.UUIDField(null=True)

    class Meta:
        db_table = 'sjia"."alert_decision'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id", "rule_key"], name="alert_decision_id_patient_id_rule_key_uniq"),
            models.UniqueConstraint(fields=["evaluation_id", "rule_key"], name="alert_decision_evaluation_id_rule_key_uniq"),
            models.UniqueConstraint(fields=["patient_id", "rule_key", "sequence"], name="alert_decision_patient_id_rule_key_sequence_uniq"),
        ]


class AlertEvent(ErasureModel, models.Model):
    class Validity(models.TextChoices):
        VALID = "valid", "valid"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, db_column="patient_id", related_name="+")
    evaluation_id = models.UUIDField()
    release = models.ForeignKey("family_content.ReleaseBundle", on_delete=models.RESTRICT, db_column="release_id", related_name="+")
    trigger_key = models.TextField(null=True)
    trigger_policy = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="trigger_policy_id", related_name="+")
    input_snapshot = models.JSONField(null=True)
    validity = models.CharField(max_length=16, choices=Validity.choices)
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'sjia"."alert_event'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="alert_event_id_patient_uniq"),
            models.UniqueConstraint(fields=["patient_id", "trigger_policy_id", "trigger_key"], name="alert_event_patient_trigger_policy_trigger_key_uniq"),
        ]


class AlertRecipient(ErasureModel, models.Model):
    class DeliveryStatus(models.TextChoices):
        NOT_SENT = "not_sent", "not_sent"
        PENDING = "pending", "pending"
        ACCEPTED = "accepted", "accepted"
        DELIVERED = "delivered", "delivered"
        FAILED = "failed", "failed"
        UNCERTAIN = "uncertain", "uncertain"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient_id = models.UUIDField()
    alert_id = models.UUIDField()
    relation_id = models.UUIDField()
    channel = models.TextField()
    delivery_status = models.CharField(max_length=16, choices=DeliveryStatus.choices)
    read_at = models.DateTimeField(null=True)

    class Meta:
        db_table = 'sjia"."alert_recipient'
        constraints = [
            models.UniqueConstraint(fields=["id", "patient_id"], name="alert_recipient_id_patient_uniq"),
            models.UniqueConstraint(fields=["alert_id", "relation_id", "channel"], name="alert_recipient_alert_id_relation_id_channel_uniq"),
        ]
