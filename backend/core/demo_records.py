"""Synthetic experience records, written through the ordinary record services.

This module owns no identities, credentials, grants or clinical decisions. The
caller supplies already admitted, dedicated example patients and provenance.
Stable request receipts preserve user edits on rerun and never reset the account.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from uuid import UUID, uuid5

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from authorization.policy import evaluate
from configurations.field_config import validate_record_configuration
from drafts.models import Draft
from drafts.service import create_draft
from files.models import FileGroup, FileObject, FilePage
from identity.models import Patient, Principal
from records.admission import checked_retention
from records.correction import correct_record
from records.input_validation import validate_family_record_input
from records.models import Record, RecordVersion
from records.service import create_record
from requests.models import WriteIntent, WriteRequest
from requests.record_intents import _receipt
from requests.service import commit_intent, compute_digest, prepare_intent
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from sources.operations import RetentionRules


DEMO_NAMESPACE = UUID('91752ecc-4d74-5c77-a976-f0108cf0fce4')
PATIENT_KEYS = ('child_a', 'child_b')
DEMO_VERSION = 'experience-records-1'
REQUIRED_FIELDS = {
    'observation': {'temp': 'decimal', 'rash': 'categorical',
        'joint_discomfort': 'categorical', 'observation_note': 'text'},
    'report': {'report_item': 'decimal'},
    'therapy': {'medication_name': 'text', 'therapy_note': 'text',
        'therapy_status': 'categorical', 'therapy_start': 'time', 'therapy_end': 'time',
        'therapy_source': 'text', 'dose': 'decimal', 'dose_unit': 'text',
        'route_text': 'text', 'frequency_text': 'text'},
}


class DemoRecordsRejected(ValueError):
    """A dedicated fixture identity or its existing receipt is inconsistent."""


def demo_identity(key):
    return uuid5(DEMO_NAMESPACE, key)


def demo_field_config_payloads(reference):
    """Complete editable family field definitions; approval stays in metadata.

    ``reference`` is accepted for the seed command's common configuration
    interface. The field-config schema has no evidence slot; the caller binds
    that reference to its real ConfigApproval instead of inventing a JSON key.
    """
    if not isinstance(reference, str) or not reference.strip():
        raise DemoRecordsRejected('DEMO_CONFIG_REFERENCE_REQUIRED')
    labels = {'temp': '体温', 'rash': '皮疹', 'joint_discomfort': '关节不适',
        'observation_note': '补充说明', 'report_item': '原检验项目',
        'medication_name': '原药名', 'therapy_note': '资料说明', 'therapy_status': '实际使用状态',
        'therapy_start': '开始日期', 'therapy_end': '结束日期', 'therapy_source': '资料来源',
        'dose': '单次剂量', 'dose_unit': '剂量单位', 'route_text': '原始给药途径',
        'frequency_text': '原始使用频次', 'concentration_text': '原始浓度',
        'interval_value': '间隔数值', 'interval_unit': '间隔单位'}
    kinds = deepcopy(REQUIRED_FIELDS)
    kinds['therapy'].update(concentration_text='text', interval_value='decimal', interval_unit='categorical')
    values = {'rash': ['yes', 'no'], 'joint_discomfort': ['yes', 'no'],
        'therapy_status': ['ongoing', 'ended', 'not_started'], 'interval_unit': ['hour', 'day', 'week', 'month']}
    return {kind: {'schema_version': 'field-config-1', 'kind': kind, 'input_context': 'family_record',
        'fields': [{'field_code': code, 'label': labels[code], 'value_type': value_type,
            'required': False, 'writable': True, 'allowed_values': values.get(code, []),
            'allowed_units': ['C', 'F'] if code == 'temp' else [], 'dictionary_category': None,
            'minimum': None, 'maximum': None, 'max_length': 10000,
            'max_occurrences': 100 if code == 'report_item' else 1, 'time_required': False}
            for code, value_type in definitions.items()], 'watch_field_codes': []}
        for kind, definitions in kinds.items()}


def _date_time(day):
    return {'precision': 'date', 'local_date': day.isoformat(),
        'raw_text': day.isoformat(), 'resolution_status': 'local_only'}


def _field(code, value_type, value=None, *, state='known', time=None,
        origin='observation', **metadata):
    field = {'field_code': code, 'value_type': value_type, 'value_state': state,
        'sample_time_origin': origin, 'medical_time': time, **metadata}
    if value is not None:
        field[{'decimal': 'raw_numeric', 'categorical': 'categorical_value',
            'text': 'raw_text'}[value_type]] = value
    return field


def demo_record_specs(as_of):
    """Return the exact synthetic inputs; report renderers may reuse these rows.

    Keys such as ``child_a/report-1`` are stable across dates and must also key
    ``report_attachments``. No report fields claim a clinical dictionary match.
    """
    if not isinstance(as_of, datetime) or timezone.is_naive(as_of):
        raise DemoRecordsRejected('DEMO_AS_OF_MUST_BE_AWARE')
    day = as_of.astimezone(datetime_timezone.utc).date()
    specs = []
    temperatures = {'child_a': ('37.0', '37.5', '38.2', '38.0', '37.6', '37.2', '36.9'),
        'child_b': ('36.7', '36.8', '37.1', '37.0', '36.9', '36.8', '36.7')}
    for patient_key in PATIENT_KEYS:
        for index, temperature in enumerate(temperatures[patient_key]):
            observed = day - timedelta(days=7 - index)
            time = _date_time(observed)
            fields = [
                _field('temp', 'decimal', temperature, time=time, concept_code='temp',
                    original_name='体温', raw_unit='C', qualifier='eq', method='腋下'),
                _field('rash', 'categorical', None if index == 1 else
                    'yes' if patient_key == 'child_a' and index in (2, 3) else 'no',
                    state='unknown' if index == 1 else 'known', time=time, original_name='皮疹'),
                _field('joint_discomfort', 'categorical', None if index == 4 else 'no',
                    state='unknown' if index == 4 else 'known', time=time, original_name='关节不适'),
                _field('observation_note', 'text',
                    '示例观察：记录当天的体温与活动情况。' if index != 1 else
                    '示例观察：当天未确认是否出现皮疹，保留为未知。', time=time),
            ]
            if patient_key == 'child_b' and index == 3:
                fields[0].update(value_state='invalid', raw_numeric=None,
                    raw_text='读数模糊，原值无法辨认', qualifier=None)
            specs.append({'key': f'{patient_key}/observation-{index + 1}', 'patient_key': patient_key,
                'kind': 'observation', 'fields': fields,
                'recorded_at': datetime.combine(observed, datetime.min.time(), datetime_timezone.utc)
                    + timedelta(hours=12)})
    panels = {
        'child_a': [(12, '210', '86', '8.2'), (6, '175', '125', '12.0'), (2, '192', '103', '9.4')],
        'child_b': [(9, '248', '54', '4.6'), (3, '236', '61', None)],
    }
    for patient_key, visits in panels.items():
        for index, (days_ago, platelet, ferritin, crp) in enumerate(visits, 1):
            sample = day - timedelta(days=days_ago)
            time = _date_time(sample)
            fields = []
            for name, value, unit, interval, flag in (
                    ('血小板计数', platelet, '10^9/L', '100–300（示例范围）', None),
                    ('铁蛋白', ferritin, 'ng/mL', '15–150（示例范围）', None),
                    ('C反应蛋白', crp, 'mg/L', '0–8（示例范围）',
                        '↑' if crp is not None and float(crp) > 8 else None)):
                fields.append(_field('report_item', 'decimal', value,
                    state='known' if value is not None else 'unknown', time=time,
                    origin='item', original_name=name, raw_unit=unit,
                    qualifier='eq' if value is not None else None,
                    raw_text=value if value is not None else '未提供结果',
                    reference_interval=interval, report_flag=flag))
            specs.append({'key': f'{patient_key}/report-{index}', 'patient_key': patient_key,
                'kind': 'report', 'fields': fields,
                'recorded_at': datetime.combine(sample, datetime.min.time(), datetime_timezone.utc)
                    + timedelta(hours=13),
                'report_metadata': {'external_ref': f'示例-{patient_key[-1].upper()}-{index:02d}',
                    'report_status': 'final', 'sample_time': time, 'result_time': time}})
    for patient_key, status, label, days_ago in (
            ('child_a', 'ongoing', 'A', 10), ('child_a', 'ended', 'B', 14),
            ('child_b', 'not_started', 'C', 4)):
        start = _date_time(day - timedelta(days=days_ago))
        end = _date_time(day - timedelta(days=8)) if status == 'ended' else None
        fields = [
            _field('medication_name', 'text', f'示例药物 {label}', origin='therapy', time=start),
            _field('therapy_status', 'categorical', status, origin='therapy'),
            _field('therapy_start', 'time', state='unknown' if status == 'not_started' else 'known',
                time=None if status == 'not_started' else start, origin='therapy'),
            _field('therapy_end', 'time', state='known' if end else 'unknown', time=end, origin='therapy'),
            _field('therapy_source', 'text', '示例处方资料', origin='therapy'),
            _field('therapy_note', 'text', '仅用于体验资料整理，不是用药建议。', origin='therapy'),
            _field('dose', 'decimal', '1', raw_unit='示例单位', origin='therapy'),
            _field('dose_unit', 'text', '示例单位', origin='therapy'),
            _field('route_text', 'text', '按原资料登记（示例）', origin='therapy'),
            _field('frequency_text', 'text', '频次未注明', origin='therapy'),
        ]
        specs.append({'key': f'{patient_key}/therapy-{label.lower()}', 'patient_key': patient_key,
            'kind': 'therapy', 'fields': fields,
            'recorded_at': datetime.combine(day - timedelta(days=days_ago),
                datetime.min.time(), datetime_timezone.utc) + timedelta(hours=14)})
    return specs


def demo_report_plan(as_of):
    """Five independent report inputs, suitable for rendering actual originals."""
    return [row for row in demo_record_specs(as_of) if row['kind'] == 'report']


def _admit_context(*, actor, patients, retention_policies, source_revision, use_authorizations,
        field_configs):
    if (not settings.DEBUG or settings.SJIA_ENVIRONMENT not in ('test', 'synthetic-browser-demo')
            or connection.vendor != 'postgresql'
            or not str(connection.settings_dict['NAME']).startswith('test_')):
        raise DemoRecordsRejected('DEMO_NONPRODUCTION_ONLY')
    if set(patients) != set(PATIENT_KEYS) or set(use_authorizations) != set(PATIENT_KEYS):
        raise DemoRecordsRejected('DEMO_PATIENT_SET_INVALID')
    actor = Principal.objects.select_for_update().get(id=actor.id)
    if actor.account_status != 'active':
        raise DemoRecordsRejected('DEMO_ACTOR_UNAVAILABLE')
    if source_revision.id != demo_identity('source-revision'):
        raise DemoRecordsRejected('DEMO_EXTERNAL_SOURCE_REFUSED')
    revision = SourceRevision.objects.select_for_update().get(id=source_revision.id)
    source = SourceRegistry.objects.select_for_update().get(id=revision.source_id)
    now = timezone.now()
    if (source.id != demo_identity('source') or source.sensitivity != 'synthetic'
            or source.status != 'approved' or revision.status != 'approved'
            or revision.captured_at > now or 'family_observation' not in revision.allowed_use
            or not revision.rights_evidence_ref or revision.retention_policy_id is None
            or revision.retention_policy.status != 'active'):
        raise DemoRecordsRejected('DEMO_SOURCE_UNAVAILABLE')
    policies = {}
    for kind in ('observation', 'report', 'therapy', 'draft', 'request'):
        if kind not in retention_policies:
            raise DemoRecordsRejected(f'DEMO_RETENTION_REQUIRED:{kind}')
        policies[kind] = checked_retention(retention_policies[kind], data_classes=(kind,))
    admitted_patients, uses = {}, {}
    for key in PATIENT_KEYS:
        if patients[key].id != demo_identity(f'patient:{key}'):
            raise DemoRecordsRejected('DEMO_EXTERNAL_PATIENT_REFUSED')
        patient = Patient.objects.select_for_update().get(id=patients[key].id)
        use = DataUseAuthorization.objects.select_for_update().get(id=use_authorizations[key].id)
        if (patient.status != 'active' or '示例' not in patient.display_id
                or use.patient_id != patient.id or use.source_revision_id != revision.id
                or use.purpose != 'family_observation' or use.environment != settings.SJIA_ENVIRONMENT
                or use.revoked_at is not None or use.approved_at > now
                or use.valid_until is not None and use.valid_until <= now
                or use.approved_by.account_status != 'active' or not use.evidence_ref.strip()):
            raise DemoRecordsRejected('DEMO_PATIENT_AUTHORIZATION_UNAVAILABLE')
        admitted_patients[key], uses[key] = patient, use
    if set(field_configs) != set(REQUIRED_FIELDS):
        raise DemoRecordsRejected('DEMO_FIELD_CONFIG_SET_INVALID')
    return actor, admitted_patients, policies, revision, uses


def _existing_receipt(*, key, actor, patient, operation, target, resource_type):
    identity = demo_identity(f'{DEMO_VERSION}:{key}')
    rows = list(WriteIntent.all_objects.filter(request_id=identity).order_by('id'))
    if not rows:
        if WriteRequest.all_objects.filter(request_id=identity).exists():
            raise DemoRecordsRejected('DEMO_ORPHAN_RECEIPT_REFUSED')
        return None
    if len(rows) != 1:
        raise DemoRecordsRejected('DEMO_DUPLICATE_INTENT_REFUSED')
    intent = rows[0]
    if (intent.actor_id != actor.id or intent.patient_id != patient.id
            or intent.operation_id != operation or intent.target_key != target
            or intent.status != 'committed' or not intent.recovery_payload
            or intent.payload_sha256 != compute_digest(operation, target, 'back-json-1', intent.recovery_payload)):
        raise DemoRecordsRejected('DEMO_FOREIGN_OR_INCOMPLETE_INTENT_REFUSED')
    from retention.content_fences import assert_content_available
    assert_content_available('requests.writeintent', intent.id)
    receipt = _receipt(intent)
    if receipt is None or intent.write_request.resource_type != resource_type:
        raise DemoRecordsRejected('DEMO_RECEIPT_INVALID')
    return receipt


def _check_record(receipt, *, actor, patient, source_revision, use, kind):
    record = Record.objects.get(id=receipt['record_id'], patient=patient)
    initial = RecordVersion.all_objects.get(record_id=record.id, patient=patient, version=1)
    if (record.kind != kind or initial.author_id != actor.id
            or initial.source_revision_id != source_revision.id
            or initial.use_authorization_id != use.id or initial.source_organization != '体验资料'):
        raise DemoRecordsRejected('DEMO_FOREIGN_RECORD_REFUSED')
    return record


def _attachment_fields(spec, attachment, *, patient, actor, source_revision, use):
    fields = deepcopy(spec['fields'])
    if attachment is None:
        return fields, []
    if (not isinstance(attachment, dict) or set(attachment) - {'file_ids', 'source_file_id', 'source_text'}
            or not isinstance(attachment.get('file_ids'), list) or not attachment['file_ids']):
        raise DemoRecordsRejected('DEMO_ATTACHMENT_INPUT_INVALID')
    ids = [str(value) for value in attachment['file_ids']]
    for identity in ids:
        file = FileObject.objects.filter(id=identity, patient=patient, status='available', purpose='original').first()
        if file is None or not FileGroup.objects.filter(id=file.file_group_id, patient=patient, owner=actor,
                source_revision=source_revision, use_authorization_id=use.id, status='available').exists():
            raise DemoRecordsRejected('DEMO_FOREIGN_ATTACHMENT_REFUSED')
    source_file = attachment.get('source_file_id')
    if source_file is not None:
        if (str(source_file) not in ids or not isinstance(attachment.get('source_text'), str)
                or not attachment['source_text'].strip()
                or FilePage.objects.filter(file_id=source_file, patient=patient).count() != 1):
            raise DemoRecordsRejected('DEMO_ATTACHMENT_SOURCE_INVALID')
        for field in fields:
            field.update(source_file_id=str(source_file), source_text=attachment['source_text'])
    return fields, ids


@transaction.atomic
def seed_demo_records(*, actor, patients, retention_policies, source_revision, use_authorizations,
        field_configs, report_attachments=None, as_of=None):
    """Create once; return exact receipts, counts and restorable draft identities.

    The enclosing transaction includes every service write and its real receipt.
    Existing receipt identities are inspected before dated inputs are rebuilt,
    so later runs keep original dates and any subsequent user corrections.
    """
    actor, patients, policies, revision, uses = _admit_context(actor=actor, patients=patients,
        retention_policies=retention_policies, source_revision=source_revision,
        use_authorizations=use_authorizations, field_configs=field_configs)
    as_of = as_of or timezone.now()
    specs = demo_record_specs(as_of)
    attachments = report_attachments or {}
    if set(attachments) - {row['key'] for row in specs if row['kind'] == 'report'}:
        raise DemoRecordsRejected('DEMO_ATTACHMENT_KEY_INVALID')
    result = {'dataset_version': DEMO_VERSION, 'synthetic': True, 'records': {}, 'drafts': {},
        'created_records': 0, 'created_corrections': 0, 'created_drafts': 0}
    created_keys = set()
    for spec in specs:
        key, kind = spec['key'], spec['kind']
        patient, use = patients[spec['patient_key']], uses[spec['patient_key']]
        target = f'/api/patients/{patient.id}/records'
        receipt = _existing_receipt(key=key, actor=actor, patient=patient, operation='record_create',
            target=target, resource_type='record')
        if receipt is None:
            fields, file_ids = _attachment_fields(spec, attachments.get(key), patient=patient,
                actor=actor, source_revision=revision, use=use)
            payload = {'kind': kind, 'capture_method': 'manual',
                'origin_type': 'report_transcription' if kind == 'report' else 'self_report',
                'field_config_version': str(field_configs[kind].id), 'fields': fields,
                'source_organization': '体验资料', 'file_ids': file_ids}
            if kind == 'report':
                payload['report'] = spec['report_metadata']
            validate_family_record_input(payload)
            validate_record_configuration(field_configs[kind].id, kind, fields)
            evaluate(principal=actor, patient=patient, action='write_record',
                purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
                session_auth_epoch=actor.auth_epoch, author_id=actor.id,
                field_codes=[field['field_code'] for field in fields], require_relation=True)
            intent = prepare_intent(actor=actor, patient=patient, operation_id='record_create',
                target_key=target, request_id=demo_identity(f'{DEMO_VERSION}:{key}'),
                payload=payload, recovery_payload=payload, schema_version='back-json-1',
                retention_policy=policies['request'])
            receipt = create_record(patient=patient, actor=actor, kind=kind, capture_method='manual',
                origin_type=payload['origin_type'], retention_policy=policies[kind],
                use_authorization_id=use.id, fields=fields, source_revision=revision,
                source_organization='体验资料', recorded_at=spec['recorded_at'], file_ids=file_ids,
                report_metadata=spec.get('report_metadata'))
            commit_intent(intent=intent, action='record_create', resource_type='record',
                resource_id=receipt['record_id'])
            result['created_records'] += 1
            created_keys.add(key)
        _check_record(receipt, actor=actor, patient=patient, source_revision=revision, use=use, kind=kind)
        result['records'][key] = receipt

    # This example correction is part of the initial transaction only. An
    # unexplained missing receipt never authorizes overwriting a user's version.
    original_key = 'child_a/observation-3'
    patient, use = patients['child_a'], uses['child_a']
    record = Record.objects.get(id=result['records'][original_key]['record_id'], patient=patient)
    target = f'/api/records/{record.id}/corrections'
    correction_key = f'{original_key}/correction'
    corrected = _existing_receipt(key=correction_key, actor=actor, patient=patient,
        operation='record_correct', target=target, resource_type='record_version')
    if corrected is None:
        if original_key not in created_keys or record.current_version != 1:
            raise DemoRecordsRejected('DEMO_MISSING_CORRECTION_RECEIPT')
        fields = deepcopy(next(row['fields'] for row in specs if row['key'] == original_key))
        fields[0]['raw_numeric'] = '38.1'
        reason = '示例更正：复看记录后，将体温由 38.2 更正为 38.1。'
        payload = {'expected_version': 1, 'reason': reason, 'fields': fields}
        evaluate(principal=actor, patient=patient, action='correct_record',
            purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
            session_auth_epoch=actor.auth_epoch, author_id=actor.id, record_id=record.id,
            field_codes=[field['field_code'] for field in fields], require_relation=True)
        intent = prepare_intent(actor=actor, patient=patient, operation_id='record_correct',
            target_key=target, request_id=demo_identity(f'{DEMO_VERSION}:{correction_key}'),
            payload=payload, recovery_payload=payload, schema_version='back-json-1',
            retention_policy=policies['request'])
        corrected = correct_record(record=record, expected_version=1, actor=actor, reason=reason,
            fields=fields, use_authorization_id=use.id, retention_policy=policies['observation'],
            source_revision=revision)
        commit_intent(intent=intent, action='record_correct', resource_type='record_version',
            resource_id=corrected['record_version_id'], expected_version=1)
        result['created_corrections'] += 1
    if (corrected['record_id'] != str(record.id) or corrected['version'] != 2):
        raise DemoRecordsRejected('DEMO_CORRECTION_RECEIPT_INVALID')
    corrected_version = RecordVersion.objects.get(id=corrected['record_version_id'], patient=patient)
    if (corrected_version.author_id != actor.id or corrected_version.source_revision_id != revision.id
            or corrected_version.use_authorization_id != use.id):
        raise DemoRecordsRejected('DEMO_FOREIGN_CORRECTION_REFUSED')
    result['correction'] = corrected

    rules = RetentionRules.model_validate(policies['draft'].rules)
    expires_at = timezone.now() + timedelta(days=rules.retention_days) if rules.disposition == 'delete' else None
    for key in PATIENT_KEYS:
        marker = f'backend-draft-1:synthetic:{demo_identity(f"{DEMO_VERSION}:{key}/draft")}'
        existing = list(Draft.all_objects.filter(schema_version=marker))
        if len(existing) > 1:
            raise DemoRecordsRejected('DEMO_DUPLICATE_DRAFT_REFUSED')
        if existing:
            draft = existing[0]
            if (draft.patient_id != patients[key].id or draft.owner_id != actor.id
                    or draft.form_type != 'observation' or draft.retention_policy_id != policies['draft'].id):
                raise DemoRecordsRejected('DEMO_FOREIGN_DRAFT_REFUSED')
        else:
            evaluate(principal=actor, patient=patients[key], action='manage_draft',
                purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
                session_auth_epoch=actor.auth_epoch, author_id=actor.id, require_relation=True)
            payload = {'temp': '37.0', 'tempUnit': '摄氏度（°C）',
                'description': '示例草稿：可以继续补充今天的观察，再确认保存。',
                'method': '腋下', 'measuredTime': {'precision': 'unknown', 'value': '', 'timezone': ''},
                'observedTime': {'precision': 'unknown', 'value': '', 'timezone': ''},
                'symptoms': {'rash': 'no'}, 'voiceApplied': False}
            draft = create_draft(patient=patients[key], owner=actor, form_type='observation',
                schema_version=marker, payload=payload, retention_policy=policies['draft'], expires_at=expires_at)
            result['created_drafts'] += 1
        result['drafts'][key] = {'draft_id': str(draft.id), 'patient_id': str(draft.patient_id),
            'status': draft.status, 'version': draft.version}
    return result
