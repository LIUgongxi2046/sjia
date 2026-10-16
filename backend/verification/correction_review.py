"""Professional correction queue; every returned row rechecks exact case scope."""
from uuid import UUID

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.urls import reverse

from assessment.qualified_facts import QualificationRejected
from authorization.models import AccessGrant
from authorization.policy import Denied, check_service_grant
from records.correction import CorrectionRejected
from records.models import RecordField, RecordVersion
from sources.models import DataUseAuthorization
from verification.correction_requests import correction_scope
from verification.corrections import request_view
from verification.models import CorrectionRequest
from verification.service import VerificationRejected
from verification.workflow import source_authorization

SALT = 'correction-review-cursor-1'
SCOPE_ERRORS = (Denied, QualificationRejected, CorrectionRejected, VerificationRejected)


def review_scope(row, **identity):
    return correction_scope(**identity, operation='correction_request_resolve',
        target=reverse('correction_request_resolve', kwargs={'correction_request_id': row.id}),
        payload={'expected_version': str(row.version), 'decision': 'rejected', 'reason': 'Read authorized review scope',
            'resolution_record_version_id': None})


def field_view(field):
    return {key: getattr(field, key) for key in ('field_code', 'concept_code', 'value_type', 'value_state',
        'raw_text', 'raw_unit', 'qualifier', 'categorical_value')} | {'field_id': str(field.id),
        'raw_numeric': str(field.raw_numeric) if field.raw_numeric is not None else None}


def review_view(row, scope):
    patient, actor, record, original, fields, _, _ = scope
    return {'correction': request_view(row), 'patient_id': str(patient.id), 'display_id': patient.display_id,
        'record_id': str(record.id), 'record_version': str(original.version),
        'current_record_version': str(record.current_version), 'created_at': row.created_at.isoformat(),
        'resolution_reason': row.resolution_reason, 'resolved_at': row.resolved_at.isoformat() if row.resolved_at else None,
        'fields': [field_view(field) for field in fields]}


@transaction.atomic
def read_review(request_id, **identity):
    row = CorrectionRequest.objects.get(id=request_id)
    scope = review_scope(row, **identity)
    row.refresh_from_db()
    result = review_view(row, scope)
    _, actor, record, original, fields, _, _ = scope
    replacement = RecordVersion.objects.select_for_update().filter(record_id=record.id, patient_id=record.patient_id,
        version=record.current_version, supersedes_version=original.version, version__gt=original.version,
        corrected_at__isnull=False, recorded_at__gte=row.created_at).first()
    result['resolution_options'] = []
    if replacement is not None and replacement.correction_reason and replacement.correction_reason.strip():
        replacements = list(RecordField.objects.select_for_update().filter(patient_id=record.patient_id,
            record_version_id=replacement.id, field_code__in=[field.field_code for field in fields]))
        if sorted((field.field_code, field.concept_code, field.value_type) for field in fields) == sorted(
                (field.field_code, field.concept_code, field.value_type) for field in replacements):
            authorization = DataUseAuthorization.objects.get(id=original.use_authorization_id)
            try:
                source_authorization(replacement, authorization.purpose, identity['environment'])
            except VerificationRejected:
                pass
            else:
                result['resolution_options'] = [{'record_version_id': str(replacement.id), 'version': str(replacement.version),
                    'reason': replacement.correction_reason, 'fields': [field_view(field) for field in replacements]}]
    return result


@transaction.atomic
def review_queue(*, parameters, **identity):
    if identity['audience'] != 'support':
        raise Denied('SUPPORT_AUDIENCE_REQUIRED')
    if set(parameters) - {'cursor'} or any(len(parameters.getlist(key)) != 1 for key in parameters):
        raise CorrectionRejected('CORRECTION_QUERY_INVALID')
    actor = identity['actor']
    # The session and environment are also checked when the queue is empty.
    from identity.models import Principal
    from family_content.models import EnvironmentGate
    from authorization.policy import check_principal_session
    gate = EnvironmentGate.objects.select_for_update().get(environment=identity['environment'])
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, identity['epoch'])
    if not gate.enabled:
        raise Denied('ENVIRONMENT_DISABLED')
    check_service_grant(actor, 'verify_field', identity['environment'], timezone.now())
    patient_ids = AccessGrant.objects.filter(principal=actor, action='correct_record').values('patient_id')
    rows = CorrectionRequest.objects.filter(patient_id__in=patient_ids, status__in=['pending', 'in_review'])
    cursor = parameters.get('cursor')
    if cursor:
        try:
            saved = signing.loads(cursor, salt=SALT)
            if saved['actor'] != str(actor.id) or saved['epoch'] != str(actor.auth_epoch) or saved['environment'] != identity['environment']:
                raise ValueError
            point, key = parse_datetime(saved['created_at']), UUID(saved['id'])
            if point is None or point.utcoffset() is None:
                raise ValueError
        except (signing.BadSignature, KeyError, ValueError, TypeError):
            raise CorrectionRejected('CORRECTION_CURSOR_INVALID')
        rows = rows.filter(Q(created_at__gt=point) | Q(created_at=point, id__gt=key))
    candidates = list(rows.order_by('created_at', 'id')[:101])
    items = []
    for row in candidates[:100]:
        try:
            scope = review_scope(row, **identity)
        except SCOPE_ERRORS:
            continue
        items.append(review_view(row, scope))
    next_cursor = None
    if len(candidates) > 100:
        last = candidates[99]
        next_cursor = signing.dumps({'actor': str(actor.id), 'epoch': str(actor.auth_epoch),
            'environment': identity['environment'], 'created_at': last.created_at.isoformat(), 'id': str(last.id)}, salt=SALT)
    return {'items': items, 'next_cursor': next_cursor}
