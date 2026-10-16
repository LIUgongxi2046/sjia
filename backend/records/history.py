"""Field-scoped, precision-preserving medical-time directory (T16).

Unknown/relative or unresolved times are never assigned a synthetic instant.
The legacy ``entries`` projection is retained during the HistoryList transition.
"""
import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.tz import datetime_ambiguous, datetime_exists
from django.core import signing
from django.db import connection
from django.db.models import Case, Count, F, IntegerField, OuterRef, Subquery, Value, When
from django.db.models.functions import Coalesce, ExtractDay, ExtractHour, ExtractMinute, ExtractMonth, ExtractYear

from configurations.service import compute_checksum
from medical_time.models import MedicalTime
from records.models import Record, RecordVersion


PRECISIONS = ('year', 'month', 'month_range', 'date', 'minute', 'second', 'relative', 'unknown')


def begin_history_snapshot():
    # The HTTP view opens the transaction and calls this before its first read.
    # Identity, grants, epochs, times and source rows must be one coherent view.
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')


def checked_query(payload):
    from records.queries import QueryRejected

    allowed = {'kind', 'precision', 'from_date', 'to_date', 'scope_timezone', 'cursor', 'limit'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise QueryRejected('HISTORY_QUERY_INVALID')
    query = dict(payload)
    if 'kind' in query and query['kind'] not in ('observation', 'report', 'therapy'):
        raise QueryRejected('HISTORY_QUERY_INVALID')
    if 'precision' in query and query['precision'] not in PRECISIONS:
        raise QueryRejected('HISTORY_QUERY_INVALID')
    limit = query.get('limit', 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise QueryRejected('LIMIT_INVALID')
    if 'cursor' in query and (not isinstance(query['cursor'], str) or not query['cursor'] or len(query['cursor']) > 32768):
        raise QueryRejected('CURSOR_INVALID')
    if any(name in query for name in ('from_date', 'to_date', 'scope_timezone')):
        if not all(name in query for name in ('from_date', 'to_date', 'scope_timezone')):
            raise QueryRejected('HISTORY_DATE_SCOPE_REQUIRED')
        try:
            start, end = date.fromisoformat(query['from_date']), date.fromisoformat(query['to_date'])
            if start.isoformat() != query['from_date'] or end.isoformat() != query['to_date'] or start > end:
                raise ValueError()
            ZoneInfo(query['scope_timezone'])
            end + timedelta(days=1)
        except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
            raise QueryRejected('HISTORY_DATE_SCOPE_INVALID') from None
    return query


def _interval(value):
    """Return a real half-open UTC interval, or a calendar-only interval."""
    if value.precision in ('unknown', 'relative') or value.resolution_status in ('unknown', 'invalid', 'ambiguous'):
        return None
    try:
        if value.precision == 'year':
            lower, upper = date(value.local_year, 1, 1), date(value.local_year + 1, 1, 1)
        elif value.precision in ('month', 'month_range'):
            year = value.end_year if value.precision == 'month_range' else value.local_year
            month = value.end_month if value.precision == 'month_range' else value.local_month
            lower = date(value.local_year, value.local_month, 1)
            upper = date(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=1)
        elif value.precision == 'date':
            lower, upper = value.local_date, value.local_date + timedelta(days=1)
        elif value.precision in ('minute', 'second'):
            width = timedelta(minutes=1) if value.precision == 'minute' else timedelta(seconds=1)
            if value.instant is not None:
                return 'instant', value.instant, value.instant + width
            if value.local_datetime is None:
                return None
            lower, upper = value.local_datetime, value.local_datetime + width
        else:
            return None
        zone = (ZoneInfo(value.timezone_name) if value.timezone_name else
                timezone(timedelta(minutes=value.utc_offset_minutes)) if value.utc_offset_minutes is not None else None)
        if zone is None:
            # Calendar precision can be filtered as a calendar interval, but a
            # clock time lacking a zone cannot be mapped into the query zone.
            return ('calendar', lower, upper) if isinstance(lower, date) and not isinstance(lower, datetime) else None
        if not isinstance(lower, datetime):
            lower, upper = datetime.combine(lower, time.min), datetime.combine(upper, time.min)
        if any(not datetime_exists(point, zone) or datetime_ambiguous(point, zone) for point in (lower, upper)):
            return None
        return 'instant', lower.replace(tzinfo=zone).astimezone(timezone.utc), upper.replace(tzinfo=zone).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return None


def _in_scope(value, query):
    if 'from_date' not in query:
        return True
    span = _interval(value)
    if span is None:
        return False
    start, end = date.fromisoformat(query['from_date']), date.fromisoformat(query['to_date']) + timedelta(days=1)
    if span[0] == 'instant':
        zone = ZoneInfo(query['scope_timezone'])
        start, end = datetime.combine(start, time.min, zone), datetime.combine(end, time.min, zone)
    return span[1] < end and span[2] > start


def _sources(fields, patient):
    from files.models import FilePage
    from recognition.models import SourceAnchor
    from verification.models import FieldVerificationState, Verification

    versions = {item.id: item for item in RecordVersion.objects.filter(patient=patient,
        id__in={field.record_version_id for field in fields})}
    anchors = {item.id: item for item in SourceAnchor.objects.filter(patient=patient,
        id__in={field.anchor_id for field in fields if field.anchor_id})}
    pages = {item.id: item for item in FilePage.objects.filter(patient_id=patient.id,
        id__in={anchor.page_id for anchor in anchors.values()})}
    states = list(FieldVerificationState.objects.filter(patient_id=patient.id,
        field_id__in=[field.id for field in fields], purpose='family_observation', status='verified'))
    proofs = {item.id for item in Verification.objects.filter(patient=patient, revoked_at__isnull=True,
        id__in=[state.current_verification_id for state in states])}
    by_field = {state.field_id: state.current_verification_id for state in states if state.current_verification_id in proofs}
    result = []
    for field in fields:
        version, anchor = versions[field.record_version_id], anchors.get(field.anchor_id)
        page = pages.get(anchor.page_id) if anchor else None
        result.append({'source_revision_id': str(version.source_revision_id) if version.source_revision_id else None,
            'record_version_id': str(version.id), 'field_id': str(field.id),
            'file_id': str(page.file_id) if page else None, 'page_id': str(page.id) if page else None,
            'anchor_id': str(anchor.id) if anchor else None,
            'verification_ids': [str(by_field[field.id])] if field.id in by_field else [], 'mapping_id': None})
    return result


def search(*, patient, principal=None, permission_scopes=None, **payload):
    from records.queries import QueryRejected, _permitted_fields
    from records.service import medical_time_view

    query = checked_query({key: value for key, value in payload.items() if value is not None})
    limit = query.get('limit', 20)
    fields = _permitted_fields(patient=patient, principal=principal, permission_scopes=permission_scopes)
    current = RecordVersion.objects.filter(patient=patient, record_id=OuterRef('id'), version=OuterRef('current_version'))
    records = Record.objects.filter(patient=patient, status='active')
    if query.get('kind'):
        records = records.filter(kind=query['kind'])
    records = records.annotate(current_version_id=Subquery(current.values('id')[:1]))
    fields = fields.filter(record_version_id__in=records.values('current_version_id'))
    counts = fields.filter(medical_time_id=OuterRef('id')).order_by().values('medical_time_id').annotate(total=Count('id'))
    times = MedicalTime.objects.filter(patient=patient, id__in=fields.values('medical_time_id'))
    if query.get('precision'):
        times = times.filter(precision=query['precision'])
    times = times.annotate(field_count=Subquery(counts.values('total')),
        precision_order=Case(*[When(precision=name, then=Value(index)) for index, name in enumerate(PRECISIONS)], output_field=IntegerField()),
        sort_year=Coalesce(ExtractYear('instant'), ExtractYear('local_datetime'), ExtractYear('local_date'), F('local_year'), output_field=IntegerField()),
        sort_month=Coalesce(ExtractMonth('instant'), ExtractMonth('local_datetime'), ExtractMonth('local_date'), F('local_month'), output_field=IntegerField()),
        sort_day=Coalesce(ExtractDay('instant'), ExtractDay('local_datetime'), ExtractDay('local_date'), output_field=IntegerField()),
        sort_hour=Coalesce(ExtractHour('instant'), ExtractHour('local_datetime'), output_field=IntegerField()),
        sort_minute=Coalesce(ExtractMinute('instant'), ExtractMinute('local_datetime'), output_field=IntegerField()),
    ).order_by('precision_order', *[F(name).asc(nulls_last=True) for name in
        ('sort_year', 'sort_month', 'sort_day', 'sort_hour', 'sort_minute')], 'instant', 'local_datetime', 'id')
    scope = {'patient': str(patient.id), 'data_epoch': str(patient.data_epoch),
        'verification_epoch': str(patient.verification_epoch), 'principal': str(principal.id) if principal else None,
        'auth_epoch': str(principal.auth_epoch) if principal else None, 'permissions': permission_scopes,
        'query': {key: value for key, value in query.items() if key != 'cursor'}}
    digest, offset = compute_checksum(scope), 0
    if query.get('cursor'):
        saved = signing.loads(query['cursor'], salt='medical-time-directory-v2', max_age=3600)
        if not isinstance(saved, dict) or set(saved) != {'snapshot', 'offset'}:
            raise QueryRejected('CURSOR_INVALID')
        if saved['snapshot'] != digest:
            raise QueryRejected('CURSOR_STALE')
        offset = saved['offset']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise QueryRejected('CURSOR_INVALID')
    rows, matched = [], 0
    # Stream rather than materialize all historical times. Authorization is
    # already in SQL; the precise interval predicate runs before pagination.
    for value in times.iterator(chunk_size=256):
        if not _in_scope(value, query):
            continue
        if matched >= offset:
            rows.append(value)
            if len(rows) > limit:
                break
        matched += 1
    more, rows = len(rows) > limit, rows[:limit]
    selected_fields = list(fields.filter(medical_time_id__in=[row.id for row in rows]).order_by('record_version_id', 'ordinal'))
    source_refs = _sources(selected_fields, patient)
    by_time = {}
    for field, source in zip(selected_fields, source_refs):
        by_time.setdefault(field.medical_time_id, []).append(source)
    return {'items': [{'medical_time_id': str(row.id), 'medical_time': medical_time_view(row.id, patient),
            'sources': by_time[row.id]} for row in rows],
        'snapshot_version': digest,
        'next_cursor': signing.dumps({'snapshot': digest, 'offset': offset + limit}, salt='medical-time-directory-v2') if more else None,
        'entries': [{'medical_time_id': str(row.id), 'precision': row.precision, 'raw_text': row.raw_text,
            'local_date': row.local_date.isoformat() if row.local_date else None,
            'instant': row.instant.isoformat() if row.instant else None,
            'resolution_status': row.resolution_status, 'field_count': row.field_count} for row in rows]}
