import json

from django.utils import timezone
from django.conf import settings

from alerting.models import AlertEvaluation, AlertEvaluationInput
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.service import release_available
from records.models import RecordField
from family_content.policies import ContentPolicy, VisibilityPolicy


def alert_content(event, field_codes):
    now = timezone.now()
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).first()
    evaluation = AlertEvaluation.objects.filter(id=event.evaluation_id, patient=event.patient).first()
    current = bool(event.validity == 'valid' and gate and gate.enabled and gate.active_release_id == event.release_id
                   and release_available(event.release, now) and evaluation and evaluation.validity == 'valid'
                   and evaluation.data_epoch == event.patient.data_epoch
                   and evaluation.verification_epoch == event.patient.verification_epoch
                   and evaluation.publication_epoch == gate.publication_epoch)
    if not current:
        return {'validity': 'invalid', 'content_state': 'unavailable', 'content': None, 'assessed_at': None}
    component = ReleaseComponent.objects.filter(release=event.release, component_kind='content', config__schema_version='content-1', config__status='enabled').select_related('config').first()
    if component is None or not isinstance(component.config.payload.get('approved_text'), str) or not component.config.payload['approved_text'].strip():
        return {'validity': 'valid', 'content_state': 'configuration_pending', 'content': None, 'assessed_at': None}
    policy = ContentPolicy.model_validate_json(json.dumps(component.config.payload))
    if evaluation.policy_id != policy.rule_id or evaluation.window_policy_id != policy.window_policy_id:
        return {'validity': 'invalid', 'content_state': 'unavailable', 'content': None, 'assessed_at': None}
    visibility_component = ReleaseComponent.objects.get(release=event.release, component_kind='visibility')
    visibility = VisibilityPolicy.model_validate_json(json.dumps(visibility_component.config.payload))
    permitted = RecordField.objects.filter(patient=event.patient, field_code__in=set(field_codes) & set(visibility.field_codes))
    references = AlertEvaluationInput.objects.filter(evaluation_id=event.evaluation_id, patient_id=event.patient_id, field_id__in=permitted.values('id')).select_related('source_revision')
    source_refs = [{'record_version_id': str(item.record_version_id), 'field_id': str(item.field_id), 'source_revision_id': str(item.source_revision_id), 'source_version': item.source_revision.version} for item in references]
    contact = event.release.contact_version
    contact_view = {'state': 'unavailable'}
    if contact.status == 'active' and contact.valid_until > now:
        contact_view = {'state': 'available', 'contact_version_id': str(contact.id), 'contact_name': contact.contact_name,
                        'purpose': contact.purpose, 'channel': contact.channel, 'channel_value': contact.channel_value, 'valid_until': contact.valid_until.isoformat()}
    return {'validity': 'valid', 'content_state': 'available', 'assessed_at': evaluation.assessment_at.isoformat(), 'content': {
        'content_id': str(component.config_id), 'content_version': str(component.config.version),
        'approved_text': policy.approved_text, 'source_refs': source_refs, 'contact': contact_view,
    }}
