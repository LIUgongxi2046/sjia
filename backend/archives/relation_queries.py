import json

from django.core import signing
from django.utils import timezone

from archives.batches import batch_runtime
from archives.models import ArchiveFact, ArchiveFactVersion, ArchiveRelationVersion
from archives.queries import fact_source
from archives.relation_context import ArchiveRelationReadContext
from archives.relations import discover_candidates, fact_source_view, file_source_view, relation_actions, relation_view, source_scope
from archives.relation_schemas import checked_relation
from authorization.policy import matching_case_grants
from configurations.service import compute_checksum
from files.models import FileGroup, FileObject
from records.models import RecordVersion
from api.base import ApiError


def source_granted(actor, patient, audience, kind, value, *, read_context=None):
    if kind == 'fact':
        field, version = fact_source(value, patient) if read_context is None else read_context.fact_source(value)
        original = (RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient_id=patient.id, version=1)
            if read_context is None else read_context.original_record(version.record_id))
        action = 'read_raw' if audience == 'family' else 'read_case'
        scope = {'field_codes': [field.field_code], 'record_id': version.record_id, 'author_id': original.author_id}
    else:
        group = (FileGroup.objects.get(id=value.file_group_id, patient_id=patient.id) if read_context is None
            else read_context.get(FileGroup, id=value.file_group_id, patient_id=patient.id))
        action, scope = 'read_file', {'author_id': group.owner_id, 'whole_resource': True}
    return any(matching_case_grants(actor, patient, action, timezone.now(), require_relation=audience == 'family', **scope))


def relation_directory(request, actor, epoch, audience, patient):
    runtime = batch_runtime()
    if set(request.query_params) - {'limit', 'cursor'} or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise ApiError('ARCHIVE_RELATION_QUERY_INVALID', 422)
    maximum = runtime.payload['api']['max_page_size']
    raw = request.query_params.get('limit', str(maximum))
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or str(int(raw)) != raw or not 1 <= int(raw) <= maximum:
        raise ApiError('ARCHIVE_RELATION_QUERY_INVALID', 422)
    limit = int(raw)
    files = list(FileObject.objects.filter(patient=patient, purpose='original', status='available').order_by('created_at', 'id'))
    facts = list(ArchiveFact.objects.filter(patient_id=patient.id).order_by('created_at', 'id'))
    latest = {}
    for row in ArchiveRelationVersion.objects.filter(patient_id=patient.id).order_by('relation_id', '-version'):
        latest.setdefault(row.relation_id, row)
    context = ArchiveRelationReadContext(patient, facts, files, list(latest.values()))
    sources = {}
    for source in files:
        if source_granted(actor, patient, audience, 'file', source, read_context=context):
            source_scope(request, actor, patient, 'file', source.id, 'read_file', read_context=context)
            sources[str(source.id)] = file_source_view(source)
    for fact in facts:
        for version in context.fact_versions[fact.id]:
            if source_granted(actor, patient, audience, 'fact', version, read_context=context):
                source_scope(request, actor, patient, 'fact', version.id,
                    'read_raw' if version.version == fact.current_version else 'read_history', read_context=context)
                sources[str(version.id)] = fact_source_view(version, patient, read_context=context)
    relations = []
    for row in latest.values():
        kind = 'fact' if row.left_fact_version_id else 'file'
        ids = (row.left_fact_version_id, row.right_fact_version_id) if kind == 'fact' else (row.left_file_id, row.right_file_id)
        rows = [context.get(ArchiveFactVersion if kind == 'fact' else FileObject,
            id=identity, patient_id=patient.id) for identity in ids]
        if not all(source_granted(actor, patient, audience, kind, value, read_context=context) for value in rows):
            continue
        for value in rows:
            source_scope(request, actor, patient, kind, value.id, 'read_raw' if kind == 'fact' else 'read_file', read_context=context)
            if str(value.id) not in sources:
                sources[str(value.id)] = fact_source_view(value, patient, read_context=context) if kind == 'fact' else file_source_view(value)
        relations.append(relation_view(row, allowed_actions=relation_actions(request, actor, patient, row, audience)))
    candidates = discover_candidates(list(sources.values()))
    entries = [('source', item['id']) for item in sources.values()] + [('candidate', index) for index in range(len(candidates))] + [('relation', index) for index in range(len(relations))]
    snapshot = compute_checksum({'sources': list(sources.values()), 'relations': relations, 'candidates': candidates})
    bound = {'actor_id': str(actor.id), 'auth_epoch': str(epoch), 'audience': audience, 'patient_id': str(patient.id),
        'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum, 'snapshot': snapshot, 'limit': limit}
    offset, expiry = 0, request.session.get_expiry_date().timestamp()
    cursor = request.query_params.get('cursor')
    if cursor is not None:
        if not cursor or len(cursor) > 65536:
            raise ApiError('ARCHIVE_RELATION_QUERY_INVALID', 422)
        saved = signing.loads(cursor, salt='archive-relation-directory')
        if saved['scope'] != bound or saved['expires_at'] <= timezone.now().timestamp():
            raise ApiError('CURSOR_STALE', 409)
        offset, expiry = saved['offset'], saved['expires_at']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ApiError('CURSOR_INVALID', 422)
    ids, selected_candidates, selected_relations = set(), [], []
    for kind, index in entries[offset:offset + limit]:
        if kind == 'source':
            ids.add(index)
        elif kind == 'candidate':
            value = candidates[index]
            selected_candidates.append(value)
            ids.update((value['left_id'], value['right_id']))
        else:
            value = relations[index]
            selected_relations.append(value)
            ids.update(identity for identity in (value['left_fact_version_id'], value['right_fact_version_id'], value['left_file_id'], value['right_file_id']) if identity)
    result = {'patient_id': str(patient.id), 'sources': [source for identity, source in sources.items() if identity in ids],
        'candidates': selected_candidates, 'relations': selected_relations, 'snapshot_version': snapshot,
        'next_cursor': signing.dumps({'scope': bound, 'offset': offset + limit, 'expires_at': expiry}, salt='archive-relation-directory') if offset + limit < len(entries) else None}
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('ARCHIVE_RELATION_RESPONSE_LIMIT_EXCEEDED', 422)
    return checked_relation('RelationDirectory', result)
