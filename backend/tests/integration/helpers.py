from uuid import uuid4

from configurations.models import ConfigVersion
from configurations.service import approve_config, enable_config, register_config, validate_config
from family_content.models import EnvironmentGate
from sources.models import SourceRevision


def post_record(client, patient, payload):
    target = f'/api/patients/{patient.id}/records'
    request_id = str(uuid4())
    prepared = client.post('/api/write-intents', {'operation_id': 'record_create', 'target_key': target, 'request_id': request_id, 'payload': payload, 'schema_version': 'back-json-1'}, content_type='application/json')
    if prepared.status_code != 201:
        raise RuntimeError(f'提交意图创建失败：{prepared.content}')
    intent = prepared.json()
    return client.post(target, payload, content_type='application/json', HTTP_X_WRITE_INTENT=intent['intent_id'], HTTP_X_INTENT_GENERATION=intent['generation'], HTTP_IDEMPOTENCY_KEY=request_id)


def activate_summary_configs(principal, field_codes=None):
    gate = EnvironmentGate.objects.get(environment='test')
    revision = SourceRevision.objects.first()
    for kind in ('summary', 'visibility'):
        ConfigVersion.objects.filter(kind=kind, status='enabled').update(status='disabled')
        config = register_config(kind=kind, config_key=f'integration-{kind}', schema_version=f'{kind}-1', payload={'field_codes': field_codes if field_codes is not None else ['temp'], 'simulation': True}, created_by=principal, source_revision=revision)
        approve_config(config=config, approved_by=principal, approval_role='local_test', evidence_ref='本地模拟验收', effective_scope={'environment': 'test'})
        validate_config(config=config, environment=gate, artifact_sha256=config.checksum, dataset_revision=revision, evidence_ref='本地模拟定义', evidence_sha256=revision.checksum, outcome='pass', executed_by=principal)
        enable_config(config=config, environment=gate)
