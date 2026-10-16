"""Metadata-only, fail-closed audit boundary for protected HTTP operations.

No request body, query string, credentials, response body or clinical text is
copied into an audit event. Public identity setup must remain bootstrappable.
"""
from functools import wraps
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from audit.service import write_audit
from identity.models import Patient, Principal
from identity.session import read_session_principal
from retention.models import RetentionPolicy
from retention.policy_locks import read_retention_policy_shared


PUBLIC_OPERATIONS = frozenset({
    'auth_bootstrap', 'auth_start', 'auth_complete', 'auth_register', 'auth_login',
    'session_read', 'session_logout', 'capabilities_read', 'support_login',
    'support_session_read', 'support_logout',
})

# These operations perform storage or inference work outside short business
# transactions. Audit admission before that work, then its observable outcome.
EXTERNAL_OPERATIONS = frozenset({
    'file_upload', 'file_content', 'original_content', 'original_source_content',
    'voice_transcription', 'voice_structure', 'report_ocr', 'therapy_ocr',
    'content_disposition_execute',
})

# POST is used for these read-only searches to keep sensitive filters out of
# URLs. Their own snapshot transactions must start before their first query.
READ_OPERATIONS = frozenset({'record_search', 'history_search_view',
    'analyte_search_view', 'dictionary_search', 'archive_search', 'material_notice_search',
    'material_reminder_search', 'material_reminder_target_search', 'archive_directory_search',
    'archive_batch_search', 'personal_record_task_search'})


class AuditUnavailable(Exception):
    pass


class RejectedResponse(Exception):
    def __init__(self, response):
        self.response = response


def audit_policy(*, lock=True):
    now = timezone.now()
    candidates = list(RetentionPolicy.objects.filter(data_class='audit', status='active',
        approved_by__account_status='active', approved_at__lte=now).exclude(
            evidence_ref__isnull=True).order_by('id'))
    candidates = [row for row in candidates if row.evidence_ref.strip()]
    if len(candidates) != 1:
        raise AuditUnavailable()
    policy = read_retention_policy_shared(candidates[0].id) if lock else candidates[0]
    if (policy.status != 'active' or policy.data_class != 'audit'
            or policy.approved_at is None or policy.approved_at > now
            or policy.approved_by_id is None or not policy.evidence_ref
            or not policy.evidence_ref.strip() or not Principal.objects.filter(
                id=policy.approved_by_id, account_status='active').exists()):
        raise AuditUnavailable()
    return policy


def _uuid(value):
    try:
        return UUID(str(value)) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def request_metadata(request, operation, route):
    principal_id, _, _ = read_session_principal(request)
    actor = Principal.objects.filter(id=_uuid(principal_id)).first() if _uuid(principal_id) else None
    patient_id = _uuid(route.get('patient_id'))
    patient = Patient.objects.filter(id=patient_id).first() if patient_id else None
    resource_keys = ('record_version_id', 'record_id', 'draft_id', 'file_id', 'group_id',
        'application_id', 'relation_id', 'task_id', 'intent_id', 'summary_id', 'alert_id',
        'document_id', 'page_id', 'copy_id', 'recipient_id', 'notice_id', 'version_id',
        'config_id', 'batch_id', 'reminder_id', 'period_id', 'release_id', 'contact_version_id',
        'disposition_id', 'candidate_set_id', 'verification_id', 'source_id', 'source_revision_id',
        'voice_id', 'fact_id', 'run_id', 'dataset_id', 'resource_id', 'approval_id', 'assessment_id',
        'authorization_id', 'contact_id', 'correction_request_id', 'delta_id', 'evaluation_id',
        'field_id', 'grant_id', 'item_id', 'plan_id', 'policy_id', 'principal_id', 'upload_id', 'verification_request_id')
    key = next((key for key in resource_keys if _uuid(route.get(key))), None)
    return dict(actor=actor, patient=patient, action=operation,
        object_type=key.removesuffix('_id') if key else 'api_operation',
        object_id=_uuid(route[key]) if key else None,
        request_id=_uuid(request.headers.get('Idempotency-Key')))


def _record(request, operation, route, response, *, admission=False):
    policy = audit_policy()
    status = response.status_code if response is not None else 200
    outcome = ('allowed' if admission or request.method in ('GET', 'HEAD', 'OPTIONS')
        or operation in READ_OPERATIONS or operation.endswith(('_search', '_view', '_read')) else 'committed')
    if status >= 400:
        outcome = 'denied' if status < 500 else 'failed'
    # HTTP status is sufficient for safe refusal metadata. Free-form provider
    # and validator messages can contain clinical input and are never copied.
    write_audit(**request_metadata(request, operation, route), outcome=outcome,
        reason_code='ADMISSION' if admission else f'HTTP_{status}', retention_policy=policy)


def unavailable_response():
    return Response({'code': 'AUDIT_UNAVAILABLE', 'message': '当前无法保存访问审计，请稍后重试。',
        'retryable': False, 'recovery': 'none', 'field_errors': []}, status=503,
        headers={'Cache-Control': 'no-store, private'})


def audited_operation(view, handled):
    operation = view.__name__
    protected_module = view.__module__.startswith('api.views_') or view.__module__ == 'api.patient_selection'
    if operation in PUBLIC_OPERATIONS or not protected_module:
        return handled

    @wraps(handled)
    def audited(*args, **kwargs):
        request = args[0]
        try:
            if operation in EXTERNAL_OPERATIONS or operation in READ_OPERATIONS or request.method in ('GET', 'HEAD', 'OPTIONS'):
                with transaction.atomic():
                    _record(request, operation, kwargs, None, admission=True)
                response = handled(*args, **kwargs)
                with transaction.atomic():
                    _record(request, operation, kwargs, response)
                return response
            try:
                with transaction.atomic():
                    # Validate before executing any protected operation. A
                    # failure while writing its audit rolls back local effects.
                    # Do not take a retention lock before the operation's
                    # environment/actor locks. The final audit acquires and
                    # rechecks the policy after those business locks.
                    audit_policy(lock=False)
                    response = handled(*args, **kwargs)
                    if response.status_code >= 400:
                        raise RejectedResponse(response)
                    _record(request, operation, kwargs, response)
                return response
            except RejectedResponse as rejected:
                # The business transaction has ended; refused/failed actions
                # remain auditable even when their business changes roll back.
                with transaction.atomic():
                    _record(request, operation, kwargs, rejected.response)
                return rejected.response
        except Exception as exc:
            # Never log exception text, SQL, request data or provider payloads.
            import logging
            logging.getLogger('sjia.audit').error('protected_operation_audit_unavailable',
                extra={'operation': operation, 'error_type': type(exc).__name__})
            return unavailable_response()

    return audited
