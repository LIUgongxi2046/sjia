from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from alerting.home import determine_assessment_state
from alerting.models import AlertEvaluation, AlertEvent, AlertRecipient
from family_content.models import EnvironmentGate
from family_content.service import release_available
from family_content.alert_detail import current_event_context, select_current_event
from records.models import Record, RecordField, RecordVersion
from relations.models import Relation
from tasks.models import Task


def home_projection(*, patient, principal, field_codes, environment):
    now = timezone.now()
    gate = EnvironmentGate.objects.filter(environment=environment).select_related('active_release__contact_version').first()
    available = bool(gate and gate.enabled and gate.active_release and release_available(gate.active_release, now))
    evaluation = AlertEvaluation.objects.filter(patient=patient, environment=environment, mode='current').order_by('-assessment_at', '-context_sequence').first()
    state = determine_assessment_state(config_available=available, evaluation=evaluation)
    if evaluation and state == 'current' and (evaluation.data_epoch != patient.data_epoch
            or evaluation.verification_epoch != patient.verification_epoch
            or evaluation.publication_epoch != gate.publication_epoch):
        state = 'stale'
    if evaluation and state == 'current':
        from alerting.presets import publication_binding_reason
        if publication_binding_reason(evaluation) is not None:
            state = 'stale'
    task = Task.objects.filter(patient=patient, task_type='alert_evaluation',
        input_refs__payload__mode='current', input_refs__authorization_context__environment=environment).order_by('-created_at', '-id').first()
    processing = 'idle'
    if task:
        if task.status in ['accepted', 'blocked', 'queued', 'awaiting_confirmation', 'awaiting_resolution', 'uncertain', 'cancel_requested']:
            processing = 'pending'
        elif task.status == 'running':
            processing = 'running'
        elif task.status == 'failed':
            processing = 'failed'
        if available and task.status == 'failed' and (evaluation is None or task.created_at >= evaluation.assessment_at):
            state = 'stale' if evaluation else 'not_evaluated'
    eligible = []
    if available:
        candidates = AlertEvent.objects.filter(patient=patient, validity='valid',
            release_id=gate.active_release_id).select_related('patient', 'release', 'trigger_policy')
        for candidate in candidates:
            _, reason = current_event_context(candidate, gate, now)
            if reason is None:
                eligible.append(candidate)
    event, selection_reason = select_current_event(eligible)
    current_records = Record.objects.filter(patient=patient, status='active', id=OuterRef('record_id'), current_version=OuterRef('version'))
    versions = RecordVersion.objects.filter(patient=patient).filter(Exists(current_records))
    fields = RecordField.objects.filter(patient=patient, record_version_id__in=versions.values('id'), field_code__in=field_codes)
    data_state = 'empty' if not fields.exists() else 'incomplete' if fields.exclude(value_state='known').exists() else 'present'
    read_state = 'not_applicable'
    if event:
        relations = Relation.objects.filter(patient=patient, guardian=principal, status='active', valid_from__lte=now).filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        recipients = AlertRecipient.objects.filter(patient_id=patient.id, alert_id=event.id, relation_id__in=relations.values('id'))
        if recipients.exists():
            read_state = 'read' if recipients.filter(read_at__isnull=False).exists() else 'unread'
    return {'assessment_state': state, 'assessed_at': evaluation.assessment_at.isoformat() if available and evaluation else None,
            'current_alert_id': str(event.id) if event else None, 'service_state': 'enabled' if available else 'disabled',
            'data_state': data_state, 'processing_state': processing, 'alert_state': 'current' if event else 'invalid' if selection_reason else 'none', 'read_state': read_state}
