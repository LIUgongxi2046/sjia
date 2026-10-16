from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.base import ApiError, authorize_case
from archives.batches import batch_grants, batch_runtime
from archives.documents import document_record_scope, document_sources
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion, ArchiveFact, ArchiveFactVersion, ArchivePage, ArchiveRelationVersion, DeltaSummary, DeltaSummaryItem
from archives.queries import fact_source
from archives.search import document_scope, instant_bounds
from archives.sampling import sampling_groups
from assessment.policies import QualificationPolicy
from assessment.provenance import entity_reference
from assessment.qualified_facts import verification_proof
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from configurations.evidence import deployed_artifact_checksum
from dependencies.models import DependencyEdge, DependencyNode
from files.purpose_dependencies import connect, resource_node
from files.models import FileGroup
from files.purpose import checked_bytes, lock_original, lock_use_authorization
from medical_time.models import MedicalTime
from records.models import Record, RecordVersion
from records.service import medical_time_view
from relations.models import Relation
from summaries.delta_schemas import checked_delta
from summaries.jobs import lock_summary_scope
from summaries.service import current_summary_config
from tasks.models import Task, TaskAttempt
from tasks.service import acquire_lease, complete_task, create_attempt, finish_attempt, register_task
from verification.models import FieldVerificationState, Verification, VerificationField


DELTA_SCHEMA = 'archive-delta-1'
DELTA_OWNER = 'archive-delta-deterministic'


def delta_task_policy(runtime, *, required=True):
    """Select the explicit policy from an already-qualified runtime, never a default."""
    policies = [row for row in runtime.payload['tasks'] if row['task_type'] == 'summary']
    if len(policies) != 1:
        if required:
            raise ApiError('ARCHIVE_DELTA_TASK_POLICY_UNAVAILABLE', 503)
        return None
    return deepcopy(policies[0])


def lock_delta_scope(actor, epoch, patient_id):
    gate, actor, patient = lock_summary_scope(actor.id, patient_id, settings.SJIA_ENVIRONMENT)
    if actor.account_status != 'active' or actor.auth_epoch != epoch:
        raise ApiError('AUTH_REQUIRED', 401)
    if patient.status != 'active':
        raise ApiError('PATIENT_INACTIVE', 403)
    return gate, actor, patient


def check_delta_execution(task, generation):
    """Fence local output at checkpoints; expiration never renews or takes over work."""
    task.refresh_from_db()
    now = timezone.now()
    if (task.status != 'running' or task.lease_owner != DELTA_OWNER
            or task.lease_generation != generation):
        raise ApiError('ARCHIVE_DELTA_TASK_RESULT_REJECTED', 409)
    if task.deadline_at <= now or task.lease_until is None or task.lease_until <= now:
        raise ApiError('ARCHIVE_DELTA_TASK_DEADLINE_EXCEEDED', 409)


def documents_in_batch(batch, patient):
    return [version for version in ArchiveDocumentVersion.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('id')
        if ArchiveDocument.objects.filter(id=version.document_id, patient_id=patient.id,
            retired_at__isnull=True, current_version=version.version).exists()]


def fact_value(fact, field, patient, *, artifact_sha256=None):
    state = FieldVerificationState.objects.filter(patient_id=patient.id, field_id=field.id,
        record_version_id=field.record_version_id, purpose='family_observation').first()
    status = state.status if state else 'unverified'
    proof = None
    if status == 'verified':
        if state.current_verification_id is None:
            raise ApiError('ARCHIVE_DELTA_VERIFICATION_INVALID', 409)
        saved = Verification.objects.get(id=state.current_verification_id, patient=patient,
            record_version_id=field.record_version_id)
        policy = ConfigVersion.objects.get(id=saved.policy_id)
        reason = configuration_qualification_reason(policy, settings.SJIA_ENVIRONMENT, audience='support', artifact_sha256=artifact_sha256)
        if reason is None:
            plan = QualificationPolicy.model_validate(policy.payload)
            version = RecordVersion.objects.get(id=field.record_version_id, patient=patient)
            proof, reason = verification_proof(field, version, policy, plan, environment=settings.SJIA_ENVIRONMENT,
                known_at=timezone.now(), mode='current')
        if reason is not None:
            status = 'revoked' if saved.revoked_at else 'unverified'
    value = {name: getattr(field, name) for name in ('field_code', 'concept_code', 'original_name', 'value_type', 'value_state',
        'raw_text', 'raw_unit', 'qualifier', 'method', 'categorical_value')}
    value.update(fact_version_id=str(fact.id), record_version_id=str(field.record_version_id), field_id=str(field.id),
        raw_numeric=str(field.raw_numeric) if field.raw_numeric is not None else None,
        medical_time=medical_time_view(field.medical_time_id, patient), context_type=fact.context_type,
        assertion_state=fact.assertion_state, verification_status=status)
    return checked_delta('Fact', value), proof


def source_refs(document, version, scope, fact=None):
    pages = scope['pages'] if fact is None else [page for page in scope['pages'] if page.id == fact.page_id]
    if not pages:
        raise ApiError('ARCHIVE_DELTA_SOURCE_INVALID', 409)
    return [checked_delta('Source', {'document_id': str(document.id), 'document_version_id': str(version.id),
        'page_id': str(page.id), 'file_page_id': str(page.file_page_id), 'file_id': str(page.file_id),
        'object_version': page.file_object_version, 'checksum': page.checksum,
        'record_version_id': str(fact.record_version_id) if fact else None, 'field_id': str(fact.field_id) if fact else None,
        'fact_version_id': str(fact.id) if fact else None, 'anchor_id': str(fact.source_anchor_id) if fact and fact.source_anchor_id else None}) for page in pages]


def material_scope(request, actor, epoch, batch_id, payload, frozen=None, *, generating=False):
    checked_delta('Input', payload)
    seed = ArchiveBatch.objects.get(id=batch_id)
    gate, actor, patient = lock_delta_scope(actor, epoch, seed.patient_id)
    runtime = batch_runtime()
    policy = delta_task_policy(runtime, required=generating)
    batch = ArchiveBatch.objects.select_for_update().get(id=batch_id, patient_id=patient.id)
    pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('ordinal'))
    grants = batch_grants(actor, patient, batch, pages)
    if not pages or not grants:
        raise ApiError('ARCHIVE_DELTA_SCOPE_DENIED', 403)
    current = documents_in_batch(batch, patient) if frozen is None else list(ArchiveDocumentVersion.objects.filter(
        id__in=frozen['current_document_version_ids'], batch_id=batch.id, patient_id=patient.id).order_by('id'))
    baseline_batch = ArchiveBatch.objects.get(id=payload['baseline_batch_id'], patient_id=patient.id) if payload['baseline_batch_id'] else None
    identities = payload['baseline_document_version_ids'] if frozen is None else frozen['baseline_document_version_ids']
    if identities:
        baseline = list(ArchiveDocumentVersion.objects.filter(id__in=identities, patient_id=patient.id).order_by('id'))
        if len(baseline) != len(identities) or baseline_batch and any(row.batch_id != baseline_batch.id for row in baseline):
            raise ApiError('ARCHIVE_DELTA_BASELINE_INVALID', 422)
    else:
        baseline = documents_in_batch(baseline_batch, patient) if baseline_batch else []
    if not current or baseline_batch is not None and not baseline:
        raise ApiError('ARCHIVE_DELTA_DOCUMENTS_REQUIRED', 409)
    if {row.id for row in current} & {row.id for row in baseline}:
        raise ApiError('ARCHIVE_DELTA_BASELINE_OVERLAP', 422)
    if len(current) + len(baseline) > 100 or len(pages) > runtime.payload['api']['max_page_size']:
        raise ApiError('ARCHIVE_DELTA_SCOPE_LIMIT_EXCEEDED', 422)
    visibility = current_summary_config('visibility', settings.SJIA_ENVIRONMENT) if frozen is None else ConfigVersion.objects.get(id=frozen['visibility_policy_id'])
    policy_reason = configuration_qualification_reason(visibility, settings.SJIA_ENVIRONMENT)
    if generating and policy_reason:
        raise ApiError('ARCHIVE_DELTA_VISIBILITY_UNAVAILABLE', 503)
    if visibility.payload['purpose'] != 'family_observation':
        raise ApiError('ARCHIVE_DELTA_VISIBILITY_UNAVAILABLE', 503)
    materials, references, facts = {}, [], {'current': [], 'baseline': []}
    for side, rows in (('current', current), ('baseline', baseline)):
        for version in rows:
            document = ArchiveDocument.objects.get(id=version.document_id, patient_id=patient.id)
            scope = document_scope(actor, patient, document, version)
            if scope is None:
                raise ApiError('ARCHIVE_DELTA_SCOPE_DENIED', 403)
            uses = []
            for file in scope['files']:
                group = FileGroup.objects.get(id=file.file_group_id, patient=patient)
                authorization = lock_use_authorization(group.use_authorization_id, file, 'family_observation', settings.SJIA_ENVIRONMENT)
                uses.append({'authorization': entity_reference(authorization), 'source': entity_reference(group.source_revision),
                    'retention': entity_reference(file.retention_policy)})
                if generating:
                    checked_bytes(lock_original(file.id))
            if version.version != document.current_version or document.retired_at is not None:
                decision = authorize_case(request, actor, patient, 'read_history', require_relation=True,
                    author_id=scope['batch'].submitted_by, record_id=document_record_scope(document, patient, version), whole_resource=True)
                grants.append(decision.grant)
            grants.extend(scope['grants'])
            _, _, _, _, _, linked = document_sources(document=document, patient=patient, version=version)
            if document.retired_at is None and (side == 'current' or version.version == document.current_version):
                document_versions = ArchiveDocumentVersion.objects.filter(document_id=document.id, patient_id=patient.id).values('id')
                candidates = ArchiveFactVersion.objects.filter(document_version_id__in=document_versions, patient_id=patient.id).order_by('id')
                linked = [fact for fact in candidates if ArchiveFact.objects.get(id=fact.fact_id, patient_id=patient.id).current_version == fact.version]
            values = []
            for fact in linked:
                field, record_version = fact_source(fact, patient)
                if field.field_code not in visibility.payload['field_codes']:
                    raise ApiError('ARCHIVE_DELTA_FIELD_VISIBILITY_DENIED', 403)
                original = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record_version.record_id, patient=patient, version=1)
                record = Record.objects.get(id=record_version.record_id, patient=patient)
                decision = authorize_case(request, actor, patient, 'read_raw', require_relation=True,
                    author_id=original.author_id, record_id=record.id, field_codes=[field.field_code])
                grants.append(decision.grant)
                if record_version.version != record.current_version:
                    decision = authorize_case(request, actor, patient, 'read_history', require_relation=True,
                        author_id=original.author_id, record_id=record.id, field_codes=[field.field_code])
                    grants.append(decision.grant)
                value, proof = fact_value(fact, field, patient)
                state = FieldVerificationState.objects.filter(field_id=field.id, patient_id=patient.id,
                    record_version_id=record_version.id, purpose='family_observation').first()
                saved_proof = Verification.objects.get(id=state.current_verification_id) if state and state.current_verification_id else None
                source_version = ArchiveDocumentVersion.objects.get(id=fact.document_version_id, document_id=document.id, patient_id=patient.id)
                source_scope = document_scope(actor, patient, document, source_version)
                if source_scope is None:
                    raise ApiError('ARCHIVE_DELTA_SCOPE_DENIED', 403)
                grants.extend(source_scope['grants'])
                if source_version.version != document.current_version or document.retired_at is not None:
                    decision = authorize_case(request, actor, patient, 'read_history', require_relation=True,
                        author_id=source_scope['batch'].submitted_by, record_id=document_record_scope(document, patient, source_version), whole_resource=True)
                    grants.append(decision.grant)
                values.append({'fact': entity_reference(fact), 'current': entity_reference(ArchiveFact.objects.get(id=fact.fact_id, patient_id=patient.id)),
                    'field': entity_reference(field), 'record': entity_reference(record), 'record_version': entity_reference(record_version),
                    'state': entity_reference(state) if state else None, 'proof': entity_reference(saved_proof) if saved_proof else None,
                    'proof_policy': entity_reference(saved_proof.policy) if saved_proof else None,
                    'source_document': entity_reference(source_version), 'value': value})
                facts[side].append({'fact': fact, 'value': value, 'sources': source_refs(document, source_version, source_scope, fact), 'proof': proof,
                    'record_current': record.status == 'active' and record.current_version == record_version.version})
            references.append({'side': side, 'document': entity_reference(document), 'version': entity_reference(version),
                'pages': [entity_reference(page) for page in scope['pages']], 'files': [entity_reference(file) for file in scope['files']],
                'uses': scope['uses'], 'use_refs': uses, 'facts': values})
            materials[str(version.id)] = (document, version, scope)
    fact_ids = [item['fact'].id for side in facts.values() for item in side]
    file_ids = {page.file_id for _, _, scope in materials.values() for page in scope['pages']}
    related = ArchiveRelationVersion.objects.filter(patient_id=patient.id).filter(
        Q(left_fact_version_id__in=fact_ids, right_fact_version_id__in=fact_ids) | Q(left_file_id__in=file_ids, right_file_id__in=file_ids)).order_by('relation_id', '-version')
    relations = list({row.relation_id: row for row in reversed(list(related))}.values())
    relation_refs = []
    for row in sorted(relations, key=lambda row: str(row.id)):
        proof = Verification.objects.get(id=row.verification_id, patient=patient) if row.verification_id else None
        relation_refs.append({'relation': entity_reference(row), 'proof': entity_reference(proof) if proof else None,
            'policy': entity_reference(proof.policy) if proof else None})
    bound = {'current_document_version_ids': [str(row.id) for row in current], 'baseline_document_version_ids': [str(row.id) for row in baseline],
        'visibility_policy_id': str(visibility.id)}
    context = {**bound, 'patient_id': str(patient.id), 'batch_id': str(batch.id), 'baseline_batch_id': payload['baseline_batch_id'],
        'actor_id': str(actor.id), 'auth_epoch': str(epoch), 'environment': settings.SJIA_ENVIRONMENT,
        'implementation_sha256': deployed_artifact_checksum(runtime),
        'gate': entity_reference(gate), 'runtime': entity_reference(runtime), 'task_policy': policy, 'batch': entity_reference(batch),
        'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'visibility': entity_reference(visibility), 'policy_reason': policy_reason, 'sources': references, 'relations': relation_refs,
        'grants': [entity_reference(grant) for grant in sorted({row.id: row for row in grants}.values(), key=lambda row: str(row.id))],
        'case_relations': [entity_reference(row) for row in Relation.objects.filter(id__in={grant.relation_id for grant in grants if grant.relation_id}).order_by('id')]}
    retention = materials[str(current[0].id)][2]['files'][0].retention_policy
    if len(json.dumps(context, ensure_ascii=False).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('ARCHIVE_DELTA_SCOPE_LIMIT_EXCEEDED', 422)
    return patient, retention, context, materials, facts, relations


def item(kind, title, sources, before=None, after=None, reasons=(), relations=()):
    return checked_delta('Item', {'item_id': str(uuid4()), 'kind': kind, 'title': title, 'before': before, 'after': after,
        'source_refs': sources, 'relation_version_ids': [str(row.id) for row in relations], 'reason_codes': list(dict.fromkeys(reasons))})


def comparable(before, after, patient):
    reasons = []
    for key in ('field_code', 'concept_code', 'raw_unit', 'method'):
        if not before[key] or not after[key] or before[key] != after[key]:
            reasons.append('FIELD_' + key.upper() + '_NOT_COMPARABLE')
    for value in (before, after):
        if value['value_type'] != 'decimal' or value['value_state'] != 'known' or value['raw_numeric'] is None or value['qualifier'] != 'eq':
            reasons.append('EXACT_NUMERIC_VALUES_REQUIRED')
        elif not Decimal(value['raw_numeric']).is_finite():
            reasons.append('NUMERIC_VALUE_INVALID')
        if value['context_type'] not in ('current', 'actual') or value['assertion_state'] != 'present':
            reasons.append('CURRENT_AFFIRMED_CONTEXT_REQUIRED')
        if value['verification_status'] != 'verified':
            reasons.append('FIELD_VERIFICATION_REQUIRED')
    spans = [instant_bounds(MedicalTime.objects.get(id=ArchiveFactVersion.objects.get(id=value['fact_version_id'], patient_id=patient.id).medical_time_id,
        patient=patient)) if value['medical_time'] else None for value in (before, after)]
    if not all(spans) or spans[0][1] > spans[1][0]:
        reasons.append('MEDICAL_TIME_ORDER_NOT_CONFIRMED')
    return list(dict.fromkeys(reasons))


def qualified_sampling_relation(relation, facts):
    """A family relationship choice is not a professional sampling proof.

    `facts` contain proofs already checked by fact_value/verification_proof.
    Verification is record-scoped, so the relationship may reference either
    endpoint's proof, but must retain that verifier and exact evidence.
    """
    if relation.status != 'confirmed' or relation.verification_id is None:
        return False
    endpoints = {relation.left_fact_version_id, relation.right_fact_version_id}
    if not endpoints.issubset({value['fact'].id for value in facts}):
        return False
    now = timezone.now()
    return any(value['fact'].id in endpoints and value['proof'] is not None
        and value['proof'].id == relation.verification_id
        and value['proof'].revoked_at is None and value['proof'].verified_at <= now
        and value['proof'].verifier_id == relation.author_id
        and value['proof'].evidence_ref == relation.evidence_ref
        for value in facts)


def build_items(context, materials, facts, relations, patient):
    results = []
    baseline = context['baseline_document_version_ids']
    before_files = {source['checksum'] for identity in baseline for source in source_refs(*materials[identity])}
    observed_files = set(before_files)
    all_facts = [value for rows in facts.values() for value in rows]
    qualified_relations = [row for row in relations if qualified_sampling_relation(row, all_facts)]
    same_groups = sampling_groups({str(value['fact'].id) for value in all_facts},
        [(str(row.left_fact_version_id), str(row.right_fact_version_id)) for row in relations
            if row in qualified_relations and row.relation_type == 'same_sampling'])
    for row in relations:
        if row.status == 'confirmed' and row.relation_type == 'same_sampling' and row not in qualified_relations:
            sources = [source for value in all_facts if value['fact'].id in
                (row.left_fact_version_id, row.right_fact_version_id) for source in value['sources']]
            if sources:
                results.append(item('conflict', '采样关系尚待专业核对', sources,
                    reasons=['SAMPLING_VERIFICATION_REQUIRED'], relations=[row]))
    for identity in context['current_document_version_ids']:
        document, version, scope = materials[identity]
        sources = source_refs(document, version, scope)
        earlier = [materials[key] for key in baseline if materials[key][0].id == document.id]
        if earlier:
            historical_sources = [source for previous in earlier for source in source_refs(*previous)]
            results.append(item('change' if len(earlier) == 1 else 'conflict', '文书更正' if len(earlier) == 1 else '文书比较基准存在多个版本',
                historical_sources + sources, reasons=['SOURCE_CORRECTION'] if len(earlier) == 1 else ['BASELINE_DOCUMENT_AMBIGUOUS']))
        checksum_match = all(value['checksum'] in observed_files for value in sources)
        current_files = {page.file_id for page in scope['pages']}
        other_keys = [key for key in baseline + context['current_document_version_ids'] if key != identity]
        other_files = {page.file_id for key in other_keys for page in materials[key][2]['pages']}
        confirmed = [row for row in relations if row.status == 'confirmed' and row.relation_type in ('same_bytes', 'document_copy')
            and (row.left_file_id in current_files and row.right_file_id in other_files or row.right_file_id in current_files and row.left_file_id in other_files)]
        before_sources = [source for key in other_keys for source in source_refs(*materials[key])
            if source['checksum'] in {value['checksum'] for value in sources} or any(source['file_id'] in (str(row.left_file_id), str(row.right_file_id)) for row in confirmed)]
        if not earlier or confirmed:
            results.append(item('duplicate' if confirmed else 'document', '已确认重复资料' if confirmed else '新增文书' if baseline else '本批文书', sources,
                reasons=['DOCUMENT_RELATION_CONFIRMED'] if confirmed else [] if baseline else ['BASELINE_NOT_SELECTED'], relations=confirmed))
        if confirmed:
            results[-1]['source_refs'] += before_sources
        if checksum_match and not confirmed and not earlier:
            results.append(item('conflict', '重复资料候选，尚待确认', before_sources + sources, reasons=['FILE_CHECKSUM_MATCH_REQUIRES_CONFIRMATION']))
        observed_files.update(value['checksum'] for value in sources)
        if version.documented_time_id is None or instant_bounds(MedicalTime.objects.get(id=version.documented_time_id, patient=patient)) is None:
            results.append(item('unknown_time', '文书时间尚未明确', sources, reasons=['DOCUMENT_TIME_UNKNOWN']))
        if version.grouping_status != 'confirmed':
            results.append(item('conflict', '文书归组尚未确认', sources, reasons=['DOCUMENT_GROUPING_UNCONFIRMED']))
    seen_facts = []
    for current in facts['current']:
        value, sources = current['value'], current['sources']
        if value['verification_status'] != 'verified':
            results.append(item('unverified', '事实尚未完成专业核对', sources, after=value, reasons=['FIELD_VERIFICATION_REQUIRED']))
        if value['medical_time'] is None:
            results.append(item('unknown_time', '事实时间尚未明确', sources, after=value, reasons=['FACT_MEDICAL_TIME_UNKNOWN']))
        repeated = [previous for previous in seen_facts if any(str(previous['fact'].id) in group and str(current['fact'].id) in group for group in same_groups)]
        seen_facts.append(current)
        if repeated:
            results.append(item('duplicate', '本批同次采样的重复引用', [source for previous in repeated for source in previous['sources']] + sources,
                after=value, reasons=['SAME_SAMPLING_OR_FACT'], relations=[row for row in qualified_relations if row.relation_type == 'same_sampling'
                    and any(str(row.left_fact_version_id) in group and str(row.right_fact_version_id) in group and str(current['fact'].id) in group for group in same_groups)]))
            continue
        existing = [old for old in facts['baseline'] if old['fact'].fact_id == current['fact'].fact_id]
        if not existing:
            results.append(item('fact', '新增事实' if baseline else '本批事实', sources, after=value,
                reasons=[] if baseline else ['BASELINE_NOT_SELECTED']))
        if not baseline:
            continue
        matches = [old for old in facts['baseline'] if old['value']['field_code'] == value['field_code']]
        if not matches:
            continue
        if len(matches) != 1:
            results.append(item('conflict', '存在多个比较来源，尚未指定准确前值', sources + [source for old in matches for source in old['sources']],
                after=value, reasons=['BASELINE_FACT_AMBIGUOUS']))
            continue
        old = matches[0]
        pairs = [row for row in relations if {row.left_fact_version_id, row.right_fact_version_id} == {old['fact'].id, current['fact'].id}]
        pair_sources = old['sources'] + sources
        file_pairs = [row for row in relations if row.status == 'confirmed' and row.relation_type in ('same_bytes', 'document_copy')
            and (str(row.left_file_id) in {source['file_id'] for source in old['sources']} and str(row.right_file_id) in {source['file_id'] for source in sources}
                or str(row.right_file_id) in {source['file_id'] for source in old['sources']} and str(row.left_file_id) in {source['file_id'] for source in sources})]
        if file_pairs:
            results.append(item('duplicate', '已确认重复文书中的事实引用', pair_sources, old['value'], value, ['DOCUMENT_RELATION_CONFIRMED'], file_pairs))
            continue
        if any(row.status in ('proposed', 'disputed') for row in pairs):
            results.append(item('conflict', '采样关系尚待核对', pair_sources, old['value'], value, ['SAMPLING_RELATION_UNRESOLVED'], pairs))
            continue
        same = [row for row in pairs if row in qualified_relations and row.relation_type == 'same_sampling']
        correction = [row for row in pairs if row.status == 'confirmed' and row.relation_type == 'formal_correction']
        same_group = next((group for group in same_groups if str(old['fact'].id) in group and str(current['fact'].id) in group), None)
        if same_group:
            same = [row for row in qualified_relations if row.relation_type == 'same_sampling'
                and str(row.left_fact_version_id) in same_group and str(row.right_fact_version_id) in same_group]
        if same_group or old['fact'].fact_id == current['fact'].fact_id and old['fact'].version == current['fact'].version:
            results.append(item('duplicate', '同次采样或同一事实引用', pair_sources, old['value'], value, ['SAME_SAMPLING_OR_FACT'], same))
            continue
        if correction or old['fact'].fact_id == current['fact'].fact_id:
            results.append(item('change', '资料更正', pair_sources, old['value'], value, ['SOURCE_CORRECTION'], correction))
            continue
        independent = [row for row in pairs if row.status == 'confirmed' and row.relation_type == 'independent_sampling']
        reasons = comparable(old['value'], value, patient)
        if not old['record_current'] or not current['record_current']:
            reasons.append('SOURCE_RECORD_NOT_CURRENT')
        for row in independent:
            if row not in qualified_relations:
                reasons.append('SAMPLING_VERIFICATION_REQUIRED')
        if not independent:
            reasons.append('INDEPENDENT_SAMPLING_REQUIRED')
        if reasons:
            results.append(item('conflict', '比较条件尚未满足', pair_sources, old['value'], value, reasons, independent))
        else:
            results.append(item('change', '独立检查原值比较', pair_sources, old['value'], value, ['RAW_VALUES_ONLY_NO_CLINICAL_CLASSIFICATION'], independent))
    return results


@transaction.atomic
def generate_delta(actor, epoch, patient, context, materials, facts, relations, request_row=None):
    # Admission, capacity and the entire synchronous render share the same gate
    # lock as asynchronous summaries. No outbox, retry or takeover is introduced.
    gate, actor, patient = lock_delta_scope(actor, epoch, patient.id)
    runtime = batch_runtime()
    policy = delta_task_policy(runtime)
    current = {'actor_id': str(actor.id), 'auth_epoch': str(epoch), 'patient_id': str(patient.id),
        'environment': settings.SJIA_ENVIRONMENT, 'data_epoch': str(patient.data_epoch),
        'verification_epoch': str(patient.verification_epoch), 'gate': entity_reference(gate),
        'runtime': entity_reference(runtime), 'task_policy': policy}
    if any(context.get(key) != value for key, value in current.items()):
        raise ApiError('ARCHIVE_DELTA_PREPARATION_CHANGED', 409)
    now = timezone.now()
    inflight = Task.objects.filter(task_type='summary', status='running', lease_until__gt=now,
        deadline_at__gt=now).filter(
            Q(schema_version='summary-task-2', input_refs__context__runtime_id=str(runtime.id))
            | Q(schema_version=DELTA_SCHEMA, input_refs__runtime__id=str(runtime.id))).count()
    if inflight >= policy['max_concurrency']:
        raise ApiError('ARCHIVE_DELTA_TASK_CONCURRENCY_EXCEEDED', 409)
    task = register_task(actor=actor, patient=patient, task_type='summary', schema_version=DELTA_SCHEMA,
        input_refs=context, request_row=request_row, deadline_seconds=policy['execution_seconds'])
    if TaskAttempt.objects.filter(task=task).count() >= policy['max_attempts']:
        raise ApiError('ARCHIVE_DELTA_TASK_ATTEMPT_LIMIT_EXCEEDED', 409)
    if acquire_lease(task.id, DELTA_OWNER, timezone.now() + timedelta(seconds=policy['lease_seconds'])) != 1:
        raise ApiError('ARCHIVE_DELTA_TASK_LEASE_FAILED', 409)
    task.refresh_from_db()
    attempt = create_attempt(task, task.lease_generation)
    check_delta_execution(task, attempt.lease_generation)
    values = build_items(context, materials, facts, relations, patient)
    check_delta_execution(task, attempt.lease_generation)
    result = DeltaSummary.objects.create(patient_id=patient.id, owner_id=actor.id, batch_id=context['batch_id'],
        baseline_refs=[row for row in context['sources'] if row['side'] == 'baseline'] or None,
        current_refs=[row for row in context['sources'] if row['side'] == 'current'], data_epoch=patient.data_epoch,
        authorization_version=epoch, visibility_policy_id=context['visibility_policy_id'], validity='valid',
        generated_at=timezone.now(), task_id=task.id)
    for index, value in enumerate(values, 1):
        DeltaSummaryItem.objects.create(id=value['item_id'], patient_id=patient.id, summary_id=result.id, ordinal=index,
            item_kind=value['kind'], item_source_refs=value['source_refs'], limitations=value['reason_codes'], content=value)
    node = resource_node('archive_delta', result.id, '1', patient.id)
    for document, version, scope in materials.values():
        connect(node, resource_node('archive_document_version', version.id, entity_reference(version)['sha256'], patient.id), 'source')
        for source in scope['files']:
            connect(node, resource_node('file_object', source.id, source.object_version, patient.id), 'source')
    for side_facts in facts.values():
        for value in side_facts:
            fact, proof = value['fact'], value['proof']
            connect(node, resource_node('archive_fact_version', fact.id, entity_reference(fact)['sha256'], patient.id), 'input')
            if proof:
                connect(node, resource_node('verification', proof.id, entity_reference(proof)['sha256'], patient.id), 'verification')
    for row in relations:
        connect(node, resource_node('archive_relation_version', row.id, entity_reference(row)['sha256'], patient.id), 'input')
    connect(node, resource_node('config_version', result.visibility_policy_id, context['visibility']['sha256'], None), 'policy')
    check_delta_execution(task, attempt.lease_generation)
    finish_attempt(attempt, 'completed', usage_evidence={'engine': 'archive-delta-deterministic-1', 'item_count': len(values), 'input_sha256': task.input_sha256})
    if complete_task(task.id, attempt.lease_generation, 'archive_delta', result.id) != 1:
        raise ApiError('ARCHIVE_DELTA_TASK_RESULT_REJECTED', 409)
    return result, task


def read_delta(request, actor, epoch, result):
    # Caller objects are identity hints only. Read current scope without a row
    # lock, then reload under the common gate/actor/patient lock prefix.
    seed = DeltaSummary.objects.get(id=result.id)
    if seed.owner_id != actor.id:
        raise ApiError('ARCHIVE_DELTA_SCOPE_DENIED', 403)
    _, actor, patient = lock_delta_scope(actor, epoch, seed.patient_id)
    result = DeltaSummary.objects.select_for_update().get(id=seed.id,
        owner_id=actor.id, patient_id=patient.id)
    task = Task.objects.select_for_update().get(id=result.task_id, actor=actor, patient_id=patient.id, schema_version=DELTA_SCHEMA, status__in=['completed', 'superseded'])
    frozen = task.input_refs
    if compute_checksum(frozen) != task.input_sha256:
        raise ApiError('ARCHIVE_DELTA_TASK_INPUT_INVALID', 409)
    payload = {'baseline_batch_id': frozen['baseline_batch_id'], 'baseline_document_version_ids': frozen['baseline_document_version_ids']}
    _, _, context, _, _, _ = material_scope(request, actor, epoch, result.batch_id, payload, frozen)
    node = DependencyNode.objects.get(resource_type='archive_delta', resource_id=result.id, resource_version='1', patient_id=result.patient_id)
    invalid = (result.validity == 'invalid' or frozen != context or node.validity != 'valid'
        or DependencyEdge.objects.filter(dependent=node, prerequisite__validity='invalid').exists())
    if invalid and result.validity != 'invalid':
        result.validity = 'invalid'
        result.save(update_fields=['validity'])
    if invalid and task.status != 'superseded':
        task.status, task.status_version = 'superseded', task.status_version + 1
        task.save(update_fields=['status', 'status_version'])
    values = [] if invalid else [row.content for row in DeltaSummaryItem.objects.filter(summary_id=result.id, patient_id=result.patient_id).order_by('ordinal')]
    return checked_delta('View', {'delta_id': str(result.id), 'patient_id': str(result.patient_id), 'batch_id': str(result.batch_id),
        'baseline_batch_id': frozen['baseline_batch_id'], 'baseline_document_version_ids': frozen['baseline_document_version_ids'],
        'current_document_version_ids': frozen['current_document_version_ids'], 'task_id': str(task.id),
        'status': 'superseded' if invalid else 'completed', 'validity': 'invalid' if invalid else 'valid',
        'reason_codes': ['SOURCES_OR_PERMISSIONS_CHANGED'] if invalid else [] if frozen['baseline_document_version_ids'] else ['BASELINE_NOT_SELECTED'],
        'items': values, 'created_at': result.generated_at.isoformat()})
