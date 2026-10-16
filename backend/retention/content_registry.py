"""Fixed, reviewed logical-payload adapters; policy cannot execute arbitrary paths.

Rows retain ONLY the registry's explicit relationship/operational skeleton. Those
identities/hashes are not anonymous; a policy must approve its exact digest and a
finite metadata horizon. This module never claims WAL/media/backup erasure.
"""
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.apps import apps

REGISTRY_PATH = Path(__file__).with_name('payload_registry.json')
REGISTRY_BYTES = REGISTRY_PATH.read_bytes()
REGISTRY = json.loads(REGISTRY_BYTES)['adapters']
REGISTRY_SHA256 = hashlib.sha256(REGISTRY_BYTES).hexdigest()
UUID_TOKEN = re.compile(r'(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])', re.I)
MARKERS = {'content_erased_at', 'content_disposition_id'}


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    return value


def digest(value):
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


PRESERVATION_SHA256 = digest({key: value['preserved_fields'] for key, value in REGISTRY.items()})


def adapter(label):
    from retention.disposition import DispositionRejected
    if label not in REGISTRY:
        raise DispositionRejected('CONTENT_ADAPTER_UNREGISTERED')
    spec = REGISTRY[label]
    model = apps.get_model(spec['model'])
    actual = {field.name for field in model._meta.concrete_fields}
    expected = set(spec['payload_fields']) | set(spec['preserved_fields']) | MARKERS
    if actual != expected:
        raise DispositionRejected('CONTENT_SCHEMA_UNCLASSIFIED')
    return model, spec


def row_values(row):
    return {field.name: canonical(getattr(row, field.attname)) for field in row._meta.concrete_fields
        if field.name not in MARKERS}


def row_digest(row):
    return digest(row_values(row))


def row_version(row):
    for name in ('version', 'object_version', 'status_version'):
        if hasattr(row, name):
            return str(getattr(row, name))
    return 'row-1'


def references(value):
    """Exact UUID tokens, including stable URN/target-key references, never SQL."""
    if isinstance(value, UUID):
        return {str(value)}
    if isinstance(value, dict):
        return set().union(*(references(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(references(item) for item in value)) if value else set()
    if isinstance(value, str):
        return {item.lower() for item in UUID_TOKEN.findall(value)}
    return set()


def row_references(row):
    values = row_values(row)
    # A row's own primary key is not a dependency; patient/account IDs must not
    # turn exact-version closure into a whole-patient or whole-account deletion.
    for field in row._meta.concrete_fields:
        if field.primary_key or field.name in ('patient', 'patient_id', 'actor', 'owner',
                'requested_by', 'author', 'confirmed_by', 'created_by', 'operator_id'):
            values.pop(field.name, None)
    return references(values)


INDIRECT_SCOPE = {
    'assessment.assessmentoutput': 'assessment__patient_id',
    'notifications.notificationreceipt': 'attempt__patient_id',
    'tasks.taskattempt': 'task__patient_id',
    'tasks.outboxevent': 'task__patient_id',
}


def patient_rows(patient_id):
    """Fixed complete candidate inventory, bounded by a refusing limit."""
    from retention.disposition import DispositionRejected
    from archives.models import EvaluationPatientPartition, EvaluationRun
    from tasks.models import Task
    from django.db.models import Q
    dataset_ids = EvaluationPatientPartition.all_objects.filter(patient_id=patient_id).values_list('dataset_version_id', flat=True)
    run_ids = [str(value) for value in EvaluationRun.all_objects.filter(dataset_version_id__in=dataset_ids).values_list('id',flat=True)]
    task_ids = Task.all_objects.filter(Q(patient_id=patient_id) | Q(input_refs__evaluation_run_id__in=run_ids)).values_list('id',flat=True)
    rows = []
    for label in sorted(REGISTRY):
        model, spec = adapter(label)
        names = {f.attname for f in model._meta.concrete_fields}
        if label == 'tasks.task':
            scope = {'id__in':task_ids}
        elif label == 'tasks.taskattempt':
            scope = {'task_id__in':task_ids}
        elif label == 'tasks.outboxevent':
            result = list(model.all_objects.filter(Q(task_id__in=task_ids) |
                Q(task_id__isnull=True,resource_refs__patient_id=str(patient_id))).order_by(model._meta.pk.attname)[:5001])
            rows.extend(result)
            if len(rows) > 5000:
                raise DispositionRejected('CONTENT_INVENTORY_LIMIT_EXCEEDED')
            continue
        elif 'patient_id' in names:
            scope = {'patient_id': patient_id}
        elif label in INDIRECT_SCOPE:
            scope = {INDIRECT_SCOPE[label]: patient_id}
        elif label == 'archives.evaluationrun':
            scope = {'dataset_version_id__in': dataset_ids}
        else:
            raise DispositionRejected('CONTENT_SCOPE_ADAPTER_MISSING')
        result = list(model.all_objects.filter(**scope).order_by(model._meta.pk.attname)[:5001])
        rows.extend(result)
        if len(rows) > 5000:
            raise DispositionRejected('CONTENT_INVENTORY_LIMIT_EXCEEDED')
    return rows


# Forward references whose target holds clinical payload. Other UUIDs retain
# provenance/relationships and do not authorize erasing referenced account/config.
OWNED_REFERENCES = {
    'records.recordversion': ('report_id',),
    'tasks.task': ('request_row_id',),
    'records.recordfield': ('medical_time_id', 'anchor_id', 'confirmation_id'),
    'records.report': ('sample_time_id', 'result_time_id'),
    'recognition.candidateconfirmation': ('candidate_field_id',),
    'recognition.candidatefield': ('candidate_set_id', 'anchor_id'),
    'recognition.candidateset': ('task_id', 'raw_response_file_id'),
    'recognition.sourceanchor': ('page_id',),
    'recognition.voiceinput': ('task_id', 'raw_response_file_id', 'original_file_id'),
    'recognition.transcriptionreceipt': ('confirmed_original_file_id',),
    'assessment.assessmentinput': ('assessment_id',),
    'summaries.summaryitem': ('summary_id',),
    'archives.deltasummaryitem': ('summary_id',),
    'alerting.alertevaluationinput': ('evaluation_id',),
    'alerting.alertevaluationassessment': ('evaluation_id',),
    'alerting.alertsignalresult': ('evaluation_id',),
    'archives.archivefactversion': ('document_version_id', 'source_anchor_id', 'medical_time_id', 'documented_time_id'),
    'archives.archivedocumentversion': ('document_id', 'documented_time_id'),
    'archives.evaluationpatientpartition': ('dataset_version_id',),
    'archives.purposecopy': ('derived_file_id',),
    'case_details.patientprofileversion': ('age_reference_time_id',),
}


def owned_references(row):
    names = set(OWNED_REFERENCES.get(row._meta.label_lower, ()))
    if hasattr(row, 'task_id'):
        names.add('task_id')
    if row._meta.label_lower == 'notifications.notificationattempt':
        names.add('send_permit_id')
    if row._meta.app_label == 'case_details':
        names |= {f.attname for f in row._meta.concrete_fields
            if f.attname.endswith('_time_id')}
    return {str(getattr(row, name)) for name in names if getattr(row, name, None) is not None}
