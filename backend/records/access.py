from django.utils import timezone

from authorization.models import AccessGrant
from authorization.policy import matching_case_grants


def permitted_field_codes(principal, patient, action, *, record_id, author_id):
    return {code for grant in matching_case_grants(principal, patient, action, timezone.now(),
        record_id=record_id, author_id=author_id, purpose='family_observation',
        require_relation=True) for code in grant.field_codes}


def query_permissions(principal, patient):
    now = timezone.now()
    grants = {}
    for candidate in AccessGrant.objects.filter(principal=principal, patient=patient,
            action='read_raw', purpose='family_observation'):
        for grant in matching_case_grants(principal, patient, 'read_raw', now,
                author_id=principal.id, record_id=candidate.assigned_record_id,
                purpose='family_observation', require_relation=True):
            if grant.field_codes:
                grants[grant.id] = grant
    return [{'id': str(grant.id), 'field_codes': sorted(grant.field_codes), 'record_scope': grant.record_scope,
        'assigned_record_id': str(grant.assigned_record_id) if grant.assigned_record_id else None,
        'valid_from': grant.valid_from.isoformat(), 'valid_until': grant.valid_until.isoformat() if grant.valid_until else None,
        'relation_id': str(grant.relation_id) if grant.relation_id else None}
        for grant in sorted(grants.values(), key=lambda item: str(item.id))]
