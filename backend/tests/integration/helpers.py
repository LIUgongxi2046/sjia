from uuid import uuid4

from configurations.models import ConfigVersion
from configurations.service import approve_config, enable_config, register_config, validate_config
from family_content.models import EnvironmentGate
from sources.models import SourceRevision


def post_record(client, patient, payload):
    from tests.synthetic_fixtures import current_record_payload
    payload = current_record_payload(patient, payload)
    target = f'/api/patients/{patient.id}/records'
    request_id = str(uuid4())
    prepared = client.post('/api/write-intents', {'operation_id': 'record_create', 'target_key': target, 'request_id': request_id, 'payload': payload, 'schema_version': 'back-json-1'}, content_type='application/json')
    if prepared.status_code != 201:
        raise RuntimeError(f'提交意图创建失败：{prepared.content}')
    intent = prepared.json()
    return client.post(target, payload, content_type='application/json', HTTP_X_WRITE_INTENT=intent['intent_id'], HTTP_X_INTENT_GENERATION=intent['generation'], HTTP_IDEMPOTENCY_KEY=request_id)


def activate_summary_configs(case, field_codes=None):
    """Real qualification of software-only publication and summary fixtures."""
    from tests.synthetic_fixtures import synthetic_publication
    fields = field_codes if field_codes is not None else ['temp']
    ConfigVersion.objects.filter(kind='summary', status='enabled').update(status='disabled')
    release, contact, configs = synthetic_publication(case.synthetic, field_codes=fields)
    template = case.synthetic.configuration('summary', {'schema_version': 'summary-1',
        'environment': case.synthetic.gate.environment, 'purpose': 'software_test',
        'field_codes': fields, 'evidence_refs': [case.synthetic.reference]})
    return template, configs['visibility']


def post_intended(client, target, operation, payload, schema_version='1.0.0'):
    request_id = str(uuid4())
    prepared = client.post('/api/write-intents', {'operation_id': operation, 'target_key': target,
        'request_id': request_id, 'payload': payload, 'schema_version': schema_version},
        content_type='application/json')
    if prepared.status_code != 201:
        raise AssertionError(f'Synthetic {operation} intent rejected: {prepared.status_code} {prepared.content}')
    intent = prepared.json()
    return client.post(target, payload, content_type='application/json',
        HTTP_X_WRITE_INTENT=intent['intent_id'], HTTP_X_INTENT_GENERATION=intent['generation'],
        HTTP_IDEMPOTENCY_KEY=request_id)


def support_session(client):
    session = client.session
    session['_sjia_audience'] = 'support'
    session.save()


def create_and_approve_synthetic_config(case, key):
    from tests.integration.helpers import post_intended, support_session
    support_session(case.client)
    payload = {'kind': 'summary', 'config_key': key, 'schema_version': 'summary-1',
        'payload': {'schema_version': 'summary-1', 'environment': 'test', 'purpose': 'software_test',
            'field_codes': ['temp'], 'evidence_refs': [case.synthetic.reference]},
        'source_revision_id': str(case.synthetic.source.id)}
    created = post_intended(case.client, '/api/support/configs', 'config_create', payload)
    case.assertEqual(created.status_code, 201, created.content)
    config_id = created.json()['resource_id']
    current = case.client.get(f'/api/support/configs/{config_id}')
    case.assertEqual(current.status_code, 200, current.content)
    case.assertEqual(current.json()['config']['payload'], payload['payload'])
    submitted = post_intended(case.client, f'/api/support/configs/{config_id}/submit', 'config_submit',
        {'expected_state': 'draft', 'expected_version': '1', 'reason': 'synthetic schema lifecycle'})
    case.assertEqual(submitted.status_code, 200, submitted.content)
    approved = post_intended(case.client, f'/api/support/configs/{config_id}/approvals', 'config_approve',
        {'config_checksum': current.json()['checksum'], 'approval_role': 'synthetic_software_test',
            'evidence_ref': case.synthetic.reference, 'environment': 'test',
            'audience': ['family', 'support'], 'valid_until': None})
    case.assertEqual(approved.status_code, 201, approved.content)
    config = ConfigVersion.objects.get(id=config_id)
    case.assertEqual(config.status, 'approved')
    return config


def correction_fields(patient, record_id, changes):
    from records.queries import record_version_read
    from records.models import Record, RecordVersion
    record = Record.objects.get(id=record_id, patient=patient)
    version = RecordVersion.objects.get(patient=patient, record_id=record.id, version=record.current_version)
    fields = record_version_read(patient=patient, record_version_id=version.id)['fields']
    keys = ('field_code', 'concept_code', 'original_name', 'value_type', 'value_state', 'raw_text',
        'raw_numeric', 'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval',
        'report_flag', 'sample_time_origin', 'medical_time')
    return [{**{key: field[key] for key in keys}, **changes.get(field['field_code'], {})} for field in fields]


def complete_synthetic_upload(case, group_id):
    """Real intent + async validator, no provider; preserve exact uploaded bytes."""
    from files.models import FileGroup, UploadPart
    from files.validation import execute_validation
    from tasks.models import OutboxEvent, Task
    group = FileGroup.objects.get(id=group_id)
    payload = {'expected_version': str(group.version), 'file_ids': [str(identity) for identity in
        UploadPart.objects.filter(file_group_id=group_id).order_by('ordinal').values_list('file_id', flat=True)]}
    response = post_intended(case.client, f'/api/uploads/{group_id}/complete', 'file_group_complete', payload)
    case.assertEqual(response.status_code, 202, response.content)
    case.assertEqual(response.json()['task_type'], 'file_validation')
    task = Task.objects.get(id=response.json()['task_id'])
    execute_validation(OutboxEvent.objects.get(task=task, event_type='file_validation.requested'), 'synthetic-file-validator')
    task.refresh_from_db()
    case.assertEqual(task.status, 'completed')
    read = case.client.get(f'/api/uploads/{group_id}')
    case.assertEqual(read.status_code, 200, read.content)
    return read
