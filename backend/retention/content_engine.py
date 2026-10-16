"""Explicit-policy logical payload disposition, disabled unless configured.

No patient DELETE, arbitrary SQL, policy JSON paths, invented medical values,
production policy seed, external-provider receipt or media-erasure claim.
"""
from datetime import timedelta
from uuid import UUID, uuid4, uuid5

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from retention.content_registry import (REGISTRY, REGISTRY_SHA256, PRESERVATION_SHA256,
    adapter, digest, owned_references, patient_rows, row_digest, row_references, row_version)
from retention.content_schemas import (ContentApproval, ContentManifest, ContentPlanInput,
    ContentPolicyRules, ContentPolicyAttestation, ContentHoldAttestation, DatabaseProof, LocalObjectProof)
from retention.disposition import DispositionRejected
from retention.models import (ContentDispositionHold, ContentDispositionPlan,
    ContentDispositionPolicy, ContentErasure, ContentAccessRevocation)


def reject(code):
    raise DispositionRejected(code)


def write_barrier(*, exclusive=False):
    if connection.vendor != 'postgresql' or not connection.in_atomic_block:
        reject('CONTENT_TRANSACTION_REQUIRED')
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(1397377345,26)' if exclusive else
            'SELECT pg_advisory_xact_lock_shared(1397377345,26)')


def _identity(actor, auth_epoch, patient, purpose):
    from assessment.qualified_facts import lock_case_permissions
    from authorization.policy import check_principal_session, check_service_grant
    from family_content.models import EnvironmentGate
    from identity.models import Principal
    now = timezone.now()
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    if not gate.enabled:
        reject('CONTENT_ENVIRONMENT_DISABLED')
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    grant = check_service_grant(actor, 'manage_disposition', gate.environment, now)
    case_grants = lock_case_permissions(actor, patient, 'read_case', purpose, now, whole_resource=True)
    if not any(item.record_scope == 'all' and not item.field_codes for item in case_grants):
        reject('CONTENT_WHOLE_RESOURCE_AUTHORIZATION_REQUIRED')
    return actor, grant, case_grants


def _policy(policy_id, *, execution=False):
    from configurations.evidence import inspect_approved_evidence
    from identity.models import Principal
    policy = ContentDispositionPolicy.objects.select_for_update().get(id=policy_id)
    rules = ContentPolicyRules.model_validate(policy.rules)
    now = timezone.now()
    if (policy.environment != settings.SJIA_ENVIRONMENT or policy.checksum != digest(policy.rules)
            or rules.registry_sha256 != REGISTRY_SHA256 or rules.preservation_sha256 != PRESERVATION_SHA256
            or not set(rules.allowed_adapters) <= set(REGISTRY)
            or min(rules.receipt_keep_until, rules.metadata_keep_until, rules.recheck_at) <= now):
        reject('CONTENT_POLICY_CONTRACT_UNAVAILABLE')
    if (policy.approved_at is None or policy.approved_at > now or policy.approved_by_id is None
            or not Principal.objects.filter(id=policy.approved_by_id, account_status='active').exists()):
        reject('CONTENT_POLICY_APPROVAL_REQUIRED')
    from authorization.policy import check_service_grant
    check_service_grant(Principal.objects.get(id=policy.approved_by_id), 'manage_disposition',
        settings.SJIA_ENVIRONMENT, now)
    attestation = ContentPolicyAttestation(schema_version='content-disposition-policy-approval-1',
        policy_id=policy.id,policy_version=policy.version,environment=policy.environment,
        rules_sha256=policy.checksum,approved_by=policy.approved_by_id,approved_at=policy.approved_at,
        execution_enabled=policy.enabled).model_dump(mode='json')
    _, content, reason = inspect_approved_evidence(f'urn:sjia:source-revision:{policy.source_revision_id}',
        purpose='configuration_approval', expected_checksum=digest(attestation), lock=True)
    if reason:
        reject('CONTENT_POLICY_EVIDENCE_UNAVAILABLE')
    if ContentPolicyAttestation.model_validate_json(content).model_dump(mode='json') != attestation:
        reject('CONTENT_POLICY_EVIDENCE_UNAVAILABLE')
    if execution and (not getattr(settings, 'SJIA_CONTENT_DISPOSITION_ENABLED', False) or not policy.enabled):
        reject('CONTENT_EXECUTION_DISABLED')
    return policy, rules


def _hold(patient_id, hold_id, version):
    from configurations.evidence import inspect_approved_evidence
    from identity.models import Principal
    now = timezone.now()
    hold = ContentDispositionHold.objects.select_for_update().filter(id=hold_id,
        patient_id=patient_id, version=version).first()
    if (hold is None or hold.state != 'clear' or hold.reviewed_at > now or hold.valid_until <= now
            or not Principal.objects.filter(id=hold.reviewed_by_id, account_status='active').exists()):
        reject('CONTENT_HOLD_UNRESOLVED')
    from authorization.policy import check_service_grant
    reviewer = Principal.objects.get(id=hold.reviewed_by_id)
    check_service_grant(reviewer,'manage_disposition',settings.SJIA_ENVIRONMENT,now)
    attestation = ContentHoldAttestation(schema_version='content-disposition-hold-1',
        hold_id=hold.id,patient_id=hold.patient_id,hold_version=hold.version,environment=settings.SJIA_ENVIRONMENT,
        state=hold.state,reviewed_by=hold.reviewed_by_id,reviewed_at=hold.reviewed_at,valid_until=hold.valid_until).model_dump(mode='json')
    _, content, reason = inspect_approved_evidence(f'urn:sjia:source-revision:{hold.source_revision_id}',
        purpose='configuration_approval',expected_checksum=digest(attestation),lock=True)
    if reason:
        reject('CONTENT_HOLD_EVIDENCE_UNAVAILABLE')
    # Parse and compare as well as hashing; evidence for another patient, state,
    # horizon, environment or reviewer cannot be reused as clearance.
    if ContentHoldAttestation.model_validate_json(content).model_dump(mode='json') != attestation:
        reject('CONTENT_HOLD_EVIDENCE_UNAVAILABLE')
    # Any tied or later version is a conflict; timestamps cannot break a
    # clearance/hold tie in favour of destructive execution.
    if ContentDispositionHold.objects.filter(patient_id=patient_id,version__gte=version).exclude(id=hold.id).exists():
        reject('CONTENT_HOLD_REVIEW_SUPERSEDED')
    latest = ContentDispositionHold.objects.filter(patient_id=patient_id).order_by('-version').first()
    if latest is None or latest.id != hold.id:
        reject('CONTENT_HOLD_REVIEW_SUPERSEDED')
    return hold


def _assert_exclusive_paths(items):
    from files.models import FileObject
    if not items:
        return
    from files.deletion import object_path,LocalDeletionRejected
    from files.service import object_store
    ids = {identity for identity, _ in items}
    try:
        paths = [object_path(object_store,key) for _,key in items]
        if len(paths)!=len(set(paths)):
            reject('CONTENT_SHARED_LOCAL_PATH_SCOPE')
        selected=set(paths)
        # Legacy metadata may contain noncanonical keys or symlink aliases.
        # Validate every registered local path without reading any file body;
        # ambiguous inventory must block instead of unlinking an unrelated row.
        for row in FileObject.objects.only('id','object_key').iterator(chunk_size=1000):
            path=object_path(object_store,row.object_key)
            if path in selected and row.id not in ids:
                reject('CONTENT_SHARED_LOCAL_PATH_SCOPE')
    except LocalDeletionRejected:
        reject('CONTENT_LOCAL_PATH_INVENTORY_UNVERIFIED')


def _file_rows(patient_id):
    from files.models import FileObject
    return list(FileObject.objects.filter(patient_id=patient_id).order_by('id')[:5001])


def _closure(patient_id, roots):
    """Compute, never approve, exact mirrored-copy closure.

    Shared canonical records cannot be silently added. Shared evaluation outputs
    fail closed pending a separate per-patient partition adapter.
    """
    from archives.models import EvaluationPatientPartition
    from files.models import FilePage
    from records.models import RecordAttachment, RecordVersion
    from requests.models import WriteRequest
    rows = patient_rows(patient_id)
    by_key = {(row._meta.label_lower, str(row.pk)): row for row in rows}
    files = _file_rows(patient_id)
    if len(files) > 5000:
        reject('CONTENT_INVENTORY_LIMIT_EXCEEDED')
    files_by_id = {str(row.id): row for row in files}
    selected, wanted, root_versions = set(), set(), set()
    for target in roots:
        if target.kind == 'database':
            key = (target.adapter, str(target.object_id))
            row = by_key.get(key)
            if row is None or row.content_erased_at is not None:
                reject('CONTENT_TARGET_UNAVAILABLE')
            if row_version(row) != target.version:
                reject('CONTENT_VERSION_CONFLICT')
            selected.add(key)
            wanted.add(str(row.pk))
            if target.adapter == 'records.recordversion':
                root_versions.add(str(row.id))
        else:
            row = files_by_id.get(str(target.file_id))
            if row is None or row.object_version != target.object_version or row.status == 'disposed':
                reject('CONTENT_VERSION_CONFLICT')
            wanted.add(str(row.id))
    initial = set(selected)
    file_selected = set()
    # Exact scalar/array/JSON references and registered forward payload owners.
    # The bounded inventory is scanned to a fixed point, never truncated silently.
    for _ in range(len(rows) + len(files) + 1):
        before = (len(selected), len(wanted), len(file_selected))
        for key, row in by_key.items():
            if row.content_erased_at is not None:
                continue
            if key in selected or str(row.pk) in wanted or row_references(row) & wanted:
                if key not in initial:
                    if key[0] == 'records.recordversion' and str(row.id) not in root_versions:
                        reject('CONTENT_SHARED_CANONICAL_SCOPE')
                    if key[0] in ('records.recordfield', 'archives.archivefactversion') and str(row.record_version_id) not in root_versions:
                        reject('CONTENT_SHARED_CANONICAL_SCOPE')
                if key[0] == 'archives.evaluationrun':
                    if EvaluationPatientPartition.all_objects.filter(dataset_version_id=row.dataset_version_id).exclude(patient_id=patient_id).exists():
                        reject('CONTENT_SHARED_EVALUATION_PARTITION_REQUIRED')
                selected.add(key)
                wanted.add(str(row.pk))
                wanted.update(owned_references(row))
        from dependencies.models import DependencyNode, DependencyEdge
        prerequisite_ids = DependencyNode.objects.filter(resource_id__in=[UUID(value) for value in wanted]).values('id')
        dependent_ids = DependencyEdge.objects.filter(prerequisite_id__in=prerequisite_ids).values('dependent_id')
        for node in DependencyNode.objects.filter(id__in=dependent_ids):
            if node.patient_id is not None and node.patient_id != patient_id:
                reject('CONTENT_SHARED_DEPENDENCY_SCOPE')
            identity = str(node.resource_id)
            if not any(key[1] == identity for key in by_key) and identity not in files_by_id:
                reject('CONTENT_DEPENDENT_ADAPTER_UNREGISTERED')
            wanted.add(identity)
        selected_version_ids = [UUID(key[1]) for key in selected if key[0] == 'records.recordversion']
        for link in RecordAttachment.objects.filter(patient_id=patient_id, record_version_id__in=selected_version_ids):
            wanted.add(str(link.file_id))
        # The create receipt points to the record root, not its first version.
        first_roots = [row.record_id for key, row in by_key.items() if key in selected
            and key[0] == 'records.recordversion' and row.version == 1]
        wanted.update(str(value) for value in WriteRequest.all_objects.filter(patient_id=patient_id,
            resource_type='record', resource_id__in=first_roots).values_list('id', flat=True))
        for page in FilePage.objects.filter(patient_id=patient_id, id__in=[UUID(value) for value in wanted]):
            wanted.add(str(page.file_id))
        for file in files:
            if str(file.id) in wanted or str(file.derived_from_id) in file_selected:
                file_selected.add(str(file.id)); wanted.add(str(file.id))
        if before == (len(selected), len(wanted), len(file_selected)):
            break
    else:
        reject('CONTENT_CLOSURE_UNSTABLE')
    _assert_exclusive_paths([(files_by_id[identity].id, files_by_id[identity].object_key) for identity in file_selected])
    # An original may be shared by an adjacent version even when no payload row
    # directly references it. Explicitly enumerate every such attachment.
    if RecordAttachment.objects.filter(patient_id=patient_id, file_id__in=file_selected).exclude(
            record_version_id__in=[UUID(value) for value in root_versions]).exists():
        reject('CONTENT_SHARED_LOCAL_OBJECT_SCOPE')
    # Unscoped/global outbox mirrors cannot silently escape patient inventory.
    # Exact known references without a patient/task partition refuse a plan.
    from django.db.models import Q
    from tasks.models import OutboxEvent
    global_match = Q(aggregate_id__in=[UUID(value) for value in wanted]) | Q(correlation_id__in=[UUID(value) for value in wanted])
    for value in wanted:
        global_match |= Q(resource_refs__icontains=value)
    scoped_outbox = [UUID(key[1]) for key in by_key if key[0] == 'tasks.outboxevent']
    if OutboxEvent.all_objects.filter(global_match).exclude(id__in=scoped_outbox).exists():
        reject('CONTENT_SHARED_GLOBAL_OUTBOX_SCOPE')
    result = [by_key[key] for key in sorted(selected)]
    return result, [files_by_id[key] for key in sorted(file_selected)]


def _obligations(rows, files):
    # Logical live payload removal never proves physical pages/WAL or backups.
    obligations = ['physical_database_storage', 'backup_copies']
    if any(row._meta.app_label == 'recognition' or row._meta.label_lower in
            ('archives.archivepageattempt', 'archives.purposecopy') for row in rows):
        obligations.append('external_provider_copies')
    return obligations


@transaction.atomic
def preview_plan(*, actor, auth_epoch, patient_id, policy_id, data, family_request_id=None):
    from identity.models import Patient
    request = ContentPlanInput.model_validate(data)
    write_barrier(exclusive=True)
    patient = Patient.objects.select_for_update().get(id=patient_id)
    policy, rules = _policy(policy_id)
    _identity(actor, auth_epoch, patient, rules.access_purpose)
    _hold(patient.id, request.hold_id, request.hold_version)
    if family_request_id is not None:
        from retention.content_family import bind_family_request
        bind_family_request(request_id=family_request_id,patient_id=patient.id,policy=policy,roots=request.targets)
        if ContentDispositionPlan.objects.filter(family_request_id=family_request_id).exists():
            reject('CONTENT_FAMILY_PLAN_ALREADY_EXISTS')
    rows, files = _closure(patient.id, request.targets)
    if any(row._meta.label_lower not in rules.allowed_adapters for row in rows):
        reject('CONTENT_CLOSURE_OUTSIDE_POLICY')
    if files and not rules.local_objects:
        reject('CONTENT_LOCAL_OBJECT_POLICY_REQUIRED')
    now = timezone.now()
    # Clock applies to explicit source targets; mirrors inherit their source's
    # eligibility, not an invented age based on their later copying time.
    by_key = {(row._meta.label_lower, str(row.pk)): row for row in rows}
    for target in request.targets:
        if target.kind == 'database':
            row = by_key[(target.adapter, str(target.object_id))]
            start = getattr(row, rules.clock, None)
        else:
            start = next(row.created_at for row in files if row.id == target.file_id)
            if rules.clock != 'created_at':
                reject('CONTENT_RETENTION_CLOCK_UNRESOLVED')
        if start is None or not hasattr(start, 'tzinfo') or start.tzinfo is None:
            reject('CONTENT_RETENTION_CLOCK_UNRESOLVED')
        if start + timedelta(seconds=rules.retention_seconds) > now:
            reject('CONTENT_RETENTION_NOT_DUE')
    items = [DatabaseProof(kind='database', adapter=row._meta.label_lower, object_id=row.pk,
        version=row_version(row), row_sha256=row_digest(row)) for row in rows]
    items.extend(LocalObjectProof(kind='local_object', file_id=file.id, object_version=file.object_version,
        object_key=file.object_key, checksum=file.checksum, size_bytes=file.size_bytes, operation_id=uuid4()) for file in files)
    manifest = ContentManifest(family_request_id=family_request_id,schema_version='content-disposition-manifest-1', patient_id=patient.id,
        policy_id=policy.id, policy_version=policy.version, policy_sha256=policy.checksum,
        registry_sha256=REGISTRY_SHA256, preservation_sha256=PRESERVATION_SHA256, mode=rules.mode,
        environment=policy.environment, hold_id=request.hold_id, hold_version=request.hold_version,
        roots=request.targets, items=items, obligations=_obligations(rows, files), created_at=now).model_dump(mode='json')
    if rules.all_copies_required and manifest['obligations']:
        reject('CONTENT_ALL_COPIES_PROTOCOL_UNAVAILABLE')
    return ContentDispositionPlan.objects.create(family_request_id=family_request_id,patient=patient, policy=policy, requested_by=actor,
        manifest=manifest, manifest_sha256=digest(manifest), created_at=now, status='preview')


def _current_plan(plan_id, actor, auth_epoch, *, execution):
    from identity.models import Patient
    plan = ContentDispositionPlan.objects.select_for_update().get(id=plan_id)
    patient = Patient.objects.select_for_update().get(id=plan.patient_id)
    policy, rules = _policy(plan.policy_id, execution=execution)
    actor, service, grants = _identity(actor, auth_epoch, patient, rules.access_purpose)
    manifest = ContentManifest.model_validate(plan.manifest)
    if (digest(plan.manifest) != plan.manifest_sha256 or manifest.patient_id != plan.patient_id
            or manifest.policy_id != policy.id or manifest.policy_version != policy.version
            or manifest.policy_sha256 != policy.checksum or manifest.registry_sha256 != REGISTRY_SHA256
            or manifest.preservation_sha256 != PRESERVATION_SHA256 or manifest.mode != rules.mode):
        reject('CONTENT_MANIFEST_CHANGED')
    _hold(patient.id, manifest.hold_id, manifest.hold_version)
    if manifest.family_request_id!=plan.family_request_id:
        reject('CONTENT_FAMILY_REQUEST_CHANGED')
    if plan.family_request_id:
        from retention.content_family import bind_family_request
        bind_family_request(request_id=plan.family_request_id,patient_id=patient.id,policy=policy,
            roots=manifest.roots,check_available=plan.status in ('preview','approved'))
    return plan, manifest, policy, rules, actor, service, grants


def _verify_unchanged(manifest):
    from files.models import FileObject
    for item in manifest.items:
        if item.kind == 'database':
            model, _ = adapter(item.adapter)
            row = model.all_objects.select_for_update().get(pk=item.object_id)
            if row.content_erased_at is not None or row_version(row) != item.version or row_digest(row) != item.row_sha256:
                reject('CONTENT_MANIFEST_CHANGED')
        else:
            file = FileObject.objects.select_for_update().get(id=item.file_id, patient_id=manifest.patient_id)
            if (file.object_version != item.object_version or file.object_key != item.object_key
                    or file.checksum != item.checksum or file.size_bytes != item.size_bytes or file.status == 'disposed'):
                reject('CONTENT_MANIFEST_CHANGED')
    # Detect late mirrored snapshots and new dependents created after preview.
    roots = manifest.roots
    rows, files = _closure(manifest.patient_id, roots)
    actual = {(row._meta.label_lower, str(row.pk)) for row in rows}
    expected = {(item.adapter, str(item.object_id)) for item in manifest.items if item.kind == 'database'}
    if actual != expected or {str(row.id) for row in files} != {str(item.file_id) for item in manifest.items if item.kind == 'local_object'}:
        reject('CONTENT_CLOSURE_CHANGED')


@transaction.atomic
def approve_plan(*, plan_id, actor, auth_epoch, expected_manifest_sha256, source_revision_id):
    from configurations.evidence import inspect_approved_evidence
    write_barrier(exclusive=True)
    plan, manifest, policy, rules, actor, service, grants = _current_plan(plan_id, actor, auth_epoch, execution=False)
    if expected_manifest_sha256 != plan.manifest_sha256:
        reject('CONTENT_APPROVAL_SCOPE_CHANGED')
    if plan.status == 'approved' and plan.approval and plan.approval.get('source_revision_id') == str(source_revision_id):
        _approval(plan, manifest, policy, rules)
        return plan
    if plan.status != 'preview':
        reject('CONTENT_APPROVAL_SCOPE_CHANGED')
    _, _, reason = inspect_approved_evidence(f'urn:sjia:source-revision:{source_revision_id}',
        purpose='configuration_approval', expected_checksum=plan.manifest_sha256, lock=True)
    if reason:
        reject('CONTENT_APPROVAL_EVIDENCE_UNAVAILABLE')
    _verify_unchanged(manifest)
    now = timezone.now()
    plan.approval = ContentApproval(schema_version='content-disposition-approval-1',
        manifest_sha256=plan.manifest_sha256, policy_sha256=policy.checksum, approver_id=actor.id,
        auth_epoch=auth_epoch, service_grant_id=service.id, case_grant_ids=[row.id for row in grants],
        source_revision_id=source_revision_id, approved_at=now).model_dump(mode='json')
    plan.approved_by = actor; plan.approved_at = now; plan.status = 'approved'
    plan.save(update_fields=['approval', 'approved_by', 'approved_at', 'status'])
    return plan


def _approval(plan, manifest, policy, rules):
    from configurations.evidence import inspect_approved_evidence
    from identity.models import Principal
    approval = ContentApproval.model_validate(plan.approval)
    if (approval.manifest_sha256 != plan.manifest_sha256 or approval.policy_sha256 != policy.checksum
            or approval.approver_id != plan.approved_by_id or approval.approved_at != plan.approved_at
            or approval.approved_at > timezone.now()):
        reject('CONTENT_APPROVAL_UNAVAILABLE')
    approver = Principal.objects.get(id=approval.approver_id)
    _, service, grants = _identity(approver, approval.auth_epoch, plan.patient, rules.access_purpose)
    if service.id != approval.service_grant_id or not set(approval.case_grant_ids) <= {row.id for row in grants}:
        reject('CONTENT_APPROVAL_AUTHORITY_CHANGED')
    _, _, reason = inspect_approved_evidence(f'urn:sjia:source-revision:{approval.source_revision_id}',
        purpose='configuration_approval', expected_checksum=plan.manifest_sha256, lock=True)
    if reason:
        reject('CONTENT_APPROVAL_EVIDENCE_UNAVAILABLE')
    return approval


def _envelope(plan):
    return {'schema_version':'content-disposition-journal-1', 'plan_id':str(plan.id),
        'manifest_sha256':plan.manifest_sha256, 'manifest':plan.manifest, 'approval':plan.approval}


def _invalidate_consumers(manifest):
    from dependencies.models import DependencyNode, DependencyEdge
    from django.db.models import F
    from identity.models import Patient
    ids = [item.object_id if item.kind == 'database' else item.file_id for item in manifest.items]
    pending = set(DependencyNode.objects.filter(resource_id__in=ids).values_list('id',flat=True))
    changed = set()
    while pending - changed:
        current = pending - changed
        DependencyNode.objects.filter(id__in=current).update(validity='invalid')
        changed.update(current)
        pending.update(DependencyEdge.objects.filter(prerequisite_id__in=current).values_list('dependent_id',flat=True))
    Patient.objects.filter(id=manifest.patient_id).update(data_epoch=F('data_epoch')+1,
        verification_epoch=F('verification_epoch')+1)


def _apply_revocation(envelope):
    manifest = ContentManifest.model_validate(envelope['manifest'])
    _assert_exclusive_paths([(item.file_id,item.object_key) for item in manifest.items if item.kind=='local_object'])
    for item in manifest.items:
        label = item.adapter if item.kind == 'database' else 'files.fileobject'
        identity = item.object_id if item.kind == 'database' else item.file_id
        version = item.version if item.kind == 'database' else item.object_version
        existing = ContentAccessRevocation.objects.filter(adapter=label, object_id=identity).first()
        if existing:
            if str(existing.plan_id) != envelope['plan_id'] or existing.object_version != version:
                reject('CONTENT_REVOCATION_IDENTITY_CONFLICT')
            continue
        ContentAccessRevocation.objects.create(plan_id=envelope['plan_id'], patient_id=manifest.patient_id,
            adapter=label, object_id=identity, object_version=version, revoked_at=timezone.now(),
            proof_sha256=digest(item.model_dump(mode='json')))
    _invalidate_consumers(manifest)


def _apply_database(envelope, *, replay=False, generation=1):
    """Only callable inside the exclusive transaction after durable approval."""
    from files.models import FileObject
    manifest = ContentManifest.model_validate(envelope['manifest'])
    _assert_exclusive_paths([(item.file_id,item.object_key) for item in manifest.items if item.kind=='local_object'])
    plan_id = UUID(envelope['plan_id'])
    now = timezone.now()
    scoped_keys = {(row._meta.label_lower, str(row.pk)) for row in patient_rows(manifest.patient_id)}
    for item in manifest.items:
        label = item.adapter if item.kind == 'database' else 'files.fileobject'
        identity = item.object_id if item.kind == 'database' else item.file_id
        version = item.version if item.kind == 'database' else item.object_version
        existing = ContentErasure.objects.filter(adapter=label, object_id=identity).first()
        if existing and (existing.plan_id != plan_id or existing.object_version != version):
            reject('CONTENT_ERASURE_IDENTITY_CONFLICT')
        if not existing:
            ContentErasure.objects.create(plan_id=plan_id, patient_id=manifest.patient_id, adapter=label,
                object_id=identity, object_version=version, generation=generation,
                erased_at=now, proof_sha256=digest(item.model_dump(mode='json')))
    for item in manifest.items:
        if item.kind == 'database':
            model, spec = adapter(item.adapter)
            row = model.all_objects.select_for_update().filter(pk=item.object_id).first()
            if row is None:
                if replay: continue
                reject('CONTENT_TARGET_UNAVAILABLE')
            if row.content_erased_at is not None:
                if row.content_disposition_id != plan_id or not ContentErasure.objects.filter(
                        adapter=item.adapter,object_id=row.pk,patient_id=manifest.patient_id,plan_id=plan_id).exists():
                    reject('CONTENT_ERASURE_IDENTITY_CONFLICT')
                continue
            if (item.adapter, str(row.pk)) not in scoped_keys:
                reject('CONTENT_RESTORE_PATIENT_SCOPE_CONFLICT')
            if not replay and (row_version(row) != item.version or row_digest(row) != item.row_sha256):
                reject('CONTENT_MANIFEST_CHANGED')
            if replay and row_version(row) != item.version:
                # Earlier snapshots of a mutable UUID are covered; a newer
                # restored version or non-monotonic identity remains blocked.
                old = row_version(row)
                if not old.isdigit() or not item.version.isdigit() or int(old) > int(item.version):
                    reject('CONTENT_RESTORE_VERSION_CONFLICT')
            values = {name:None for name in spec['payload_fields']}
            values.update(content_erased_at=now, content_disposition_id=plan_id)
            model.all_objects.filter(pk=row.pk).update(**values)
        else:
            file = FileObject.objects.select_for_update().filter(id=item.file_id).first()
            if file is None:
                if replay: continue
                reject('CONTENT_TARGET_UNAVAILABLE')
            if (file.patient_id != manifest.patient_id or file.object_version != item.object_version
                    or file.object_key != item.object_key or file.checksum != item.checksum or file.size_bytes != item.size_bytes):
                reject('CONTENT_RESTORE_FILE_IDENTITY_CONFLICT')
            FileObject.objects.filter(id=file.id).update(status='disposed')
    _invalidate_consumers(manifest)


def _apply_local_objects(envelope, *, replay=False):
    from files.deletion import delete_local_object, object_path
    from files.service import object_store
    manifest=ContentManifest.model_validate(envelope['manifest'])
    _assert_exclusive_paths([(item.file_id,item.object_key) for item in manifest.items if item.kind=='local_object'])
    for item in manifest.items:
        if item.kind == 'local_object':
            if replay and not object_path(object_store, item.object_key).exists():
                # A pre-creation backup can legitimately lack this exact object.
                # No deletion receipt is fabricated; proof is absence on replay.
                continue
            operation_id = item.operation_id
            if replay:
                from retention.models import ContentRestoreState
                state = ContentRestoreState.objects.filter(singleton=True).first()
                if state is None:
                    reject('CONTENT_RESTORE_IDENTITY_REQUIRED')
                operation_id = uuid5(item.operation_id, f'restore:{state.id}')
            delete_local_object(object_store, operation_id=operation_id, object_key=item.object_key,
                checksum=item.checksum, size_bytes=item.size_bytes)


def execute_plan(*, plan_id, actor, auth_epoch, expected_manifest_sha256):
    from retention.content_journal import append_manifest
    # A caller's outer transaction would roll back SQL fences after unlink.
    if connection.in_atomic_block:
        reject('CONTENT_OUTER_TRANSACTION_FORBIDDEN')
    with transaction.atomic():
        write_barrier(exclusive=True)
        plan, manifest, policy, rules, actor, service, grants = _current_plan(plan_id, actor, auth_epoch, execution=True)
        if expected_manifest_sha256 != plan.manifest_sha256:
            reject('CONTENT_APPROVAL_SCOPE_CHANGED')
        _approval(plan, manifest, policy, rules)
        if plan.status in ('retained', 'access_revoked', 'logical_erasure_complete'):
            if plan.status == 'logical_erasure_complete' and not verify_manifest(_envelope(plan)):
                reject('CONTENT_RECEIPT_NOT_VERIFIED')
            return plan
        if plan.status not in ('approved', 'erasing'):
            reject('CONTENT_PLAN_NOT_APPROVED')
        if plan.status == 'approved':
            _verify_unchanged(manifest)
        if rules.mode == 'retain':
            plan.status='retained'; plan.completed_at=timezone.now()
            plan.receipt={'schema_version':'content-disposition-receipt-1','logical_payload_erased':False,
                'all_copies_erased':False,'retained_until':rules.metadata_keep_until.isoformat(),
                'recheck_at':rules.recheck_at.isoformat()}
            plan.save(update_fields=['status','completed_at','receipt']); return plan
        envelope = _envelope(plan)
        entry = append_manifest(envelope)  # must fsync before any SQL payload loss
        if rules.mode == 'revoke_access':
            _apply_revocation(envelope)
            plan.status='access_revoked'; plan.completed_at=timezone.now(); plan.journal_sequence=entry['sequence']
            plan.receipt={'schema_version':'content-disposition-receipt-1','logical_payload_erased':False,
                'all_copies_erased':False,'access_revoked':True,'journal_sequence':entry['sequence']}
            plan.save(update_fields=['status','completed_at','journal_sequence','receipt']); return plan
        _apply_database(envelope, generation=entry['sequence'])
        plan.status='erasing'; plan.journal_sequence=entry['sequence']
        plan.save(update_fields=['status','journal_sequence'])
    # DB payload and API/producer fences commit before irreversible object I/O.
    # A crash here resumes by exact plan/journal/operation ID, never recreates body.
    _apply_local_objects(envelope)
    with transaction.atomic():
        write_barrier(exclusive=True)
        plan=ContentDispositionPlan.objects.select_for_update().get(id=plan_id)
        if not verify_manifest(envelope):
            reject('CONTENT_RECEIPT_NOT_VERIFIED')
        plan.status='logical_erasure_complete'; plan.completed_at=timezone.now()
        plan.receipt={'schema_version':'content-disposition-receipt-1',
            'manifest_sha256':plan.manifest_sha256,'journal_sequence':plan.journal_sequence,
            'logical_payload_erased':True,'all_copies_erased':False,
            'unverified_copy_obligations':plan.manifest['obligations'],
            'proof_scope':'current_database_payload_and_controlled_local_path_absence'}
        plan.save(update_fields=['status','completed_at','receipt'])
        return plan


def replay_manifest(envelope):
    """Restore only: trusted independent journal; never needs live-policy enable."""
    from retention.content_journal import validate_envelope, configured_journal
    validate_envelope(envelope)
    entries = configured_journal().verify()['entries']
    journal_entry = next((entry for entry in entries if entry['envelope'] == envelope), None)
    if journal_entry is None:
        reject('CONTENT_REPLAY_NOT_JOURNALED')
    manifest=ContentManifest.model_validate(envelope['manifest'])
    if manifest.registry_sha256 != REGISTRY_SHA256 or manifest.preservation_sha256 != PRESERVATION_SHA256:
        reject('CONTENT_RESTORE_ADAPTER_VERSION_UNAVAILABLE')
    if manifest.mode not in ('erase_selected_content','revoke_access'):
        reject('CONTENT_RESTORE_MODE_UNSUPPORTED')
    with transaction.atomic():
        write_barrier(exclusive=True)
        if manifest.mode == 'revoke_access':
            _apply_revocation(envelope)
        else:
            _apply_database(envelope,replay=True,generation=journal_entry['sequence'])
    if manifest.mode == 'erase_selected_content':
        _apply_local_objects(envelope,replay=True)
    return verify_manifest(envelope)


def verify_manifest(envelope):
    from files.deletion import object_path
    from files.models import FileObject
    from files.service import object_store
    manifest=ContentManifest.model_validate(envelope['manifest'])
    try:
        _assert_exclusive_paths([(item.file_id,item.object_key) for item in manifest.items if item.kind=='local_object'])
    except DispositionRejected:
        return False
    if manifest.mode == 'revoke_access':
        return all(ContentAccessRevocation.objects.filter(plan_id=envelope['plan_id'],
            adapter=item.adapter if item.kind=='database' else 'files.fileobject',
            object_id=item.object_id if item.kind=='database' else item.file_id,
            proof_sha256=digest(item.model_dump(mode='json'))).exists() for item in manifest.items)
    for item in manifest.items:
        label=item.adapter if item.kind=='database' else 'files.fileobject'
        identity=item.object_id if item.kind=='database' else item.file_id
        if not ContentErasure.objects.filter(adapter=label,object_id=identity,
                plan_id=envelope['plan_id'],proof_sha256=digest(item.model_dump(mode='json'))).exists():
            return False
        if item.kind=='database':
            model,spec=adapter(item.adapter)
            row=model.all_objects.filter(pk=item.object_id).first()
            if row is not None and (row.content_erased_at is None or str(row.content_disposition_id)!=envelope['plan_id']
                    or any(getattr(row,name) is not None for name in spec['payload_fields'])):
                return False
        else:
            file=FileObject.objects.filter(id=item.file_id).first()
            if file is not None and (file.status!='disposed' or file.object_version!=item.object_version
                    or file.object_key!=item.object_key or file.checksum!=item.checksum or file.size_bytes!=item.size_bytes):
                return False
            if object_path(object_store,item.object_key).exists():
                return False
            if file is not None:
                from files.deletion import read_deletion_receipt, deletion_keys
                from retention.models import ContentRestoreState
                operation_id = item.operation_id
                state = ContentRestoreState.objects.filter(singleton=True).first()
                if state is not None:
                    restored_id = uuid5(item.operation_id, f'restore:{state.id}')
                    if object_path(object_store,deletion_keys(restored_id)[1]).exists():
                        operation_id = restored_id
                try:
                    read_deletion_receipt(object_store,operation_id=operation_id,
                        object_key=item.object_key,checksum=item.checksum,size_bytes=item.size_bytes)
                except Exception:
                    return False
    return True


@transaction.atomic
def read_plan(*, plan_id, actor, auth_epoch):
    plan = ContentDispositionPlan.objects.select_for_update().get(id=plan_id)
    rules = ContentPolicyRules.model_validate(plan.policy.rules)
    _identity(actor, auth_epoch, plan.patient, rules.access_purpose)
    return plan
