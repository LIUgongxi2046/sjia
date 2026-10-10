import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django
from django.db import connection, transaction

django.setup()

from alerting.models import AlertEvent, AlertEvaluation, AlertRecipient
from assessment.models import Assessment
from authorization.models import AccessGrant
from identity.models import Patient
from notifications.models import NotificationAttempt, NotificationReceipt
from records.models import Record, RecordField
from relations.models import Relation
from sources.models import DataUseAuthorization
from summaries.models import Summary


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-workflow-input-report.json'
PATIENT_DISPLAY_ID = 'SIMULATION隔离权限患儿B'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def patient_inventory():
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema()')
            database_name, schema_name = cursor.fetchone()
            require(database_name == 'sjia_simulation' and schema_name == 'sjia', 'Database identity is outside the permitted simulation scope.')
        patient = Patient.objects.get(display_id=PATIENT_DISPLAY_ID)
        relations = list(Relation.objects.filter(patient=patient).order_by('id').values(
            'id', 'guardian_id', 'status', 'auth_epoch', 'valid_from', 'valid_until', 'revoked_at'))
        grants = list(AccessGrant.objects.filter(patient=patient).order_by('id').values(
            'id', 'principal_id', 'relation_id', 'action', 'purpose', 'field_codes', 'record_scope',
            'evidence_ref', 'valid_from', 'valid_until', 'revoked_at'))
        authorizations = list(DataUseAuthorization.objects.filter(patient=patient).order_by('id').values(
            'id', 'source_revision_id', 'purpose', 'environment', 'evidence_ref', 'approved_at', 'valid_until', 'revoked_at'))
        records = list(Record.objects.filter(patient=patient).order_by('id').values(
            'id', 'kind', 'current_version', 'status'))
        facts = list(RecordField.objects.filter(patient=patient).order_by('id').values(
            'id', 'record_version_id', 'field_code', 'concept_code', 'value_type', 'value_state', 'qualifier',
            'raw_unit', 'method', 'medical_time_id', 'confirmation_id'))
        counts = {
            'static_assessments': Assessment.objects.filter(patient=patient).count(),
            'dynamic_evaluations': AlertEvaluation.objects.filter(patient=patient).count(),
            'alert_events': AlertEvent.objects.filter(patient=patient).count(),
            'alert_recipients': AlertRecipient.objects.filter(patient_id=patient.id).count(),
            'notification_attempts': NotificationAttempt.objects.filter(patient_id=patient.id).count(),
            'notification_receipts': NotificationReceipt.objects.filter(attempt__patient_id=patient.id).count(),
            'summaries': Summary.objects.filter(patient=patient).count(),
        }
        return {
            'database': database_name,
            'schema': schema_name,
            'patient_id': str(patient.id),
            'patient_display_id': patient.display_id,
            'relations': relations,
            'access_grants': grants,
            'data_use_authorizations': authorizations,
            'records': records,
            'facts': facts,
            'counts': counts,
        }


def workflow_plan(inventory):
    facts = inventory['facts']
    required_static = {
        'classification': ['sjia', 'fever', 'ferritin', 'platelet_count', 'ast', 'triglycerides', 'fibrinogen'],
        'ms': ['sjia', 'fever', 'cns', 'hemorrhagic_manifestations', 'active_arthritis', 'platelet_count', 'ldh', 'fibrinogen', 'ferritin'],
    }
    present_concepts = sorted({row['concept_code'] for row in facts if row['concept_code']})
    missing = []
    if not any(row['status'] == 'active' and row['revoked_at'] is None for row in inventory['relations']):
        missing.append('active_relation')
    if not any(row['revoked_at'] is None for row in inventory['access_grants']):
        missing.append('existing_support_access_grant')
    if not any(row['revoked_at'] is None for row in inventory['data_use_authorizations']):
        missing.append('active_data_use_authorization')
    if not facts:
        missing.append('source_linked_record_fields_and_verification_proofs')
    for kind in ('classification', 'ms'):
        missing.append(f'qualified_{kind}_options_with_all_required_facts')
    missing.extend([
        'qualified_dynamic_creation_plan_and_current_observation_period',
        'current_publication_release_content_and_contact_qualification',
        'alert_event_created_by_authorized_workflow',
        'existing_authorized_alert_recipient',
        'single_notification_task_and_provider_receipt',
        'summary_creation_and_current_family_readback',
    ])
    return {
        'schema_version': 't29-workflow-input-plan-1',
        'mode': 'SIMULATION_ONLY',
        'status': 'PRECONDITIONS_MISSING' if missing else 'READY_FOR_PARENT_EXECUTION',
        'database': {'name': inventory['database'], 'schema': inventory['schema'], 'read_only': True},
        'patient_id': inventory['patient_id'],
        'observed_concept_codes': present_concepts,
        'required_static_fact_codes': required_static,
        'fixed_formulae': {
            'classification': 'sJIA=true and fever=true; ferritin > 684 ng/mL; at least 2 of platelet_count <= 181 x10^9/L, ast > 48 U/L, triglycerides > 156 mg/dL, fibrinogen <= 360 mg/dL',
            'ms': '2.44*CNS + 1.54*hemorrhagic_manifestations - 1.30*active_arthritis - 0.003*platelet_count + 0.001*ldh - 0.004*fibrinogen + 0.0001*ferritin; compare the unrounded Decimal total with -2.1',
        },
        'required_configuration_schemas': [
            {'kind': 'rule', 'schema_version': 'assessment-rule-2'},
            {'kind': 'window', 'schema_version': 'alert-window-1'},
            {'kind': 'verification', 'schema_version': 'qualification-policy-2'},
            {'kind': 'rule', 'schema_version': 'assessment-context-policy-2'},
            {'kind': 'mapping', 'schema_version': 'unit-mapping-2'},
            {'kind': 'rule', 'schema_version': 'alert-trigger-1'},
            {'kind': 'rule', 'schema_version': 'dynamic-input-policy-2'},
            {'kind': 'rule', 'schema_version': 'alert-lifecycle-2', 'required_instances': ['reset', 'retrigger']},
            {'kind': 'publication', 'collections': ['release', 'config', 'contact']},
            {'kind': 'provider', 'schema_version': 'backend-runtime-1', 'provider_schema_version': 'notification-in-app-2'},
            {'kind': 'content', 'schema_version': 'content-1'},
            {'kind': 'notification', 'schema_version': 'notification-1'},
            {'kind': 'summary', 'schema_version': 'summary-1'},
            {'kind': 'visibility', 'schema_version': 'visibility-1'},
        ],
        'configuration_payload_authorities': [
            {'config': 'assessment-rule-2', 'source': 'backend/assessment/policies.py::StaticRulePolicy'},
            {'config': 'qualification-policy-2', 'source': 'backend/assessment/policies.py::QualificationPolicy'},
            {'config': 'assessment-context-policy-2', 'source': 'backend/assessment/policies.py::StaticContextPolicy'},
            {'config': 'unit-mapping-2', 'source': 'backend/assessment/policies.py::MappingPolicy'},
            {'config': 'alert-window-1', 'source': 'alert-design/policy.schema.json::WindowPolicy'},
            {'config': 'alert-trigger-1', 'source': 'alert-design/policy.schema.json::TriggerPolicy'},
            {'config': 'dynamic-input-policy-2', 'source': 'backend/alerting/policies.py::DynamicInputPolicy'},
            {'config': 'alert-lifecycle-2', 'source': 'backend/alerting/policies.py::LifecyclePolicy'},
            {'config': 'backend-runtime-1', 'source': 'backend-design/runtime.schema.json'},
            {'config': 'notification-in-app-2', 'source': 'backend/notifications/policies.py::NativeNotificationPolicy'},
            {'config': 'content-1', 'source': 'backend/family_content/policies.py::ContentPolicy'},
            {'config': 'notification-1', 'source': 'backend/family_content/policies.py::NotificationPolicy'},
            {'config': 'summary-1', 'source': 'backend/family_content/policies.py::SummaryPolicy'},
            {'config': 'visibility-1', 'source': 'backend/family_content/policies.py::VisibilityPolicy'},
        ],
        'factual_input_requirements': {
            'permission': ['patient_id', 'active_relation_id', 'current_auth_epoch', 'support_access_grant_id', 'grant_purpose', 'grant_field_scope'],
            'source_and_use': ['approved_source_revision_id', 'source_license_and_rights_evidence', 'active_retention_policy_id', 'patient_data_use_authorization_id', 'authorization_purpose', 'environment'],
            'each_clinical_fact': ['fact_version_id', 'record_version_id', 'field_id', 'exact_concept_code', 'raw_value', 'raw_unit', 'method', 'medical_time_id', 'verification_id', 'verification_status', 'unit_mapping_config_id', 'normalized_decimal_or_categorical_value'],
            'static_context': ['sjia', 'fever', 'population', 'therapy', 'clinical_finding', 'late_result'],
            'dynamic_selection': ['current_or_baseline_role', 'fact_version_id', 'selection_reason', 'evidence_ref', 'prior_task_id'],
            'publication_and_notification': ['release_id', 'release_component_ids_and_checksums', 'contact_id_and_current_version', 'active_relation_id', 'single_in_app_recipient_id', 'request_id', 'provider_request_id', 'signed_receipt_id_and_status'],
            'summary': ['from_date', 'to_date', 'scope_timezone', 'include_unknown_time', 'record_kinds', 'include_source_status', 'write_intent_id', 'request_id', 'generation'],
        },
        'request_plan': [
            {'method': 'GET', 'path': f"/api/v2/support/patients/{inventory['patient_id']}/assessment-options", 'query': ['mode=current', 'known_at=<aware ISO-8601 timestamp>']},
            {'method': 'POST', 'path': f"/api/v2/support/patients/{inventory['patient_id']}/assessments", 'payload_schema': 'AssessmentInput', 'execution_owner': 'parent', 'requires_options_plan': True},
            {'method': 'GET', 'path': '/api/v2/support/assessment-tasks/{task_id}'},
            {'method': 'GET', 'path': '/api/v2/support/assessments/{assessment_id}'},
            {'method': 'GET', 'path': f"/api/v2/support/patients/{inventory['patient_id']}/alert-evaluation-options", 'query': ['mode=current', 'known_at=<aware ISO-8601 timestamp>']},
            {'method': 'POST', 'path': f"/api/v2/support/patients/{inventory['patient_id']}/alert-evaluations", 'payload_schema': 'DynamicInput', 'execution_owner': 'parent', 'requires_options_plan': True},
            {'method': 'GET', 'path': '/api/v2/support/alert-evaluation-tasks/{task_id}'},
            {'method': 'GET', 'path': '/api/v2/support/alert-evaluations/{evaluation_id}'},
            {'method': 'GET', 'path': '/api/v2/support/publication-directory', 'query': ['collection=release|config|contact', 'action=<exact action>']},
            {'method': 'POST', 'path': '/api/v2/support/notification-records/{recipient_id}/send', 'payload_schema': 'notification.SendInput', 'requires_write_intent_headers': ['X-Write-Intent', 'Idempotency-Key', 'X-Intent-Generation'], 'execution_owner': 'parent', 'single_recipient_only': True},
            {'method': 'GET', 'path': '/api/v2/support/notification-records/{recipient_id}'},
            {'method': 'GET', 'path': f"/api/v2/support/patients/{inventory['patient_id']}/notification-records", 'audience': 'support'},
            {'method': 'GET', 'path': '/api/v2/notification-records/{recipient_id}/message', 'audience': 'family'},
            {'method': 'POST', 'path': '/api/v2/notification-records/{recipient_id}/delivered', 'payload_schema': 'NotificationAcknowledgement', 'requires_delivery_ticket_from_message': True, 'audience': 'family'},
            {'method': 'GET', 'path': '/api/v2/tasks/{notification_task_id}', 'audience': 'support'},
            {'method': 'GET', 'path': f"/api/patients/{inventory['patient_id']}/summaries", 'audience': 'family'},
            {'method': 'POST', 'path': '/api/write-intents', 'payload_schema': 'SummaryIntentInput', 'operation_id': 'summary_create', 'execution_owner': 'parent'},
            {'method': 'POST', 'path': f"/api/patients/{inventory['patient_id']}/summaries", 'payload_schema': 'SummaryInput', 'requires_write_intent_headers': ['X-Write-Intent', 'Idempotency-Key', 'X-Intent-Generation'], 'execution_owner': 'parent'},
            {'method': 'GET', 'path': '/api/summaries/{summary_id}', 'audience': 'family'},
        ],
        'summary_payload_fields': ['from_date', 'to_date', 'scope_timezone', 'include_unknown_time', 'record_kinds', 'include_source_status'],
        'notification_state_requirements': ['accepted has provider evidence', 'delivered has verified provider receipt', 'read_at follows recipient acknowledgement'],
        'missing_prerequisites': missing,
        'observed_object_counts': inventory['counts'],
        'clinical_results_created': 0,
        'alerts_created': 0,
        'notification_sends': 0,
        'external_calls': 0,
    }


def json_default(value):
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def main():
    parser = argparse.ArgumentParser(description='Read-only T29 simulation workflow input inventory and request plan')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report_path = args.report.resolve()
    require(report_path.is_relative_to((ROOT / '.planning-work' / 'parallel' / 'window1').resolve()),
        'Report must remain within the window1 planning directory.')
    inventory = patient_inventory()
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'inventory': inventory, 'workflow_plan': workflow_plan(inventory)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_default) + '\n', encoding='utf-8')
    reread = json.loads(report_path.read_text(encoding='utf-8'))
    require(reread == report, 'Saved workflow report did not match its readback.')
    print(json.dumps({'status': report['workflow_plan']['status'], 'patient_id': inventory['patient_id'],
        'counts': inventory['counts'], 'missing_prerequisites': report['workflow_plan']['missing_prerequisites'],
        'report_path': str(report_path), 'saved_and_read': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
