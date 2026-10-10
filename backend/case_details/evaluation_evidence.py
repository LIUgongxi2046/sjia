import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import PurposeCopy
from authorization.policy import check_principal_session, check_service_grant
from case_details.evaluation_bindings import lock_evaluation_file_bindings, required_file_bindings
from case_details.evaluation_inventory import SourceManifest
from case_details.evaluation_protocol import EvaluationLabels, EvaluationProtocol
from case_details.evaluation_units import EvaluationUnitManifest, EvaluationUnitsRejected, UNIT_NAMES
from configurations.evidence import evidence_reference, inspect_approved_evidence
from family_content.models import EnvironmentGate
from identity.models import Principal


DATASET_INPUT = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/DatasetInput'},
    registry=REGISTRY, format_checker=FormatChecker())


@dataclass(frozen=True)
class EvaluationSources:
    protocol: EvaluationProtocol
    manifest: EvaluationUnitManifest
    inventory: dict
    proofs: dict


def dataset_input(payload):
    if not DATASET_INPUT.is_valid(payload):
        raise EvaluationUnitsRejected('DATASET_INPUT_INVALID')
    if not payload['purpose_copy_ids'] or len(set(payload['purpose_copy_ids'])) != len(payload['purpose_copy_ids']):
        raise EvaluationUnitsRejected('DATASET_PURPOSE_COPIES_REQUIRED')
    for name in ('protocol_ref', 'source_manifest_ref', 'label_version_ref'):
        value = payload[name]
        if value is not None and evidence_reference(value) is None:
            raise EvaluationUnitsRejected('DATASET_EVIDENCE_REFERENCE_INVALID')
    return payload


def require_raw_manifest_reference(manifest):
    if evidence_reference(manifest.raw_source_manifest_ref) is None:
        raise EvaluationUnitsRejected('UNIT_RAW_MANIFEST_REFERENCE_REQUIRED')
    return manifest.raw_source_manifest_ref


def lock_evaluation_actor(actor, auth_epoch, *, action='manage_data_use'):
    environment = settings.SJIA_ENVIRONMENT
    gate = EnvironmentGate.objects.select_for_update().filter(environment=environment).first()
    if gate is None or not gate.enabled:
        raise EvaluationUnitsRejected('UNIT_ENVIRONMENT_UNAVAILABLE')
    current = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(current, auth_epoch)
    grant = check_service_grant(current, action, environment, timezone.now())
    return current, gate, grant


def approved_evaluation_artifact(reference, *, kind, purpose, checksum=None):
    revision, content, reason = inspect_approved_evidence(reference, purpose=kind,
        expected_checksum=checksum, lock=True)
    if reason:
        raise EvaluationUnitsRejected(reason)
    if purpose not in revision.allowed_use:
        raise EvaluationUnitsRejected('UNIT_EVIDENCE_PURPOSE_DENIED')
    return revision, content


def artifact_proof(reference, revision):
    return {'reference': reference, 'revision_id': str(revision.id),
        'source_id': str(revision.source_id), 'sha256': revision.checksum,
        'captured_at': revision.captured_at.isoformat(),
        'retention_policy_id': str(revision.retention_policy_id)}


@transaction.atomic
def lock_evaluation_sources(*, payload, actor, auth_epoch, action='manage_data_use'):
    payload = dataset_input(payload)
    actor, gate, grant = lock_evaluation_actor(actor, auth_epoch, action=action)
    copies = list(PurposeCopy.objects.filter(id__in=payload['purpose_copy_ids']).order_by('id'))
    if len(copies) != len(payload['purpose_copy_ids']):
        raise EvaluationUnitsRejected('UNIT_PURPOSE_COPY_UNAVAILABLE')
    purposes = {copy.purpose for copy in copies}
    if len(purposes) != 1 or any(copy.environment != gate.environment for copy in copies):
        raise EvaluationUnitsRejected('UNIT_PURPOSE_SCOPE_INVALID')
    purpose = next(iter(purposes))
    protocol, protocol_bytes = approved_evaluation_artifact(payload['protocol_ref'], kind='evaluation_protocol', purpose=purpose)
    plan = EvaluationProtocol.model_validate_json(protocol_bytes)
    if plan.purpose != purpose or plan.available_before > timezone.now():
        raise EvaluationUnitsRejected('UNIT_PROTOCOL_SCOPE_INVALID')
    plan_proofs = []
    for reference in sorted({plan.measurement_plan_ref, *plan.criteria_refs}):
        plan_revision, _ = approved_evaluation_artifact(reference, kind='evaluation_protocol', purpose=purpose)
        if plan_revision.captured_at > protocol.captured_at:
            raise EvaluationUnitsRejected('UNIT_PROTOCOL_EVIDENCE_CHANGED')
        plan_proofs.append(artifact_proof(reference, plan_revision))
    revision, content = approved_evaluation_artifact(payload['source_manifest_ref'],
        kind='evaluation_manifest', purpose=purpose)
    manifest = EvaluationUnitManifest.model_validate_json(content)
    raw_ref = require_raw_manifest_reference(manifest)
    raw_revision, raw_bytes = approved_evaluation_artifact(raw_ref,
        kind='evaluation_manifest', purpose=purpose, checksum=manifest.source_manifest_sha256)
    raw_manifest = SourceManifest.model_validate_json(raw_bytes)
    # 元数据清单不读取其中的路径；原件与用途副本由文件服务按当前许可读取。
    inventory = {'source_manifest_sha256': hashlib.sha256(raw_bytes).hexdigest(),
        'dataset_version': raw_manifest.dataset_version,
        'files': [{'source_id': item.id, 'sha256': item.sha256, 'size_bytes': item.size_bytes}
            for item in raw_manifest.sources]}
    bindings = required_file_bindings(inventory, manifest)
    if {str(item.purpose_copy_id) for item in bindings.values()} != set(payload['purpose_copy_ids']):
        raise EvaluationUnitsRejected('UNIT_PURPOSE_COPY_SET_MISMATCH')
    file_proof = lock_evaluation_file_bindings(inventory=inventory, manifest=manifest, actor=actor,
        auth_epoch=auth_epoch, purpose=purpose, target=payload['protocol_ref'], environment=gate.environment, action=action)
    references = set()
    for name in UNIT_NAMES:
        coverage = getattr(manifest, name)
        if coverage is not None:
            references.update(coverage.evidence_refs)
            for item in coverage.items:
                references.update(item.evidence_refs)
    evidence = []
    for reference in sorted(references):
        proof_revision, _ = approved_evaluation_artifact(reference, kind='evaluation_manifest', purpose=purpose)
        evidence.append(artifact_proof(reference, proof_revision))
    label_proof = None
    if payload['label_version_ref'] is None:
        if manifest.labels is not None:
            raise EvaluationUnitsRejected('UNIT_LABEL_VERSION_REQUIRED')
    else:
        label_revision, label_bytes = approved_evaluation_artifact(payload['label_version_ref'],
            kind='evaluation_labels', purpose=purpose)
        labels = EvaluationLabels.model_validate_json(label_bytes)
        if (labels.protocol_ref != payload['protocol_ref'] or labels.source_manifest_sha256 != manifest.source_manifest_sha256
                or labels.labels != manifest.labels):
            raise EvaluationUnitsRejected('UNIT_LABEL_VERSION_MISMATCH')
        label_proof = artifact_proof(payload['label_version_ref'], label_revision)
    proofs = {'protocol': artifact_proof(payload['protocol_ref'], protocol),
        'unit_manifest': artifact_proof(payload['source_manifest_ref'], revision),
        'raw_manifest': artifact_proof(raw_ref, raw_revision), 'unit_evidence': evidence,
        'measurement_plan': plan_proofs, 'label_version': label_proof,
        'files': file_proof, 'purpose': purpose, 'environment': gate.environment,
        'actor_id': str(actor.id), 'auth_epoch': str(actor.auth_epoch), 'service_grant_id': str(grant.id),
        'gate_version': str(gate.version), 'clinical_qualification': 'not_evaluated'}
    return EvaluationSources(protocol=plan, manifest=manifest, inventory=inventory, proofs=proofs)
