import json
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from archives.documents import document_sources
from archives.models import ArchiveDocument, ArchiveDocumentVersion, ObservationPeriodVersion
from authorization.models import AccessGrant
from authorization.policy import check_service_grant, matching_case_grants
from case_details.evaluation_evidence import approved_evaluation_artifact, artifact_proof
from case_details.evaluation_units import EvaluationUnitsRejected, UNIT_NAMES
from case_details.models import ClinicalEpisode
from configurations.service import compute_checksum
from files.models import FileObject, FilePage
from identity.models import Patient, Principal
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import RecordAttachment, RecordField, RecordVersion
from relations.models import Relation


def object_digest(instance):
    values = {field.attname: field.value_from_object(instance) for field in instance._meta.concrete_fields}
    return compute_checksum(json.loads(json.dumps(values, cls=DjangoJSONEncoder)))


def medical_time_proof(time_id, patient_id, available_before):
    if time_id is None:
        return None
    value = MedicalTime.objects.select_for_update().get(id=time_id, patient_id=patient_id)
    if value.instant is not None and value.instant > available_before:
        raise EvaluationUnitsRejected('UNIT_FUTURE_INFORMATION')
    return {'time_id': str(value.id), 'patient_id': str(value.patient_id), 'sha256': object_digest(value),
        'precision': value.precision, 'resolution_status': value.resolution_status}


def confirmed_reference(reference, item, *, kind, purpose, environment):
    revision, content = approved_evaluation_artifact(reference, kind=kind, purpose=purpose)
    recorded = type(item).model_validate_json(content)
    if recorded != item:
        raise EvaluationUnitsRejected('UNIT_INDEPENDENT_EVIDENCE_MISMATCH')
    confirmer = Principal.objects.select_for_update().get(id=item.confirmed_by)
    now = timezone.now()
    if (confirmer.account_status != 'active' or item.confirmed_at > revision.captured_at
            or item.confirmed_at > now):
        raise EvaluationUnitsRejected('UNIT_CONFIRMATION_UNAVAILABLE')
    grant = check_service_grant(confirmer, 'verify_field', environment, item.confirmed_at)
    current_grant = check_service_grant(confirmer, 'verify_field', environment, now)
    if grant.revoked_at is not None and grant.revoked_at <= now:
        raise EvaluationUnitsRejected('UNIT_CONFIRMATION_UNAVAILABLE')
    patient = Patient.objects.select_for_update().get(id=item.patient_id)
    permissions = list(matching_case_grants(confirmer, patient, 'verify_field', item.confirmed_at,
        whole_resource=True))
    permissions = [permission for permission in permissions if permission.purpose == purpose and permission.evidence_ref.strip()]
    list(AccessGrant.objects.select_for_update().filter(id__in=[permission.id for permission in permissions]).order_by('id'))
    current_ids = {permission.id for permission in matching_case_grants(confirmer, patient, 'verify_field', now,
        whole_resource=True) if permission.purpose == purpose and permission.evidence_ref.strip()}
    permitted_ids = sorted(permission.id for permission in permissions if permission.id in current_ids)
    if not permitted_ids:
        raise EvaluationUnitsRejected('UNIT_CONFIRMATION_SCOPE_DENIED')
    proof = artifact_proof(reference, revision)
    proof.update(confirmed_by=str(confirmer.id), confirmed_at=item.confirmed_at.isoformat(),
        service_grant_id=str(grant.id), current_service_grant_id=str(current_grant.id),
        case_grant_ids=[str(value) for value in permitted_ids])
    return proof


def current_field_scope(actor, patient, field, version, purpose):
    initial = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient_id=patient.id, version=1)
    for grant in matching_case_grants(actor, patient, 'read_raw', timezone.now(),
            field_codes=[field.field_code], record_id=version.record_id, author_id=initial.author_id):
        if grant.purpose != purpose or not grant.evidence_ref.strip():
            continue
        AccessGrant.objects.select_for_update().get(id=grant.id)
        if grant.relation_id is not None:
            Relation.objects.select_for_update().get(id=grant.relation_id, patient_id=patient.id)
        if any(current.id == grant.id for current in matching_case_grants(actor, patient,
                'read_raw', timezone.now(), field_codes=[field.field_code], record_id=version.record_id,
                author_id=initial.author_id)):
            return {'grant_id': str(grant.id), 'record_version_id': str(version.id), 'field_id': str(field.id)}
    raise EvaluationUnitsRejected('UNIT_FIELD_SCOPE_DENIED')


def field_source(field_id, patient, source_ids, files, actor, purpose, available_before):
    field = RecordField.objects.select_for_update().get(id=field_id, patient_id=patient.id)
    version = RecordVersion.objects.select_for_update().get(id=field.record_version_id, patient_id=patient.id)
    if version.recorded_at > available_before:
        raise EvaluationUnitsRejected('UNIT_FUTURE_INFORMATION')
    proof = current_field_scope(actor, patient, field, version, purpose)
    proof.update(field_sha256=object_digest(field), record_sha256=object_digest(version))
    proof['medical_time'] = medical_time_proof(field.medical_time_id, patient.id, available_before)
    original_ids = {files[source_id].id for source_id in source_ids}
    if field.anchor_id is not None:
        anchor = SourceAnchor.objects.select_for_update().get(id=field.anchor_id, patient_id=patient.id)
        page = FilePage.objects.select_for_update().get(id=anchor.page_id, patient_id=patient.id)
        if page.file_id not in original_ids:
            raise EvaluationUnitsRejected('UNIT_FIELD_SOURCE_MISMATCH')
        proof.update(anchor_id=str(anchor.id), page_id=str(page.id), file_id=str(page.file_id))
    else:
        attached = set(RecordAttachment.objects.filter(record_version_id=version.id,
            patient_id=patient.id).values_list('file_id', flat=True))
        if not attached.intersection(original_ids):
            raise EvaluationUnitsRejected('UNIT_FIELD_SOURCE_MISMATCH')
        proof['file_ids'] = sorted(str(value) for value in attached.intersection(original_ids))
    return field, proof


def actual_period(item, available_before):
    period = ObservationPeriodVersion.objects.select_for_update().get(id=item.id, patient_id=item.patient_id)
    if (period.period_id != item.period_id or period.version != int(item.version)
            or period.start_time_id != item.start_time_id or period.end_time_id != item.end_time_id):
        raise EvaluationUnitsRejected('UNIT_OBSERVATION_VERSION_MISMATCH')
    if period.created_at > available_before:
        raise EvaluationUnitsRejected('UNIT_FUTURE_INFORMATION')
    start = MedicalTime.objects.select_for_update().get(id=item.start_time_id, patient_id=item.patient_id)
    end = MedicalTime.objects.select_for_update().get(id=item.end_time_id, patient_id=item.patient_id)
    if (start.resolution_status != 'resolved' or end.resolution_status != 'resolved'
            or start.instant is None or end.instant is None):
        return period, None
    if start.instant >= end.instant or end.instant > available_before:
        raise EvaluationUnitsRejected('UNIT_OBSERVATION_RANGE_INVALID')
    return period, (start.instant, end.instant)


@transaction.atomic
def lock_evaluation_objects(*, sources, actor):
    manifest, plan = sources.manifest, sources.protocol
    environment, purpose = sources.proofs['environment'], sources.proofs['purpose']
    files = {item.source_id: item for item in manifest.entries('files')}
    patients = {item.id: Patient.objects.select_for_update().get(id=item.id) for item in manifest.entries('patients')}
    if any(patient.status != 'active' for patient in patients.values()):
        raise EvaluationUnitsRejected('UNIT_PATIENT_UNAVAILABLE')
    if {item.patient_id for item in files.values()} != set(patients):
        raise EvaluationUnitsRejected('UNIT_PATIENT_COVERAGE_MISMATCH')
    current_actor = Principal.objects.select_for_update().get(id=actor.id)
    proofs = {name: [] for name in UNIT_NAMES}
    content = {'files': [], 'pages': [], 'documents': [], 'encounters': [], 'events': [], 'periods': []}
    actual_files = list(FileObject.objects.select_for_update().filter(id__in=[item.id for item in files.values()]))
    if len(actual_files) != len(files) or any(file.created_at > plan.available_before for file in actual_files):
        raise EvaluationUnitsRejected('UNIT_FUTURE_INFORMATION')
    content['files'] = sorted((str(file.id), object_digest(file)) for file in actual_files)
    for item in manifest.entries('pages'):
        page = FilePage.objects.select_for_update().get(id=item.id)
        content['pages'].append((str(page.id), object_digest(page)))
    if manifest.pages is not None and manifest.pages.state == 'complete':
        actual_pages = set(FilePage.objects.filter(file_id__in=[item.id for item in files.values()]).values_list('id', flat=True))
        if actual_pages != {item.id for item in manifest.pages.items}:
            raise EvaluationUnitsRejected('UNIT_PAGE_COVERAGE_MISMATCH')
    for item in manifest.entries('documents'):
        patient = patients[item.patient_id]
        document = ArchiveDocument.objects.select_for_update().get(id=item.document_id, patient_id=item.patient_id)
        version = ArchiveDocumentVersion.objects.select_for_update().get(id=item.id,
            document_id=item.document_id, patient_id=item.patient_id, version=int(item.version))
        if version.available_at > plan.available_before:
            raise EvaluationUnitsRejected('UNIT_FUTURE_INFORMATION')
        _, pages, actual_files, _, episodes, facts = document_sources(document=document, patient=patient, version=version)
        if ({page.file_page_id for page in pages} != set(item.page_ids)
                or {file.id for file in actual_files} != {files[source_id].id for source_id in item.source_ids}):
            raise EvaluationUnitsRejected('UNIT_DOCUMENT_SOURCE_MISMATCH')
        source_fields = {fact.field_id for fact in facts}
        source_fields.update(ClinicalEpisode.objects.get(id=link.episode_id, patient_id=patient.id).source_field_id
            for link in episodes)
        field_proofs = [field_source(field_id, patient, item.source_ids, files, current_actor, purpose,
            plan.available_before)[1] for field_id in sorted(source_fields)]
        proofs['documents'].append({'id': str(item.id), 'document_id': str(document.id),
            'version': str(version.version), 'fields': field_proofs})
        content['documents'].append((str(version.id), object_digest(version),
            [(proof['field_id'], proof['field_sha256'], proof['record_sha256'], proof['medical_time']) for proof in field_proofs]))
    for item in manifest.entries('encounters'):
        episode = ClinicalEpisode.objects.select_for_update().get(id=item.id, patient_id=item.patient_id)
        _, proof = field_source(episode.source_field_id, patients[item.patient_id], item.source_ids, files,
            current_actor, purpose, plan.available_before)
        proofs['encounters'].append({'id': str(item.id), 'source': proof})
        content['encounters'].append((str(item.id), object_digest(episode), proof['field_sha256'], proof['record_sha256'],
            proof['medical_time'], medical_time_proof(episode.start_time_id, episode.patient_id, plan.available_before),
            medical_time_proof(episode.end_time_id, episode.patient_id, plan.available_before)))
    for name in ('sampling_events', 'mas_events'):
        field_sets = set()
        for item in manifest.entries(name):
            fields = frozenset(item.source_field_ids)
            if fields in field_sets:
                raise EvaluationUnitsRejected('UNIT_EVENT_SOURCE_REPEATED')
            field_sets.add(fields)
            proof = confirmed_reference(item.confirmation_ref, item, kind='evaluation_event',
                purpose=purpose, environment=environment)
            if item.confirmed_at > plan.available_before:
                raise EvaluationUnitsRejected('UNIT_FUTURE_INFORMATION')
            proof['medical_time'] = medical_time_proof(item.event_time_id, item.patient_id, plan.available_before)
            proof['fields'] = [field_source(field_id, patients[item.patient_id], item.source_ids, files,
                current_actor, purpose, plan.available_before)[1] for field_id in sorted(item.source_field_ids)]
            if name == 'mas_events':
                proof['answer'] = confirmed_reference(item.reference_answer_ref, item, kind='evaluation_answer',
                    purpose=purpose, environment=environment)
            proofs[name].append({'id': str(item.id), 'proof': proof})
            content['events'].append((name, str(item.id), proof['medical_time'],
                [(field['field_id'], field['field_sha256'], field['record_sha256'], field['medical_time'])
                    for field in proof['fields']]))
    intervals, precise = {}, True
    for item in manifest.entries('observation_periods'):
        period, interval = actual_period(item, plan.available_before)
        permissions = [grant for grant in matching_case_grants(current_actor, patients[item.patient_id], 'read_raw',
            timezone.now(), record_id=period.period_id, author_id=period.owner_id, whole_resource=True)
            if grant.purpose == purpose and grant.evidence_ref.strip()]
        if not permissions:
            raise EvaluationUnitsRejected('UNIT_OBSERVATION_SCOPE_DENIED')
        list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in permissions]).order_by('id'))
        if interval is None:
            precise = False
        else:
            intervals.setdefault(item.patient_id, []).append(interval)
        proofs['observation_periods'].append({'id': str(period.id), 'version': str(period.version),
            'start_time_id': str(period.start_time_id), 'end_time_id': str(period.end_time_id),
            'case_grant_ids': [str(grant.id) for grant in permissions],
            'interval': None if interval is None else [value.isoformat() for value in interval]})
        content['periods'].append((str(period.id), object_digest(period),
            object_digest(MedicalTime.objects.get(id=period.start_time_id)),
            object_digest(MedicalTime.objects.get(id=period.end_time_id))))
    for item in manifest.entries('labels'):
        proof = {'id': str(item.id), 'label_state': item.label_state}
        if item.confirmed_by is not None:
            if item.reference_answer_ref is None:
                raise EvaluationUnitsRejected('UNIT_LABEL_ANSWER_REQUIRED')
            proof['answer'] = confirmed_reference(item.reference_answer_ref, item, kind='evaluation_answer',
                purpose=purpose, environment=environment)
        elif item.reference_answer_ref is not None:
            revision, content_bytes = approved_evaluation_artifact(item.reference_answer_ref,
                kind='evaluation_answer', purpose=purpose)
            if type(item).model_validate_json(content_bytes) != item:
                raise EvaluationUnitsRejected('UNIT_INDEPENDENT_EVIDENCE_MISMATCH')
            proof['answer'] = artifact_proof(item.reference_answer_ref, revision)
        proofs['labels'].append(proof)
    counts = {name: None for name in ('files', 'pages', 'documents', 'patients', 'encounters',
        'sampling_events', 'mas_events', 'observation_periods', 'observation_person_hours',
        'confirmed_labels', 'unknown_labels')}
    for name in UNIT_NAMES:
        coverage = getattr(manifest, name)
        if coverage is not None and coverage.state == 'complete':
            if name == 'labels':
                counts['confirmed_labels'] = sum(item.label_state != 'unknown' for item in coverage.items)
                counts['unknown_labels'] = sum(item.label_state == 'unknown' for item in coverage.items)
            else:
                counts[name] = len(coverage.items)
    if counts['observation_periods'] is not None and precise:
        microseconds = 0
        for ranges in intervals.values():
            end = None
            for start, current_end in sorted(ranges):
                if end is not None and start < end:
                    raise EvaluationUnitsRejected('UNIT_OBSERVATION_OVERLAP')
                duration = current_end - start
                microseconds += (duration.days * 86400 + duration.seconds) * 1000000 + duration.microseconds
                end = current_end
        counts['observation_person_hours'] = str(Decimal(microseconds) / Decimal(3600000000))
    return {'counts': counts, 'objects': proofs, 'content_sha256': compute_checksum(content),
        'clinical_qualification': 'not_evaluated'}
