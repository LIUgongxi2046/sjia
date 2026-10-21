from dataclasses import dataclass
from graphlib import TopologicalSorter
from itertools import combinations
import json

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from archives.models import ArchiveFactVersion, ArchiveRelationVersion
from archives.queries import fact_source
from archives.sampling import sampling_groups
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualificationRejected, lock_case_permissions
from authorization.policy import check_service_grant
from configurations.evidence import inspect_approved_evidence
from records.models import RecordField, RecordVersion
from identity.models import Principal
from verification.models import Verification, VerificationField


@dataclass(frozen=True)
class SamplingRelation:
    reference: dict
    left_id: str
    right_id: str
    relation_type: str
    reasons: tuple[str, ...]
    proof_refs: tuple[dict, ...] = ()


@dataclass(frozen=True)
class VariableSelection:
    selected: object | None
    exclusions: tuple[dict, ...]
    reasons: tuple[str, ...]
    relations: tuple[dict, ...]


def strictly_before(left, right):
    return left.upper < right.lower or left.upper == right.lower and not left.upper_inclusive


def selection_exclusion(value, reasons):
    return {'field_id': value.reference['field_id'], 'fact_version_id': value.reference['fact_version_id'],
        'reason_codes': list(dict.fromkeys(reasons))}


@transaction.atomic
def qualified_relations(*, facts, patient, policy_config, policy, environment, known_at, mode, maximum_bytes):
    identities = {item.reference['fact_version_id']: item for item in facts}
    query = ArchiveRelationVersion.objects.select_for_update().filter(patient_id=patient.id,
        created_at__lte=known_at).filter(Q(left_fact_version_id__in=identities) | Q(right_fact_version_id__in=identities))
    latest = {}
    consumed = 0
    for row in query.order_by('relation_id', '-version').iterator():
        consumed += len(json.dumps(entity_reference(row), separators=(',', ':')).encode('utf-8'))
        if consumed > maximum_bytes:
            raise QualificationRejected('ASSESSMENT_RELATION_LIMIT_EXCEEDED')
        if row.relation_id not in latest:
            latest[row.relation_id] = row
    result = []
    for row in latest.values():
        newer = ArchiveRelationVersion.objects.filter(patient_id=patient.id, relation_id=row.relation_id, version__gt=row.version)
        if mode == 'current' and newer.exists():
            raise QualificationRejected('ASSESSMENT_RELATION_CHANGED')
        if row.relation_type not in ('same_sampling', 'independent_sampling', 'formal_correction') or row.status == 'released':
            continue
        reasons, proof_refs = [], []
        left, right = identities.get(str(row.left_fact_version_id)), identities.get(str(row.right_fact_version_id))
        if row.status != 'confirmed':
            reasons.append('SAMPLING_RELATION_NOT_CONFIRMED')
        if row.verification_id is None:
            reasons.append('SAMPLING_RELATION_VERIFICATION_REQUIRED')
        corrected_source = None
        if left is None and right is not None and row.relation_type == 'formal_correction':
            corrected_source = ArchiveFactVersion.objects.select_for_update().get(id=row.left_fact_version_id, patient_id=patient.id)
            corrected_field, corrected_version = fact_source(corrected_source, patient)
            current_fact = ArchiveFactVersion.objects.get(id=row.right_fact_version_id, patient_id=patient.id)
            current_field, current_version = fact_source(current_fact, patient)
            if corrected_version.record_id != current_version.record_id or corrected_version.version >= current_version.version or corrected_field.field_code != current_field.field_code:
                reasons.append('SAMPLING_RELATION_CORRECTION_INVALID')
            proof_refs.extend(entity_reference(value) for value in (corrected_source, corrected_field, corrected_version))
        if (left is None and corrected_source is None) or right is None:
            reasons.append('SAMPLING_RELATION_INPUT_INCOMPLETE')
        if not reasons:
            proof = Verification.objects.select_for_update().get(id=row.verification_id, patient_id=patient.id)
            verifier = Principal.objects.select_for_update().get(id=proof.verifier_id)
            proof_refs.extend(entity_reference(value) for value in (proof, verifier))
            if (proof.policy_id != policy_config.id or proof.purpose != policy.purpose or proof.revoked_at is not None
                    or proof.verified_at > known_at or proof.evidence_ref != row.evidence_ref
                    or verifier.account_status != 'active' or row.author_id != verifier.id):
                reasons.append('SAMPLING_RELATION_VERIFICATION_SCOPE_CHANGED')
            evidence, _, reason = inspect_approved_evidence(row.evidence_ref,
                purpose=policy.verification_evidence_purpose, lock=True)
            if reason:
                reasons.append(reason)
            elif (evidence.captured_at > known_at or policy.purpose not in evidence.allowed_use
                    or proof.evidence_source_id != evidence.id):
                reasons.append('SAMPLING_RELATION_EVIDENCE_UNAVAILABLE')
            if reason is None:
                proof_refs.extend(entity_reference(value) for value in (evidence, evidence.source, evidence.retention_policy))
            field_ids = [left.reference['field_id'] if left else str(corrected_source.field_id), right.reference['field_id']]
            proof_fields = VerificationField.objects.select_for_update().filter(verification_id=proof.id,
                patient_id=patient.id, field_id__in=field_ids, result='consistent')
            if not proof_fields.exists():
                reasons.append('SAMPLING_RELATION_VERIFICATION_SCOPE_CHANGED')
            proof_refs.extend(entity_reference(value) for value in proof_fields.order_by('id'))
            for point in (proof.verified_at, timezone.now()):
                proof_refs.append(entity_reference(check_service_grant(verifier, 'verify_field', environment, point)))
            for field_id in field_ids:
                field = RecordField.objects.select_for_update().get(id=field_id, patient_id=patient.id)
                version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient_id=patient.id)
                for point in (proof.verified_at, timezone.now()):
                    proof_refs.extend(entity_reference(grant) for grant in lock_case_permissions(verifier, patient, 'verify_field', policy.purpose, point,
                        record_id=version.record_id, author_id=version.author_id, field_codes=[field.field_code]))
        result.append(SamplingRelation(entity_reference(row), str(row.left_fact_version_id), str(row.right_fact_version_id),
            row.relation_type, tuple(dict.fromkeys(reasons)),
            tuple({(item['model'], item['id']): item for item in proof_refs}.values())))
    return tuple(result)


def independent_samples(facts, relations):
    identities = [item.reference['fact_version_id'] for item in facts]
    if len(set(identities)) != len(identities):
        raise QualificationRejected('ASSESSMENT_CANDIDATE_DUPLICATE')
    exclusions = [selection_exclusion(item, item.reasons) for item in facts if not item.eligible]
    available = {item.reference['fact_version_id']: item for item in facts if item.eligible}
    if any(item.interval is None or item.value is None
            or item.reference['verification_id'] is None and not item.simulation_qualified for item in available.values()):
        raise QualificationRejected('ASSESSMENT_QUALIFIED_INPUT_INCOMPLETE')
    related_refs = tuple(item.reference for item in relations)
    blocked = {}
    corrections = {}
    for relation in relations:
        if relation.reasons:
            for identity in (relation.left_id, relation.right_id):
                if identity in available:
                    blocked.setdefault(identity, []).extend(relation.reasons)
            continue
        if relation.left_id not in available or relation.right_id not in available:
            if relation.relation_type == 'formal_correction' and relation.right_id in available:
                continue
            for identity in (relation.left_id, relation.right_id):
                if identity in available:
                    blocked.setdefault(identity, []).append('SAMPLING_RELATION_INPUT_INELIGIBLE')
            continue
        left, right = available[relation.left_id], available[relation.right_id]
        if relation.relation_type == 'formal_correction':
            corrections.setdefault(relation.right_id, set()).add(relation.left_id)
    tuple(TopologicalSorter(corrections).static_order())
    replaced = {identity for parents in corrections.values() for identity in parents}
    for relation in relations:
        if (relation.reasons or relation.relation_type != 'same_sampling'
                or relation.left_id not in available or relation.right_id not in available
                or relation.left_id in replaced or relation.right_id in replaced):
            continue
        left, right = available[relation.left_id], available[relation.right_id]
        if (left.interval != right.interval or left.value != right.value
                or left.reference['qualifier'] != right.reference['qualifier']):
            for identity in (relation.left_id, relation.right_id):
                blocked.setdefault(identity, []).append('SAME_SAMPLING_VALUE_CONFLICT')
    for identity in sorted(blocked.keys() | replaced):
        reasons = blocked.get(identity, []) + (['FORMAL_CORRECTION_REPLACED'] if identity in replaced else [])
        exclusions.append(selection_exclusion(available.pop(identity), reasons))
    pairs = [(relation.left_id, relation.right_id) for relation in relations if not relation.reasons
        and relation.relation_type == 'same_sampling' and relation.left_id in available and relation.right_id in available]
    groups = sampling_groups(available, pairs)
    conflicts = set()
    for relation in relations:
        if not relation.reasons and relation.relation_type == 'independent_sampling':
            for group in groups:
                if relation.left_id in group and relation.right_id in group:
                    conflicts.update(group)
    for identity in sorted(conflicts):
        exclusions.append(selection_exclusion(available.pop(identity), ['SAMPLING_RELATION_CONFLICT']))
    for group in groups:
        remaining = sorted(group - conflicts)
        for duplicate in remaining[1:]:
            exclusions.append(selection_exclusion(available.pop(duplicate), ['SAME_SAMPLING_DUPLICATE']))
    return available, exclusions, related_refs


def select_current(facts, relations, window, *, explicit_fact_id=None):
    available, exclusions, related_refs = independent_samples(facts, relations)
    if not available:
        return VariableSelection(None, tuple(exclusions), ('NO_ELIGIBLE_FACT',), related_refs)
    if window['current_selection'] == 'explicit_clinical_selection':
        if explicit_fact_id is None:
            return VariableSelection(None, tuple(exclusions), ('EXPLICIT_CLINICAL_SELECTION_REQUIRED',), related_refs)
        if explicit_fact_id not in available:
            raise QualificationRejected('ASSESSMENT_EXPLICIT_SELECTION_INELIGIBLE')
        selected = available[explicit_fact_id]
    elif window['current_selection'] == 'latest_eligible_sample':
        if explicit_fact_id is not None:
            raise QualificationRejected('ASSESSMENT_EXPLICIT_SELECTION_NOT_APPROVED')
        latest = [value for value in available.values()
            if all(other is value or strictly_before(other.interval, value.interval) for other in available.values())]
        if len(latest) != 1:
            for value in available.values():
                exclusions.append(selection_exclusion(value, ['SAMPLING_ORDER_OR_CONFLICT_UNRESOLVED']))
            return VariableSelection(None, tuple(exclusions), ('SAMPLING_ORDER_OR_CONFLICT_UNRESOLVED',), related_refs)
        selected = latest[0]
    else:
        raise QualificationRejected('ASSESSMENT_SELECTION_POLICY_UNSUPPORTED')
    for identity, value in available.items():
        if identity != selected.reference['fact_version_id']:
            exclusions.append(selection_exclusion(value, ['ELIGIBLE_SAMPLE_NOT_SELECTED']))
    return VariableSelection(selected, tuple(exclusions), (), related_refs)


def cross_analyte_reason(selections, maximum_gap_seconds):
    intervals = [item.selected.interval for item in selections.values() if item.selected is not None]
    if len(intervals) < 2:
        return None
    for left, right in combinations(intervals, 2):
        largest_gap = max(abs(left.upper - right.lower), abs(right.upper - left.lower))
        if largest_gap.total_seconds() > maximum_gap_seconds:
            return 'CROSS_ANALYTE_TIME_GAP_EXCEEDED'
    return None
