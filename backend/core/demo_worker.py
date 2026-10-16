"""Explicit, provider-free consumer for the dedicated fictional family dataset.

This is not a Celery worker or a replacement registry. It only transports two
local event types to their existing services, preserving their authority,
transactions, task leases and receipts. Reconciliation remains support-only:
the family account cannot authorize that service and this consumer never runs it.
"""
import json
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.evidence import artifact_verification_scope, inspect_approved_evidence
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.schemas import validate_payload
from core.demo_experience import KEY, NAMESPACE, RIGHTS, identity, require_test_context
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal, PrincipalIdentity
from relations.models import Relation
from retention.content_fences import content_reader
from sources.models import DataUseAuthorization
from tasks.models import ConsumerReceipt, OutboxEvent, Task


RUNTIME_TASKS = frozenset({'reconcile', 'file_validation', 'summary'})
LOCAL_EVENTS = {
    'file_validation.requested': ('file_validation', 'file-validation-task-1'),
    'summary.requested': ('summary', 'summary-task-2'),
}
PATIENTS = {'child_a': '安安（示例）', 'child_b': '乐乐（示例）'}
TERMINAL = ('completed', 'failed', 'cancelled', 'superseded')


@dataclass(frozen=True)
class DemoWorkerScope:
    actor_id: UUID
    patient_ids: tuple
    environment: str
    runtime_id: UUID
    runtime_checksum: str
    lease_seconds: int
    retry_seconds: int


def _account_arguments(username, principal_id):
    if not isinstance(username, str) or not username.strip() or username != username.strip():
        raise CommandError('DEMO_REQUIRES_EXACT_EXISTING_USERNAME')
    try:
        return UUID(str(principal_id))
    except (ValueError, TypeError, AttributeError):
        raise CommandError('DEMO_REQUIRES_EXACT_EXISTING_PRINCIPAL') from None


def require_local_runtime(runtime, environment):
    """Fail closed for widened tasks, providers or a different seed/runtime."""
    payload = runtime.payload
    tasks = payload.get('tasks') if isinstance(payload, dict) else None
    if (runtime.kind != 'provider' or runtime.schema_version != 'backend-runtime-1'
            or runtime.config_key != f'{KEY}:runtime' or runtime.status != 'enabled'
            or runtime.source_revision_id != identity('source-revision')
            or not isinstance(payload, dict) or payload.get('environment') != environment
            or payload.get('providers') != [] or not isinstance(tasks, list)
            or len(tasks) != len(RUNTIME_TASKS)
            or any(not isinstance(row, dict) or not isinstance(row.get('task_type'), str) for row in tasks)
            or {row.get('task_type') for row in tasks} != RUNTIME_TASKS):
        raise CommandError('DEMO_WORKER_REQUIRES_RESTRICTED_LOCAL_RUNTIME')
    validate_payload(runtime.kind, runtime.schema_version, payload)


@transaction.atomic
def worker_scope(*, username, principal_id, acknowledge_test_only=False):
    """Read and lock the existing seed identity; never provision or repair it."""
    require_test_context(acknowledge_test_only=acknowledge_test_only)
    principal_id = _account_arguments(username, principal_id)
    environment = settings.SJIA_ENVIRONMENT
    # Gate -> principal -> patients follows normal service lock ordering. Only
    # non-secret principal fields are selected here; execute_* retain their own
    # unchanged current-authority checks.
    gate = EnvironmentGate.objects.select_for_update().filter(environment=environment, enabled=True).first()
    if gate is None or gate.runtime_config_id is None:
        raise CommandError('DEMO_EXISTING_TEST_RUNTIME_REQUIRED')
    if not PrincipalIdentity.objects.filter(provider='self', provider_subject=username,
            principal_id=principal_id).exists():
        raise CommandError('DEMO_ACCOUNT_IDENTITY_MISMATCH')
    actor = Principal.objects.select_for_update().only('id', 'account_status').get(id=principal_id)
    if actor.account_status != 'active':
        raise CommandError('DEMO_ACCOUNT_NOT_ACTIVE')
    if ServiceRoleGrant.objects.filter(principal_id=actor.id, revoked_at__isnull=True).exists():
        raise CommandError('DEMO_REQUIRES_FAMILY_ONLY_ACCOUNT')
    patient_ids = tuple(identity(f'patient:{key}') for key in PATIENTS)
    if (Relation.objects.filter(guardian_id=actor.id).exclude(patient_id__in=patient_ids).exists()
            or AccessGrant.objects.filter(principal_id=actor.id).exclude(patient_id__in=patient_ids).exists()):
        raise CommandError('DEMO_ACCOUNT_ALREADY_HAS_OTHER_PATIENT_SCOPE')
    runtime = ConfigVersion.objects.select_for_update().get(id=gate.runtime_config_id)
    require_local_runtime(runtime, environment)
    reason = configuration_qualification_reason(runtime, environment)
    if reason:
        raise CommandError(f'DEMO_WORKER_RUNTIME_UNAVAILABLE:{reason}')
    reference = f'urn:sjia:source-revision:{identity("source-revision")}'
    revision, content, reason = inspect_approved_evidence(reference, purpose='software_test')
    if reason:
        raise CommandError(f'DEMO_WORKER_DATASET_UNAVAILABLE:{reason}')
    if (revision.source_id != identity('source') or revision.rights_evidence_ref != RIGHTS
            or revision.source.source_key != KEY or revision.source.owner != KEY
            or revision.source.purpose != 'software_test' or revision.source.sensitivity != 'synthetic'):
        raise CommandError('DEMO_WORKER_DATASET_IDENTITY_MISMATCH')
    try:
        proof = json.loads(content)
    except (ValueError, TypeError):
        raise CommandError('DEMO_WORKER_DATASET_IDENTITY_MISMATCH') from None
    if (not isinstance(proof, dict) or proof.get('namespace') != str(NAMESPACE)
            or proof.get('account_principal_id') != str(actor.id) or proof.get('environment') != environment
            or proof.get('mode') != 'fictional_family_experience' or proof.get('purpose') != 'software_test'
            or proof.get('clinical_validation') is not False or proof.get('medical_approval') is not False
            or proof.get('external_providers') != []):
        raise CommandError('DEMO_DATASET_OWNER_MISMATCH')
    now = timezone.now()
    for key, display_id in PATIENTS.items():
        patient_id = identity(f'patient:{key}')
        if not Patient.objects.select_for_update().filter(id=patient_id, display_id=display_id,
                status='active').exists():
            raise CommandError('DEMO_PATIENT_CONFLICT')
        if not Relation.objects.filter(id=identity(f'relation:{key}'), patient_id=patient_id,
                guardian_id=actor.id, application_id=identity(f'application:{key}'), status='active',
                revoked_at__isnull=True, valid_from__lte=now, application__applicant_id=actor.id,
                application__patient_id=patient_id, application__status='approved',
                application__evidence_ref=reference).filter(
                    Q(valid_until__isnull=True) | Q(valid_until__gt=now)).exists():
            raise CommandError('DEMO_RELATION_CONFLICT')
        if not DataUseAuthorization.objects.filter(id=identity(f'use:{key}'), patient_id=patient_id,
                source_revision_id=revision.id, environment=environment, purpose='family_observation',
                evidence_ref=reference, revoked_at__isnull=True, approved_at__lte=now).filter(
                    Q(valid_until__isnull=True) | Q(valid_until__gt=now)).exists():
            raise CommandError('DEMO_USE_AUTHORIZATION_CONFLICT')
    policies = [row for row in runtime.payload['tasks'] if row['task_type'] != 'reconcile']
    return DemoWorkerScope(actor.id, patient_ids, environment, runtime.id, runtime.checksum,
        max(row['visibility_timeout_seconds'] for row in policies),
        max(row['heartbeat_seconds'] for row in policies))


def scoped_events(scope):
    """Subqueries avoid joining/locking unrelated task or patient rows."""
    predicate = Q(pk__in=[])
    for event_type, (task_type, schema) in LOCAL_EVENTS.items():
        tasks = Task.objects.filter(actor_id=scope.actor_id, patient_id__in=scope.patient_ids,
            task_type=task_type, schema_version=schema,
            input_refs__context__environment=scope.environment,
            input_refs__context__actor_id=str(scope.actor_id), input_refs__context__audience='family',
            input_refs__context__runtime_id=str(scope.runtime_id),
            input_refs__context__runtime_checksum=scope.runtime_checksum)
        # Bind the JSON patient as well as the relational patient, independently
        # for each dedicated ID; a cross-patient snapshot is not eligible.
        patients = Q(pk__in=[])
        for patient_id in scope.patient_ids:
            patients |= Q(patient_id=patient_id, input_refs__context__patient_id=str(patient_id))
        predicate |= Q(event_type=event_type, schema_version=schema,
            task_id__in=tasks.filter(patients).values('id'))
    return OutboxEvent.objects.filter(predicate)


def admit_family_worker(**options):
    """The executable entrypoint must admit its real dependencies even in DEBUG."""
    from config.runtime_admission import admit_process_runtime
    # Restrict the dataset before normal admission inspects runtime dependencies.
    worker_scope(**options)
    admit_process_runtime('worker')
    require_family_worker_admission()


def require_family_worker_admission():
    """A changed runtime requires a restart, never a silently refreshed snapshot."""
    from config.runtime_admission import RuntimeAdmissionRejected, admission_status
    result = admission_status()
    loaded = result['loaded_config_version']
    if (result['state'] != 'admitted_current' or loaded is None
            or loaded.get('process_kind') != 'worker'):
        raise RuntimeAdmissionRejected(result['reason_code'] or 'PROCESS_RUNTIME_NOT_ADMITTED')
    for kind in ('file_validation', 'summary'):
        capability = result['capabilities'].get(kind, {})
        if capability.get('state') not in ('configured', 'budget_configured'):
            raise RuntimeAdmissionRejected(capability.get('reason_code') or 'RUNTIME_LOCAL_CAPABILITY_UNAVAILABLE')


@transaction.atomic
def _claim_one(*, scope, owner):
    now = timezone.now()
    event = scoped_events(scope).select_for_update(skip_locked=True).filter(
        published_at__isnull=True, available_at__lte=now).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lte=now)).order_by('available_at', 'id').first()
    if event is None:
        return None
    OutboxEvent.objects.filter(id=event.id).update(lease_owner=owner,
        lease_until=now + timedelta(seconds=scope.lease_seconds), lease_generation=F('lease_generation') + 1)
    event.refresh_from_db()
    return event


def execute_local_event(event, owner):
    """Closed dispatch, intentionally independent of the global worker registry."""
    if event.event_type == 'file_validation.requested' and event.schema_version == 'file-validation-task-1':
        from files.validation import execute_validation
        return execute_validation(event, owner)
    if event.event_type == 'summary.requested' and event.schema_version == 'summary-task-2':
        from summaries.jobs import execute_summary
        return execute_summary(event, owner)
    raise CommandError('DEMO_WORKER_EVENT_NOT_ALLOWED')


def _settle(event, owner, scope, *, delay_seconds=None):
    """Only transport metadata is written here; business effects need receipts."""
    consumer = LOCAL_EVENTS[event.event_type][0]
    task = Task.objects.get(id=event.task_id, actor_id=scope.actor_id, patient_id__in=scope.patient_ids)
    status, effect = task.status, task.id
    if status == 'completed':
        if consumer == 'summary':
            from summaries.models import Summary
            effect = Summary.objects.filter(task_id=task.id, patient_id=task.patient_id,
                requested_by_id=scope.actor_id).values_list('id', flat=True).first()
        else:
            from files.models import FileGroup
            effect = FileGroup.objects.filter(id=task.input_refs['context']['group_id'],
                patient_id=task.patient_id, owner_id=scope.actor_id, status='available').values_list('id', flat=True).first()
    terminal = status in TERMINAL and effect is not None and ConsumerReceipt.objects.filter(
        event_id=event.id, consumer=consumer, effect_resource_id=effect).exists()
    now = timezone.now()
    updates = {'lease_owner': None, 'lease_until': None}
    if terminal:
        updates['published_at'] = now
    else:
        updates['available_at'] = now + timedelta(seconds=delay_seconds or scope.retry_seconds)
    changed = OutboxEvent.objects.filter(id=event.id, lease_owner=owner,
        lease_generation=event.lease_generation, published_at__isnull=True).update(**updates)
    return status if terminal and changed else 'deferred'


def consume_family_events_once(*, username, principal_id, acknowledge_test_only=False, limit=25):
    """Process a bounded batch; no broker, generic dispatcher or outbound calls."""
    require_test_context(acknowledge_test_only=acknowledge_test_only)
    _account_arguments(username, principal_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CommandError('DEMO_WORKER_LIMIT_MUST_BE_1_TO_100')
    from files.validation import ValidationDeferred
    from summaries.jobs import SummaryDeferred
    owner = f'family-experience-local:{uuid4()}'
    result = {'claimed': 0, 'completed': 0, 'failed': 0, 'cancelled': 0, 'superseded': 0,
        'deferred': 0, 'errors': 0}
    for _ in range(limit):
        # The lifetime fence is also used by the normal worker. Do not wrap the
        # execution in one outer DB transaction: its durable lease/adoption split
        # and heartbeats must remain unchanged.
        with content_reader(), artifact_verification_scope():
            scope = worker_scope(username=username, principal_id=principal_id,
                acknowledge_test_only=acknowledge_test_only)
            event = _claim_one(scope=scope, owner=owner)
            if event is None:
                break
            result['claimed'] += 1
            # Check again immediately before dispatch. execute_* then recheck
            # their own current authority and exact immutable admission.
            current = worker_scope(username=username, principal_id=principal_id,
                acknowledge_test_only=acknowledge_test_only)
            if current != scope or not scoped_events(current).filter(id=event.id).exists():
                raise CommandError('DEMO_WORKER_SCOPE_CHANGED')
            delay = None
            try:
                execute_local_event(event, owner)
            except (ValidationDeferred, SummaryDeferred) as deferred:
                delay = deferred.seconds
            except CommandError:
                raise
            except Exception:
                # The real services record their own fenced failures. Never
                # synthesize a completed/failed task or expose input/exception
                # text in a worker log. Unknown failures remain retryable.
                result['errors'] += 1
            result[_settle(event, owner, scope, delay_seconds=delay)] += 1
    return result
