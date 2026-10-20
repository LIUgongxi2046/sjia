import json
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from api.base import ApiError, authorize_case, get_patient
from archives.batches import batch_runtime, directory_grants
from archives.models import ArchiveDocumentVersion, ArchiveFact, ArchiveFactVersion, ArchiveRelationVersion, ObservationPeriodVersion
from archives.queries import fact_read, fact_source
from archives.sampling import sampling_groups
from archives.search import grants_for
from assessment.medical_intervals import medical_interval
from assessment.provenance import entity_reference
from case_details.models import ClinicalEpisode
from configurations.evidence import inspect_approved_evidence, deployed_artifact_checksum
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from family_content.gate_locks import read_environment_gate_shared
from files.models import FileGroup, FileObject
from files.purpose import lock_use_authorization
from files.purpose_dependencies import connect, invalidate_resource, resource_node
from identity.models import Patient
from medical_time.models import MedicalTime
from records.medical_times import create_medical_time, time_values
from records.models import Record, RecordField, RecordVersion
from records.period_policies import SamplingFrequencyPolicy
from records.period_schemas import checked_period
from records.service import medical_time_view
from retention.models import RetentionPolicy
from summaries.delta import comparable, fact_value
from verification.models import Verification


def current_period(patient, actor):
    return ObservationPeriodVersion.objects.filter(patient_id=patient.id, owner_id=actor.id).order_by('-created_at', '-version', 'id').first()


def period_scope(request, actor, patient_id, *, writing=False):
    gate = (EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
        if writing else read_environment_gate_shared(settings.SJIA_ENVIRONMENT))
    runtime = batch_runtime()
    patient = Patient.objects.select_for_update(no_key=not writing).get(id=get_patient(patient_id).id)
    if patient.status != 'active':
        raise ApiError('PATIENT_INACTIVE', 403)
    if not directory_grants(actor, patient):
        raise ApiError('CASE_ACTION_DENIED', 403)
    if writing:
        authorize_case(request, actor, patient, 'write_record', author_id=actor.id, require_relation=True, whole_resource=True)
    return patient, gate, runtime


def interval_or_none(value):
    if value is None or value.precision not in ('date', 'minute', 'second') or value.resolution_status in ('unknown', 'ambiguous', 'invalid'):
        return None
    if value.timezone_name is None and value.utc_offset_minutes is None:
        return None
    if value.precision != 'date' and (value.resolution_status != 'resolved' or value.instant is None):
        return None
    return medical_interval(value)


def checked_range(patient, payload):
    checked_period('Input', payload)
    first, last = [MedicalTime(patient=patient, **time_values(payload[key])) for key in ('start', 'end')]
    if first.timezone_name is not None and last.timezone_name is not None and first.timezone_name != last.timezone_name:
        raise ApiError('OBSERVATION_PERIOD_TIMEZONE_INCONSISTENT', 422)
    if (first.utc_offset_minutes is not None and last.utc_offset_minutes is not None
            and first.timezone_name is None and last.timezone_name is None and first.utc_offset_minutes != last.utc_offset_minutes):
        raise ApiError('OBSERVATION_PERIOD_TIMEZONE_INCONSISTENT', 422)
    start, end = interval_or_none(first), interval_or_none(last)
    if start is not None and end is not None and (start.lower > end.upper or start.lower == end.upper and not end.upper_inclusive):
        raise ApiError('OBSERVATION_PERIOD_RANGE_INVALID', 422)
    return first, last


def episode_scope(actor, patient, episode):
    version = RecordVersion.objects.get(id=episode.record_version_id, patient=patient)
    field = RecordField.objects.get(id=episode.source_field_id, record_version_id=version.id, patient=patient)
    original = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient=patient, version=1)
    scope = {'record_id': version.record_id, 'author_id': original.author_id, 'field_codes': [field.field_code]}
    if not grants_for(actor, patient, 'read_raw', **scope):
        return None
    record = Record.objects.get(id=version.record_id, patient=patient)
    if record.current_version != version.version and not grants_for(actor, patient, 'read_history', **scope):
        return None
    return version, field


def period_retention_policies(runtime):
    rows = list(RetentionPolicy.objects.select_for_update().filter(id__in=runtime.payload['retention_policy_ids'],
        data_class='observation', status='active').order_by('id'))
    return [row for row in rows if row.approved_by_id is not None and row.approved_at is not None
        and isinstance(row.evidence_ref, str) and row.evidence_ref.strip()]


def period_retention(runtime):
    rows = period_retention_policies(runtime)
    if len(rows) != 1:
        raise ApiError('OBSERVATION_PERIOD_RETENTION_UNAVAILABLE', 503)
    return rows[0]


def prepared_scope(request, actor, patient_id, payload, *, writing=True, check_version=True):
    patient, gate, runtime = period_scope(request, actor, patient_id, writing=writing)
    checked_range(patient, payload)
    current = current_period(patient, actor)
    expected = None if payload['expected_version'] is None else int(payload['expected_version'])
    actual = current.version if current is not None else None
    if check_version and actual != expected:
        raise ApiError('VERSION_CONFLICT', 409)
    if actual == 9223372036854775807:
        raise ApiError('OBSERVATION_PERIOD_VERSION_LIMIT', 409)
    episode = None
    if payload['episode_id'] is not None:
        episode = ClinicalEpisode.objects.get(id=payload['episode_id'], patient_id=patient.id)
        if episode_scope(actor, patient, episode) is None:
            raise ApiError('OBSERVATION_PERIOD_EPISODE_DENIED', 403)
    retention = period_retention(runtime)
    grants = grants_for(actor, patient, 'write_record' if writing else 'read_raw', author_id=actor.id, whole_resource=True)
    context = {'environment': settings.SJIA_ENVIRONMENT, 'patient_id': str(patient.id), 'actor_id': str(actor.id),
        'auth_epoch': str(actor.auth_epoch), 'data_epoch': str(patient.data_epoch), 'gate_version': str(gate.version),
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'current_period': entity_reference(current) if current is not None else None,
        'episode': entity_reference(episode) if episode is not None else None,
        'retention': entity_reference(retention), 'grants': sorted([entity_reference(row) for row in grants], key=lambda item: item['id'])}
    return patient, current, retention, context


def save_period(patient, actor, payload, previous):
    checked_range(patient, payload)
    times = [create_medical_time(patient, payload[key]) for key in ('start', 'end')]
    version = 1 if previous is None else previous.version + 1
    saved = ObservationPeriodVersion.objects.create(patient_id=patient.id, period_id=uuid4() if previous is None else previous.period_id,
        version=version, owner_id=actor.id, start_time_id=times[0].id, end_time_id=times[1].id,
        label=payload['label'], label_source='self_report', episode_id=payload['episode_id'], created_at=timezone.now(),
        supersedes_version=previous.version if previous else None)
    node = resource_node('observation_period_version', saved.id, str(version), patient.id)
    for value in times:
        connect(node, resource_node('medical_time', value.id, compute_checksum(entity_reference(value)), patient.id), 'source')
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    patient.refresh_from_db()
    if previous is not None:
        invalidate_resource('observation_period_version', previous.id)
    return saved


def frequency_policy(artifact):
    configs = list(ConfigVersion.objects.filter(kind='window', schema_version='sampling-frequency-1', status='enabled').order_by('id'))
    eligible = []
    for row in configs:
        if configuration_qualification_reason(row, settings.SJIA_ENVIRONMENT, audience='family', artifact_sha256=artifact) is not None:
            continue
        policy = SamplingFrequencyPolicy.model_validate_json(json.dumps(row.payload))
        valid = True
        for reference in policy.evidence_refs:
            _, _, reason = inspect_approved_evidence(reference, purpose='clinical_policy', lock=True)
            if reason is not None:
                valid = False
        if valid:
            eligible.append((row, policy))
    if len(eligible) != 1:
        return None, None, 'SAMPLING_FREQUENCY_POLICY_AMBIGUOUS' if len(eligible) > 1 else 'SAMPLING_FREQUENCY_POLICY_UNAVAILABLE'
    return *eligible[0], None


def sampling_materials(actor, patient, visibility, runtime, artifact):
    fields = RecordField.objects.filter(patient=patient, value_type='decimal', field_code__in=visibility.payload['field_codes'])
    latest = ArchiveFactVersion.objects.filter(fact_id=OuterRef('id'), version=OuterRef('current_version'), patient_id=patient.id).values('id')[:1]
    identities = ArchiveFact.objects.filter(patient_id=patient.id).annotate(current_id=Subquery(latest)).values('current_id')
    versions = list(ArchiveFactVersion.objects.filter(id__in=identities, field_id__in=fields.values('id'), patient_id=patient.id).order_by('id'))
    if len(versions) > runtime.payload['api']['max_page_size'] * 20:
        raise ApiError('OBSERVATION_PERIOD_SCOPE_LIMIT_EXCEEDED', 422)
    result = []
    for fact in versions:
        field, source = fact_source(fact, patient)
        record = Record.objects.get(id=source.record_id, patient=patient)
        if record.status != 'active' or record.current_version != source.version:
            continue
        initial = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1)
        scope = {'record_id': record.id, 'author_id': initial.author_id, 'field_codes': [field.field_code]}
        if not grants_for(actor, patient, 'read_raw', **scope):
            continue
        value, proof = fact_value(fact, field, patient, artifact_sha256=artifact)
        projection = fact_read(fact=ArchiveFact.objects.get(id=fact.fact_id, patient_id=patient.id), version=fact, patient=patient)
        source_ref = projection['source']
        if source_ref['file_id'] is not None:
            file = FileObject.objects.get(id=source_ref['file_id'], patient=patient)
            group = FileGroup.objects.get(id=file.file_group_id, patient=patient)
            if not grants_for(actor, patient, 'read_file', record_id=record.id, author_id=group.owner_id, whole_resource=True):
                continue
            lock_use_authorization(group.use_authorization_id, file, 'family_observation', settings.SJIA_ENVIRONMENT)
        document = ArchiveDocumentVersion.objects.get(id=fact.document_version_id, patient_id=patient.id) if fact.document_version_id else None
        time = MedicalTime.objects.get(id=fact.medical_time_id, patient=patient) if fact.medical_time_id else None
        result.append({'fact': fact, 'value': value, 'proof': proof, 'source': source_ref, 'document': document,
            'interval': interval_or_none(time), 'time': time})
    return result


def current_relations(patient, identities):
    latest = ArchiveRelationVersion.objects.filter(relation_id=OuterRef('relation_id'), patient_id=patient.id).order_by('-version').values('id')[:1]
    rows = ArchiveRelationVersion.objects.filter(patient_id=patient.id).annotate(current_id=Subquery(latest)).filter(id=F('current_id'))
    return [row for row in rows.order_by('id') if row.left_fact_version_id in identities and row.right_fact_version_id in identities and row.status != 'released']


def hours(value):
    seconds = Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(1000000)
    return format(seconds / Decimal(3600), 'f')


def sampling_interval(before, after, relations, patient, frequency, frequency_reason):
    pair = [row for row in relations if {row.left_fact_version_id, row.right_fact_version_id} == {before['fact'].id, after['fact'].id}]
    independent = [row for row in pair if row.status == 'confirmed' and row.relation_type == 'independent_sampling']
    reasons = comparable(before['value'], after['value'], patient)
    for value in (before, after):
        if value['fact'].availability_status != 'known' or value['fact'].available_at is None:
            reasons.append('FACT_AVAILABILITY_UNKNOWN')
        elif value['fact'].available_at > timezone.now():
            reasons.append('FACT_NOT_YET_AVAILABLE')
    if not independent:
        reasons.append('INDEPENDENT_SAMPLING_REQUIRED')
    for relation in independent:
        proof = Verification.objects.get(id=relation.verification_id, patient=patient) if relation.verification_id else None
        if proof is None or proof.revoked_at is not None or proof.id not in {row['proof'].id for row in (before, after) if row['proof'] is not None}:
            reasons.append('SAMPLING_RELATION_VERIFICATION_REQUIRED')
    if any(row.status != 'confirmed' or row.relation_type in ('same_sampling', 'formal_correction') for row in pair):
        reasons.append('SAMPLING_ORDER_UNCONFIRMED')
    first, last = before['interval'], after['interval']
    actual = minimum = maximum = None
    if first is None or last is None or first.upper > last.lower or first.upper == last.lower and first.upper_inclusive:
        reasons.append('SAMPLING_ORDER_UNCONFIRMED')
    else:
        minimum, maximum = hours(last.lower - first.upper), hours(last.upper - first.lower)
        if first.lower == first.upper and last.lower == last.upper:
            actual = hours(last.lower - first.lower)
    if frequency is None or before['value']['concept_code'] not in frequency.concept_codes or after['value']['concept_code'] not in frequency.concept_codes:
        reasons.append(frequency_reason or 'SAMPLING_FREQUENCY_POLICY_UNAVAILABLE')
    elif maximum is not None:
        if minimum is not None and Decimal(minimum) > Decimal(frequency.interval_hours):
            reasons.append('SAMPLING_GAP_EXCEEDS_APPROVED_INTERVAL')
        elif Decimal(maximum) > Decimal(frequency.interval_hours):
            reasons.append('SAMPLING_GAP_PRECISION_INSUFFICIENT')
    state = 'comparable' if not reasons else 'unknown' if any(code in reasons for code in (
        'SAMPLING_ORDER_UNCONFIRMED', 'SAMPLING_GAP_PRECISION_INSUFFICIENT', 'SAMPLING_FREQUENCY_POLICY_UNAVAILABLE',
        'SAMPLING_FREQUENCY_POLICY_AMBIGUOUS', 'INDEPENDENT_SAMPLING_REQUIRED', 'SAMPLING_RELATION_VERIFICATION_REQUIRED')) else 'incomparable'
    return checked_period('Interval', {'before_fact_version_id': str(before['fact'].id), 'after_fact_version_id': str(after['fact'].id),
        'actual_hours': actual, 'minimum_hours': minimum, 'maximum_hours': maximum, 'comparability': state,
        'reason_codes': list(dict.fromkeys(reasons)), 'relation_version_ids': [str(row.id) for row in pair]})


def observation_visibility(runtime):
    identity = runtime.payload.get('observation_period_visibility_config_id')
    if identity is None:
        raise ApiError('OBSERVATION_PERIOD_VISIBILITY_UNAVAILABLE', 503)
    config = ConfigVersion.objects.filter(id=identity, kind='visibility', schema_version='visibility-1').first()
    if (config is None or config.payload['environment'] != settings.SJIA_ENVIRONMENT
            or config.payload['purpose'] != 'family_observation' or config.payload['clinical_fields']
            or configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT)):
        raise ApiError('OBSERVATION_PERIOD_VISIBILITY_UNAVAILABLE', 503)
    return config


def period_view(actor, patient, runtime, row=None):
    current = current_period(patient, actor)
    if row is None:
        row = current
    if row is not None and (row.patient_id != patient.id or row.owner_id != actor.id):
        raise ApiError('OBSERVATION_PERIOD_SCOPE_DENIED', 403)
    visibility = observation_visibility(runtime)
    first, last = [MedicalTime.objects.get(id=identity, patient=patient) for identity in (row.start_time_id, row.end_time_id)] if row else (None, None)
    start, end = interval_or_none(first), interval_or_none(last)
    reasons = ['OBSERVATION_PERIOD_NOT_SELECTED'] if row is None else ['OBSERVATION_PERIOD_TIME_UNKNOWN'] if start is None or end is None else []
    retention_ready = len(period_retention_policies(runtime)) == 1
    if not retention_ready:
        reasons.append('OBSERVATION_PERIOD_RETENTION_UNAVAILABLE')
    if row is not None and current.id != row.id:
        reasons.append('OBSERVATION_PERIOD_SUPERSEDED')
    artifact = deployed_artifact_checksum(runtime)
    config, frequency, frequency_reason = frequency_policy(artifact)
    if frequency_reason:
        reasons.append(frequency_reason)
    materials = sampling_materials(actor, patient, visibility, runtime, artifact)
    relations = current_relations(patient, {item['fact'].id for item in materials})
    proof_ids = {item['fact'].id: item['proof'].id if item['proof'] is not None else None for item in materials}
    same_sampling = [row for row in relations if row.status == 'confirmed' and row.relation_type == 'same_sampling'
        and row.verification_id is not None and row.verification_id in (proof_ids[row.left_fact_version_id], proof_ids[row.right_fact_version_id])]
    groups = sampling_groups([str(item['fact'].id) for item in materials], [(str(row.left_fact_version_id), str(row.right_fact_version_id))
        for row in same_sampling])
    group_ids = {identity: min(group) for group in groups for identity in group}
    unknown_order = timezone.now()
    materials.sort(key=lambda item: (item['interval'] is None, item['interval'].lower if item['interval'] else unknown_order, str(item['fact'].id)))
    samplings, inside, unknown, selected_groups = [], {}, [], set()
    for item in materials:
        fact, interval = item['fact'], item['interval']
        codes = []
        if interval is None:
            state = 'unknown'; unknown.append(str(fact.id)); codes.append('FACT_MEDICAL_TIME_UNKNOWN' if item['time'] is None or item['time'].precision == 'unknown' else 'MEDICAL_TIME_PRECISION_INSUFFICIENT')
        elif row is None:
            state = 'not_selected'
        elif start is None or end is None:
            state = 'unknown'; codes.append('OBSERVATION_PERIOD_TIME_UNKNOWN')
        elif interval.upper < start.lower or interval.upper == start.lower and not interval.upper_inclusive or interval.lower > end.upper or interval.lower == end.upper and not end.upper_inclusive:
            state = 'outside'; codes.append('FACT_OUTSIDE_OBSERVATION_PERIOD')
        elif interval.lower < start.lower or interval.upper > end.upper or interval.upper == end.upper and interval.upper_inclusive and not end.upper_inclusive:
            state = 'overlap'; codes.append('FACT_OVERLAPS_OBSERVATION_BOUNDARY')
        else:
            state = 'inside'
        if fact.availability_status != 'known' or fact.available_at is None:
            codes.append('FACT_AVAILABILITY_UNKNOWN')
        elif fact.available_at > timezone.now():
            codes.append('FACT_NOT_YET_AVAILABLE')
        if item['value']['verification_status'] != 'verified':
            codes.append('FIELD_VERIFICATION_REQUIRED')
        group_id = group_ids[str(fact.id)]
        if group_id != str(fact.id):
            codes.append('SAME_SAMPLING_DUPLICATE')
        if state in ('inside', 'not_selected'):
            key = item['value']['concept_code'] or item['value']['field_code']
            group_key = (key, group_id)
            if group_key not in selected_groups:
                selected_groups.add(group_key)
                inside.setdefault(key, []).append(item)
        samplings.append(checked_period('Sampling', {'fact': item['value'], 'source': item['source'],
            'document_id': str(item['document'].document_id) if item['document'] else None,
            'document_version_id': str(fact.document_version_id) if fact.document_version_id else None, 'page_id': str(fact.page_id) if fact.page_id else None,
            'available_at': fact.available_at.isoformat() if fact.available_at else None, 'sampling_group_id': group_id,
            'relation_version_ids': [str(row.id) for row in relations if fact.id in (row.left_fact_version_id, row.right_fact_version_id)],
            'range_state': state, 'reason_codes': codes}))
    intervals = [sampling_interval(before, after, relations, patient, frequency, frequency_reason)
        for values in inside.values() for before, after in zip(values, values[1:])]
    if not inside:
        reasons.append('NO_SAMPLING_DATA_IN_PERIOD')
    if not samplings or not any(item['available_at'] is not None and row is not None
            and ArchiveFactVersion.objects.get(id=item['fact']['fact_version_id']).available_at > row.created_at for item in samplings):
        reasons.append('NO_NEW_SAMPLING_DATA')
    episodes = [episode for episode in ClinicalEpisode.objects.filter(patient_id=patient.id).order_by('id') if episode_scope(actor, patient, episode) is not None]
    associated = next((value for value in episodes if row is not None and value.id == row.episode_id), None)
    if row is not None and row.episode_id is not None and associated is None:
        reasons.append('OBSERVATION_PERIOD_EPISODE_UNAVAILABLE')
    zone = first.timezone_name if first is not None and last is not None and first.timezone_name == last.timezone_name else None
    value = {'patient_id': str(patient.id), 'period_id': str(row.period_id) if row else None, 'version_id': str(row.id) if row else None,
        'version': str(row.version) if row else None, 'owner_id': str(row.owner_id) if row else None, 'label': row.label if row else None,
        'label_source': row.label_source if row else None, 'episode_id': str(associated.id) if associated else None, 'episode_label': associated.label if associated else None,
        'created_at': row.created_at.isoformat() if row else None, 'is_current': row is None or row.id == current.id,
        'start': medical_time_view(first.id, patient) if first else None, 'end': medical_time_view(last.id, patient) if last else None, 'timezone': zone,
        'target_interval_hours': frequency.interval_hours if frequency else None, 'interval_basis_ref': frequency.evidence_refs[0] if frequency else None,
        'interval_policy_id': str(config.id) if config else None, 'samplings': samplings, 'intervals': intervals,
        'unknown_time_fact_ids': unknown, 'reason_codes': list(dict.fromkeys(reasons)),
        'episode_options': [{'episode_id': str(item.id), 'label': item.label, 'organization': item.organization_text} for item in episodes],
        'allowed_actions': ['observation_period_change'] if retention_ready
            and grants_for(actor, patient, 'write_record', author_id=actor.id, whole_resource=True) else []}
    value['snapshot_version'] = compute_checksum({'view': value, 'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'auth_epoch': str(actor.auth_epoch), 'grants': [entity_reference(grant) for grant in directory_grants(actor, patient)],
        'runtime': entity_reference(runtime), 'visibility': entity_reference(visibility)})
    if len(json.dumps(value, separators=(',', ':')).encode('utf-8')) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('OBSERVATION_PERIOD_SCOPE_LIMIT_EXCEEDED', 422)
    return checked_period('View', value)
