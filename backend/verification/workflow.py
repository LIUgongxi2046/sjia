from uuid import UUID
import json

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import ROOT, REGISTRY, SCHEMAS
from assessment.configuration_proofs import configuration_proof
from assessment.policies import QualificationPolicy
from assessment.provenance import entity_reference
from assessment.qualified_facts import lock_case_permissions, qualified_configuration
from assessment.service import lock_actor
from authorization.models import ServiceRoleGrant
from authorization.policy import check_principal_session, check_service_grant, matching_case_grants
from configurations.evidence import inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from files.purpose_dependencies import invalidate_resource
from files.originals import original_source_view
from files.purpose import checked_bytes, lock_file_access, lock_original, lock_use_authorization
from files.models import FileObject
from identity.models import Principal
from records.models import Record, RecordAttachment, RecordField, RecordVersion
from requests.models import WriteIntent
from requests.service import NORMALIZATION_VERSION, compute_digest
from audit.models import AuditEvent
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from verification.models import FieldVerificationState, Verification, VerificationField, VerificationRequest
from verification.service import VerificationRejected


PROTOCOL = SCHEMAS[(ROOT / 'backend-design/protocol.schema.json').as_uri()]
INPUTS = {name: Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, registry=REGISTRY,
    format_checker=FormatChecker()) for name in ('VerificationRequestInput', 'VerificationInput')}
V2_PROTOCOL = SCHEMAS[(ROOT / 'backend-design/v2-protocol.schema.json').as_uri()]
QUERIES = {name: Draft202012Validator({**V2_PROTOCOL, '$ref': f'#/$defs/{name}'}, registry=REGISTRY,
    format_checker=FormatChecker()) for name in ('VerificationCreationQuery', 'VerificationOriginalQuery')}


def verification_query(name, parameters):
    result = dict(parameters.items())
    if any(len(parameters.getlist(key)) != 1 for key in parameters) or not QUERIES[name].is_valid(result):
        raise VerificationRejected('VERIFICATION_QUERY_INVALID')
    return result


def verification_payload(operation, payload):
    if operation == 'verification_v2_revoke':
        if (not isinstance(payload, dict) or set(payload) != {'expected_verification_sha256', 'reason'}
                or not isinstance(payload['reason'], str) or not payload['reason'].strip()
                or not isinstance(payload['expected_verification_sha256'], str)
                or len(payload['expected_verification_sha256']) != 64
                or any(value not in '0123456789abcdef' for value in payload['expected_verification_sha256'])):
            raise VerificationRejected('VERIFICATION_INPUT_INVALID')
        return payload
    name = {'verification_v2_request_create': 'VerificationRequestInput', 'verification_v2_submit': 'VerificationInput'}[operation]
    if not INPUTS[name].is_valid(payload) or not payload['purpose'].strip():
        raise VerificationRejected('VERIFICATION_INPUT_INVALID')
    identities = payload['field_ids'] if name == 'VerificationRequestInput' else [item['field_id'] for item in payload['fields']]
    if len(set(identities)) != len(identities):
        raise VerificationRejected('VERIFICATION_FIELDS_DUPLICATE')
    if name == 'VerificationInput' and (not payload['evidence_ref'].strip()
            or any(int(item['expected_state_version']) > 9223372036854775807
                or item['reason'] is not None and not item['reason'].strip()
                or item['result'] != 'consistent' and item['reason'] is None for item in payload['fields'])):
        raise VerificationRejected('VERIFICATION_INPUT_INVALID')
    return payload


def source_authorization(version, purpose, environment):
    row = DataUseAuthorization.objects.select_for_update().get(id=version.use_authorization_id, patient_id=version.patient_id)
    revision = SourceRevision.objects.select_for_update().get(id=row.source_revision_id)
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    approver = Principal.objects.select_for_update().get(id=row.approved_by_id)
    now = timezone.now()
    if (row.purpose != purpose or row.environment != environment or row.revoked_at is not None
            or row.approved_at > now or row.valid_until is not None and row.valid_until <= now
            or not row.evidence_ref.strip() or version.source_revision_id is not None and row.source_revision_id != version.source_revision_id
            or revision.status != 'approved' or source.status != 'approved' or not revision.rights_evidence_ref
            or not revision.rights_evidence_ref.strip()
            or purpose not in revision.allowed_use or approver.account_status != 'active'):
        raise VerificationRejected('VERIFICATION_SOURCE_SCOPE_DENIED')
    return row


def version_scope(version_id, actor, epoch, environment, purpose, field_ids, *, action='verify_field', current=True):
    initial = RecordVersion.objects.get(id=version_id)
    gate, patient, actor, role, runtime = lock_actor(initial.patient_id, actor, epoch, environment, action)
    version = RecordVersion.objects.select_for_update().get(id=initial.id, patient_id=patient.id)
    record = Record.objects.select_for_update().get(id=version.record_id, patient_id=patient.id)
    if record.status != 'active' or current and record.current_version != version.version:
        raise VerificationRejected('VERIFICATION_RECORD_CHANGED')
    fields = list(RecordField.objects.select_for_update().filter(record_version_id=version.id,
        patient_id=patient.id, id__in=field_ids).order_by('ordinal', 'id'))
    if not fields or {str(row.id) for row in fields} != {str(value) for value in field_ids}:
        raise VerificationRejected('VERIFICATION_FIELDS_MISMATCH')
    grants = []
    for field in fields:
        grants.extend(lock_case_permissions(actor, patient, action, purpose, timezone.now(),
            record_id=record.id, author_id=version.author_id, field_codes=[field.field_code]))
    authorization = source_authorization(version, purpose, environment)
    retention = version.retention_policy
    if (retention.status != 'active' or retention.approved_at is None or retention.approved_by_id is None
            or not retention.evidence_ref.strip()):
        raise VerificationRejected('VERIFICATION_RETENTION_UNAVAILABLE')
    context = {'environment': environment, 'actor_id': str(actor.id), 'auth_epoch': str(actor.auth_epoch),
        'patient_id': str(patient.id), 'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'publication_epoch': str(gate.publication_epoch), 'record': entity_reference(record), 'version': entity_reference(version),
        'fields': [entity_reference(row) for row in fields], 'role': entity_reference(role),
        'grants': [entity_reference(value) for value in sorted({row.id: row for row in grants}.values(), key=lambda row: str(row.id))],
        'authorization': entity_reference(authorization), 'retention': entity_reference(retention),
        'source_revision': entity_reference(SourceRevision.objects.get(id=authorization.source_revision_id)),
        'source_registry': entity_reference(SourceRegistry.objects.get(id=authorization.source_revision.source_id)),
        'runtime': configuration_proof(runtime, environment, timezone.now(), 'current')}
    if len(json.dumps(context, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise VerificationRejected('VERIFICATION_CONTEXT_LIMIT_EXCEEDED')
    return patient, actor, version, fields, retention, runtime, context


def request_identity(row):
    intent = WriteIntent.objects.select_for_update().get(operation_id='verification_v2_request_create', status='committed',
        write_request__resource_type='verification_request', write_request__resource_id=row.id,
        write_request__status='committed', actor_id=row.requested_by_id, patient_id=row.patient_id)
    payload = verification_payload('verification_v2_request_create', intent.recovery_payload)
    saved = intent.write_request
    if (payload['record_version_id'] != str(row.record_version_id) or payload['purpose'] != row.purpose
            or payload['assigned_to'] != str(row.assigned_to_id)
            or intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.request_id != intent.request_id or saved.action != intent.operation_id
            or saved.payload_sha256 != intent.payload_sha256 or saved.completed_at != intent.completed_at
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=row.patient_id,
                action=intent.operation_id, object_type='verification_request', object_id=row.id, object_version='1',
                request_id=intent.request_id, outcome='committed', occurred_at=intent.completed_at).exists()):
        raise VerificationRejected('VERIFICATION_REQUEST_IDENTITY_CHANGED')
    return payload


def states_for(fields, purpose):
    return {row.field_id: row for row in FieldVerificationState.objects.select_for_update().filter(
        field_id__in=[field.id for field in fields], purpose=purpose).order_by('field_id')}


@transaction.atomic
def prepare_verification_scope(operation, target_id, payload, actor, epoch, environment):
    verification_payload(operation, payload)
    if operation == 'verification_v2_revoke':
        proof = Verification.objects.get(id=target_id)
        fields = list(VerificationField.objects.filter(verification_id=proof.id).values_list('field_id', flat=True))
        scoped = version_scope(proof.record_version_id, actor, epoch, environment, proof.purpose, fields,
            action='revoke_verification', current=False)
        proof = Verification.objects.select_for_update().get(id=proof.id, patient_id=scoped[0].id)
        if proof.revoked_at is not None or entity_reference(proof)['sha256'] != payload['expected_verification_sha256']:
            raise VerificationRejected('VERIFICATION_PROOF_CHANGED')
        context = {**scoped[-1], 'proof': entity_reference(proof)}
        return (*scoped[:-1], context)
    field_ids = payload['field_ids'] if operation == 'verification_v2_request_create' else [item['field_id'] for item in payload['fields']]
    scoped = version_scope(payload['record_version_id'], actor, epoch, environment, payload['purpose'], field_ids)
    patient, actor, version, fields, retention, runtime, context = scoped
    states = states_for(fields, payload['purpose'])
    context['states'] = [entity_reference(row) for row in sorted(states.values(), key=lambda row: str(row.field_id))]
    if operation == 'verification_v2_request_create':
        assigned = Principal.objects.select_for_update().get(id=payload['assigned_to'])
        check_principal_session(assigned, assigned.auth_epoch)
        assigned_role = check_service_grant(assigned, 'verify_field', environment, timezone.now())
        assigned_grants = []
        for field in fields:
            assigned_grants.extend(lock_case_permissions(assigned, patient, 'verify_field', payload['purpose'], timezone.now(),
                record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code]))
        context['assigned_role'] = entity_reference(assigned_role)
        context['assigned_principal'] = entity_reference(assigned)
        context['assigned_grants'] = [entity_reference(row) for row in sorted(
            {row.id: row for row in assigned_grants}.values(), key=lambda row: str(row.id))]
    else:
        if len(states) != len(fields):
            raise VerificationRejected('VERIFICATION_REQUEST_REQUIRED')
        requested = {row.request_id for row in states.values()}
        if len(requested) != 1 or None in requested:
            raise VerificationRejected('VERIFICATION_REQUEST_MISMATCH')
        request = VerificationRequest.objects.select_for_update().get(id=requested.pop(), patient_id=patient.id,
            record_version_id=version.id, purpose=payload['purpose'], assigned_to=actor)
        identity = request_identity(request)
        if request.status not in ('pending', 'in_review') or not set(field_ids) <= set(identity['field_ids']):
            raise VerificationRejected('VERIFICATION_REQUEST_CHANGED')
        for item in payload['fields']:
            state = states[UUID(item['field_id'])]
            if str(state.version) != item['expected_state_version'] or state.status != 'pending':
                raise VerificationRejected('VERIFICATION_STATE_CHANGED')
        policy = qualified_configuration(payload['policy_id'], kind='verification', schema_version='qualification-policy-2',
            environment=environment, known_at=timezone.now(), mode='current')
        definition = QualificationPolicy.model_validate(policy.payload)
        if definition.purpose != payload['purpose'] or version.origin_type not in definition.allowed_origin_types:
            raise VerificationRejected('VERIFICATION_POLICY_SCOPE_DENIED')
        evidence, _, reason = inspect_approved_evidence(payload['evidence_ref'], purpose=definition.verification_evidence_purpose, lock=True)
        if reason:
            raise VerificationRejected(reason)
        if str(evidence.id) != payload['evidence_source_id'] or payload['purpose'] not in evidence.allowed_use:
            raise VerificationRejected('VERIFICATION_EVIDENCE_CHANGED')
        evidence_uses = list(DataUseAuthorization.objects.select_for_update().filter(patient=patient,
            source_revision_id=evidence.id, purpose=payload['purpose'], environment=environment,
            revoked_at__isnull=True, approved_at__lte=timezone.now(), approved_by__account_status='active').filter(
                Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())).order_by('id'))
        evidence_uses = [row for row in evidence_uses if row.evidence_ref.strip()]
        if not evidence_uses:
            raise VerificationRejected('VERIFICATION_EVIDENCE_SCOPE_DENIED')
        context.update(request=entity_reference(request), policy=configuration_proof(policy, environment, timezone.now(), 'current'),
            evidence=entity_reference(evidence), evidence_uses=[entity_reference(row) for row in evidence_uses])
    if len(json.dumps({'payload': payload, 'context': context}, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise VerificationRejected('VERIFICATION_CONTEXT_LIMIT_EXCEEDED')
    return patient, actor, version, fields, retention, runtime, context


@transaction.atomic
def apply_verification(operation, target_id, payload, actor, epoch, environment):
    patient, actor, version, fields, _, _, context = prepare_verification_scope(operation, target_id, payload, actor, epoch, environment)
    now = timezone.now()
    if operation == 'verification_v2_revoke':
        proof = Verification.objects.select_for_update().get(id=target_id, patient=patient)
        proof.revoked_at, proof.revoked_by, proof.revocation_reason = now, actor, payload['reason']
        proof.save(update_fields=['revoked_at', 'revoked_by', 'revocation_reason'])
        FieldVerificationState.objects.filter(current_verification_id=proof.id).update(status='revoked',
            current_verification_id=None, version=F('version') + 1)
        invalidate_resource('verification', proof.id)
        result = proof
    elif operation == 'verification_v2_request_create':
        result = VerificationRequest.objects.create(patient_id=patient.id, record_version_id=version.id, requested_by=actor,
            assigned_to_id=payload['assigned_to'], purpose=payload['purpose'], status='pending', created_at=now)
        states = states_for(fields, payload['purpose'])
        old_requests = {row.request_id for row in states.values() if row.request_id is not None}
        VerificationRequest.objects.filter(id__in=old_requests, status__in=['pending', 'in_review']).update(status='superseded')
        for field in fields:
            state = states.get(field.id)
            if state is None:
                FieldVerificationState.objects.create(field_id=field.id, record_version_id=version.id, patient_id=patient.id,
                    purpose=payload['purpose'], version=1, request_id=result.id, current_verification_id=None, status='pending')
            else:
                state.request_id, state.current_verification_id, state.status = result.id, None, 'pending'
                state.version += 1
                state.save(update_fields=['request_id', 'current_verification_id', 'status', 'version'])
    else:
        result = Verification.objects.create(patient=patient, record_version_id=version.id, verifier=actor, policy_id=payload['policy_id'],
            purpose=payload['purpose'], evidence_source_id=payload['evidence_source_id'], evidence_ref=payload['evidence_ref'], verified_at=now)
        states = states_for(fields, payload['purpose'])
        for item in payload['fields']:
            field_id = UUID(item['field_id'])
            VerificationField.objects.create(verification_id=result.id, record_version_id=version.id, patient_id=patient.id,
                field_id=field_id, result=item['result'], reason=item['reason'])
            state = states[field_id]
            state.current_verification_id = result.id
            state.status = 'verified' if item['result'] == 'consistent' else 'needs_correction'
            state.version += 1
            state.save(update_fields=['current_verification_id', 'status', 'version'])
        request = VerificationRequest.objects.select_for_update().get(id=context['request']['id'])
        pending = FieldVerificationState.objects.filter(request_id=request.id, status='pending').exists()
        request.status, request.completed_at = ('in_review', None) if pending else ('completed', now)
        request.save(update_fields=['status', 'completed_at'])
    patient.verification_epoch += 1
    patient.save(update_fields=['verification_epoch'])
    from alerting.triggers import emit_verification_change

    emit_verification_change(patient, result, operation, environment, result.purpose)
    return result


def field_view(field, state):
    return {'field_id': str(field.id), 'field_code': field.field_code, 'raw_text': field.raw_text,
        'raw_numeric': format(field.raw_numeric, 'f') if field.raw_numeric is not None else None,
        'original_name': field.original_name, 'value_type': field.value_type,
        'categorical_value': field.categorical_value, 'qualifier': field.qualifier, 'method': field.method,
        'reference_interval': field.reference_interval, 'report_flag': field.report_flag,
        'raw_unit': field.raw_unit, 'value_state': field.value_state,
        'medical_time_id': str(field.medical_time_id) if field.medical_time_id else None,
        'source_anchor_id': str(field.anchor_id) if field.anchor_id else None,
        'state_version': str(state.version) if state is not None else None,
        'status': state.status if state is not None else 'unverified'}


def permitted_fields(actor, patient, version, purpose, fields):
    return [field for field in fields if any(grant.purpose == purpose and grant.evidence_ref.strip()
        for grant in matching_case_grants(actor, patient, 'verify_field', timezone.now(),
            record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code]))]


@transaction.atomic
def verification_creation_scope(version_id, purpose, actor, epoch, environment):
    version = RecordVersion.objects.get(id=version_id)
    _, patient, actor, _, _ = lock_actor(version.patient_id, actor, epoch, environment, 'verify_field')
    fields = list(RecordField.objects.filter(patient=patient, record_version_id=version.id).order_by('ordinal', 'id'))
    fields = permitted_fields(actor, patient, version, purpose, fields)
    patient, actor, version, fields, _, runtime, _ = version_scope(version.id, actor, epoch, environment,
        purpose, [field.id for field in fields])
    states = states_for(fields, purpose)
    assignees = []
    principals = Principal.objects.filter(account_status='active', id__in=ServiceRoleGrant.objects.filter(
        environment=environment, action='verify_field').values('principal_id')).order_by('id')
    for principal in principals:
        role = ServiceRoleGrant.objects.filter(principal=principal, environment=environment,
            action='verify_field').order_by('-valid_from', '-id').first()
        now = timezone.now()
        if (role.valid_from > now or role.revoked_at is not None and role.revoked_at <= now
                or role.valid_until is not None and role.valid_until <= now or not role.evidence_ref.strip()):
            continue
        allowed = permitted_fields(principal, patient, version, purpose, fields)
        if allowed:
            assignees.append({'principal_id': str(principal.id), 'field_ids': [str(field.id) for field in allowed]})
    result = {'schema_version': 'verification-creation-scope-2', 'patient_id': str(patient.id),
        'record_version_id': str(version.id), 'record_id': str(version.record_id), 'purpose': purpose,
        'fields': [field_view(field, states.get(field.id)) for field in fields], 'assignees': assignees}
    if len(json.dumps(result, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise VerificationRejected('VERIFICATION_CONTEXT_LIMIT_EXCEEDED')
    return result


@transaction.atomic
def read_verification_resource(kind, resource_id, actor, epoch, environment, *, action='verify_field'):
    row = (VerificationRequest if kind == 'verification_request' else Verification).objects.get(id=resource_id)
    if kind == 'verification_request':
        identity = request_identity(row)
        field_ids = identity['field_ids']
    else:
        field_ids = list(VerificationField.objects.filter(verification_id=row.id).values_list('field_id', flat=True))
    patient, actor, version, fields, _, _, _ = version_scope(row.record_version_id, actor, epoch, environment,
        row.purpose, field_ids, current=False, action=action)
    row = (VerificationRequest if kind == 'verification_request' else Verification).objects.select_for_update().get(id=row.id)
    states = states_for(fields, row.purpose)
    result = {'schema_version': 'verification-view-2', 'resource_type': kind, 'resource_id': str(row.id),
        'patient_id': str(patient.id), 'record_version_id': str(version.id), 'record_id': str(version.record_id),
        'purpose': row.purpose, 'verification_epoch': str(patient.verification_epoch),
        'reference_sha256': entity_reference(row)['sha256'], 'fields': [field_view(field, states.get(field.id)) for field in fields]}
    if kind == 'verification_request':
        result.update(status=row.status, assigned_to=str(row.assigned_to_id), created_at=row.created_at.isoformat(),
            completed_at=row.completed_at.isoformat() if row.completed_at else None)
    else:
        result.update(verifier_id=str(row.verifier_id), policy_id=str(row.policy_id), evidence_source_id=str(row.evidence_source_id),
            evidence_ref=row.evidence_ref, verified_at=row.verified_at.isoformat(), revoked_at=row.revoked_at.isoformat() if row.revoked_at else None,
            results=[{'field_id': str(item.field_id), 'result': item.result, 'reason': item.reason,
                'current_for_purpose': states[item.field_id].current_verification_id == row.id}
                for item in VerificationField.objects.filter(verification_id=row.id).order_by('field_id')])
    return result


@transaction.atomic
def verification_options(request_id, actor, epoch, environment):
    request = VerificationRequest.objects.get(id=request_id)
    identity = request_identity(request)
    patient, actor, version, fields, _, runtime, _ = version_scope(request.record_version_id, actor, epoch, environment,
        request.purpose, identity['field_ids'])
    request = VerificationRequest.objects.select_for_update().get(id=request.id)
    if request.status not in ('pending', 'in_review') or request.assigned_to_id != actor.id:
        raise VerificationRejected('VERIFICATION_REQUEST_SCOPE_DENIED')
    policies = []
    definitions = {}
    for row in ConfigVersion.objects.filter(kind='verification', schema_version='qualification-policy-2').order_by('config_key', 'version'):
        if configuration_qualification_reason(row, environment, audience='support') is not None:
            continue
        definition = QualificationPolicy.model_validate(row.payload)
        if definition.purpose != request.purpose or version.origin_type not in definition.allowed_origin_types:
            continue
        row = qualified_configuration(row.id, kind='verification', schema_version='qualification-policy-2',
            environment=environment, known_at=timezone.now(), mode='current')
        configuration_proof(row, environment, timezone.now(), 'current')
        definitions[row.id] = definition
        policies.append({'policy_id': str(row.id), 'config_key': row.config_key, 'version': str(row.version),
            'evidence_purpose': definition.verification_evidence_purpose})
    use_ids = DataUseAuthorization.objects.filter(patient=patient, purpose=request.purpose,
        environment=environment, revoked_at__isnull=True, approved_at__lte=timezone.now(), approved_by__account_status='active').filter(
            Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())).exclude(evidence_ref='').values('source_revision_id')
    evidence = []
    for source in SourceRevision.objects.filter(id__in=use_ids, status='approved').order_by('id'):
        reference = f'urn:sjia:source-revision:{source.id}'
        allowed = []
        for policy_id, definition in definitions.items():
            _, _, reason = inspect_approved_evidence(reference, purpose=definition.verification_evidence_purpose, lock=True)
            if reason is None and request.purpose in source.allowed_use:
                allowed.append(str(policy_id))
        if allowed:
            evidence.append({'source_revision_id': str(source.id), 'evidence_ref': reference, 'allowed_policy_ids': allowed})
    result = {'schema_version': 'verification-options-2', 'verification_request_id': str(request.id),
        'patient_id': str(patient.id), 'record_version_id': str(version.id), 'purpose': request.purpose,
        'policies': policies, 'evidence': evidence}
    if len(json.dumps(result, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise VerificationRejected('VERIFICATION_CONTEXT_LIMIT_EXCEEDED')
    return result


def original_scope(kind, resource_id, actor, epoch, environment, file_id=None):
    if kind not in ('verification_request', 'verification'):
        raise VerificationRejected('VERIFICATION_RESOURCE_INVALID')
    resource = read_verification_resource(kind, resource_id, actor, epoch, environment)
    version = RecordVersion.objects.select_for_update().get(id=resource['record_version_id'], patient_id=resource['patient_id'])
    attachments = RecordAttachment.objects.filter(patient_id=version.patient_id, record_version_id=version.id).order_by('ordinal')
    if file_id is not None:
        attachments = attachments.filter(file_id=file_id)
        if not attachments.exists():
            raise VerificationRejected('VERIFICATION_ORIGINAL_SCOPE_DENIED')
    files = []
    for attachment in attachments:
        file = FileObject.objects.select_for_update().get(id=attachment.file_id, patient_id=version.patient_id)
        if file.purpose != 'original':
            raise VerificationRejected('VERIFICATION_ORIGINAL_SCOPE_DENIED')
        _, _, _, group = lock_file_access(file, actor, epoch, purpose=resource['purpose'],
            service_action='verify_field', require_available=False)
        lock_use_authorization(group.use_authorization_id, file, resource['purpose'], environment)
        files.append(file)
    return resource, version, files


@transaction.atomic
def verification_originals(kind, resource_id, actor, epoch, environment):
    resource, version, _ = original_scope(kind, resource_id, actor, epoch, environment)
    result = original_source_view(version, [field['field_code'] for field in resource['fields']])
    for file in result['originals']:
        file['use_authorizations'] = [item for item in file['use_authorizations']
            if item['purpose'] == resource['purpose'] and item['environment'] == environment]
        file['purpose_copies'] = [item for item in file['purpose_copies'] if item['purpose'] == resource['purpose']]
    return result


@transaction.atomic
def verification_original_content(kind, resource_id, file_id, actor, epoch, environment, checksum, object_version):
    _, _, files = original_scope(kind, resource_id, actor, epoch, environment, file_id)
    file = lock_original(files[0].id)
    if file.checksum != checksum or file.object_version != object_version:
        raise VerificationRejected('VERIFICATION_ORIGINAL_CHANGED')
    return file.media_type, checked_bytes(file)
