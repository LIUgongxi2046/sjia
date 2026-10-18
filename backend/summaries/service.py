from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Exists, OuterRef

from summaries.models import Summary, SummaryItem
from configurations.models import ConfigVersion
from configurations.evidence import artifact_verification_scope
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from identity.models import Patient
from records.models import Record, RecordVersion, RecordField
from records.service import read_record
from tasks.models import Task
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.gate_locks import read_environment_gate_shared
from family_content.qualification import release_qualification_reason


def summary_publication_gate(environment, template, visibility):
    if any(configuration_qualification_reason(config, environment) for config in (template, visibility)):
        return None
    gate = EnvironmentGate.objects.filter(environment=environment, enabled=True).select_related('active_release').first()
    if gate is None or gate.active_release_id is None or release_qualification_reason(gate.active_release):
        return None
    components = {item.component_kind: item.config for item in ReleaseComponent.objects.filter(release=gate.active_release).select_related('config')}
    if (components['visibility'].id != visibility.id or template.payload['purpose'] != components['content'].payload['purpose']
            or not set(template.payload['field_codes']) <= set(visibility.payload['field_codes'])):
        return None
    return gate


def summary_publication_current(summary, inputs):
    gate = summary_publication_gate(inputs.get('environment'), summary.template, summary.visibility_policy)
    return bool(gate is not None and inputs.get('release_id') == str(gate.active_release_id)
        and inputs.get('publication_epoch') == str(gate.publication_epoch))


def current_summary_config(kind, environment, *, lock=True):
    gate = EnvironmentGate.objects.filter(environment=environment, enabled=True).first()
    if gate is None or gate.active_release_id is None:
        raise SummaryRejected('SUMMARY_PUBLICATION_UNAVAILABLE')
    components = {row.component_kind: row.config for row in ReleaseComponent.objects.filter(
        release_id=gate.active_release_id).select_related('config')}
    if 'visibility' not in components or 'content' not in components:
        raise SummaryRejected('SUMMARY_PUBLICATION_UNAVAILABLE')
    if kind == 'visibility':
        config = components['visibility']
        if configuration_qualification_reason(config, environment) is not None:
            raise SummaryRejected('SUMMARY_CONFIGURATION_PENDING')
        return config
    configs = summary_configuration_candidates(kind, environment, components, lock=lock)
    if len(configs) != 1:
        raise SummaryRejected('SUMMARY_CONFIGURATION_PENDING')
    return configs[0]


def summary_configuration_candidates(kind, environment, components, *, lock=True):
    configs = ConfigVersion.objects.filter(kind=kind, status='enabled', schema_version=f'{kind}-1')
    if lock:
        configs = configs.select_for_update()
    return [config for config in configs
        if config.payload['purpose'] == components['content'].payload['purpose']
        and set(config.payload['field_codes']) <= set(components['visibility'].payload['field_codes'])
        and configuration_qualification_reason(config, environment) is None]


def field_date(field, zone):
    time = field['medical_time']
    if not time:
        return None
    if time['instant']:
        return datetime.fromisoformat(time['instant']).astimezone(zone).date()
    if time['local_date']:
        return datetime.fromisoformat(time['local_date']).date()
    if time['local_datetime']:
        return datetime.fromisoformat(time['local_datetime']).date()
    return None


def summary_record_fields(record, *, from_date, to_date, zone, include_unknown_time, permitted):
    therapy_in_scope = False
    if record['kind'] == 'therapy':
        boundaries = {field['field_code']: field_date(field, zone) for field in record['fields'] if field['field_code'] in ('therapy_start', 'therapy_end')}
        start, end = boundaries.get('therapy_start'), boundaries.get('therapy_end')
        ongoing = any(field['field_code'] == 'therapy_status' and field['value_state'] == 'known' and field['categorical_value'] == 'ongoing' for field in record['fields'])
        therapy_in_scope = bool(
            (start and end and start <= to_date and end >= from_date)
            or (start and from_date <= start <= to_date)
            or (end and from_date <= end <= to_date)
            or (ongoing and start and start <= to_date)
        )
    selected = []
    for field in record['fields']:
        if field['field_code'] not in permitted:
            continue
        actual_date = field_date(field, zone)
        if actual_date is None and not include_unknown_time and not therapy_in_scope:
            continue
        if actual_date is not None and not from_date <= actual_date <= to_date and not therapy_in_scope:
            continue
        selected.append(field)
    return selected


@transaction.atomic
def generate_summary(*, patient, requested_by, from_date, to_date, scope_timezone, include_unknown_time, field_codes, environment, record_kinds=None, include_source_status=None, execution_task=None):
    if to_date < from_date:
        raise SummaryRejected('DATE_RANGE_INVALID')
    zone = ZoneInfo(scope_timezone)
    read_environment_gate_shared(environment)
    template = current_summary_config('summary', environment)
    visibility = current_summary_config('visibility', environment)
    gate = summary_publication_gate(environment, template, visibility)
    if gate is None:
        raise SummaryRejected('SUMMARY_PUBLICATION_UNAVAILABLE')
    template_fields = template.payload.get('field_codes')
    visible_fields = visibility.payload.get('field_codes')
    if not isinstance(template_fields, list) or not isinstance(visible_fields, list):
        raise SummaryRejected('SUMMARY_CONFIGURATION_PENDING')
    permitted = set(field_codes) & set(template_fields) & set(visible_fields)
    patient = Patient.objects.select_for_update(no_key=True).get(id=patient.id)
    current_records = Record.objects.filter(patient=patient, status='active', id=OuterRef('record_id'), current_version=OuterRef('version'))
    versions = RecordVersion.objects.filter(patient=patient).filter(Exists(current_records)).order_by('record_id')
    snapshots = []
    for version in versions:
        record = read_record(version.record_id, patient)
        if record_kinds is not None and record['kind'] != 'professional' and record['kind'] not in record_kinds:
            continue
        for field in summary_record_fields(record, from_date=from_date, to_date=to_date, zone=zone,
            include_unknown_time=include_unknown_time, permitted=permitted):
            snapshots.append({**field, 'record_id': record['record_id'], 'record_version_id': record['record_version_id'], 'kind': record['kind'], 'origin_type': record['origin_type'], 'source_organization': record['source_organization'], 'quality_status': record['quality_status'],
                'source_definition_revision_id': str(version.source_revision_id) if version.source_revision_id else None})
    if not snapshots:
        raise SummaryRejected('SUMMARY_SCOPE_EMPTY')
    now = datetime.now(timezone.utc)
    inputs = {'patient_id': str(patient.id), 'from_date': from_date.isoformat(), 'to_date': to_date.isoformat(), 'scope_timezone': scope_timezone, 'include_unknown_time': include_unknown_time, 'template_id': str(template.id), 'visibility_policy_id': str(visibility.id), 'fields': snapshots}
    inputs.update({'record_kinds': record_kinds, 'include_source_status': include_source_status,
        'environment': environment, 'template_checksum': template.checksum, 'visibility_checksum': visibility.checksum,
        'release_id': str(gate.active_release_id), 'publication_epoch': str(gate.publication_epoch)})
    task = execution_task or Task.objects.create(actor=requested_by, patient=patient, task_type='summary', schema_version='task-1', input_refs=inputs, input_sha256=compute_checksum(inputs), status='running', status_version=1, created_at=now, deadline_at=now + timedelta(seconds=30))
    if (task.patient_id != patient.id or task.actor_id != requested_by.id or task.task_type != 'summary'
            or task.status != 'running' or task.deadline_at <= now):
        raise SummaryRejected('SUMMARY_TASK_INVALID')
    summary = create_summary(patient=patient, requested_by=requested_by, from_date=from_date, to_date=to_date, scope_timezone=scope_timezone, include_unknown_time=include_unknown_time, template=template, visibility_policy=visibility, task_id=task.id)
    if execution_task is not None:
        summary.execution_snapshot, summary.execution_sha256 = inputs, compute_checksum(inputs)
        summary.save(update_fields=['execution_snapshot', 'execution_sha256'])
    for ordinal, snapshot in enumerate(snapshots, start=1):
        add_summary_item(summary=summary, record_version_id=snapshot['record_version_id'], field_id=snapshot['field_id'], ordinal=ordinal, snapshot=snapshot)
    finished_at = datetime.now(timezone.utc)
    if task.deadline_at <= finished_at or (execution_task is not None and
            (task.lease_until is None or task.lease_until <= finished_at)):
        raise SummaryRejected('SUMMARY_DEADLINE_EXCEEDED')
    task.status = 'completed'
    task.status_version += 1
    task.save(update_fields=['status', 'status_version'])
    return summary


class SummaryRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@transaction.atomic
def create_summary(*, patient, requested_by, from_date, to_date, scope_timezone, include_unknown_time, template, visibility_policy, task_id) -> Summary:
    if to_date < from_date:
        raise SummaryRejected("DATE_RANGE_INVALID")
    return Summary.objects.create(
        patient=patient,
        requested_by=requested_by,
        from_date=from_date,
        to_date=to_date,
        scope_timezone=scope_timezone,
        include_unknown_time=include_unknown_time,
        template=template,
        visibility_policy=visibility_policy,
        data_epoch=patient.data_epoch,
        verification_epoch=patient.verification_epoch,
        validity=Summary.Validity.VALID,
        task_id=task_id,
        generated_at=datetime.now(timezone.utc),
    )


@transaction.atomic
def add_summary_item(*, summary, record_version_id, field_id, ordinal, snapshot) -> SummaryItem:
    return SummaryItem.objects.create(
        summary_id=summary.id,
        patient_id=summary.patient_id,
        record_version_id=record_version_id,
        field_id=field_id,
        ordinal=ordinal,
        snapshot=snapshot,
    )


def needs_regeneration(summary: Summary, patient) -> bool:
    return (
        patient.status != 'active'
        or summary.validity == Summary.Validity.INVALID
        or patient.data_epoch > summary.data_epoch
        or patient.verification_epoch > summary.verification_epoch
        or summary.template.status != 'enabled'
        or summary.visibility_policy.status != 'enabled'
    )


@transaction.atomic
def approved_snapshot_config(config, checksum, environment):
    if checksum != config.checksum or configuration_qualification_reason(config, environment) is not None:
        return False
    gate = EnvironmentGate.objects.filter(environment=environment, enabled=True).first()
    if gate is None or gate.active_release_id is None:
        return False
    components = {row.component_kind: row.config for row in ReleaseComponent.objects.filter(
        release_id=gate.active_release_id).select_related('config')}
    if 'visibility' not in components or 'content' not in components:
        return False
    if config.kind == 'visibility':
        return components['visibility'].id == config.id
    return [row.id for row in summary_configuration_candidates(config.kind, environment, components, lock=False)] == [config.id]


@transaction.atomic
@artifact_verification_scope()
def read_summary(summary_id, patient) -> dict:
    summary = Summary.objects.select_related('template', 'visibility_policy').filter(id=summary_id, patient=patient).first()
    if summary is None:
        raise SummaryRejected("SUMMARY_NOT_FOUND")
    items = list(SummaryItem.objects.filter(summary_id=summary.id, patient_id=patient.id).order_by("ordinal"))
    task = Task.objects.get(id=summary.task_id, patient=patient, actor=summary.requested_by, task_type='summary')
    if compute_checksum(task.input_refs) != task.input_sha256:
        raise SummaryRejected('SUMMARY_TASK_INPUT_INVALID')
    inputs = task.input_refs
    if task.schema_version == 'summary-task-2':
        inputs = summary.execution_snapshot
        if (task.status != 'completed' or not isinstance(inputs, dict)
                or compute_checksum(inputs) != summary.execution_sha256):
            raise SummaryRejected('SUMMARY_EXECUTION_SNAPSHOT_INVALID')
        from summaries.inputs import SummaryInput
        scope_input = SummaryInput.model_validate(task.input_refs['scope']).model_dump()
        if any(inputs.get(key) != value for key, value in scope_input.items()):
            raise SummaryRejected('SUMMARY_TASK_SNAPSHOT_MISMATCH')
        admission = task.input_refs['context']
        if any(inputs.get(key) != admission.get(key) for key in ('environment', 'template_id',
                'visibility_policy_id', 'template_checksum', 'visibility_checksum', 'release_id', 'publication_epoch')):
            raise SummaryRejected('SUMMARY_TASK_SNAPSHOT_MISMATCH')
    scope = {'patient_id': str(patient.id), 'from_date': summary.from_date.isoformat(), 'to_date': summary.to_date.isoformat(),
        'scope_timezone': summary.scope_timezone, 'include_unknown_time': summary.include_unknown_time,
        'template_id': str(summary.template_id), 'visibility_policy_id': str(summary.visibility_policy_id)}
    if any(inputs.get(key) != value for key, value in scope.items()) or inputs.get('fields') != [item.snapshot for item in items]:
        raise SummaryRejected('SUMMARY_TASK_SNAPSHOT_MISMATCH')
    for item in items:
        if item.snapshot['record_version_id'] != str(item.record_version_id) or item.snapshot['field_id'] != str(item.field_id):
            raise SummaryRejected('SUMMARY_SOURCE_INVALID')
    stale = needs_regeneration(summary, patient) or not summary_publication_current(summary, inputs) or not approved_snapshot_config(summary.template,
        inputs.get('template_checksum'), inputs.get('environment')) or not approved_snapshot_config(summary.visibility_policy,
        inputs.get('visibility_checksum'), inputs.get('environment'))
    from summaries.trends import build_summary_trends
    return {
        "trend_projection": None if stale else build_summary_trends([item.snapshot for item in items], compute_checksum(inputs)),
        "patient_id": str(patient.id), "display_id": patient.display_id, "task_id": str(task.id),
        "summary_id": str(summary.id),
        "from_date": summary.from_date.isoformat(),
        "to_date": summary.to_date.isoformat(),
        "scope_timezone": summary.scope_timezone,
        "include_unknown_time": summary.include_unknown_time,
        "validity": summary.validity,
        "generated_at": summary.generated_at.isoformat(),
        "needs_regeneration": stale,
        "record_kinds": inputs.get('record_kinds'),
        "include_source_status": inputs.get('include_source_status'),
        "environment": inputs.get('environment'),
        "template_id": str(summary.template_id), "visibility_policy_id": str(summary.visibility_policy_id),
        "template_checksum": inputs.get('template_checksum'),
        "visibility_checksum": inputs.get('visibility_checksum'),
        "items": [
            {
                "record_version_id": str(item.record_version_id),
                "field_id": str(item.field_id),
                "ordinal": item.ordinal,
                "snapshot": snapshot_view(item.snapshot),
            }
            for item in items if not stale
        ],
    }


def snapshot_view(snapshot):
    metadata = ('record_id', 'record_version_id', 'kind', 'origin_type', 'source_organization', 'quality_status')
    field = {key: value for key, value in snapshot.items() if key not in metadata and key != 'source_definition_revision_id'}
    field['confirmation_id'] = snapshot.get('confirmation_id')
    field['source_anchor'] = snapshot.get('source_anchor')
    return {**{key: snapshot[key] for key in metadata}, 'field': field,
        'source_definition_revision_id': snapshot.get('source_definition_revision_id'),
        'confirmation_snapshot_state': 'recorded' if 'confirmation_id' in snapshot else 'not_recorded',
        'source_anchor_snapshot_state': 'recorded' if 'source_anchor' in snapshot else 'not_recorded'}


@transaction.atomic
@artifact_verification_scope()
def list_summaries(patient, field_codes=None):
    rows = []
    for summary in Summary.objects.select_related('template', 'visibility_policy').filter(patient=patient).order_by('-generated_at', '-id'):
        task = Task.objects.get(id=summary.task_id, actor_id=summary.requested_by_id, patient=patient, task_type='summary')
        inputs = summary.execution_snapshot if task.schema_version == 'summary-task-2' else task.input_refs
        snapshot_invalid = task.schema_version == 'summary-task-2' and (not isinstance(inputs, dict)
            or compute_checksum(inputs) != summary.execution_sha256 or task.status != 'completed')
        inputs = inputs if isinstance(inputs, dict) else {}
        stale = (snapshot_invalid or needs_regeneration(summary, patient) or not summary_publication_current(summary, inputs) or compute_checksum(task.input_refs) != task.input_sha256
            or not approved_snapshot_config(summary.template, inputs.get('template_checksum'), inputs.get('environment'))
            or not approved_snapshot_config(summary.visibility_policy, inputs.get('visibility_checksum'), inputs.get('environment')))
        if field_codes is not None and any(item.snapshot['field_code'] not in field_codes
            for item in SummaryItem.objects.filter(summary_id=summary.id, patient_id=patient.id)):
            stale = True
        rows.append({'summary_id': str(summary.id), 'patient_id': str(patient.id), 'task_id': str(task.id),
            'from_date': summary.from_date.isoformat(), 'to_date': summary.to_date.isoformat(),
            'validity': summary.validity, 'generated_at': summary.generated_at.isoformat(), 'needs_regeneration': stale})
    return {'patient_id': str(patient.id), 'summaries': rows}
