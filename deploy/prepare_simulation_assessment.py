import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from archives.batch_creation import save_received_batch
from archives.grouping import save_document
from archives.models import ArchiveDocumentVersion, ArchiveFactVersion, ArchivePage, ArchiveRelationVersion
from authorization.models import AccessGrant
from configurations.models import ConfigVersion
from configurations.schemas import validate_payload
from configurations.service import compute_checksum
from core.management.commands.seed_simulation import account, evidence, qualify_simulation_configuration, require_simulation
from family_content.models import EnvironmentGate
from files.models import FileObject, FilePage
from files.service import complete_file_group, create_file_group, declare_upload_part, store_file_object
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from records.models import RecordField, RecordVersion
from records.service import create_record, read_record
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision
from verification.service import verify_fields


FIXTURE = ROOT / '.planning-work/parallel/window1/t29-simulation-fixture.json'
TARGET = settings.SIMULATION_ROOT / 't29-business-fixture.json'
CONFIG_ROLES = {
    'classification': 'classification_rule', 'ms': 'ms_rule', 'qualification': 'qualification',
    'units': 'unit_mapping', 'context-population': 'population_context', 'context-therapy': 'therapy_context',
    'context-clinical-finding': 'finding_context', 'context-late-result': 'late_result_context',
    'window': 'window', 'dynamic-inputs': 'dynamic_inputs', 'reset': 'reset_lifecycle',
    'retrigger': 'retrigger_lifecycle', 'trigger': 'trigger', 'approved-content': 'content',
    'visibility': 'visibility', 'notification': 'notification', 'summary': 'summary',
}


def replace_references(value, replacements):
    if isinstance(value, dict):
        return {name: replace_references(item, replacements) for name, item in value.items()}
    if isinstance(value, list):
        return [replace_references(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def medical_time(instant):
    local = instant.removesuffix('+08:00')
    return {'precision': 'second', 'raw_text': instant, 'local_date': local[:10], 'local_datetime': local,
        'timezone_name': 'Asia/Shanghai', 'utc_offset_minutes': None, 'instant': None,
        'resolution_status': 'local_only'}


def read_saved(saved, expected_patient_id):
    if saved['mode'] != 'SIMULATION_ONLY' or saved['patient_id'] != str(expected_patient_id):
        raise RuntimeError('模拟业务输入清单身份不匹配')
    patient = Patient.objects.get(id=saved['patient_id'])
    for item in saved['records']:
        record = read_record(item['record_id'], patient)
        if record['record_version_id'] != item['record_version_id'] or record['quality_status'] != 'valid':
            raise RuntimeError('模拟业务输入未通过保存重读')
    for identity in saved['file_ids']:
        source = FileObject.objects.get(id=identity, patient=patient, status='available', purpose='original')
        from files.purpose import checked_bytes

        checked_bytes(source)
    return patient


def ensure_field_grants(saved, patient):
    actor_ids = [saved['owner_id'], saved['support_id']]
    codes = sorted(set(RecordField.objects.filter(patient=patient,
        record_version_id__in=[row['record_version_id'] for row in saved['records']]).values_list('field_code', flat=True)))
    if not codes:
        raise RuntimeError('模拟输入没有字段授权对象')
    grants = list(AccessGrant.objects.select_for_update().filter(patient=patient, principal_id__in=actor_ids,
        evidence_ref=saved['evidence_ref'], revoked_at=None, field_codes=[]).order_by('id'))
    for grant in grants:
        AccessGrant.objects.get_or_create(patient=patient, principal_id=grant.principal_id, action=grant.action,
            field_codes=codes, relation_id=grant.relation_id, record_scope=grant.record_scope,
            purpose=grant.purpose, evidence_ref=grant.evidence_ref, revoked_at=None,
            defaults={'granted_by_id': grant.granted_by_id, 'valid_from': timezone.now()})
    count = AccessGrant.objects.filter(patient=patient, principal_id__in=actor_ids,
        evidence_ref=saved['evidence_ref'], revoked_at=None, field_codes=codes).count()
    if count != len(grants):
        raise RuntimeError('模拟字段授权保存重读不一致')
    return {'field_codes': codes, 'field_grants_saved_and_read': count}


@transaction.atomic
def prepare(*, patient_id='405514ce-9335-4705-a750-d811148bda4c',
        family_username='simulation-family-b', target=TARGET, reuse_case=None):
    require_simulation()
    if target.exists():
        saved = json.loads(target.read_text())
        if saved['family_username'] != family_username:
            raise RuntimeError('模拟资料目录的家庭账号不匹配')
        patient = read_saved(saved, patient_id)
        return saved | {'reused': True, 'authorization_readback': ensure_field_grants(saved, patient)}
    fixture = json.loads(FIXTURE.read_text())
    if fixture['mode'] != 'SIMULATION_ONLY' or not fixture['not_persisted']:
        raise RuntimeError('输入必须具有明确的模拟候选标识')
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    patient = Patient.objects.select_for_update().get(id=patient_id, status='active')
    owner = account(family_username)
    executor = PrincipalCredential.objects.get(username='simulation-support').principal
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    if reuse_case is None:
        source, reference = evidence(key='t29-business-input', payload=fixture | {
            'actual_input_authorization': '2026-10-09用户授权全部模拟开发',
            'clinical_approval': 'NOT_RUN', 'actual_results': 'COMPUTED_BY_SERVICES'},
            purposes=['clinical_policy', 'family_observation', 'verification_evidence',
                'configuration_approval', 'configuration_validation'], retention=retention)
    else:
        if reuse_case['mode'] != 'SIMULATION_ONLY' or str(patient.id) == reuse_case['patient_id']:
            raise RuntimeError('首次提示场景必须使用独立模拟患儿')
        source = SourceRevision.objects.get(id=reuse_case['source_revision_id'], status='approved', source__status='approved')
        reference = reuse_case['evidence_ref']
    application = RelationApplication.objects.create(version=1, applicant=owner, patient=patient,
        evidence_ref=reference, status='approved', checked_by=executor, checked_at=timezone.now(), created_at=timezone.now())
    relation = Relation.objects.create(patient=patient, guardian=owner, application=application,
        status='active', version=1, checked_by=executor, checked_at=timezone.now(), valid_from=timezone.now())
    authorizations = {}
    for purpose in ('clinical_policy', 'family_observation'):
        authorizations[purpose] = DataUseAuthorization.objects.create(patient=patient, source_revision=source,
            purpose=purpose, environment='simulation', evidence_ref=reference, approved_by=executor,
            approved_at=timezone.now())
        for actor, actions in ((owner, ('read_raw', 'read_history', 'read_file', 'write_record',
                'receive_alert', 'read_summary', 'create_summary', 'manage_draft', 'view_request', 'correct_record')),
                (executor, ('read_case', 'read_file', 'verify_field', 'execute_assessment', 'read_assessment'))):
            for action in actions:
                AccessGrant.objects.create(principal=actor, patient=patient, action=action, field_codes=[],
                    relation_id=relation.id if actor == owner else None, record_scope='all', purpose=purpose,
                    granted_by=executor, evidence_ref=reference, valid_from=timezone.now())
    configurations = {}
    if reuse_case is None:
        roles = {name: str(uuid4()) for name in fixture['candidate_configuration_refs']}
        roles['summary'] = str(uuid4())
        replacements = {identifier: roles[name] for name, identifier in fixture['candidate_configuration_refs'].items()}
        replacements.update({fixture['source_family']['candidate_payload_evidence_revision_id']: str(source.id),
            f"urn:sjia:source-revision:{fixture['source_family']['candidate_payload_evidence_revision_id']}": reference})
        for candidate in fixture['candidate_payloads']:
            key = candidate['config_key']
            role = CONFIG_ROLES[key.removeprefix('t29-simulation-')]
            payload = replace_references(candidate['payload'], replacements)
            if role == 'summary':
                payload['purpose'] = 'clinical_policy'
            validate_payload(candidate['kind'], candidate['schema_version'], payload)
            configurations[role] = ConfigVersion.objects.create(id=UUID(roles[role]), config_key=key,
                kind=candidate['kind'], version=1, schema_version=candidate['schema_version'], payload=payload,
                checksum=compute_checksum(payload), source_revision=source, status='enabled', created_by=executor,
                created_at=timezone.now())
        for config in configurations.values():
            qualify_simulation_configuration(config, actor=executor, gate=gate, source=source,
                reference=reference, retention=retention)
    else:
        for role, identifier in reuse_case['config_ids'].items():
            configurations[role] = ConfigVersion.objects.get(id=identifier, status='enabled')
    record_policy = RetentionPolicy.objects.get(policy_key='simulation-report', status='active')
    file_policy = RetentionPolicy.objects.filter(policy_key='simulation-file', status='active').order_by('-version').first()
    if file_policy is None:
        raise RuntimeError('模拟原件保存策略不可用')
    quota = ConfigVersion.objects.get(config_key='simulation-directory-input-quota', status='draft')
    saved_records, file_ids, fact_ids, verification_ids, document_ids = [], [], [], [], []
    point_sets = [[fixture['comparison_points'][0]], fixture['fact_candidates']]
    for points in point_sets:
        content = ('SIMULATION ONLY\n' + '\n'.join(f"{item['source_text']}; sample time {item['medical_time']}" for item in points)).encode()
        authorization = authorizations['clinical_policy']
        group = create_file_group(patient=patient, owner=owner, source_revision=source, use_authorization_id=authorization.id)
        part = declare_upload_part(file_group=group, client_upload_id=uuid4(), ordinal=1,
            declared_checksum=digest_bytes(content), declared_size_bytes=len(content), declared_media_type='text/plain')
        stored = store_file_object(file_group=group, upload_part=part, data=content, purpose='original',
            retention_policy=file_policy, limits=gate.runtime_config.payload['limits'])
        complete_file_group(file_group=group)
        page = FilePage.objects.get(file_id=stored.id, patient=patient)
        batch = save_received_batch(actor=owner, patient=patient, request_id=uuid4(), pages=[page], quota=quota)
        batch.status = 'awaiting_confirmation'
        batch.save(update_fields=['status'])
        archive_page = ArchivePage.objects.get(batch_id=batch.id, file_id=stored.id, patient_id=patient.id)
        document, document_version = save_document(patient=patient, actor=owner, payload={'batch_id': str(batch.id),
            'page_ids': [str(archive_page.id)], 'document_type': 'laboratory', 'organization': 'SIMULATION模拟输入机构',
            'documented_time': medical_time(points[0]['medical_time']), 'episode_ids': [], 'confirmed_text': content.decode()})
        fields = []
        for item in points:
            numeric = item['unit'] is not None
            field = {'field_code': item['variable_code'], 'concept_code': item['concept_code'] if 'concept_code' in item else item['variable_code'],
                'original_name': 'SIMULATION ' + item['variable_code'], 'value_type': 'decimal' if numeric else 'categorical',
                'value_state': 'known', 'raw_numeric': item['value'] if numeric else None,
                'categorical_value': None if numeric else item['value'], 'raw_unit': item['unit'],
                'qualifier': 'eq' if numeric else None, 'method': item.get('method'), 'sample_time_origin': 'item',
                'medical_time': medical_time(item['medical_time']), 'source_file_id': str(stored.id), 'source_text': item['source_text']}
            if item['source_text'].encode() not in content:
                raise RuntimeError('模拟字段原文不在保存原件中')
            fields.append(field)
        created = create_record(patient=patient, actor=owner, kind='report', capture_method='manual',
            origin_type='report_transcription', retention_policy=record_policy, use_authorization_id=authorization.id,
            source_revision=source, file_ids=[stored.id], fields=fields,
            report_metadata={'external_ref': 'SIMULATION-T29-' + str(stored.id), 'report_status': 'final'})
        saved_fields = list(RecordField.objects.filter(record_version_id=created['record_version_id']).order_by('ordinal'))
        by_field = {fact.field_id: fact for fact in ArchiveFactVersion.objects.filter(record_version_id=created['record_version_id'])}
        facts = [by_field[field.id] for field in saved_fields]
        for fact, item in zip(facts, points, strict=True):
            fact.document_version_id, fact.page_id = document_version.id, archive_page.id
            fact.documented_time_id = document_version.documented_time_id
            fact.context_type = item['context_type']
            fact.assertion_state = 'absent' if item['value'] == 'absent' else 'present'
            fact.save(update_fields=['document_version_id', 'page_id', 'documented_time_id', 'context_type', 'assertion_state'])
        proof = verify_fields(patient=patient, record_version_id=created['record_version_id'], verifier=executor,
            policy=configurations['qualification'], purpose='clinical_policy', evidence_ref=reference, evidence_source=source,
            field_results=[(field.id, 'consistent') for field in saved_fields])
        reread = read_record(created['record_id'], patient)
        if reread['quality_status'] != 'valid' or reread['record_version_id'] != created['record_version_id']:
            checked = RecordVersion.objects.get(id=created['record_version_id'])
            raise RuntimeError(f'模拟记录格式或保存重读未通过：{checked.quality_reasons}')
        saved_records.append(created)
        file_ids.append(str(stored.id)); fact_ids.append([str(fact.id) for fact in facts])
        verification_ids.append(str(proof.id)); document_ids.append(str(document.id))
    baseline = ArchiveFactVersion.objects.get(id=fact_ids[0][0])
    current_field = RecordField.objects.get(record_version_id=saved_records[1]['record_version_id'], field_code='ferritin')
    current = ArchiveFactVersion.objects.get(record_version_id=saved_records[1]['record_version_id'], field_id=current_field.id)
    sampling = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=uuid4(), version=1,
        left_fact_version_id=baseline.id, right_fact_version_id=current.id, relation_type='independent_sampling',
        status='confirmed', evidence_ref=reference, reason='SIMULATION明确提供两次独立采样', author_id=owner.id,
        created_at=timezone.now(), verification_id=verification_ids[1])
    saved = {'mode': 'SIMULATION_ONLY', 'patient_id': str(patient.id), 'family_username': family_username,
        'owner_id': str(owner.id), 'support_id': str(executor.id), 'source_revision_id': str(source.id), 'evidence_ref': reference,
        'authorization_ids': {name: str(row.id) for name, row in authorizations.items()},
        'config_ids': {name: str(row.id) for name, row in configurations.items()}, 'records': saved_records,
        'file_ids': file_ids, 'fact_ids': fact_ids, 'verification_ids': verification_ids, 'document_ids': document_ids,
        'sampling_relation_version_id': str(sampling.id), 'known_at': timezone.now().isoformat(),
        'clinical_approval': 'NOT_RUN', 'assessment_results': 'NOT_RUN', 'dynamic_results': 'NOT_RUN',
        'alert_notification_summary': 'NOT_RUN', 'reused': False}
    if reuse_case is not None:
        saved['scenario'] = 'INDEPENDENT_FIRST_ALERT'
        saved['simulation_input_template_patient_id'] = reuse_case['patient_id']
        saved['clinical_independence'] = 'NOT_CLAIMED'
    read_saved(saved, patient.id)
    saved['authorization_readback'] = ensure_field_grants(saved, patient)
    transaction.on_commit(lambda: target.write_text(json.dumps(saved, ensure_ascii=False, indent=2)))
    return saved


if __name__ == '__main__':
    print(json.dumps(prepare(), ensure_ascii=False, indent=2))
