from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, authorize_case, get_patient, handle
from archives.models import ArchiveFact, ArchiveFactVersion
from archives.queries import fact_read, fact_source, fact_versions
from authorization.policy import matching_case_grants
from records.models import RecordVersion
from family_content.models import EnvironmentGate
from configurations.models import ConfigVersion, ConfigApproval, ConfigValidation
from configurations.service import compute_checksum


def fact_access(request, action, fact_id):
    principal, auth_epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    fact = ArchiveFact.objects.get(id=fact_id)
    patient = get_patient(fact.patient_id)
    version = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
    field, record_version = fact_source(version, patient)
    original = RecordVersion.objects.get(record_id=record_version.record_id, patient=patient, version=1)
    decision = authorize_case(request, principal, patient, action, field_codes=[field.field_code],
        record_id=record_version.record_id, author_id=original.author_id, require_relation=True)
    patient.refresh_from_db()
    fact = ArchiveFact.objects.select_for_update().get(id=fact.id, patient_id=patient.id)
    version = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
    field, record_version = fact_source(version, patient)
    original = RecordVersion.objects.get(record_id=record_version.record_id, patient=patient, version=1)
    decision = authorize_case(request, principal, patient, action, field_codes=[field.field_code],
        record_id=record_version.record_id, author_id=original.author_id, require_relation=True)
    return principal, auth_epoch, patient, fact, decision


def fact_history_limit(request, *, query_code='FACT_QUERY_INVALID'):
    if set(request.query_params) - {'cursor', 'limit'} or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise ApiError(query_code, 422)
    gate = EnvironmentGate.objects.select_related('runtime_config').get(environment=settings.SJIA_ENVIRONMENT)
    config = ConfigVersion.objects.select_for_update().get(id=gate.runtime_config_id) if gate.runtime_config_id else None
    now = timezone.now()
    if (config is None or config.status != 'enabled' or config.kind != 'provider' or config.schema_version != 'backend-runtime-1'
        or compute_checksum(config.payload) != config.checksum
        or not ConfigApproval.objects.select_for_update().filter(config=config, revoked_at__isnull=True)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now)).exists()
        or not ConfigValidation.objects.select_for_update().filter(config=config, environment_id=settings.SJIA_ENVIRONMENT,
            config_checksum=config.checksum, outcome='pass', revoked_at__isnull=True).exists()):
        raise ApiError('RUNTIME_CONFIGURATION_PENDING', 503)
    api = config.payload.get('api')
    maximum = api.get('max_page_size') if isinstance(api, dict) else None
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
        raise ApiError('RUNTIME_CONFIGURATION_PENDING', 503)
    raw = request.query_params.get('limit')
    if raw is None:
        return maximum, config
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or str(int(raw)) != raw or not 1 <= int(raw) <= maximum:
        raise ApiError(query_code, 422)
    return int(raw), config


def fact_actions(principal, patient, fact):
    version = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
    field, record = fact_source(version, patient)
    initial = RecordVersion.objects.get(record_id=record.record_id, patient=patient, version=1)
    return [action for action in ('correct_record', 'read_history') if any(grant.purpose == 'family_observation'
        and grant.evidence_ref.strip() for grant in matching_case_grants(principal, patient, action, timezone.now(),
            field_codes=[field.field_code], record_id=record.record_id, author_id=initial.author_id, require_relation=True))]


@api_view(['GET'])
@handle
@transaction.atomic
def archive_fact_read(request, fact_id):
    principal, _, patient, fact, _ = fact_access(request, 'read_raw', fact_id)
    if request.query_params:
        raise ApiError('FACT_QUERY_INVALID', 422)
    return Response(fact_read(fact=fact, patient=patient, allowed_actions=fact_actions(principal, patient, fact)))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_fact_history(request, fact_id):
    principal, auth_epoch, patient, fact, decision = fact_access(request, 'read_history', fact_id)
    limit, config = fact_history_limit(request)
    cursor = request.query_params.get('cursor')
    if cursor is not None and (not cursor or len(cursor) > 65536):
        raise ApiError('FACT_QUERY_INVALID', 422)
    scope = {'principal_id': str(principal.id), 'auth_epoch': str(auth_epoch), 'grant_id': str(decision.grant.id),
        'field_codes': sorted(decision.grant.field_codes), 'runtime_id': str(config.id), 'runtime_checksum': config.checksum}
    page = fact_versions(fact=fact, patient=patient, scope=scope, limit=limit,
        expires_at=request.session.get_expiry_date(), cursor=cursor)
    for item in page['items']:
        version = ArchiveFactVersion.objects.get(id=item['version_id'], fact_id=fact.id, patient_id=patient.id)
        field, record_version = fact_source(version, patient)
        original = RecordVersion.objects.get(record_id=record_version.record_id, patient=patient, version=1)
        authorize_case(request, principal, patient, 'read_history', field_codes=[field.field_code],
            record_id=record_version.record_id, author_id=original.author_id, require_relation=True)
    return Response(page)
