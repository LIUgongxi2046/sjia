from decimal import Decimal
import json

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import AwareDatetime, TypeAdapter

from archives.inputs import PROTOCOL, REGISTRY
from authorization.policy import matching_case_grants
from authorization.models import AccessGrant, ServiceRoleGrant
from case_details.evaluation_datasets import checked_dataset, current_dataset
from case_details.evaluation_evidence import lock_evaluation_actor
from case_details.evaluation_units import EvaluationUnitsRejected, UNIT_NAMES
from case_details.evaluation_views import evaluation_runtime
from configurations.evidence import deployed_artifact_checksum
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from identity.models import Patient


RUN_INPUT = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/EvaluationRunInput'},
    registry=REGISTRY, format_checker=FormatChecker())
TIMESTAMP = TypeAdapter(AwareDatetime)


def run_input(payload):
    if not RUN_INPUT.is_valid(payload):
        raise EvaluationUnitsRejected('DATASET_RUN_INPUT_INVALID')
    if int(payload['expected_version']) > 9223372036854775807:
        raise EvaluationUnitsRejected('DATASET_VERSION_INVALID')
    if not payload['config_ids'] or len(set(payload['config_ids'])) != len(payload['config_ids']):
        raise EvaluationUnitsRejected('DATASET_RUN_CONFIGURATIONS_REQUIRED')
    return payload


def partition_units(manifest, patient_ids):
    patients = set(patient_ids)
    if not patients or not patients <= {item.id for item in manifest.entries('patients')}:
        raise EvaluationUnitsRejected('DATASET_RUN_PARTITION_INVALID')
    sources = {item.source_id for item in manifest.entries('files') if item.patient_id in patients}
    selected = {}
    for name in UNIT_NAMES:
        items = manifest.entries(name)
        if name == 'patients':
            selected[name] = [item for item in items if item.id in patients]
        elif name == 'pages':
            selected[name] = [item for item in items if item.source_id in sources]
        else:
            selected[name] = [item for item in items if item.patient_id in patients]
    return selected


def partition_denominators(manifest, objects, patient_ids):
    selected = partition_units(manifest, patient_ids)
    counts = {name: None for name in objects['counts']}
    for name in UNIT_NAMES:
        coverage = getattr(manifest, name)
        if coverage is None or coverage.state != 'complete':
            continue
        if name == 'labels':
            counts['confirmed_labels'] = sum(item.label_state != 'unknown' for item in selected[name])
            counts['unknown_labels'] = sum(item.label_state == 'unknown' for item in selected[name])
        else:
            counts[name] = len(selected[name])
    if counts['observation_periods'] is not None:
        intervals = {item['id']: item['interval'] for item in objects['objects']['observation_periods']}
        patient_intervals, precise = {}, True
        for item in selected['observation_periods']:
            if str(item.id) not in intervals:
                raise EvaluationUnitsRejected('DATASET_RUN_OBSERVATION_PROOF_REQUIRED')
            interval = intervals[str(item.id)]
            if interval is None:
                precise = False
                continue
            start, end = [TIMESTAMP.validate_python(value) for value in interval]
            if start >= end:
                raise EvaluationUnitsRejected('DATASET_RUN_OBSERVATION_INVALID')
            patient_intervals.setdefault(item.patient_id, []).append((start, end))
        if precise:
            microseconds = 0
            for intervals in patient_intervals.values():
                previous_end = None
                for start, end in sorted(intervals):
                    if previous_end is not None and start < previous_end:
                        raise EvaluationUnitsRejected('UNIT_OBSERVATION_OVERLAP')
                    duration = end - start
                    microseconds += (duration.days * 86400 + duration.seconds) * 1000000 + duration.microseconds
                    previous_end = end
            counts['observation_person_hours'] = str(Decimal(microseconds) / Decimal(3600000000))
    return {'counts': counts,
        'patient_ids': sorted(str(value) for value in patient_ids),
        'unit_ids': {name: sorted(str(item.id) for item in selected[name]) for name in UNIT_NAMES},
        'eligible_label_ids': sorted(str(item.id) for item in selected['labels'] if item.label_state != 'unknown'),
        'excluded_unknown_label_ids': sorted(str(item.id) for item in selected['labels'] if item.label_state == 'unknown'),
        'clinical_qualification': 'not_evaluated'}


def qualified_run_configurations(config_ids, environment, cutoff):
    rows = list(ConfigVersion.objects.select_for_update().filter(id__in=config_ids).order_by('id'))
    if {str(item.id) for item in rows} != set(config_ids):
        raise EvaluationUnitsRejected('DATASET_RUN_CONFIGURATIONS_REQUIRED')
    keys, proofs = set(), []
    for row in rows:
        if row.config_key in keys or row.created_at > cutoff:
            raise EvaluationUnitsRejected('DATASET_RUN_CONFIGURATION_SCOPE_INVALID')
        keys.add(row.config_key)
        approval = ConfigApproval.objects.select_for_update().filter(config=row, revoked_at__isnull=True,
            approved_at__lte=cutoff, effective_scope__environment=environment,
            effective_scope__config_checksum=row.checksum, effective_scope__audience__contains=['support']).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gt=timezone.now())).order_by('-approved_at', '-id').first()
        validation = ConfigValidation.objects.select_for_update().filter(config=row, environment_id=environment,
            config_checksum=row.checksum, outcome='pass', revoked_at__isnull=True, executed_at__lte=cutoff,
            artifact_sha256=deployed_artifact_checksum(row)).order_by('-executed_at', '-id').first()
        if approval is None or validation is None:
            raise EvaluationUnitsRejected('DATASET_RUN_CONFIGURATION_SCOPE_INVALID')
        for point in (cutoff, timezone.now()):
            reason = configuration_qualification_reason(row, environment, now=point, audience='support', allow_historical=True,
                approval_id=approval.id, validation_id=validation.id)
            if reason:
                raise EvaluationUnitsRejected(reason)
        proofs.append({'id': str(row.id), 'config_key': row.config_key, 'kind': row.kind,
            'version': str(row.version), 'schema_version': row.schema_version, 'sha256': row.checksum,
            'approval_id': str(approval.id), 'approved_at': approval.approved_at.isoformat(),
            'validation_id': str(validation.id), 'validated_at': validation.executed_at.isoformat()})
    return proofs


def check_run_configuration_proofs(snapshot, cutoff):
    proofs = snapshot['configurations']
    if {item['id'] for item in proofs} != set(snapshot['input']['config_ids']) or len(proofs) != len(snapshot['input']['config_ids']):
        raise EvaluationUnitsRejected('DATASET_RUN_CONFIGURATION_SCOPE_INVALID')
    for proof in proofs:
        row = ConfigVersion.objects.select_for_update().get(id=proof['id'])
        approval = ConfigApproval.objects.select_for_update().get(id=proof['approval_id'])
        validation = ConfigValidation.objects.select_for_update().get(id=proof['validation_id'])
        if (row.config_key != proof['config_key'] or row.kind != proof['kind'] or str(row.version) != proof['version']
                or row.schema_version != proof['schema_version'] or row.checksum != proof['sha256'] or row.created_at > cutoff
                or approval.config_id != row.id or approval.approved_at.isoformat() != proof['approved_at'] or approval.approved_at > cutoff
                or validation.config_id != row.id or validation.executed_at.isoformat() != proof['validated_at'] or validation.executed_at > cutoff
                or (row.schema_version != 'purpose-processing-2' and validation.artifact_sha256 != snapshot['implementation_sha256'])):
            raise EvaluationUnitsRejected('DATASET_RUN_CONFIGURATION_SCOPE_INVALID')
        reason = configuration_qualification_reason(row, snapshot['environment'], audience='support', allow_historical=True,
            approval_id=approval.id, validation_id=validation.id, artifact_sha256=validation.artifact_sha256)
        if reason:
            raise EvaluationUnitsRejected(reason)


@transaction.atomic
def lock_run_inputs(*, dataset_id, payload, actor, auth_epoch, action='execute_assessment', check_code=True):
    data = run_input(payload)
    actor, gate, grant = lock_evaluation_actor(actor, auth_epoch, action=action)
    locked_grant = ServiceRoleGrant.objects.select_for_update().get(id=grant.id)
    actor, gate, grant = lock_evaluation_actor(actor, auth_epoch, action=action)
    if grant.id != locked_grant.id:
        raise EvaluationUnitsRejected('DATASET_RUN_AUTHORIZATION_CHANGED')
    row = current_dataset(dataset_id, version=int(data['expected_version']))
    if row.status != 'frozen' or row.frozen_at is None:
        raise EvaluationUnitsRejected('DATASET_RUN_FROZEN_VERSION_REQUIRED')
    sources, objects, partitions, labels = checked_dataset(row, actor, auth_epoch, action=action)
    if data['label_version_ref'] != row.coverage['input']['label_version_ref']:
        raise EvaluationUnitsRejected('DATASET_RUN_LABEL_VERSION_MISMATCH')
    patient_ids = {item.patient_id for item in partitions if item.partition == data['partition']}
    denominators = partition_denominators(sources.manifest, objects, patient_ids)
    if {item.patient_id for item in labels} - {item.patient_id for item in partitions}:
        raise EvaluationUnitsRejected('DATASET_RUN_LABEL_SCOPE_INVALID')
    permissions = []
    for patient in Patient.objects.select_for_update().filter(id__in=patient_ids).order_by('id'):
        grants = [item for item in matching_case_grants(actor, patient, action, timezone.now(), whole_resource=True)
            if item.purpose == row.purpose and item.evidence_ref.strip()]
        if not grants:
            raise EvaluationUnitsRejected('DATASET_RUN_CASE_SCOPE_DENIED')
        locked_ids = {item.id for item in AccessGrant.objects.select_for_update().filter(id__in=[item.id for item in grants]).order_by('id')}
        current_grants = [item for item in matching_case_grants(actor, patient, action, timezone.now(), whole_resource=True)
            if item.id in locked_ids and item.purpose == row.purpose and item.evidence_ref.strip()]
        if not current_grants:
            raise EvaluationUnitsRejected('DATASET_RUN_CASE_SCOPE_DENIED')
        permissions.append({'patient_id': str(patient.id), 'grant_ids': sorted(str(item.id) for item in current_grants),
            'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch)})
    runtime = evaluation_runtime(gate)
    tasks = [item for item in runtime.payload['tasks'] if item['task_type'] == 'assessment']
    if len(tasks) != 1:
        raise EvaluationUnitsRejected('DATASET_RUN_RUNTIME_UNAVAILABLE')
    implementation = deployed_artifact_checksum(runtime)
    if check_code and data['code_version'] != implementation:
        raise EvaluationUnitsRejected('DATASET_RUN_IMPLEMENTATION_CHANGED')
    configurations = qualified_run_configurations(data['config_ids'], row.environment, row.frozen_at)
    snapshot = {'schema_version': 'evaluation-run-inputs-1', 'dataset_id': str(row.dataset_id),
        'dataset_version_id': str(row.id), 'dataset_version': str(row.version), 'dataset_checksum': row.checksum,
        'input': data, 'source_snapshot': row.coverage['snapshot'], 'configurations': configurations,
        'denominators': denominators, 'available_before': sources.protocol.available_before.isoformat(),
        'implementation_sha256': data['code_version'], 'environment': row.environment,
        'purpose': row.purpose, 'mode': 'replay'}
    if data.get('clinical_capture_ref') is not None:
        from case_details.clinical_adoption import adopt_clinical_capture, clinical_admission_binding
        adoption = adopt_clinical_capture(row=row, sources=sources, snapshot=snapshot, capture_ref=data['clinical_capture_ref'])
        snapshot['clinical_capture_binding'] = clinical_admission_binding(adoption)
    if len(json.dumps(snapshot, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise EvaluationUnitsRejected('DATASET_RUN_CONTEXT_LIMIT_EXCEEDED')
    context = {'actor_id': str(actor.id), 'auth_epoch': str(actor.auth_epoch), 'gate_version': str(gate.version),
        'service_grant_id': str(grant.id), 'case_permissions': permissions,
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'source_proofs_sha256': compute_checksum(sources.proofs), 'snapshot_sha256': compute_checksum(snapshot)}
    return row, sources, runtime, tasks[0], snapshot, context
