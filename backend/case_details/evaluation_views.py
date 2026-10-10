from django.core import signing
from django.db import transaction
from django.utils import timezone

from archives.models import EvaluationDatasetVersion
from authorization.models import ServiceRoleGrant
from authorization.policy import _active_at, matching_case_grants
from case_details.evaluation_datasets import checked_dataset, current_dataset, operation_input
from case_details.evaluation_evidence import lock_evaluation_actor
from case_details.evaluation_units import EvaluationUnitsRejected, UNIT_NAMES
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from files.models import FileObject
from files.purpose import current_file_grants
from identity.models import Patient


def evaluation_runtime(gate):
    config = ConfigVersion.objects.filter(id=gate.runtime_config_id, kind='provider', schema_version='backend-runtime-1').first()
    if config is None or configuration_qualification_reason(config, gate.environment, audience='support'):
        raise EvaluationUnitsRejected('DATASET_RUNTIME_UNAVAILABLE')
    return config


def can_confirm_labels(actor, sources):
    now = timezone.now()
    grant = ServiceRoleGrant.objects.filter(principal_id=actor.id, action='verify_field',
        environment=sources.proofs['environment']).order_by('-valid_from', '-id').first()
    if grant is None or not _active_at(grant, now) or not grant.evidence_ref.strip():
        return False
    for item in sources.manifest.entries('patients'):
        patient = Patient.objects.get(id=item.id)
        if not any(permission.purpose == sources.proofs['purpose'] and permission.evidence_ref.strip()
                for permission in matching_case_grants(actor, patient, 'verify_field', now, whole_resource=True)):
            return False
    return True


def can_execute_run(actor, row):
    if row.status != 'frozen' or row.coverage['input']['label_version_ref'] is None:
        return False
    now = timezone.now()
    grant = ServiceRoleGrant.objects.filter(principal_id=actor.id, action='execute_assessment',
        environment=row.environment).order_by('-valid_from', '-id').first()
    if grant is None or not _active_at(grant, now) or not grant.evidence_ref.strip():
        return False
    for partition in ('development', 'validation'):
        patients = {item['patient_id'] for item in row.coverage['partitions'] if item['partition'] == partition}
        rows = list(Patient.objects.filter(id__in=patients))
        if patients and len(rows) == len(patients) and all(any(permission.purpose == row.purpose and permission.evidence_ref.strip()
                for permission in matching_case_grants(actor, patient, 'execute_assessment', now, whole_resource=True))
                for patient in rows):
            return True
    return False


def dataset_view(row, sources, objects, actor, *, current=True):
    coverage = row.coverage
    counts = dict(objects['counts'])
    if len(coverage['labels']) != len(sources.manifest.entries('labels')):
        counts['confirmed_labels'], counts['unknown_labels'] = None, None
    actions = ['dataset_create'] if current else []
    if can_execute_run(actor, row):
        actions.append('dataset_run_create')
    if current and row.status == 'draft':
        if len(coverage['partitions']) < len(sources.manifest.entries('patients')):
            actions.append('dataset_partition')
        if len(coverage['labels']) < len(sources.manifest.entries('labels')) and can_confirm_labels(actor, sources):
            actions.append('dataset_label')
        if (len(coverage['partitions']) == len(sources.manifest.entries('patients'))
                and len(coverage['labels']) == len(sources.manifest.entries('labels'))
                and all(getattr(sources.manifest, name) is not None and getattr(sources.manifest, name).state == 'complete'
                    for name in sources.protocol.required_units)
                and (sources.protocol.evaluation_kind != 'clinical' or counts['observation_person_hours'] is not None)):
            actions.append('dataset_freeze')
    units = []
    for name in UNIT_NAMES:
        unit = getattr(sources.manifest, name)
        units.append({'unit': name, 'state': 'unrecorded' if unit is None else unit.state,
            'registered_items': None if unit is None else len(unit.items),
            'evidence_refs': [] if unit is None else unit.evidence_refs,
            'limitations': [] if unit is None else unit.limitations})
    return {'dataset_id': str(row.dataset_id), 'version_id': str(row.id), 'version': str(row.version),
        'protocol_ref': row.protocol_ref, 'source_manifest_ref': coverage['input']['source_manifest_ref'],
        'purpose_copy_ids': coverage['input']['purpose_copy_ids'], 'label_version_ref': coverage['input']['label_version_ref'],
        'status': row.status, 'checksum': row.checksum, 'frozen_at': row.frozen_at.isoformat() if row.frozen_at else None,
        'counts': counts, 'partitions': coverage['partitions'], 'labels': coverage['labels'],
        'coverage': {'evaluation_kind': sources.protocol.evaluation_kind, 'purpose': sources.protocol.purpose,
            'available_before': sources.protocol.available_before.isoformat(), 'required_units': sources.protocol.required_units,
            'units': units, 'unrecorded_units': [name for name, count in counts.items() if count is None],
            'clinical_qualification': 'not_evaluated'}, 'allowed_actions': actions}


@transaction.atomic
def read_dataset(*, dataset_id, actor, auth_epoch, version=None):
    actor, _, _ = lock_evaluation_actor(actor, auth_epoch)
    current = current_dataset(dataset_id)
    row = current if version is None else current_dataset(dataset_id, version=version)
    sources, objects, _, _ = checked_dataset(row, actor, auth_epoch)
    return dataset_view(row, sources, objects, actor, current=row.id == current.id)


def directory_visible(row, actor):
    now = timezone.now()
    bindings = row.coverage['source_proofs']['files']['file_bindings']
    if not bindings:
        return False
    for binding in bindings:
        original = FileObject.objects.filter(id=binding['file_id'], patient_id=binding['patient_id']).first()
        if original is None or not any(current_file_grants(original, actor, row.purpose, now)):
            return False
    return True


@transaction.atomic
def search_datasets(*, actor, auth_epoch, payload, expires_at):
    query = operation_input('dataset_search', payload)
    actor, gate, grant = lock_evaluation_actor(actor, auth_epoch)
    runtime = evaluation_runtime(gate)
    maximum = runtime.payload['api']['max_page_size']
    limit = query['limit'] if 'limit' in query else maximum
    if limit > maximum:
        raise EvaluationUnitsRejected('DATASET_QUERY_INVALID')
    rows = EvaluationDatasetVersion.objects.filter(environment=gate.environment).order_by('dataset_id', '-version')
    selected, seen = [], set()
    for row in rows:
        if row.dataset_id in seen:
            continue
        seen.add(row.dataset_id)
        if (('protocol_ref' in query and row.protocol_ref != query['protocol_ref'])
                or ('status' in query and row.status != query['status']) or row.status == 'revoked'):
            continue
        if not directory_visible(row, actor):
            continue
        current = current_dataset(row.dataset_id)
        sources, objects, _, _ = checked_dataset(current, actor, auth_epoch)
        selected.append(dataset_view(current, sources, objects, actor))
    scope = {'actor_id': str(actor.id), 'auth_epoch': str(actor.auth_epoch), 'gate_version': str(gate.version),
        'grant_id': str(grant.id), 'runtime_checksum': runtime.checksum, 'limit': limit,
        'filters': {name: value for name, value in query.items() if name != 'cursor'}, 'rows': selected}
    digest = compute_checksum(scope)
    now = timezone.now()
    if expires_at <= now:
        raise EvaluationUnitsRejected('CURSOR_STALE')
    offset, expiry = 0, expires_at.timestamp()
    if 'cursor' in query:
        cursor = signing.loads(query['cursor'], salt='evaluation-datasets')
        if cursor['snapshot'] != digest or cursor['expires_at'] <= now.timestamp():
            raise EvaluationUnitsRejected('CURSOR_STALE')
        offset, expiry = cursor['offset'], cursor['expires_at']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise EvaluationUnitsRejected('DATASET_QUERY_INVALID')
    return {'items': selected[offset:offset + limit], 'snapshot_version': digest,
        'next_cursor': signing.dumps({'snapshot': digest, 'offset': offset + limit, 'expires_at': expiry},
            salt='evaluation-datasets') if len(selected) > offset + limit else None}
