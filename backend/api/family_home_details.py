from django.db.models import Q
from django.utils import timezone

from alerting.models import AlertEvent, AlertEvaluationInput
from api.family_home import home_projection
from archives.models import ArchiveFact, ArchiveFactVersion
from archives.queries import fact_read
from authorization.models import AccessGrant
from authorization.policy import matching_case_grants
from family_content.alert_detail import alert_content
from files.models import FileObject
from identity.models import PrincipalIdentity
from records.models import Record, RecordAttachment, RecordField, RecordVersion, Report
from records.queries import record_search
from records.service import medical_time_view
from relations.models import Relation


def family_read_permissions(principal, patient):
    now = timezone.now()
    scopes = {}
    candidates = AccessGrant.objects.filter(principal=principal, patient=patient,
        action='read_raw', purpose='family_observation')
    for candidate in candidates:
        for grant in matching_case_grants(principal, patient, 'read_raw', now,
            author_id=principal.id, record_id=candidate.assigned_record_id,
            purpose='family_observation', require_relation=True):
            if grant.field_codes:
                scopes[str(grant.id)] = grant
    return [{'id': str(grant.id), 'field_codes': sorted(grant.field_codes),
        'record_scope': grant.record_scope,
        'assigned_record_id': str(grant.assigned_record_id) if grant.assigned_record_id else None,
        'valid_from': grant.valid_from.isoformat(),
        'valid_until': grant.valid_until.isoformat() if grant.valid_until else None,
        'relation_id': str(grant.relation_id) if grant.relation_id else None}
        for grant in sorted(scopes.values(), key=lambda item: str(item.id))]


def current_readable_codes(principal, patient, record):
    first_version = RecordVersion.objects.filter(patient=patient, record_id=record.id, version=1).first()
    if first_version is None:
        return set()
    return {code for grant in matching_case_grants(principal, patient, 'read_raw', timezone.now(),
        record_id=record.id, author_id=first_version.author_id, purpose='family_observation',
        require_relation=True) for code in grant.field_codes}


def current_read_file_allowed(principal, patient, record):
    first_version = RecordVersion.objects.filter(patient=patient, record_id=record.id, version=1).first()
    if first_version is None:
        return False
    now = timezone.now()
    return any(
        not grant.field_codes and grant.purpose == 'family_observation' and grant.evidence_ref.strip()
        for grant in matching_case_grants(principal, patient, 'read_file', now,
            record_id=record.id, author_id=first_version.author_id, purpose='family_observation',
            require_relation=True)
    )


def current_read_history_allowed(principal, patient, record):
    first_version = RecordVersion.objects.filter(patient=patient, record_id=record.id, version=1).first()
    if first_version is None:
        return False
    return any(matching_case_grants(principal, patient, 'read_history', timezone.now(),
        record_id=record.id, author_id=first_version.author_id, purpose='family_observation',
        require_relation=True))


def author_view(version, principal):
    identity = PrincipalIdentity.objects.filter(principal_id=version.author_id).order_by(
        'provider', 'provider_subject').first()
    role = 'family' if identity is not None and identity.provider in ('self', 'wechat') else 'professional'
    return {'display_id': identity.provider_subject if identity is not None else None,
        'role': role, 'is_self': version.author_id == principal.id}


def report_view(version, patient):
    report = Report.objects.filter(id=version.report_id, patient=patient).first() if version.report_id else None
    if report is None:
        return None
    return {
        'report_id': str(report.id), 'external_ref': report.external_ref,
        'source_organization': report.source_organization, 'report_status': report.report_status,
        'sample_time': medical_time_view(report.sample_time_id, patient),
        'result_time': medical_time_view(report.result_time_id, patient),
        'file_group_id': str(report.file_group_id) if report.file_group_id else None,
        'replaces_report_id': str(report.replaces_report_id) if report.replaces_report_id else None,
    }


def family_record(principal, patient, record, detail):
    version = RecordVersion.objects.get(patient=patient, record_id=record.id, version=record.current_version)
    readable_codes = current_readable_codes(principal, patient, record)
    if not readable_codes or detail is None:
        return None
    facts = []
    verification_states = []
    editable_codes = {code for grant in matching_case_grants(principal, patient, 'write_record',
        timezone.now(), record_id=record.id, author_id=version.author_id,
        purpose='family_observation', require_relation=True) for code in grant.field_codes}
    for source_field in detail['fields']:
        if source_field['field_code'] not in readable_codes:
            continue
        candidates = ArchiveFactVersion.objects.filter(patient_id=patient.id,
            record_version_id=version.id, field_id=source_field['field_id']).order_by('-version')
        fact = None
        fact_version = None
        for candidate in candidates:
            current_fact = ArchiveFact.objects.get(id=candidate.fact_id, patient_id=patient.id)
            if candidate.version == current_fact.current_version:
                fact = current_fact
                fact_version = candidate
                break
        if fact is None or fact_version is None:
            raise RuntimeError(f'当前记录字段缺少有效事实引用: {source_field["field_id"]}')
        materialized = fact_read(fact=fact, patient=patient, version=fact_version)
        facts.append({'field': materialized['field']['field'],
            'verification_status': materialized['field']['verification_status'],
            'verification_purpose': materialized['field']['verification_purpose']})
        verification_states.append(materialized['field']['verification_status'])

    attachments = list(RecordAttachment.objects.filter(patient_id=patient.id,
        record_version_id=version.id).order_by('ordinal'))
    file_allowed = current_read_file_allowed(principal, patient, record)
    history_allowed = current_read_history_allowed(principal, patient, record)
    original_files = []
    if file_allowed:
        for attachment in attachments:
            file = FileObject.objects.get(id=attachment.file_id, patient=patient)
            original_files.append({'file_id': str(file.id), 'status': file.status,
                'checksum': file.checksum, 'size_bytes': file.size_bytes,
                'media_type': file.media_type,
                'content_path': f'/api/v2/record-versions/{version.id}/originals' if history_allowed else None})
    write_codes = editable_codes & readable_codes
    original_count = len(attachments)
    return {
        'record_id': str(record.id), 'record_version_id': str(version.id), 'version': str(version.version),
        'patient_id': str(patient.id), 'display_id': patient.display_id, 'kind': record.kind,
        'origin_type': version.origin_type, 'capture_method': version.capture_method,
        'recorded_at': version.recorded_at.isoformat(), 'quality_status': version.quality_status,
        'fields': facts, 'report': report_view(version, patient),
        'editable_field_ids': [item['field']['field_id'] for item in facts
            if item['field']['field_code'] in write_codes],
        'verification_coverage': ('none' if not verification_states or all(state == 'unverified' for state in verification_states)
            else 'all' if all(state == 'verified' for state in verification_states) else 'partial'),
        'correction_reason': version.correction_reason, 'author': author_view(version, principal),
        'original_files': original_files,
        'original_files_complete': original_count == 0 or (file_allowed and len(original_files) == original_count),
    }


def published_source_references(principal, patient, event, approved_refs):
    references = AlertEvaluationInput.objects.filter(evaluation_id=event.evaluation_id,
        patient_id=patient.id).select_related('source_revision').order_by('signal_id', 'position', 'id')
    approved_keys = {(item['record_version_id'], item['field_id'], item['source_revision_id'], item['source_version'])
        for item in approved_refs}
    projected = []
    for item in references:
        key = (str(item.record_version_id), str(item.field_id), str(item.source_revision_id), item.source_revision.version)
        if key not in approved_keys:
            continue
        version = RecordVersion.objects.get(id=item.record_version_id, patient=patient)
        record = Record.objects.get(id=version.record_id, patient=patient, status='active',
            current_version=version.version)
        field = RecordField.objects.get(id=item.field_id, patient=patient,
            record_version_id=version.id)
        if field.field_code not in current_readable_codes(principal, patient, record):
            continue
        fact_versions = ArchiveFactVersion.objects.filter(patient_id=patient.id,
            record_version_id=version.id, field_id=field.id).order_by('-version')
        fact = None
        fact_version = None
        for candidate in fact_versions:
            current = ArchiveFact.objects.get(id=candidate.fact_id, patient_id=patient.id)
            if candidate.version == current.current_version:
                fact = current
                fact_version = candidate
                break
        if fact is None or fact_version is None:
            raise RuntimeError(f'当前评估输入缺少有效事实引用: {field.id}')
        materialized = fact_read(fact=fact, patient=patient, version=fact_version)
        source = materialized['field']['field']['source_reference']
        if source['source_revision_id'] != str(item.source_revision_id):
            raise RuntimeError(f'当前评估输入与事实来源版本不一致: {field.id}')
        projected.append(source)
    return projected


def build_home(principal, patient, environment):
    now = timezone.now()
    relation = Relation.objects.filter(patient=patient, guardian=principal, status='active',
        revoked_at__isnull=True, valid_from__lte=now).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now)).first()
    if relation is None:
        raise RuntimeError('当前家庭账号与患儿没有有效关联')
    initial_grants = list(matching_case_grants(principal, patient, 'read_raw',
        now, purpose='family_observation', require_relation=True))
    if not initial_grants:
        raise RuntimeError('当前家庭账号没有 read_raw 授权')
    readable_codes = {code for grant in initial_grants for code in grant.field_codes}
    projection = home_projection(patient=patient, principal=principal,
        field_codes=readable_codes, environment=environment)
    details = record_search(patient=patient, principal=principal,
        permission_scopes=family_read_permissions(principal, patient), limit=100)['records']
    projected_records = []
    for item in details:
        record = Record.objects.get(id=item['record_id'], patient=patient, status='active')
        view = family_record(principal, patient, record, item['detail'])
        if view is not None:
            projected_records.append(view)
        if len(projected_records) == 5:
            break
    visible_value_states = []
    for record in Record.objects.filter(patient=patient, status='active').order_by('id'):
        current = RecordVersion.objects.filter(patient=patient, record_id=record.id,
            version=record.current_version).first()
        if current is None:
            raise RuntimeError(f'当前记录缺少当前版本: {record.id}')
        codes = current_readable_codes(principal, patient, record)
        if codes:
            visible_value_states.extend(RecordField.objects.filter(patient=patient,
                record_version_id=current.id, field_code__in=codes).values_list('value_state', flat=True))
    data_state = ('empty' if not visible_value_states else
        'incomplete' if any(value != 'known' for value in visible_value_states) else 'present')
    capabilities = []
    permission_map = {
        'record:write': ('write_record', principal.id),
        'record:read': ('read_raw', None),
        'draft:manage': ('manage_draft', principal.id),
        'alert:receive': ('receive_alert', None),
        'summary:create': ('create_summary', None),
    }
    now = timezone.now()
    for capability_id, (action, author_id) in permission_map.items():
        permitted = any(matching_case_grants(principal, patient, action, now,
            author_id=author_id, purpose='family_observation', require_relation=True))
        capabilities.append({'capability_id': capability_id,
            'state': 'available' if permitted else 'disabled',
            'reason_code': None if permitted else 'CASE_ACTION_DENIED', 'config_version': None})
    result = {
        'patient_id': str(patient.id), 'display_id': patient.display_id,
        **projection, 'data_state': data_state, 'recent_records': projected_records,
        'capabilities': capabilities,
    }
    if projection['current_alert_id'] is not None:
        event = AlertEvent.objects.get(id=projection['current_alert_id'], patient=patient)
        receive_allowed = any(matching_case_grants(principal, patient, 'receive_alert',
            timezone.now(), purpose='family_observation', require_relation=True))
        if not receive_allowed:
            result['alert_state'] = 'invalid'
            result['current_alert_id'] = None
            result['read_state'] = 'not_applicable'
        else:
            published = alert_content(event, readable_codes)
            if published['validity'] == 'valid' and published['content_state'] == 'available':
                content = dict(published['content'])
                content['source_refs'] = published_source_references(principal, patient,
                    event, content['source_refs'])
                result['clinical'] = content
            else:
                result['alert_state'] = 'invalid'
                result['current_alert_id'] = None
                result['read_state'] = 'not_applicable'
    return result
