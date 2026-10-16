import json
from datetime import timedelta
from uuid import UUID

from django.utils import timezone
from django.conf import settings

from alerting.models import AlertEpisode, AlertEvaluation, AlertEvaluationInput
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.service import release_available
from family_content.publication_groups import publication_groups, matching_group
from records.models import RecordField
from family_content.policies import ContentPolicy, VisibilityPolicy


def current_event_reason(event, evaluation, gate, episode, now):
    """Pure validity check for current publication; keeps stored history untouched."""
    if (event.validity != 'valid' or gate is None or not gate.enabled
            or gate.active_release_id != event.release_id or evaluation is None
            or evaluation.patient_id != event.patient_id or evaluation.mode != 'current'
            or evaluation.environment != gate.environment or evaluation.status != 'completed'
            or evaluation.decision != 'met' or evaluation.validity != 'valid'
            or evaluation.policy_id != event.trigger_policy_id
            or evaluation.data_epoch != event.patient.data_epoch
            or evaluation.verification_epoch != event.patient.verification_epoch
            or evaluation.publication_epoch != gate.publication_epoch):
        return 'ALERT_CURRENT_DEPENDENCY_CHANGED'
    policy = event.trigger_policy.payload
    if not isinstance(policy, dict) or not isinstance(policy.get('episode'), dict):
        return 'ALERT_PUBLICATION_PROOF_INVALID'
    publication = policy.get('publication')
    snapshot = event.input_snapshot
    if (not isinstance(publication, dict) or type(publication.get('valid_for_seconds')) is not int
            or publication['valid_for_seconds'] <= 0 or type(publication.get('priority')) is not int
            or publication['priority'] <= 0 or not isinstance(policy.get('rule_key'), str)
            or not policy['rule_key'].strip() or not isinstance(snapshot, dict)
            or snapshot.get('schema_version') != 'dynamic-family-event-2'
            or snapshot.get('publication') != publication
            or snapshot.get('evaluation_id') != str(evaluation.id)
            or snapshot.get('input_digest') != evaluation.input_digest
            or snapshot.get('context_sequence') != str(evaluation.context_sequence)
            or snapshot.get('data_epoch') != str(evaluation.data_epoch)
            or snapshot.get('verification_epoch') != str(evaluation.verification_epoch)
            or snapshot.get('publication_epoch') != str(evaluation.publication_epoch)):
        return 'ALERT_PUBLICATION_PROOF_INVALID'
    try:
        expiry = event.created_at + timedelta(seconds=publication['valid_for_seconds'])
    except (OverflowError, TypeError):
        return 'ALERT_PUBLICATION_PROOF_INVALID'
    if event.created_at > now or expiry <= now:
        return 'ALERT_EXPIRED'
    if (episode is None or episode.patient_id != event.patient_id or episode.status != 'open'
            or snapshot.get('episode_id') != str(episode.id) or episode.last_event_id != event.id
            or episode.rule_family_key != policy.get('episode', {}).get('rule_family_key')):
        return 'ALERT_EPISODE_NOT_CURRENT'
    return None


def current_event_context(event, gate, now):
    evaluation = AlertEvaluation.objects.filter(id=event.evaluation_id, patient_id=event.patient_id).first()
    try:
        episode_id = UUID(event.input_snapshot.get('episode_id', ''))
    except (ValueError, TypeError, AttributeError):
        return evaluation, 'ALERT_PUBLICATION_PROOF_INVALID'
    episode = AlertEpisode.objects.filter(id=episode_id, patient_id=event.patient_id).first()
    reason = current_event_reason(event, evaluation, gate, episode, now)
    if reason is None:
        from alerting.presets import publication_binding_reason
        reason = publication_binding_reason(evaluation)
    if reason is None:
        groups = publication_groups(event.release)
        if groups:
            group = matching_group(event.release, event.trigger_policy_id)
            if group is None or group.window_id != evaluation.window_policy_id:
                reason = 'ALERT_RELEASE_RULE_MISMATCH'
        elif list(ReleaseComponent.objects.filter(release_id=event.release_id,
                component_kind='rule').values_list('config_id', flat=True)) != [event.trigger_policy_id]:
            reason = 'ALERT_RELEASE_RULE_MISMATCH'
    return evaluation, reason


def select_current_event(events):
    """Rank only by the common, explicitly approved numeric direction."""
    events = list(events)
    if not events:
        return None, None
    publications = [event.trigger_policy.payload['publication'] for event in events]
    priorities = {publication['priority'] for publication in publications}
    orders = {publication.get('priority_order', 'unconfigured') for publication in publications}
    if len(orders) != 1 or not orders <= {'unconfigured', 'higher_first', 'lower_first'}:
        return None, 'ALERT_PRIORITY_ORDER_UNCONFIGURED'
    order = next(iter(orders))
    if len(priorities) != 1 and order == 'unconfigured':
        return None, 'ALERT_PRIORITY_ORDER_UNCONFIGURED'
    direction = -1 if order == 'higher_first' else 1
    return min(events, key=lambda event: (direction * event.trigger_policy.payload['publication']['priority'],
        event.trigger_policy.payload['rule_key'], -event.created_at.timestamp(), str(event.id))), None


def alert_content(event, field_codes):
    now = timezone.now()
    gate = EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT).first()
    evaluation, reason = current_event_context(event, gate, now)
    if reason is not None or not release_available(event.release, now):
        return {'validity': 'invalid', 'content_state': 'unavailable', 'content': None, 'assessed_at': None}
    publishing = event.trigger_policy.payload['publication']
    component = ReleaseComponent.objects.filter(release=event.release, component_kind='content',
        config_id=publishing.get('content_config_id'), config__schema_version='content-1', config__status='enabled').select_related('config').first()
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
