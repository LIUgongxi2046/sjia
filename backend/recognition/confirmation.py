import json

from django.db import transaction
from django.utils import timezone

from files.models import FileGroup
from recognition.models import CandidateField, CandidateSet
from recognition.service import RecognitionRejected, confirm_candidate
from tasks.models import Task


@transaction.atomic
def prepare_confirmed_fields(*, candidate_set_id, patient, actor, fields, decisions):
    candidate_set = CandidateSet.objects.filter(id=candidate_set_id, patient=patient).first()
    if candidate_set is None:
        raise RecognitionRejected('CANDIDATE_SET_NOT_FOUND')
    group = FileGroup.objects.select_for_update().get(id=candidate_set.file_group_id, patient=patient)
    task = Task.objects.select_for_update().filter(id=candidate_set.task_id, actor=actor, patient=patient).first()
    if task is None:
        raise RecognitionRejected('CANDIDATE_SET_NOT_FOUND')
    candidate_set = CandidateSet.objects.select_for_update().get(id=candidate_set.id)
    if group.status != FileGroup.Status.AVAILABLE or group.version != candidate_set.file_group_version:
        raise RecognitionRejected('FILE_GROUP_NOT_AVAILABLE')
    if task.status != Task.Status.AWAITING_CONFIRMATION or candidate_set.status != CandidateSet.Status.PENDING_CONFIRMATION:
        raise RecognitionRejected('CANDIDATE_SET_NOT_CONFIRMABLE')
    candidates = {str(item.id): item for item in CandidateField.objects.filter(candidate_set_id=candidate_set.id)}
    if not isinstance(decisions, list) or any(not isinstance(item, dict) or set(item) != {'candidate_field_id', 'decision'} or not isinstance(item['candidate_field_id'], str) or not isinstance(item['decision'], str) for item in decisions):
        raise RecognitionRejected('CANDIDATE_DECISIONS_INVALID')
    selected = {item['candidate_field_id']: item['decision'] for item in decisions}
    if len(selected) != len(decisions) or set(selected) != set(candidates):
        raise RecognitionRejected('CANDIDATES_NOT_DECIDED')
    if any(value not in ('accepted', 'modified', 'unknown', 'excluded') for value in selected.values()):
        raise RecognitionRejected('CANDIDATE_DECISIONS_INVALID')
    if any(not isinstance(field.get('candidate_field_id'), str) for field in fields):
        raise RecognitionRejected('CANDIDATE_FIELDS_MISMATCH')
    submitted = {field.get('candidate_field_id'): field for field in fields}
    included = {key for key, value in selected.items() if value != 'excluded'}
    if len(submitted) != len(fields) or set(submitted) != included:
        raise RecognitionRejected('CANDIDATE_FIELDS_MISMATCH')
    prepared = []
    for key, candidate in candidates.items():
        decision = selected[key]
        if decision == 'excluded':
            confirm_candidate(candidate_field=candidate, confirmed_by=actor, decision=decision)
            continue
        field = dict(submitted[key] if decision == 'modified' else candidate.proposed_field)
        field.pop('candidate_field_id', None)
        field.pop('source_file_id', None)
        field.pop('source_text', None)
        if field.get('field_code') != candidate.proposed_field['field_code']:
            raise RecognitionRejected('CANDIDATE_FIELD_CODE_CHANGED')
        if decision == 'unknown':
            field.update(value_state='unknown', raw_numeric=None, raw_text=None, categorical_value=None, qualifier=None)
        confirmation = confirm_candidate(candidate_field=candidate, confirmed_by=actor, decision=decision,
            confirmed_text=json.dumps(field, ensure_ascii=False))
        field.update(anchor_id=candidate.anchor_id, confirmation_id=confirmation.id)
        prepared.append(field)
    return prepared, candidate_set, task, group


def complete_confirmation(candidate_set, task):
    candidate_set.status = CandidateSet.Status.CONFIRMED
    candidate_set.save(update_fields=['status'])
    task.status = Task.Status.COMPLETED
    task.status_version += 1
    task.lease_until = timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
