import calendar
import json
import re
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from time import perf_counter
from zoneinfo import ZoneInfo

from dateutil.tz import datetime_ambiguous, datetime_exists

from django.core import signing
from django.db.models import F, OuterRef, Subquery
from django.db.models.functions import Lower
from django.utils import timezone

from api.base import ApiError
from archives.batches import batch_runtime
from archives.documents import document_read, document_record_scope, document_sources
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion
from archives.queries import fact_source
from archives.search_schemas import checked_search
from archives.search_context import ArchiveSearchContext
from records.service import medical_time_view
from assessment.provenance import entity_reference
from authorization.policy import matching_case_grants
from case_details.models import ClinicalEpisode
from configurations.service import compute_checksum
from medical_time.models import MedicalTime
from files.originals import original_use_view
from records.models import Record, RecordField, RecordVersion
from verification.models import FieldVerificationState


DOCUMENT_NAMES = {'laboratory': '检验报告', 'inpatient': '住院资料', 'discharge': '出院资料',
    'outpatient': '门诊资料', 'therapy': '治疗资料', 'imaging_report': '影像报告',
    'imaging_asset': '影像附图', 'unknown': '待分类资料'}


def grants_for(actor, patient, action, **scope):
    return [grant for grant in matching_case_grants(actor, patient, action, timezone.now(),
        require_relation=True, **scope) if grant.purpose == 'family_observation' and grant.evidence_ref.strip()]


def document_scope(actor, patient, document, version):
    initial = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id, version=1)
    owner = ArchiveBatch.objects.get(id=initial.batch_id, patient_id=patient.id).submitted_by
    record_id = document_record_scope(document, patient, version)
    grants = grants_for(actor, patient, 'read_raw', record_id=record_id, author_id=owner, whole_resource=True)
    if not grants:
        return None
    batch, pages, files, groups, links, facts = document_sources(document=document, patient=patient, version=version)
    for group in groups:
        allowed = grants_for(actor, patient, 'read_file', record_id=record_id, author_id=group.owner_id, whole_resource=True)
        if not allowed:
            return None
        grants.extend(allowed)
    episodes = []
    fields = []
    for fact in facts:
        field, source = fact_source(fact, patient)
        fields.append((field, source))
    for link in links:
        episode = ClinicalEpisode.objects.get(id=link.episode_id, patient_id=patient.id)
        field = RecordField.objects.get(id=episode.source_field_id, record_version_id=episode.record_version_id, patient_id=patient.id)
        source = RecordVersion.objects.get(id=episode.record_version_id, patient_id=patient.id)
        fields.append((field, source))
        episodes.append({'episode_id': str(episode.id), 'label': episode.label, 'episode_type': episode.episode_type,
            'organization': episode.organization_text, 'record_version_id': str(episode.record_version_id)})
    for field, source in fields:
        original = RecordVersion.objects.get(record_id=source.record_id, patient_id=patient.id, version=1)
        allowed = grants_for(actor, patient, 'read_raw', record_id=source.record_id,
            author_id=original.author_id, field_codes=[field.field_code])
        if not allowed:
            return None
        grants.extend(allowed)
    uses = {str(file.id): original_use_view(file, next(group for group in groups if group.id == file.file_group_id), patient) for file in files}
    return {'grants': grants, 'batch': batch, 'pages': pages, 'files': files, 'episodes': episodes, 'fields': fields, 'uses': uses}


def date_bounds(value):
    if value is None or value.resolution_status in ('unknown', 'ambiguous', 'invalid'):
        return None
    if value.precision == 'year':
        return date(value.local_year, 1, 1), date(value.local_year, 12, 31)
    if value.precision in ('month', 'month_range'):
        year = value.end_year if value.precision == 'month_range' else value.local_year
        month = value.end_month if value.precision == 'month_range' else value.local_month
        return date(value.local_year, value.local_month, 1), date(year, month, calendar.monthrange(year, month)[1])
    if value.precision == 'date':
        return (value.local_date, value.local_date) if value.local_date else None
    if value.precision in ('minute', 'second') and value.local_datetime is not None:
        return value.local_datetime.date(), value.local_datetime.date()
    return None


def query_dates(query):
    bounds = {}
    for side in ('from', 'to'):
        if side + '_date' in query:
            bounds[side] = date.fromisoformat(query[side + '_date'])
        elif side + '_time' in query:
            payload = query[side + '_time']
            if payload['resolution_status'] != 'local_only':
                raise ApiError('ARCHIVE_SEARCH_TIME_UNRESOLVED', 422)
            if payload['precision'] == 'date':
                bounds[side] = date.fromisoformat(payload['local_date'])
            else:
                value = datetime.fromisoformat(payload['local_datetime'])
                if value.microsecond or payload['precision'] == 'minute' and value.second:
                    raise ApiError('ARCHIVE_SEARCH_TIME_PRECISION_INVALID', 422)
                if payload['local_date'] and value.date() != date.fromisoformat(payload['local_date']):
                    raise ApiError('ARCHIVE_SEARCH_TIME_CONTENT_INVALID', 422)
                zone = query_zone(payload)
                if not datetime_exists(value, zone) or datetime_ambiguous(value, zone):
                    raise ApiError('ARCHIVE_SEARCH_TIME_AMBIGUOUS', 422)
                instant = value.replace(tzinfo=zone).astimezone(datetime_timezone.utc)
                if payload['utc_offset_minutes'] is not None and value.replace(tzinfo=zone).utcoffset() != timedelta(minutes=payload['utc_offset_minutes']):
                    raise ApiError('ARCHIVE_SEARCH_TIME_CONTENT_INVALID', 422)
                bounds[side + '_instant'] = instant + timedelta(minutes=1) if side == 'to' and payload['precision'] == 'minute' else instant
                if side == 'to' and payload['precision'] == 'minute':
                    bounds['to_exclusive'] = True
                bounds[side + '_day'] = value.date()
    if bounds.get('from') and bounds.get('to') and bounds['from'] > bounds['to']:
        raise ApiError('ARCHIVE_SEARCH_DATE_INVALID', 422)
    if bounds.get('from_instant') and bounds.get('to_instant') and bounds['from_instant'] > bounds['to_instant']:
        raise ApiError('ARCHIVE_SEARCH_DATE_INVALID', 422)
    if bounds.get('from_day') and bounds.get('to') and bounds['from_day'] > bounds['to'] or bounds.get('from') and bounds.get('to_day') and bounds['from'] > bounds['to_day']:
        raise ApiError('ARCHIVE_SEARCH_DATE_INVALID', 422)
    return bounds


def query_zone(payload):
    if payload['timezone_name']:
        return ZoneInfo(payload['timezone_name'])
    if payload['utc_offset_minutes'] is not None:
        return datetime_timezone(timedelta(minutes=payload['utc_offset_minutes']))
    raise ApiError('ARCHIVE_SEARCH_TIME_ZONE_REQUIRED', 422)


def instant_bounds(value):
    span = date_bounds(value)
    if span is None or value.timezone_name is None and value.utc_offset_minutes is None:
        return None
    zone = ZoneInfo(value.timezone_name) if value.timezone_name else datetime_timezone(timedelta(minutes=value.utc_offset_minutes))
    if value.precision in ('minute', 'second'):
        local = value.local_datetime
        if local is None or not datetime_exists(local, zone) or datetime_ambiguous(local, zone):
            return None
        lower = local.replace(tzinfo=zone).astimezone(datetime_timezone.utc)
        if value.instant is not None and value.instant != lower:
            raise ApiError('ARCHIVE_SEARCH_DOCUMENT_TIME_INCONSISTENT', 409)
        return lower, lower + timedelta(seconds=1 if value.precision == 'second' else 60)
    lower, upper = datetime.combine(span[0], datetime.min.time()), datetime.combine(span[1] + timedelta(days=1), datetime.min.time())
    if not datetime_exists(lower, zone) or datetime_ambiguous(lower, zone) or not datetime_exists(upper, zone) or datetime_ambiguous(upper, zone):
        return None
    return lower.replace(tzinfo=zone).astimezone(datetime_timezone.utc), upper.replace(tzinfo=zone).astimezone(datetime_timezone.utc)


def current_fields(scope, patient):
    result = []
    for field, version in scope['fields']:
        record = Record.objects.get(id=version.record_id, patient=patient)
        if record.status != 'active':
            continue
        if version.version == record.current_version:
            result.append((field, version))
        else:
            current = RecordVersion.objects.get(record_id=record.id, patient=patient, version=record.current_version)
            result.extend((value, current) for value in RecordField.objects.filter(record_version_id=current.id,
                patient=patient, field_code=field.field_code).order_by('ordinal'))
    return list({field.id: (field, version) for field, version in result}.values())


def verification_status(fields, patient):
    states = list(FieldVerificationState.objects.filter(patient_id=patient.id, purpose='family_observation',
        field_id__in=[field.id for field, _ in fields]).values_list('status', flat=True))
    if not fields or len(states) != len(fields):
        return 'unverified'
    if set(states) == {'verified'}:
        return 'verified'
    for status in ('needs_correction', 'revoked', 'pending', 'unverified'):
        if status in states:
            return status
    return 'unverified'


def matches(version, scope, fields, status, query, bounds, medical_time, actor, text_hits):
    episodes = scope['episodes']
    if query.get('document_id') and str(version.document_id) != query['document_id']:
        return False
    if query.get('category') == 'inpatient' and version.document_type not in ('inpatient', 'discharge') and not any(value['episode_type'] in ('hospitalization', 'icu') for value in episodes):
        return False
    if query.get('category') == 'outpatient' and version.document_type != 'outpatient' and not any(value['episode_type'] == 'outpatient' for value in episodes):
        return False
    if query.get('category') == 'pending' and version.grouping_status == 'confirmed' and episodes:
        return False
    if query.get('document_type') and version.document_type != query['document_type']:
        return False
    if query.get('organization') and query['organization'].lower() not in (version.organization or '').lower():
        return False
    if query.get('unknown_organization') and version.organization:
        return False
    if query.get('episode_id') and not any(value['episode_id'] == query['episode_id'] for value in episodes):
        return False
    if query.get('unknown_episode') and episodes:
        return False
    if query.get('project') and not any(query['project'].lower() in (field.original_name or field.field_code).lower() for field, _ in fields):
        return False
    if query.get('origin_type') and not any(version.origin_type == query['origin_type'] for _, version in fields):
        return False
    requested = query.get('verification_status')
    if requested == 'self' and (version.confirmed_by != actor.id or version.confirmed_at is None):
        return False
    if requested and requested != 'self' and requested != status:
        return False
    span = date_bounds(medical_time)
    if bounds:
        if span is None:
            if not query.get('include_unknown_time', False):
                return False
        elif bounds.get('from') and span[1] < bounds['from'] or bounds.get('to') and span[0] > bounds['to']:
            return False
        if bounds.get('from_instant') or bounds.get('to_instant'):
            instants = instant_bounds(medical_time)
            if instants is None:
                if not query.get('include_unknown_time', False):
                    return False
            elif bounds.get('from_instant') and instants[1] <= bounds['from_instant'] or bounds.get('to_instant') and (
                    instants[0] >= bounds['to_instant'] if bounds.get('to_exclusive') else instants[0] > bounds['to_instant']):
                return False
    if query.get('confirmed_text') and version.id not in text_hits:
        return False
    if query.get('query') and version.id not in text_hits and not any(query['query'].lower() in value.lower() for value in (
            version.organization or '', DOCUMENT_NAMES[version.document_type], *(value['label'] for value in episodes))):
        return False
    return True


def snippet_for(version, term):
    text = version.confirmed_text
    if not term or not text or version.confirmed_by is None or version.confirmed_at is None:
        return None
    # 原文直接匹配，位置只引用本次确认的文书版本。
    match = re.search(re.escape(term), text, re.IGNORECASE)
    if match is None:
        return None
    index, end = match.span()
    start, stop = max(0, index - 60), min(len(text), end + 100)
    return {'document_version_id': str(version.id), 'leading_text': text[start:index], 'matched_text': text[index:end],
        'trailing_text': text[end:stop], 'leading_truncated': start > 0, 'trailing_truncated': stop < len(text),
        'source_scope': 'confirmed_document_text'}


def archive_search(request, actor, epoch, patient):
    started = perf_counter()
    query = checked_search('ArchiveSearchInput', request.data)
    runtime = batch_runtime()
    if len(json.dumps(query, ensure_ascii=False).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('ARCHIVE_SEARCH_INPUT_LIMIT_EXCEEDED', 422)
    limit = query.get('limit', 20)
    if limit > runtime.payload['api']['max_page_size']:
        raise ApiError('ARCHIVE_SEARCH_INPUT_INVALID', 422)
    bounds = query_dates(query)
    versions = ArchiveDocumentVersion.objects.filter(patient_id=patient.id).annotate(
        latest=Subquery(ArchiveDocument.objects.filter(id=OuterRef('document_id'), patient_id=patient.id).values('current_version')[:1]))
    versions = versions.filter(version=F('latest')).order_by('-available_at', '-id')
    term = query.get('confirmed_text', query.get('query'))
    text_hits = set(versions.filter(confirmed_by__isnull=False, confirmed_at__isnull=False)
        .annotate(search_text=Lower('confirmed_text')).filter(search_text__contains=term.lower()).values_list('id', flat=True)) if term else set()
    versions = list(versions)
    context = ArchiveSearchContext(patient, actor, versions)
    allowed, found, grant_refs = [], [], {}
    episodes_by_id = {}
    unknown = {'organization': 0, 'time': 0, 'episode': 0}
    organizations = set()
    for version in versions:
        document = context.get(ArchiveDocument, id=version.document_id, patient_id=patient.id)
        scope = context.scope(actor, document, version, timezone.now())
        if scope is None:
            continue
        fields = context.current_fields(scope)
        status = context.verification_status(fields)
        medical = context.get(MedicalTime, id=version.documented_time_id, patient_id=patient.id) if version.documented_time_id else None
        allowed.append([entity_reference(document), entity_reference(version),
            [entity_reference(field) for field, _ in fields], [entity_reference(value) for value in scope['files']], scope['uses']])
        for grant in scope['grants']:
            grant_refs[str(grant.id)] = entity_reference(grant)
        organizations.update([version.organization] if version.organization else [])
        episodes_by_id.update((value['episode_id'], value) for value in scope['episodes'])
        unknown['organization'] += int(not version.organization)
        unknown['time'] += int(date_bounds(medical) is None)
        unknown['episode'] += int(not scope['episodes'])
        if matches(version, scope, fields, status, query, bounds, medical, actor, text_hits):
            found.append((document, version, scope, status, medical))
    filters = {key: value for key, value in query.items() if key != 'cursor'}
    snapshot = compute_checksum({'documents': allowed, 'grants': grant_refs, 'verification_epoch': str(patient.verification_epoch)})
    bound = {'actor': str(actor.id), 'epoch': str(epoch), 'patient': str(patient.id), 'data_epoch': str(patient.data_epoch),
        'snapshot': snapshot, 'runtime': entity_reference(runtime), 'filters': filters, 'limit': limit}
    offset, expires = 0, request.session.get_expiry_date().timestamp()
    if query.get('cursor'):
        saved = signing.loads(query['cursor'], salt='archive-search-2')
        if saved['scope'] != bound or saved['expires_at'] <= timezone.now().timestamp():
            raise ApiError('CURSOR_STALE', 409)
        offset, expires = saved['offset'], saved['expires_at']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ApiError('CURSOR_INVALID', 422)
    items = []
    for document, version, scope, status, medical in found[offset:offset + limit]:
        view = {'document_id': str(document.id), 'patient_id': str(patient.id), 'version_id': str(version.id),
            'version': str(version.version), 'batch_id': str(scope['batch'].id), 'document_type': version.document_type,
            'organization': version.organization, 'documented_time': medical_time_view(version.documented_time_id, patient),
            'page_ids': [str(page.id) for page in scope['pages']],
            'episode_ids': [str(value['episode_id']) for value in scope['episodes']],
            'grouping_status': version.grouping_status, 'confirmed_text': version.confirmed_text,
            'proposed_metadata': version.proposed_metadata, 'available_at': version.available_at.isoformat(), 'allowed_actions': []}
        term = query.get('confirmed_text', query.get('query'))
        snippet = snippet_for(version, term)
        matched = []
        if term:
            for kind, values in (('organization', [version.organization or '']), ('document_type', [DOCUMENT_NAMES[version.document_type]]),
                    ('confirmed_text', [version.confirmed_text or '']), ('episode_label', [value['label'] for value in scope['episodes']])):
                if any(term.lower() in value.lower() for value in values):
                    matched.append(kind)
        files = {value.id: value for value in scope['files']}
        pages = [{'page_id': str(page.id), 'file_page_id': str(page.file_page_id), 'file_id': str(page.file_id),
            'page_order': index + 1, 'object_version': page.file_object_version, 'checksum': page.checksum,
            'status': files[page.file_id].status, 'media_type': files[page.file_id].media_type,
            'created_at': files[page.file_id].created_at.isoformat(), **scope['uses'][str(page.file_id)]} for index, page in enumerate(scope['pages'])]
        limitations = []
        if not version.organization:
            limitations.append('ORGANIZATION_UNKNOWN')
        if not scope['episodes']:
            limitations.append('EPISODE_UNKNOWN')
        if medical is None or date_bounds(medical) is None:
            limitations.append('DOCUMENT_TIME_UNKNOWN')
        elif medical.precision in ('year', 'month', 'month_range'):
            limitations.append('DOCUMENT_TIME_HAS_RECORDED_PRECISION')
        if any(value['status'] != 'available' for value in pages):
            limitations.append('ORIGINAL_CURRENTLY_UNAVAILABLE')
        items.append({'document': view, 'pages': pages, 'episodes': scope['episodes'], 'snippet': snippet,
            'matched_fields': matched, 'verification_status': status, 'limitations': limitations})
    result = {'patient_id': str(patient.id), 'items': items, 'snapshot_version': snapshot,
        'next_cursor': signing.dumps({'scope': bound, 'offset': offset + limit, 'expires_at': expires}, salt='archive-search-2') if offset + limit < len(found) else None,
        'facets': {'organizations': sorted(organizations), 'episodes': sorted(episodes_by_id.values(), key=lambda value: value['episode_id']),
            'unknown_organization_count': unknown['organization'], 'unknown_time_count': unknown['time'], 'unknown_episode_count': unknown['episode']},
        'authorized_document_count': len(allowed), 'matched_count': len(found), 'elapsed_ms': round((perf_counter() - started) * 1000, 3)}
    if len(json.dumps(result, ensure_ascii=False).encode()) > runtime.payload['api']['max_json_bytes']:
        raise ApiError('ARCHIVE_SEARCH_RESPONSE_LIMIT_EXCEEDED', 422)
    return checked_search('ArchiveSearchView', result)
