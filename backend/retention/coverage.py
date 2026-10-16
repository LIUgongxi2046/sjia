"""Metadata-only disposition coverage, never a deletion plan or approval.

The patient inventory includes scalar UUID/FK references and version counters,
not clinical values, JSON snapshots, file keys, credentials or evidence text.
Registered dependency edges are reported separately: their presence does not
prove that embedded JSON, provider storage, backups or account-wide data have
been inventoried. No retention clock, legal hold or destructive scope is inferred.

API integration: support-only GET, explicit patient and purpose; call
``disposition_coverage`` and retain the normal metadata-only HTTP audit boundary.
The caller must not feed this response into a disposition execution endpoint.
"""
import json
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from assessment.qualified_facts import lock_case_permissions
from authorization.policy import Denied, check_principal_session, check_service_grant
from configurations.evidence import evidence_reference
from configurations.service import compute_checksum
from dependencies.models import DependencyEdge, DependencyNode
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from retention.disposition import DispositionRejected
from retention.models import RetentionPolicy
from sources.operations import RetentionRules


SCHEMA_VERSION = 'disposition-coverage-1'
RESPONSE_SCHEMA = json.loads((Path(__file__).resolve().parents[2] /
    'backend-design/disposition-coverage.schema.json').read_text())
RESPONSE_VALIDATOR = Draft202012Validator(RESPONSE_SCHEMA, format_checker=FormatChecker())
MAX_OBJECTS = 1000
MAX_EDGES = 2000
PATIENT_APPS = frozenset({'records', 'medical_time', 'case_details', 'archives',
    'files', 'recognition', 'drafts', 'relations', 'authorization', 'verification',
    'assessment', 'alerting', 'summaries', 'notifications', 'requests', 'tasks',
    'audit', 'sources', 'retention'})
VERSION_FIELDS = ('version', 'current_version', 'status_version', 'generation',
    'lease_generation', 'object_version', 'recorded_at', 'created_at', 'generated_at')
# These models have no direct patient column. Their native FK supplies scope;
# account/session/global rows deliberately cannot enter through an actor ID.
INDIRECT_SCOPES = {
    'assessment.assessmentoutput': 'assessment__patient_id',
    'notifications.notificationreceipt': 'attempt__patient_id',
    'tasks.taskattempt': 'task__patient_id',
    'tasks.outboxevent': 'task__patient_id',
    'tasks.consumerreceipt': 'event__task__patient_id',
    'retention.dispositionitem': 'request__patient_id',
}
UNSUPPORTED = (
    ('record_versions_fields_and_case_domains', 'VERSIONED_DATABASE_DISPOSITION_CONTRACT_REQUIRED'),
    ('candidates_anchors_confirmations_and_model_payloads', 'CANDIDATE_ADOPTION_AND_EMBEDDED_PAYLOAD_INVENTORY_REQUIRED'),
    ('drafts_relations_and_patient_metadata', 'CATEGORY_RETENTION_AND_PRESERVATION_RULES_REQUIRED'),
    ('assessments_alerts_summaries_and_notifications', 'SNAPSHOT_AND_DELIVERY_HISTORY_DISPOSITION_REQUIRED'),
    ('audit_intents_requests_tasks_outbox_and_receipts', 'RETRY_DEDUP_AND_AUDIT_PRESERVATION_WINDOWS_REQUIRED'),
    ('account_identity_and_sessions', 'CROSS_PATIENT_ACCOUNT_SCOPE_AND_RETENTION_REQUIRED'),
    ('backups_archives_and_disaster_recovery_copies', 'BACKUP_CATALOG_VERSION_AND_RESTORE_PROPAGATION_REQUIRED'),
)


def capability_catalog(environment):
    """Implementation availability only; never assert target eligibility."""
    return {
        'registered_target_types': ['file_object', 'purpose_copy', 'provider_copy'],
        'executors': [
            {'target_type': 'file_object', 'availability': 'local_bytes_executor',
                'coverage': 'exact_file_bytes_only'},
            {'target_type': 'purpose_copy', 'availability': 'local_bytes_executor',
                'coverage': 'exact_derived_file_bytes_only'},
            {'target_type': 'provider_copy', 'availability': 'simulation_only',
                'environment_supported': environment == 'simulation',
                'coverage': 'SIMULATION_PROVIDER_only'},
        ],
        'unsupported_categories': [{'category': category, 'executor_registered': False,
            'required_contract': reason} for category, reason in UNSUPPORTED],
        'execution_prerequisites': [
            'EXACT_TARGET_VERSION_AND_CURRENT_SOURCE_AUTHORITY',
            'CURRENT_APPROVED_OBJECT_AND_RECEIPT_RETENTION_POLICIES',
            'EXPLICIT_SCOPE_AND_ACTION_TIME_DISPOSITION_APPROVAL',
            'RETENTION_CLOCK_AND_LEGAL_CLINICAL_HOLDS_RESOLVED',
            'DEPENDENCY_AND_EMBEDDED_PAYLOAD_CLOSURE_VERIFIED',
            'REGISTERED_RUNTIME_AND_EXACT_DELETION_QUERY_RECEIPT',
            'LIVE_PROVIDER_PROTOCOL_REQUIRED_OUTSIDE_SIMULATION',
            'BACKUP_EXPIRY_AND_RESTORE_REPLAY_CONTROLS',
        ],
    }


def inventory_models():
    """Only registered patient-scoped models; no broad account enumeration."""
    result = []
    for model in apps.get_models():
        label = model._meta.label_lower
        names = {field.attname for field in model._meta.concrete_fields}
        if model._meta.app_label not in PATIENT_APPS:
            continue
        if 'patient_id' in names:
            result.append((model, 'patient_id'))
        elif label in INDIRECT_SCOPES:
            result.append((model, INDIRECT_SCOPES[label]))
    return sorted(result, key=lambda pair: pair[0]._meta.label_lower)


def metadata_columns(model):
    """An allowlist built from field types, not arbitrary *_id/text names."""
    names = {field.attname for field in model._meta.concrete_fields}
    refs = {field.attname: (field.remote_field.model._meta.label_lower if field.is_relation else None)
        for field in model._meta.concrete_fields
        if field.get_internal_type() == 'UUIDField' or field.is_relation and
        field.target_field.get_internal_type() == 'UUIDField'}
    versions = [name for name in VERSION_FIELDS if name in names]
    return sorted(set(refs) | set(versions) | {model._meta.pk.attname}), refs, versions


def _scalar(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value) if value is not None else None


def object_metadata(model, row):
    columns, refs, versions = metadata_columns(model)
    # Consume only the declared projection even when a caller supplies extra keys.
    identity = str(row[model._meta.pk.attname])
    values = {name: _scalar(row[name]) for name in versions if row.get(name) is not None}
    return {'model': model._meta.label_lower, 'id': identity, 'versions': values,
        'version_binding': 'stored_metadata' if values else 'row_identity_only',
        'retention_policy_id': _scalar(row.get('retention_policy_id')),
        'references': [{'field': name, 'model': target, 'id': str(row[name])}
            for name, target in sorted(refs.items())
            if name != model._meta.pk.attname and row.get(name) is not None]}


def policy_metadata(policy, active_approvers, now):
    """Report configured metadata, not re-approve evidence or invent due dates."""
    evidence_id = evidence_reference(policy.evidence_ref)
    approval_current = bool(policy.status == 'active' and policy.approved_at is not None
        and policy.approved_at <= now and policy.approved_by_id in active_approvers and evidence_id)
    problems = []
    if not approval_current:
        problems.append('CURRENT_APPROVAL_METADATA_UNAVAILABLE')
    if policy.schema_version != 'retention-1':
        problems.append('RETENTION_SCHEMA_UNSUPPORTED')
    try:
        rules = RetentionRules.model_validate(policy.rules)
        if rules.disposition == 'delete' and rules.retention_days is None:
            problems.append('FINITE_RETENTION_PERIOD_UNRESOLVED')
        configured_disposition, days = rules.disposition, rules.retention_days
        local_types = list(rules.local_delete.target_types) if rules.local_delete else []
    except (ValidationError, TypeError, ValueError):
        problems.append('RETENTION_RULES_INVALID')
        configured_disposition, days, local_types = None, None, []
    return {'policy_id': str(policy.id), 'version': str(policy.version),
        'approval_metadata_current': approval_current, 'evidence_source_revision_id': _scalar(evidence_id),
        'evidence_qualification': 'not_checked', 'configured_disposition': configured_disposition,
        'configured_retention_days': days, 'registered_local_target_types': local_types,
        'retention_clock': 'unresolved_per_object', 'legal_clinical_hold': 'not_evaluated',
        'eligible_for_execution': False, 'unresolved_policy_inputs': problems}


def _dependency_manifest(patient_id, limit):
    rows = DependencyNode.objects.filter(patient_id=patient_id).order_by('id')
    total = rows.count()
    nodes = list(rows.values('id', 'resource_type', 'resource_id', 'resource_version', 'validity')[:limit])
    ids = [row['id'] for row in nodes]
    # No foreign patient's node/edge identifiers are ever returned. Global
    # prerequisites remain opaque registered references, never global payloads.
    edges = DependencyEdge.objects.filter(dependent_id__in=ids).filter(
        Q(prerequisite__patient_id=patient_id) | Q(prerequisite__patient_id__isnull=True)).order_by('id')
    edge_count = edges.count()
    selected = list(edges.values('id', 'dependent_id', 'prerequisite_id', 'edge_type',
        'prerequisite__patient_id', 'prerequisite__resource_type',
        'prerequisite__resource_id', 'prerequisite__resource_version')[:MAX_EDGES])
    hidden_edges = DependencyEdge.objects.filter(dependent_id__in=ids).exclude(
        Q(prerequisite__patient_id=patient_id) | Q(prerequisite__patient_id__isnull=True)).exists()
    return {'coverage': 'registered_edges_only', 'total_patient_nodes': total,
        'truncated': total > len(nodes) or edge_count > len(selected),
        'cross_patient_edge_rejected': hidden_edges,
        'nodes': [{key: _scalar(value) for key, value in row.items()} for row in nodes],
        'edges': [{'id': str(row['id']), 'dependent_node_id': str(row['dependent_id']),
            'prerequisite_node_id': str(row['prerequisite_id']), 'edge_type': row['edge_type'],
            'prerequisite': {'resource_type': row['prerequisite__resource_type'],
                'resource_id': str(row['prerequisite__resource_id']),
                'resource_version': row['prerequisite__resource_version'],
                'scope': 'shared' if row['prerequisite__patient_id'] is None else 'patient'}} for row in selected]}


@transaction.atomic
def disposition_coverage(*, actor, auth_epoch, audience, patient_id, purpose, environment, limit=500):
    """Inspect one fully authorized patient's metadata; do not change any row."""
    if audience != 'support':
        raise Denied('SUPPORT_AUDIENCE_REQUIRED')
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 128:
        raise DispositionRejected('COVERAGE_PURPOSE_REQUIRED')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_OBJECTS:
        raise DispositionRejected('COVERAGE_LIMIT_INVALID')
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    if not gate.enabled:
        raise Denied('ENVIRONMENT_DISABLED')
    patient = Patient.objects.select_for_update().get(id=patient_id)
    now = timezone.now()
    check_service_grant(actor, 'manage_disposition', environment, now)
    grants = lock_case_permissions(actor, patient, 'read_case', purpose, now, whole_resource=True)
    if not any(grant.record_scope == 'all' and not grant.field_codes for grant in grants):
        raise Denied('COVERAGE_WHOLE_PATIENT_SCOPE_REQUIRED')
    inventory, objects, policy_ids = [], [], set()
    for model, scope_field in inventory_models():
        query = model.objects.filter(**{scope_field: patient.id}).order_by(model._meta.pk.attname)
        total = query.count()
        remaining = max(0, limit - len(objects))
        rows = list(query.values(*metadata_columns(model)[0])[:remaining]) if remaining else []
        entries = [object_metadata(model, row) for row in rows]
        objects.extend(entries)
        policy_ids.update(UUID(row['retention_policy_id']) for row in entries if row['retention_policy_id'])
        inventory.append({'model': model._meta.label_lower, 'patient_scope_field': scope_field,
            'total_objects': total, 'included_objects': len(entries), 'truncated': total > len(entries)})
    policies = list(RetentionPolicy.objects.filter(id__in=policy_ids).order_by('id'))
    approvers = set(Principal.objects.filter(id__in=[row.approved_by_id for row in policies],
        account_status='active').values_list('id', flat=True))
    graph = _dependency_manifest(patient.id, limit)
    result = {'schema_version': SCHEMA_VERSION, 'patient_id': str(patient.id),
        'observed_at': now.isoformat(), 'purpose': purpose,
        'mode': 'read_only_inventory', 'whole_data_disposal_supported': False,
        'execution_authorized': False, 'scope_expansion_authorized': False,
        'snapshot_consistency': 'bounded_live_metadata_observation',
        'inventory_truncated': any(row['truncated'] for row in inventory),
        'catalog': capability_catalog(environment), 'inventory': inventory, 'objects': objects,
        'policies': [policy_metadata(row, approvers, now) for row in policies],
        'dependencies': graph,
        'unresolved_coverage': ['EMBEDDED_JSON_AND_FILE_BYTES_NOT_INSPECTED',
            'UNLINKED_OR_GLOBAL_OUTBOX_AND_RECEIPTS_NOT_ENUMERATED',
            'CROSS_PATIENT_IDENTITY_AND_SESSION_DATA_NOT_ENUMERATED',
            'LIVE_PROVIDER_STORAGE_NOT_INSPECTED', 'BACKUP_CATALOG_UNAVAILABLE',
            'REGISTERED_DEPENDENCIES_ARE_NOT_COMPLETE_DELETION_CLOSURE'],
        'unresolved_policy_ids': sorted(str(value) for value in policy_ids - {row.id for row in policies}),
    }
    # This fingerprint binds only the returned metadata, never clinical content.
    result['manifest_sha256'] = compute_checksum(result)
    if not RESPONSE_VALIDATOR.is_valid(result):
        raise DispositionRejected('COVERAGE_MANIFEST_INVALID')
    return result
