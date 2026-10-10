import copy
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import AwareDatetime, TypeAdapter

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import EvaluationDatasetVersion, EvaluationLabel, EvaluationPatientPartition
from authorization.models import AccessGrant
from authorization.policy import check_service_grant, matching_case_grants
from case_details.evaluation_evidence import approved_evaluation_artifact, artifact_proof, dataset_input, lock_evaluation_actor, lock_evaluation_sources
from case_details.evaluation_objects import lock_evaluation_objects
from case_details.evaluation_protocol import DatasetFreezeEvidence, DatasetPartitionEvidence
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.service import compute_checksum
from files.models import FileObject
from files.purpose import lock_use_authorization
from identity.models import Patient, Principal


VALIDATORS = {name: Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, registry=REGISTRY,
    format_checker=FormatChecker()) for name in ('DatasetFreezeInput', 'PartitionInput', 'LabelInput', 'DatasetQuery')}
TIMESTAMP = TypeAdapter(AwareDatetime)


def operation_input(operation, payload):
    if operation == 'dataset_create':
        result = dataset_input(payload)
        if 'dataset_id' not in result or 'expected_version' not in result:
            raise EvaluationUnitsRejected('DATASET_VERSION_IDENTITY_REQUIRED')
        if (result['dataset_id'] is None) != (result['expected_version'] is None):
            raise EvaluationUnitsRejected('DATASET_VERSION_IDENTITY_REQUIRED')
        if result['expected_version'] is not None and int(result['expected_version']) > 9223372036854775807:
            raise EvaluationUnitsRejected('DATASET_VERSION_INVALID')
        return result
    names = {'dataset_freeze': 'DatasetFreezeInput', 'dataset_partition': 'PartitionInput',
        'dataset_label': 'LabelInput', 'dataset_search': 'DatasetQuery'}
    if operation not in names or not VALIDATORS[names[operation]].is_valid(payload):
        raise EvaluationUnitsRejected('DATASET_INPUT_INVALID')
    if 'expected_version' in payload and int(payload['expected_version']) > 9223372036854775807:
        raise EvaluationUnitsRejected('DATASET_VERSION_INVALID')
    return payload


def dataset_checksum(row):
    return compute_checksum({'id': str(row.id), 'dataset_id': str(row.dataset_id), 'version': str(row.version),
        'purpose': row.purpose, 'environment': row.environment, 'protocol_ref': row.protocol_ref,
        'created_by': str(row.created_by), 'created_at': row.created_at.isoformat(),
        'frozen_at': row.frozen_at.isoformat() if row.frozen_at else None,
        'status': row.status, 'coverage': row.coverage})


def current_dataset(dataset_id, *, version=None):
    rows = EvaluationDatasetVersion.objects.select_for_update().filter(dataset_id=dataset_id,
        environment=settings.SJIA_ENVIRONMENT)
    if version is not None:
        rows = rows.filter(version=version)
    row = rows.order_by('-version').first()
    if row is None:
        raise EvaluationUnitsRejected('DATASET_NOT_FOUND')
    if row.status == 'revoked' or dataset_checksum(row) != row.checksum:
        raise EvaluationUnitsRejected('DATASET_UNAVAILABLE')
    return row


def source_snapshot(sources, objects):
    artifacts = [sources.proofs[name] for name in ('protocol', 'unit_manifest', 'raw_manifest')]
    artifacts.extend(sources.proofs['measurement_plan'])
    artifacts.extend(sources.proofs['unit_evidence'])
    if sources.proofs['label_version'] is not None:
        artifacts.append(sources.proofs['label_version'])
    for name in ('sampling_events', 'mas_events'):
        for item in objects['objects'][name]:
            artifacts.append(item['proof'])
            if 'answer' in item['proof']:
                artifacts.append(item['proof']['answer'])
    for item in objects['objects']['labels']:
        if 'answer' in item:
            artifacts.append(item['answer'])
    evidence = sorted({(item['reference'], item['revision_id'], item['source_id'], item['sha256']) for item in artifacts})
    return {'protocol_source_id': sources.proofs['protocol']['source_id'],
        'source_manifest_sha256': sources.manifest.source_manifest_sha256,
        'unit_manifest_sha256': sources.proofs['unit_manifest']['sha256'],
        'protocol_sha256': sources.proofs['protocol']['sha256'],
        'label_sha256': sources.proofs['label_version']['sha256'] if sources.proofs['label_version'] else None,
        'object_content_sha256': objects['content_sha256'], 'evidence_content_sha256': compute_checksum(evidence)}


def checked_dataset(row, actor, auth_epoch, *, action='manage_data_use'):
    if (row.status == 'revoked' or row.environment != settings.SJIA_ENVIRONMENT
            or dataset_checksum(row) != row.checksum):
        raise EvaluationUnitsRejected('DATASET_UNAVAILABLE')
    sources = lock_evaluation_sources(payload=row.coverage['input'], actor=actor, auth_epoch=auth_epoch, action=action)
    objects = lock_evaluation_objects(sources=sources, actor=actor)
    if source_snapshot(sources, objects) != row.coverage['snapshot']:
        raise EvaluationUnitsRejected('DATASET_SOURCE_CHANGED')
    partitions = list(EvaluationPatientPartition.objects.select_for_update().filter(dataset_version_id=row.id).order_by('patient_id'))
    labels = list(EvaluationLabel.objects.select_for_update().filter(partition_id__in=[item.id for item in partitions]).order_by('patient_id', 'observation_period_id'))
    if (partition_projection(partitions) != row.coverage['partitions']
            or label_projection(labels) != row.coverage['labels']):
        raise EvaluationUnitsRejected('DATASET_CONTENT_CHANGED')
    if row.status == 'frozen':
        proof = row.coverage['freeze_proof']
        if not proof:
            raise EvaluationUnitsRejected('DATASET_FREEZE_EVIDENCE_REQUIRED')
        revision, content = approved_evaluation_artifact(proof['reference'], kind='evaluation_freeze',
            purpose=row.purpose, checksum=proof['sha256'])
        evidence = DatasetFreezeEvidence.model_validate_json(content)
        if (evidence.dataset_id != row.dataset_id or str(evidence.version_id) != proof['source_version_id']
                or evidence.version != proof['source_version'] or evidence.checksum != proof['source_checksum']
                or evidence.protocol_ref != row.protocol_ref):
            raise EvaluationUnitsRejected('DATASET_FREEZE_EVIDENCE_MISMATCH')
        approval_actor(evidence, revision, row.environment)
    for partition in partitions:
        ensure_partition_isolation(sources, partition.patient_id, partition.partition)
        bindings = [item for item in sources.manifest.entries('files') if item.patient_id == partition.patient_id]
        if not bindings:
            raise EvaluationUnitsRejected('DATASET_PARTITION_PATIENT_INVALID')
        references = {item['source_id']: item for item in partition.source_refs}
        if set(references) != {item.source_id for item in bindings}:
            raise EvaluationUnitsRejected('DATASET_PARTITION_SOURCE_CHANGED')
        if str(partition.use_authorization_id) not in {item['authorization_id'] for item in references.values()}:
            raise EvaluationUnitsRejected('DATASET_PARTITION_AUTHORIZATION_CHANGED')
        for binding in bindings:
            original = FileObject.objects.get(id=binding.id)
            authorization = lock_use_authorization(references[binding.source_id]['authorization_id'], original, row.purpose, row.environment)
            if not any(proof['use_authorization_id'] == str(authorization.id) and proof['source_id'] == binding.source_id
                    for proof in sources.proofs['files']['file_bindings']):
                raise EvaluationUnitsRejected('DATASET_PARTITION_AUTHORIZATION_CHANGED')
        evidence = next(iter(references.values()))['evidence']
        revision, content = approved_evaluation_artifact(evidence['reference'], kind='evaluation_partition', purpose=row.purpose,
            checksum=evidence['sha256'])
        partition_evidence(DatasetPartitionEvidence.model_validate_json(content), revision,
            row, sources, partition.patient_id, partition.partition,
            {UUID(item['authorization_id']) for item in references.values()})
    return sources, objects, partitions, labels


def ensure_partition_isolation(sources, patient_id, partition):
    versions = EvaluationDatasetVersion.objects.filter(
        coverage__snapshot__protocol_source_id=sources.proofs['protocol']['source_id']).values('id')
    if EvaluationPatientPartition.objects.filter(dataset_version_id__in=versions,
            patient_id=patient_id).exclude(partition=partition).exists():
        raise EvaluationUnitsRejected('DATASET_PATIENT_PARTITION_CONFLICT')


def approval_actor(evidence, revision, environment):
    actor = Principal.objects.select_for_update().get(id=evidence.confirmed_by)
    now = timezone.now()
    if actor.account_status != 'active' or evidence.confirmed_at > revision.captured_at or evidence.confirmed_at > now:
        raise EvaluationUnitsRejected('DATASET_APPROVAL_UNAVAILABLE')
    grant = check_service_grant(actor, 'manage_data_use', environment, evidence.confirmed_at)
    check_service_grant(actor, 'manage_data_use', environment, now)
    if grant.revoked_at is not None and grant.revoked_at <= now:
        raise EvaluationUnitsRejected('DATASET_APPROVAL_UNAVAILABLE')


def partition_evidence(evidence, revision, row, sources, patient_id, partition, authorization_ids):
    if (evidence.dataset_id != row.dataset_id or evidence.protocol_ref != row.protocol_ref
            or evidence.source_manifest_sha256 != sources.manifest.source_manifest_sha256
            or evidence.patient_id != patient_id or evidence.partition != partition
            or set(evidence.authorization_ids) != authorization_ids):
        raise EvaluationUnitsRejected('DATASET_PARTITION_EVIDENCE_MISMATCH')
    approval_actor(evidence, revision, row.environment)


def partition_projection(rows):
    return [{'partition_id': str(row.id), 'patient_id': str(row.patient_id), 'partition': row.partition,
        'authorization_id': str(row.use_authorization_id), 'source_refs': row.source_refs} for row in rows]


def label_projection(rows):
    return [{'label_id': str(row.id), 'patient_id': str(row.patient_id), 'partition_id': str(row.partition_id),
        'observation_period_id': str(row.observation_period_id), 'label_state': row.label_state,
        'reference_answer_ref': row.reference_answer_ref,
        'reference_recognition_at': row.reference_recognition_at.isoformat() if row.reference_recognition_at else None,
        'confirmed_by': str(row.confirmed_by) if row.confirmed_by else None,
        'confirmed_at': row.confirmed_at.isoformat() if row.confirmed_at else None,
        'limitations': row.limitations} for row in rows]


@transaction.atomic
def persist_version(*, actor, sources, objects, payload, previous=None, partitions=(), labels=(), frozen=False, freeze_proof=None):
    version = 1 if previous is None else previous.version + 1
    if version > 9223372036854775807:
        raise EvaluationUnitsRejected('DATASET_VERSION_INVALID')
    now = timezone.now()
    row = EvaluationDatasetVersion(id=uuid4(), dataset_id=uuid4() if previous is None else previous.dataset_id,
        version=version, purpose=sources.proofs['purpose'], environment=sources.proofs['environment'],
        created_by=actor.id, created_at=now, frozen_at=now if frozen else None,
        protocol_ref=payload['protocol_ref'], status='frozen' if frozen else 'draft')
    row.coverage = {'schema_version': 'evaluation-dataset-1', 'input': copy.deepcopy(payload),
        'snapshot': source_snapshot(sources, objects), 'source_proofs': sources.proofs,
        'object_proofs': objects, 'partitions': [], 'labels': [], 'freeze_proof': freeze_proof}
    row.checksum = dataset_checksum(row)
    row.save(force_insert=True)
    copied = {}
    for item in partitions:
        current = EvaluationPatientPartition.objects.create(patient_id=item.patient_id, dataset_version_id=row.id,
            partition=item.partition, use_authorization_id=item.use_authorization_id, source_refs=item.source_refs)
        copied[item.id] = current
    for item in labels:
        EvaluationLabel.objects.create(patient_id=item.patient_id, partition_id=copied[item.partition_id].id,
            observation_period_id=item.observation_period_id, label_state=item.label_state,
            reference_answer_ref=item.reference_answer_ref, reference_recognition_at=item.reference_recognition_at,
            confirmed_by=item.confirmed_by, confirmed_at=item.confirmed_at, limitations=item.limitations)
    saved_partitions = list(EvaluationPatientPartition.objects.filter(dataset_version_id=row.id).order_by('patient_id'))
    saved_labels = list(EvaluationLabel.objects.filter(partition_id__in=[item.id for item in saved_partitions])
        .order_by('patient_id', 'observation_period_id'))
    row.coverage['partitions'] = partition_projection(saved_partitions)
    row.coverage['labels'] = label_projection(saved_labels)
    row.checksum = dataset_checksum(row)
    row.save(update_fields=['coverage', 'checksum'])
    return row


@transaction.atomic
def create_dataset(*, payload, actor, auth_epoch):
    payload = operation_input('dataset_create', payload)
    sources = lock_evaluation_sources(payload=payload, actor=actor, auth_epoch=auth_epoch)
    objects = lock_evaluation_objects(sources=sources, actor=actor)
    previous = None
    if payload['dataset_id'] is not None:
        previous = current_dataset(payload['dataset_id'])
        checked_dataset(previous, actor, auth_epoch)
        if previous.version != int(payload['expected_version']):
            raise EvaluationUnitsRejected('VERSION_CONFLICT')
        if (sources.proofs['protocol']['source_id'] != previous.coverage['snapshot']['protocol_source_id']
                or sources.proofs['purpose'] != previous.purpose):
            raise EvaluationUnitsRejected('DATASET_PROTOCOL_CHANGED')
    return persist_version(actor=actor, sources=sources, objects=objects, payload=payload, previous=previous)


@transaction.atomic
def mutate_dataset(*, operation, dataset_id, payload, actor, auth_epoch):
    payload = operation_input(operation, payload)
    actor, gate, _ = lock_evaluation_actor(actor, auth_epoch)
    row = current_dataset(dataset_id)
    if row.version != int(payload['expected_version']):
        raise EvaluationUnitsRejected('VERSION_CONFLICT')
    if row.status != 'draft':
        raise EvaluationUnitsRejected('DATASET_FROZEN')
    sources, objects, partitions, labels = checked_dataset(row, actor, auth_epoch)
    freeze_proof = None
    if operation == 'dataset_partition':
        patient_id = UUID(payload['patient_id'])
        if patient_id not in {item.id for item in sources.manifest.entries('patients')}:
            raise EvaluationUnitsRejected('DATASET_PARTITION_PATIENT_INVALID')
        if any(item.patient_id == patient_id for item in partitions):
            raise EvaluationUnitsRejected('DATASET_PARTITION_ALREADY_RECORDED')
        ensure_partition_isolation(sources, patient_id, payload['partition'])
        revision, content = approved_evaluation_artifact(payload['evidence_ref'], kind='evaluation_partition', purpose=row.purpose)
        file_proofs = {item['source_id']: item for item in sources.proofs['files']['file_bindings']
            if item['patient_id'] == str(patient_id)}
        authorization_ids = {UUID(item['use_authorization_id']) for item in file_proofs.values()}
        if UUID(payload['authorization_id']) not in authorization_ids:
            raise EvaluationUnitsRejected('DATASET_PARTITION_AUTHORIZATION_CHANGED')
        partition_evidence(DatasetPartitionEvidence.model_validate_json(content), revision, row, sources,
            patient_id, payload['partition'], authorization_ids)
        refs = []
        for binding in sources.manifest.entries('files'):
            if binding.patient_id == patient_id:
                original = FileObject.objects.get(id=binding.id)
                auth = lock_use_authorization(file_proofs[binding.source_id]['use_authorization_id'], original, row.purpose, row.environment)
                refs.append({'source_id': binding.source_id, 'file_id': str(binding.id),
                    'authorization_id': str(auth.id), 'evidence': artifact_proof(payload['evidence_ref'], revision)})
        partitions.append(EvaluationPatientPartition(id=uuid4(), patient_id=patient_id, partition=payload['partition'],
            use_authorization_id=UUID(payload['authorization_id']), source_refs=refs))
        partitions.sort(key=lambda item: item.patient_id)
    elif operation == 'dataset_label':
        check_service_grant(actor, 'verify_field', gate.environment, timezone.now())
        patient_id, partition_id = UUID(payload['patient_id']), UUID(payload['partition_id'])
        patient = Patient.objects.get(id=patient_id)
        permissions = [grant for grant in matching_case_grants(actor, patient, 'verify_field', timezone.now(),
            whole_resource=True) if grant.purpose == row.purpose and grant.evidence_ref.strip()]
        if not permissions:
            raise EvaluationUnitsRejected('DATASET_LABEL_SCOPE_DENIED')
        list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in permissions]).order_by('id'))
        partition = next((item for item in partitions if item.id == partition_id and item.patient_id == patient_id), None)
        if partition is None:
            raise EvaluationUnitsRejected('DATASET_LABEL_PARTITION_INVALID')
        observation_id = UUID(payload['observation_period_id'])
        entry = next((item for item in sources.manifest.entries('labels') if item.patient_id == patient_id
            and item.observation_period_version_id == observation_id), None)
        if entry is None:
            raise EvaluationUnitsRejected('DATASET_LABEL_SOURCE_REQUIRED')
        if (entry.label_state != payload['label_state'] or entry.reference_answer_ref != payload['reference_answer_ref']
                or entry.limitations != payload['limitations']
                or entry.reference_recognition_at != (TIMESTAMP.validate_python(payload['reference_recognition_at'])
                    if payload['reference_recognition_at'] is not None else None)):
            raise EvaluationUnitsRejected('DATASET_LABEL_SOURCE_MISMATCH')
        if any(item.observation_period_id == observation_id for item in labels):
            raise EvaluationUnitsRejected('DATASET_LABEL_ALREADY_RECORDED')
        labels.append(EvaluationLabel(id=uuid4(), patient_id=patient_id, partition_id=partition_id,
            observation_period_id=observation_id, label_state=entry.label_state,
            reference_answer_ref=entry.reference_answer_ref, reference_recognition_at=entry.reference_recognition_at,
            confirmed_by=entry.confirmed_by, confirmed_at=entry.confirmed_at, limitations=entry.limitations))
        labels.sort(key=lambda item: (item.patient_id, item.observation_period_id))
    elif operation == 'dataset_freeze':
        if payload['checksum'] != row.checksum:
            raise EvaluationUnitsRejected('DATASET_CHECKSUM_MISMATCH')
        for name in sources.protocol.required_units:
            coverage = getattr(sources.manifest, name)
            if coverage is None or coverage.state != 'complete':
                raise EvaluationUnitsRejected('DATASET_COVERAGE_INCOMPLETE')
        if {item.patient_id for item in partitions} != {item.id for item in sources.manifest.entries('patients')}:
            raise EvaluationUnitsRejected('DATASET_PARTITION_INCOMPLETE')
        if {item.observation_period_id for item in labels} != {
                item.observation_period_version_id for item in sources.manifest.entries('labels')}:
            raise EvaluationUnitsRejected('DATASET_LABELS_INCOMPLETE')
        if sources.protocol.evaluation_kind == 'clinical' and objects['counts']['observation_person_hours'] is None:
            raise EvaluationUnitsRejected('DATASET_OBSERVATION_TIME_REQUIRED')
        revision, content = approved_evaluation_artifact(payload['evidence_ref'], kind='evaluation_freeze', purpose=row.purpose)
        evidence = DatasetFreezeEvidence.model_validate_json(content)
        if (evidence.dataset_id != row.dataset_id or evidence.version_id != row.id or int(evidence.version) != row.version
                or evidence.checksum != row.checksum or evidence.protocol_ref != row.protocol_ref):
            raise EvaluationUnitsRejected('DATASET_FREEZE_EVIDENCE_MISMATCH')
        approval_actor(evidence, revision, row.environment)
        freeze_proof = {**artifact_proof(payload['evidence_ref'], revision), 'source_version_id': str(row.id),
            'source_version': str(row.version), 'source_checksum': row.checksum, 'actor_id': str(actor.id)}
    else:
        raise EvaluationUnitsRejected('DATASET_OPERATION_INVALID')
    return persist_version(actor=actor, sources=sources, objects=objects, payload=row.coverage['input'], previous=row,
        partitions=partitions, labels=labels, frozen=operation == 'dataset_freeze', freeze_proof=freeze_proof)
