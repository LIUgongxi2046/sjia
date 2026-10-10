import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from archives.models import ArchiveFactVersion
from authorization.models import AccessGrant
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from identity.models import Patient
from records.service import read_record
from relations.models import Relation
from sources.models import DataUseAuthorization, SourceRevision
from verification.models import Verification, VerificationField
from configurations.schemas import validate_payload


PATIENT_DISPLAY_ID = 'SIMULATION隔离权限患儿B'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-api-options-read.json'
INPUTS = ROOT / '.simulation-work' / 't29-business-fixture.json'
API_BASE = 'http://127.0.0.1:8001'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def error_code(response):
    body = response.json() if response.headers.get('content-type', '').startswith('application/json') else None
    return body.get('code') if isinstance(body, dict) else None


def response_view(response):
    body = response.json() if response.headers.get('content-type', '').startswith('application/json') else None
    safe_body = None
    if isinstance(body, dict):
        safe_body = {key: body[key] for key in ('code', 'detail', 'status', 'reason', 'options', 'plans') if key in body}
    elif isinstance(body, list):
        safe_body = body
    return {'status_code': response.status_code, 'error_code': error_code(response), 'body': safe_body}


def saved_input_inventory():
    saved_bytes = INPUTS.read_bytes()
    saved = json.loads(saved_bytes)
    require(saved['mode'] == 'SIMULATION_ONLY' and saved['patient_id'] == '405514ce-9335-4705-a750-d811148bda4c',
        'Saved T29 input manifest has an unexpected identity or mode.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            database, schema, port = cursor.fetchone()
        require((database, schema, port) == ('sjia_simulation', 'sjia', 5433), 'Database identity is outside the approved simulation scope.')
        patient = Patient.objects.get(id=saved['patient_id'], display_id=PATIENT_DISPLAY_ID, status='active')
        source = SourceRevision.objects.get(id=saved['source_revision_id'], status='approved')
        relations = list(Relation.objects.filter(patient=patient, guardian_id=saved['owner_id'],
            status='active', revoked_at__isnull=True).values('id', 'auth_epoch'))
        require(len(relations) == 1, 'T29 simulation guardian relation is not uniquely active.')
        grants = list(AccessGrant.objects.filter(patient=patient, revoked_at__isnull=True).values(
            'id', 'principal_id', 'action', 'purpose', 'record_scope', 'relation_id'))
        authorizations = list(DataUseAuthorization.objects.filter(patient=patient, revoked_at__isnull=True).values(
            'id', 'purpose', 'environment', 'source_revision_id', 'approved_at'))
        for record_ref in saved['records']:
            record = read_record(record_ref['record_id'], patient)
            require(record['record_version_id'] == record_ref['record_version_id'] and record['quality_status'] == 'valid',
                'Persisted T29 source record did not pass independent readback.')
        fact_ids = [identity for group in saved['fact_ids'] for identity in group]
        facts = list(ArchiveFactVersion.objects.filter(patient_id=patient.id, id__in=fact_ids).order_by('id').values(
            'id', 'record_version_id', 'field_id', 'context_type', 'assertion_state', 'available_at', 'created_at'))
        require(len(facts) == len(fact_ids), 'Persisted T29 source facts are incomplete.')
        verifications = list(Verification.objects.filter(patient=patient, id__in=saved['verification_ids'],
            revoked_at__isnull=True).order_by('id').values('id', 'purpose', 'verified_at', 'evidence_ref'))
        require(len(verifications) == len(saved['verification_ids']) and all(row['purpose'] == 'clinical_policy' for row in verifications),
            'Persisted T29 verification proofs are incomplete or inactive.')
        verification_fields = list(VerificationField.objects.filter(verification_id__in=saved['verification_ids']).values(
            'verification_id', 'field_id', 'result'))
        require(verification_fields and all(row['result'] == 'consistent' for row in verification_fields),
            'Persisted T29 field verification results are incomplete or inconsistent.')
        config_rows = list(ConfigVersion.objects.filter(id__in=saved['config_ids'].values()).order_by('id'))
        configs = [{'id': str(row.id), 'config_key': row.config_key, 'kind': row.kind,
            'schema_version': row.schema_version, 'checksum': row.checksum, 'status': row.status} for row in config_rows]
        for row in config_rows:
            validate_payload(row.kind, row.schema_version, row.payload)
        require(len(configs) == len(saved['config_ids']), 'Saved T29 configuration versions are incomplete.')
        required_keys = {'classification_rule', 'ms_rule', 'qualification', 'unit_mapping', 'population_context',
            'therapy_context', 'finding_context', 'late_result_context', 'window', 'dynamic_inputs',
            'reset_lifecycle', 'retrigger_lifecycle'}
        required_ids = {saved['config_ids'][key] for key in required_keys}
        enabled_ids = {str(row.id) for row in config_rows if row.status == 'enabled'}
        require(required_ids <= enabled_ids, 'Enabled T29 assessment or dynamic input configurations are incomplete.')
        approvals = list(ConfigApproval.objects.filter(config_id__in=required_ids, revoked_at__isnull=True).values(
            'config_id', 'effective_scope'))
        validations = list(ConfigValidation.objects.filter(config_id__in=required_ids, outcome='pass').values(
            'config_id', 'config_checksum', 'environment', 'artifact_sha256'))
        require(required_ids <= {str(row['config_id']) for row in approvals}
            and required_ids <= {str(row['config_id']) for row in validations},
            'T29 assessment or dynamic configuration approvals or validation proofs are incomplete.')
        require(any(row['purpose'] == 'clinical_policy' and row['environment'] == 'simulation'
            and row['source_revision_id'] == source.id for row in authorizations),
            'Active T29 clinical policy use authorization is missing.')
        require(any(row['action'] == 'execute_assessment' and str(row['principal_id']) == saved['support_id']
            and row['record_scope'] == 'all' for row in grants), 'Scoped support assessment grant is missing.')
        return {
            'manifest_path': str(INPUTS.relative_to(ROOT)),
            'manifest_sha256': hashlib.sha256(saved_bytes).hexdigest(),
            'source_revision_id': str(source.id),
            'active_relation': relations[0],
            'active_grant_count': len(grants),
            'active_data_use_authorization_count': len(authorizations),
            'record_versions': saved['records'],
            'fact_count': len(facts),
            'verification_field_count': len(verification_fields),
            'verification_ids': [str(row['id']) for row in verifications],
            'enabled_configurations': configs,
            'enabled_required_configurations': sorted(required_ids),
            'configuration_approval_count': len({str(row['config_id']) for row in approvals}),
            'configuration_validation_count': len({str(row['config_id']) for row in validations}),
            'saved_objects_readback': True,
        }


def main():
    parser = argparse.ArgumentParser(description='Read-only T29 simulation options and qualification probe')
    parser.add_argument('--report', type=Path, default=REPORT)
    args = parser.parse_args()
    report_path = args.report.resolve()
    require(report_path.is_relative_to((ROOT / '.planning-work' / 'parallel' / 'window1').resolve()),
        'Report must remain within the window1 planning directory.')
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    inventory = saved_input_inventory()
    database, schema, port = 'sjia_simulation', 'sjia', 5433
    patient = Patient.objects.get(id=json.loads(INPUTS.read_text(encoding='utf-8'))['patient_id'])
    known_at = datetime.now(timezone.utc).isoformat()
    with httpx.Client(base_url=API_BASE, timeout=20, trust_env=False) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        require(bootstrap.status_code == 200, f'Simulation API bootstrap returned HTTP {bootstrap.status_code}.')
        client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
        login = client.post('/api/v2/support/session/login', json={
            'username': 'simulation-support', 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
        require(login.status_code == 200, f'Simulation support login returned HTTP {login.status_code}.')
        session_body = login.json()
        require(session_body.get('audience') == 'support', 'Authenticated session audience is not support.')
        client.headers['X-CSRFToken'] = session_body['csrf_token']
        clinical_scope = client.get('/api/v2/support/clinical-scopes', params={'action': 'execute_assessment'})
        query = {'mode': 'current', 'known_at': known_at}
        assessment_options = client.get(f'/api/v2/support/patients/{patient.id}/assessment-options', params=query)
        dynamic_options = client.get(f'/api/v2/support/patients/{patient.id}/alert-evaluation-options', params=query)

    options_available = (assessment_options.status_code == 200 and dynamic_options.status_code == 200
        and bool(assessment_options.json().get('plans')) and bool(dynamic_options.json().get('plans')))
    report = {
        'task_id': 'T29',
        'mode': 'SIMULATION_ONLY',
        'status': 'QUALIFIED_OPTIONS_AVAILABLE' if options_available else 'PRECONDITIONS_MISSING',
        'api_base': API_BASE,
        'database': {'name': database, 'schema': schema, 'port': port},
        'patient_id': str(patient.id),
        'saved_input_inventory': inventory,
        'support_login': {'status_code': login.status_code, 'audience': session_body['audience']},
        'known_at': known_at,
        'clinical_scope': response_view(clinical_scope),
        'assessment_options': response_view(assessment_options),
        'dynamic_options': response_view(dynamic_options),
        'write_requests': 0,
        'task_creations': 0,
        'model_calls': 0,
        'notification_sends': 0,
        'summary_creations': 0,
        'external_calls': 0,
        'parent_approval_required': False,
    }
    if assessment_options.status_code != 200:
        report['missing_prerequisites'] = ['patient_b_assessment_options_permission_or_current_inputs']
    elif dynamic_options.status_code != 200:
        report['missing_prerequisites'] = ['patient_b_dynamic_options_permission_or_current_inputs']
    elif not assessment_options.json().get('plans'):
        report['missing_prerequisites'] = ['qualified_static_assessment_plan']
    elif not dynamic_options.json().get('plans'):
        report['missing_prerequisites'] = ['qualified_dynamic_evaluation_plan']
    else:
        report['missing_prerequisites'] = []
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    saved = json.loads(report_path.read_text(encoding='utf-8'))
    require(saved == json.loads(json.dumps(report, default=str)), 'API options report readback differs from its saved content.')
    print(json.dumps({'status': report['status'], 'clinical_scope_status': clinical_scope.status_code,
        'assessment_options_status': assessment_options.status_code, 'dynamic_options_status': dynamic_options.status_code,
        'missing_prerequisites': report['missing_prerequisites'], 'saved_and_read': True,
        'report_path': str(report_path)}, sort_keys=True))


if __name__ == '__main__':
    main()
