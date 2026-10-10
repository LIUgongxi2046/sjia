import json
from typing import Literal, get_args

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_service, handle
from api.views_assessments import query_payload
from api.views_configs import configuration_view
from api.views_releases import release_view
from assessment.provenance import entity_reference
from configurations.evidence import deployed_artifact_checksum
from configurations.lifecycle import configuration_scope
from configurations.models import ConfigApproval, ConfigVersion
from configurations.schemas import SCHEMAS, validate_payload
from configurations.service import compute_checksum
from contacts.models import ContactVersion
from family_content.models import ReleaseBundle, ReleaseComponent
from family_content.qualification import component_qualification_reason, config_qualification_reason, contact_qualification_reason, publication_policy_reason, release_qualification_reason
from requests.controlled import controlled_identity
from sources.models import SourceRevision


PublicationAction = Literal['manage_configs', 'approve_config', 'register_validation', 'publish_config', 'manage_contacts']


class PublicationQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    collection: Literal['release', 'config', 'contact', 'source']
    action: PublicationAction
    cursor: str | None = Field(default=None, min_length=1, max_length=65536)


class PublicationReadQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: PublicationAction


class PublicationCursor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    snapshot_version: str = Field(min_length=1)
    offset: int = Field(ge=0)
    expires_at: float


def contact_management_view(contact):
    return {'id': str(contact.id), 'version': str(contact.version), 'status': contact.status,
        'owner_id': str(contact.owner_id), 'verified_at': contact.verified_at.isoformat() if contact.verified_at else None,
        'updated_at': contact.updated_at.isoformat(), 'contact': {name: str(getattr(contact, name)) for name in
            ('contact_id', 'contact_name', 'purpose', 'channel', 'channel_value', 'source_revision_id', 'approval_config_id')}
            | {'valid_until': contact.valid_until.isoformat()}}


def publication_role(request, collection, action):
    allowed = {'release': {'publish_config'}, 'config': {'manage_configs', 'approve_config', 'register_validation', 'publish_config', 'manage_contacts'},
        'contact': {'publish_config', 'manage_contacts'}, 'source': set(get_args(PublicationAction))}
    actor, gate = controlled_identity(request)
    if action not in allowed[collection]:
        raise ApiError('PUBLICATION_ACTION_DENIED', 403)
    role = authorize_service(request, actor, action).service_grant
    return actor, gate, role


def directory_rows(collection, actor, gate, action):
    if collection == 'release':
        return list(ReleaseBundle.objects.filter(environment=gate.environment).order_by('-version', '-id'))
    if collection == 'config':
        # 病例用途配置通过准确原件范围单独读取，目录不暴露其身份。
        rows = ConfigVersion.objects.exclude(schema_version__in=['purpose-processing-2', 'purpose-processing-text-1']).filter(
            Q(payload__environment=gate.environment) | ~Q(payload__has_key='environment'))
        if action == 'manage_contacts':
            rows = rows.filter(kind='content', schema_version='contact-1', payload__owner_id=str(actor.id))
        return list(rows.order_by('config_key', '-version', 'id'))
    if collection == 'contact':
        rows = ContactVersion.objects.exclude(id__in=ReleaseBundle.objects.exclude(environment=gate.environment).values('contact_version_id'))
        rows = rows.filter(Q(approval_config__payload__environment=gate.environment) | ~Q(approval_config__payload__has_key='environment'))
        if action == 'manage_contacts':
            rows = rows.filter(owner=actor)
        return list(rows.order_by('contact_id', '-version', 'id'))
    return list(SourceRevision.objects.filter(allowed_use__overlap=[
        'clinical_policy', 'configuration_approval', 'configuration_validation']).order_by('-captured_at', 'id'))


def directory_item(collection, row):
    if collection == 'config':
        return {'id': str(row.id), 'title': row.config_key, 'version': str(row.version), 'status': row.status,
            'kind': row.kind, 'schema_version': row.schema_version, 'checksum': row.checksum}
    if collection == 'release':
        return {'id': str(row.id), 'title': row.environment, 'version': str(row.version), 'status': row.status,
            'kind': None, 'schema_version': None, 'checksum': None}
    if collection == 'contact':
        return {'id': str(row.id), 'title': row.contact_name, 'version': str(row.version), 'status': row.status,
            'kind': None, 'schema_version': None, 'checksum': None}
    return {'id': str(row.id), 'title': str(row.id), 'version': None, 'status': row.status,
        'kind': None, 'schema_version': None, 'checksum': row.checksum}


@api_view(['GET'])
@handle
@transaction.atomic
def publication_directory_read(request):
    controlled_identity(request)
    query = query_payload(request, PublicationQuery)
    actor, gate, role = publication_role(request, query['collection'], query['action'])
    runtime = ConfigVersion.objects.filter(id=gate.runtime_config_id, kind='provider', schema_version='backend-runtime-1').first()
    value = {'environment': gate.environment, 'gate_version': str(gate.version), 'publication_epoch': str(gate.publication_epoch),
        'active_release_id': str(gate.active_release_id) if gate.active_release_id else None, 'collection': query['collection'],
        'schemas': [{'kind': kind, 'schema_version': schema} for kind, schema in sorted(SCHEMAS)],
        'items': [], 'next_cursor': None, 'snapshot_version': compute_checksum(entity_reference(gate)),
        'capability_state': 'unavailable', 'capability_reason': 'PUBLICATION_RUNTIME_UNAVAILABLE'}
    if runtime is None:
        if query['cursor'] is not None:
            raise ApiError('PUBLICATION_DIRECTORY_CHANGED', 409)
        return Response(value)
    validate_payload(runtime.kind, runtime.schema_version, runtime.payload)
    if runtime.payload['environment'] != gate.environment or runtime.checksum != compute_checksum(runtime.payload):
        raise ApiError('PUBLICATION_RUNTIME_CHANGED', 409)
    rows = directory_rows(query['collection'], actor, gate, query['action'])
    bound = {'environment': gate.environment, 'gate': entity_reference(gate), 'actor': entity_reference(actor),
        'role': entity_reference(role), 'collection': query['collection'], 'runtime': entity_reference(runtime),
        'rows': [entity_reference(row) for row in rows]}
    snapshot_version, offset = compute_checksum(bound), 0
    expiry, now = request.session.get_expiry_date().timestamp(), timezone.now().timestamp()
    if expiry <= now:
        raise ApiError('AUTH_REQUIRED', 401)
    if query['cursor'] is not None:
        cursor = PublicationCursor.model_validate_json(json.dumps(signing.loads(query['cursor'], salt='publication-directory')))
        if cursor.snapshot_version != snapshot_version or cursor.offset >= len(rows) or not now < cursor.expires_at <= expiry:
            raise ApiError('PUBLICATION_DIRECTORY_CHANGED', 409)
        offset = cursor.offset
    selected = rows[offset:offset + runtime.payload['api']['max_page_size']]
    following = offset + len(selected)
    value.update({'items': [directory_item(query['collection'], row) for row in selected], 'snapshot_version': snapshot_version,
        'capability_state': 'available', 'capability_reason': None, 'next_cursor': signing.dumps(
            {'snapshot_version': snapshot_version, 'offset': following, 'expires_at': expiry}, salt='publication-directory') if following < len(rows) else None})
    if len(json.dumps(value, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('PUBLICATION_DIRECTORY_LIMIT_EXCEEDED', 422)
    return Response(value)


@api_view(['GET'])
@handle
@transaction.atomic
def publication_config_read(request, config_id):
    controlled_identity(request)
    query = query_payload(request, PublicationReadQuery)
    actor, gate, _ = publication_role(request, 'config', query['action'])
    config = ConfigVersion.objects.select_for_update().get(id=config_id)
    if query['action'] == 'manage_contacts' and (config.kind != 'content' or config.schema_version != 'contact-1'
            or config.payload.get('owner_id') != str(actor.id)):
        raise ApiError('PUBLICATION_CONTACT_SCOPE_DENIED', 403)
    if 'environment' in config.payload and config.payload['environment'] != gate.environment:
        raise ApiError('RESOURCE_NOT_FOUND', 404)
    configuration_scope(config, gate, actor)
    return Response({'resource': configuration_view(config), 'artifact_sha256': deployed_artifact_checksum(config),
        'approvals': [{'id': str(row.id), 'approved_at': row.approved_at.isoformat(),
            'revoked_at': row.revoked_at.isoformat() if row.revoked_at else None,
            'scope': row.effective_scope} for row in config.approvals.all().order_by('-approved_at', '-id')]})


@api_view(['GET'])
@handle
@transaction.atomic
def publication_approval_read(request, approval_id):
    actor, gate, _ = publication_role(request, 'config', 'approve_config')
    approval = ConfigApproval.objects.select_for_update().get(id=approval_id)
    config = ConfigVersion.objects.select_for_update().get(id=approval.config_id)
    if approval.effective_scope.get('environment') != gate.environment:
        raise ApiError('RESOURCE_NOT_FOUND', 404)
    configuration_scope(config, gate, actor)
    return Response({'resource': configuration_view(config), 'artifact_sha256': deployed_artifact_checksum(config),
        'approvals': [{'id': str(row.id), 'approved_at': row.approved_at.isoformat(),
            'revoked_at': row.revoked_at.isoformat() if row.revoked_at else None,
            'scope': row.effective_scope} for row in config.approvals.all().order_by('-approved_at', '-id')]})


@api_view(['GET'])
@handle
@transaction.atomic
def publication_contact_read(request, contact_id):
    controlled_identity(request)
    query = query_payload(request, PublicationReadQuery)
    actor, gate, _ = publication_role(request, 'contact', query['action'])
    contact = ContactVersion.objects.select_for_update().get(id=contact_id)
    if query['action'] == 'manage_contacts' and contact.owner_id != actor.id:
        raise ApiError('PUBLICATION_CONTACT_SCOPE_DENIED', 403)
    if not any(row.id == contact.id for row in directory_rows('contact', actor, gate, query['action'])):
        raise ApiError('RESOURCE_NOT_FOUND', 404)
    return Response(contact_management_view(contact))


def publication_release_view(release, gate):
    now = timezone.now()
    components = list(ReleaseComponent.objects.filter(release=release).select_related('config'))
    reasons = {item.component_kind: config_qualification_reason(item.config, gate.environment, now) for item in components}
    policies = {item.component_kind: item.config for item in components}
    complete = component_qualification_reason(components)
    validation_reason = config_qualification_reason(release.validation_config, gate.environment, now)
    contact_reason = contact_qualification_reason(release.contact_version, gate.environment, now)
    qualification = release_qualification_reason(release, now, require_active=release.status == 'enabled')
    binding_reason = (publication_policy_reason(release, components, gate, now)
        if complete is None and validation_reason is None and not any(reasons.values()) else None)
    def condition(key, kinds, other=None):
        reason = complete or other or next((reasons[kind] for kind in kinds if reasons[kind]), None)
        return {'key': key, 'state': 'blocked' if reason else 'available', 'reason': reason}
    readiness = [condition('verification', ['verification', 'mapping', 'visibility']),
        condition('rules', ['rule', 'window']), condition('validation', [], validation_reason or binding_reason),
        condition('content', ['content'], binding_reason), condition('contact', [], contact_reason),
        condition('notification', ['notification'])]
    # 受控发布不授予任何家庭接收关系，逐人许可仍由发送入口核验。
    content = policies.get('content')
    return {'resource': release_view(release), 'gate_version': str(gate.version), 'publication_epoch': str(gate.publication_epoch),
        'active_release_id': str(gate.active_release_id) if gate.active_release_id else None,
        'readiness': readiness, 'qualification_reason': qualification,
        'may_enable': release.status == 'approved' and qualification is None and gate.active_release_id is None,
        'approved_text': content.payload.get('approved_text') if content and content.schema_version == 'content-1' else None,
        'recipient_permission_state': 'individual_check_required'}


@api_view(['GET'])
@handle
@transaction.atomic
def publication_release_read(request, release_id):
    _, gate, _ = publication_role(request, 'release', 'publish_config')
    if request.query_params:
        raise ApiError('PUBLICATION_QUERY_INVALID', 422)
    release = ReleaseBundle.objects.select_for_update().get(id=release_id, environment=gate.environment)
    return Response(publication_release_view(release, gate))
