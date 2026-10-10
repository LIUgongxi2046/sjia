"""记录查询：时间轴、历次值、历史时间目录与来源解析。

方向只做客观比较；未知单位/方法或不同精度不比较；
游标失效重读相同条件第一页；异常标记来自报告。
"""
from datetime import datetime, timezone
from decimal import Decimal
from django.core import signing
from django.db.models import Q, F, OuterRef, Subquery, IntegerField
from django.db.models.functions import Coalesce, ExtractYear, ExtractMonth, ExtractDay, ExtractHour, ExtractMinute

from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion


class QueryRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def source_resolve(*, patient, record_version_id, field_id) -> dict:
    field = RecordField.objects.filter(id=field_id, patient=patient, record_version_id=record_version_id).first()
    if field is None:
        raise QueryRejected("FIELD_NOT_FOUND")
    version = RecordVersion.objects.filter(id=record_version_id, patient=patient).first()
    if version is None:
        raise QueryRejected("VERSION_NOT_FOUND")
    return {
        "record_id": str(version.record_id),
        "record_version_id": str(record_version_id),
        "field_id": str(field_id),
        "source_revision_id": str(version.source_revision_id) if version.source_revision_id else None,
        "source_organization": version.source_organization,
        "origin_type": version.origin_type,
        "capture_method": version.capture_method,
        "field_code": field.field_code,
        "concept_code": field.concept_code,
    }


def record_version_read(*, patient, record_version_id) -> dict:
    version = RecordVersion.objects.filter(id=record_version_id, patient=patient).first()
    if version is None:
        raise QueryRejected("VERSION_NOT_FOUND")
    from records.service import read_version

    return read_version(version, Record.objects.get(id=version.record_id, patient=patient))


def record_history_read(*, patient, record_id) -> dict:
    record = Record.objects.get(id=record_id, patient=patient)
    versions = RecordVersion.objects.filter(record_id=record.id, patient=patient).order_by('version')
    return {'patient_id': str(patient.id), 'record_id': str(record.id), 'current_version': str(record.current_version),
            'versions': [{'record_version_id': str(value.id), 'version': str(value.version), 'origin_type': value.origin_type,
                          'recorded_at': value.recorded_at.isoformat(), 'quality_status': value.quality_status} for value in versions]}


def record_search(*, patient, kind=None, concept_code=None, cursor=None, limit=20, original_name=None, origin_type=None, verification_status=None, from_date=None, to_date=None, field_codes=None, source_organization=None, confirmed_by_self=False, principal=None, permission_scopes=None) -> dict:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise QueryRejected('LIMIT_INVALID')
    records = Record.objects.filter(patient=patient, status='active')
    current_versions = RecordVersion.objects.filter(patient=patient, record_id=OuterRef('id'), version=OuterRef('current_version'))
    records = records.annotate(current_version_id=Subquery(current_versions.values('id')[:1]))
    if kind is not None:
        records = records.filter(kind=kind)
    fields = RecordField.objects.filter(patient=patient)
    if permission_scopes is not None:
        allowed = Q(pk__in=[])
        for grant in permission_scopes:
            condition = Q(field_code__in=grant['field_codes'])
            if grant['record_scope'] == 'own':
                owned = RecordVersion.objects.filter(patient=patient, version=1, author=principal).values('record_id')
                condition &= Q(record_version_id__in=RecordVersion.objects.filter(patient=patient, record_id__in=owned).values('id'))
            elif grant['record_scope'] == 'assigned':
                condition &= Q(record_version_id__in=RecordVersion.objects.filter(patient=patient, record_id=grant['assigned_record_id']).values('id'))
            allowed |= condition
        fields = fields.filter(allowed)
        records = records.filter(current_version_id__in=fields.values('record_version_id'))
    if field_codes is not None:
        fields = fields.filter(field_code__in=field_codes)
        records = records.filter(current_version_id__in=fields.values('record_version_id'))
    if concept_code:
        records = records.filter(current_version_id__in=fields.filter(concept_code=concept_code).values('record_version_id'))
    if original_name:
        records = records.filter(current_version_id__in=fields.filter(Q(original_name__icontains=original_name) | Q(field_code__icontains=original_name)).values('record_version_id'))
    if origin_type:
        records = records.filter(current_version_id__in=RecordVersion.objects.filter(patient=patient, origin_type=origin_type).values('id'))
    if source_organization is not None:
        if not isinstance(source_organization, str) or not source_organization.strip() or len(source_organization) > 120:
            raise QueryRejected('SOURCE_ORGANIZATION_INVALID')
        records = records.filter(current_version_id__in=RecordVersion.objects.filter(patient=patient,
            source_organization__icontains=source_organization).values('id'))
    if not isinstance(confirmed_by_self, bool):
        raise QueryRejected('CONFIRMATION_FILTER_INVALID')
    if confirmed_by_self:
        if principal is None:
            raise QueryRejected('CONFIRMATION_ACTOR_REQUIRED')
        from recognition.models import CandidateConfirmation

        from audit.models import AuditEvent

        confirmed_versions = AuditEvent.objects.filter(patient=patient, actor=principal,
            action='therapy_family_confirmed', object_type='record_version', outcome='committed', request_id__isnull=False)
        confirmations = CandidateConfirmation.objects.filter(patient=patient, confirmed_by=principal,
            decision__in=['accepted', 'modified', 'unknown'])
        confirmed_fields = fields.filter(confirmation_id__in=confirmations.values('id')).exclude(
            record_version_id__in=RecordVersion.objects.filter(patient=patient, record_id__in=Record.objects.filter(patient=patient, kind='therapy')).values('id'))
        records = records.filter(Q(current_version_id__in=confirmed_versions.values('object_id'))
            | Q(current_version_id__in=confirmed_fields.values('record_version_id')))
    if verification_status:
        from verification.models import FieldVerificationState

        states = FieldVerificationState.objects.filter(patient_id=patient.id, field_id__in=fields.values('id'))
        if verification_status == 'unverified':
            records = records.exclude(current_version_id__in=states.exclude(status='unverified').values('record_version_id'))
        else:
            records = records.filter(current_version_id__in=states.filter(status=verification_status).values('record_version_id'))
    if from_date or to_date:
        times = MedicalTime.objects.filter(patient=patient)
        if from_date:
            times = times.filter(Q(local_date__gte=from_date) | Q(local_datetime__date__gte=from_date) | Q(instant__date__gte=from_date))
        if to_date:
            times = times.filter(Q(local_date__lte=to_date) | Q(local_datetime__date__lte=to_date) | Q(instant__date__lte=to_date))
        records = records.filter(current_version_id__in=fields.filter(medical_time_id__in=times.values('id')).values('record_version_id'))
    # 各精度分别参与排序，不为未知日期补充时间。
    times = MedicalTime.objects.filter(patient=patient, id=OuterRef('medical_time_id')).annotate(
        sort_year=Coalesce(ExtractYear('instant'), ExtractYear('local_datetime'), ExtractYear('local_date'), F('local_year'), output_field=IntegerField()),
        sort_month=Coalesce(ExtractMonth('instant'), ExtractMonth('local_datetime'), ExtractMonth('local_date'), F('local_month'), output_field=IntegerField()),
        sort_day=Coalesce(ExtractDay('instant'), ExtractDay('local_datetime'), ExtractDay('local_date'), output_field=IntegerField()),
        sort_hour=Coalesce(ExtractHour('instant'), ExtractHour('local_datetime'), output_field=IntegerField()),
        sort_minute=Coalesce(ExtractMinute('instant'), ExtractMinute('local_datetime'), output_field=IntegerField()),
    )
    sort_names = ['sort_year', 'sort_month', 'sort_day', 'sort_hour', 'sort_minute']
    timed_fields = fields.filter(record_version_id=OuterRef('current_version_id')).annotate(**{name: Subquery(times.values(name)[:1]) for name in sort_names})
    order = [F(name).desc(nulls_last=True) for name in sort_names]
    timed_fields = timed_fields.order_by(*order, 'ordinal')
    records = records.annotate(**{name: Subquery(timed_fields.values(name)[:1]) for name in sort_names}).order_by(*order, '-created_at', 'id')
    scope = {'patient': str(patient.id), 'data_epoch': patient.data_epoch, 'verification_epoch': patient.verification_epoch,
             'kind': kind, 'concept': concept_code, 'name': original_name, 'origin': origin_type,
             'verification': verification_status, 'from': from_date, 'to': to_date,
             'organization': source_organization, 'confirmed_by_self': confirmed_by_self,
             'principal': str(principal.id) if principal is not None else None,
             'fields': sorted(field_codes) if field_codes is not None else None,
             'permission_scopes': permission_scopes, 'auth_epoch': str(principal.auth_epoch) if principal else None}
    offset = 0
    if cursor:
        position = signing.loads(cursor, salt='record-medical-order', max_age=3600)
        if position['scope'] != scope:
            raise QueryRejected('CURSOR_STALE')
        offset = position['offset']
        if not isinstance(offset, int) or offset < 0:
            raise QueryRejected('CURSOR_INVALID')
    page = list(records[offset: offset + limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    return {
        "records": [
            {
                "record_id": str(r.id),
                "kind": r.kind,
                "status": r.status,
                "current_version": r.current_version,
                "created_at": r.created_at.isoformat(),
                "detail": _record_detail(r, patient),
            }
            for r in page
        ],
        "next_cursor": signing.dumps({'scope': scope, 'offset': offset + limit}, salt='record-medical-order') if has_more else None,
    }


def _record_detail(record, patient):
    from records.service import read_record

    return read_record(record.id, patient)


def _comparable_direction(prev: RecordField, curr: RecordField, time_by_id: dict) -> str | None:
    if prev.value_state != "known" or curr.value_state != "known":
        return None
    if prev.value_type != "decimal" or curr.value_type != "decimal":
        return None
    if not prev.raw_unit or not prev.method or prev.raw_unit != curr.raw_unit or prev.method != curr.method:
        return None
    if prev.qualifier != 'eq' or curr.qualifier != 'eq':
        return None
    before = time_by_id.get(prev.medical_time_id)
    after = time_by_id.get(curr.medical_time_id)
    if not before or not after or before.precision != after.precision:
        return None
    if before.instant is not None and after.instant is not None:
        if before.instant >= after.instant:
            return None
    elif before.precision == 'date' and before.local_date is not None and after.local_date is not None:
        if not before.timezone_name or before.timezone_name != after.timezone_name or before.local_date >= after.local_date:
            return None
    elif before.precision in ('minute', 'second') and before.local_datetime is not None and after.local_datetime is not None:
        if not before.timezone_name or before.timezone_name != after.timezone_name or before.local_datetime >= after.local_datetime:
            return None
    else:
        return None
    if prev.raw_numeric is None or curr.raw_numeric is None:
        return None
    if prev.raw_numeric == curr.raw_numeric:
        return "same"
    return "up" if curr.raw_numeric > prev.raw_numeric else "down"


def _sort_key(field: RecordField, time_by_id: dict):
    time_row = time_by_id.get(field.medical_time_id)
    instant = time_row.instant if time_row else None
    local_date = time_row.local_date if time_row else None
    local_datetime = time_row.local_datetime if time_row else None
    return (instant is None, instant or datetime.min.replace(tzinfo=timezone.utc), local_datetime is None, local_datetime.isoformat() if local_datetime else '', local_date is None, local_date or datetime.min.date())


def comparison_reasons(previous, current, time_by_id):
    reasons = []
    if previous.value_state != 'known' or current.value_state != 'known' or previous.raw_numeric is None or current.raw_numeric is None:
        reasons.append('value_unknown')
    if not previous.raw_unit or not current.raw_unit:
        reasons.append('unit_unknown')
    elif previous.raw_unit != current.raw_unit:
        reasons.append('unit_mismatch')
    if not previous.method or not current.method:
        reasons.append('method_unknown')
    elif previous.method != current.method:
        reasons.append('method_mismatch')
    if previous.qualifier != 'eq' or current.qualifier != 'eq':
        reasons.append('qualified_value')
    before = time_by_id.get(previous.medical_time_id)
    after = time_by_id.get(current.medical_time_id)
    if not before or not after or before.precision == 'unknown' or after.precision == 'unknown':
        reasons.append('time_unknown')
    elif before.precision != after.precision:
        reasons.append('precision_mismatch')
    elif before.instant is not None and after.instant is not None:
        if before.instant == after.instant:
            reasons.append('same_time')
    elif before.timezone_name and before.timezone_name == after.timezone_name:
        if before.local_datetime and before.local_datetime == after.local_datetime or before.precision == 'date' and before.local_date and before.local_date == after.local_date:
            reasons.append('same_time')
    else:
        reasons.append('timezone_unconfirmed')
    return reasons


def analyte_search(*, patient, concept_code, field_codes=None) -> dict:
    from collections import Counter

    current_records = Record.objects.filter(patient=patient, status='active', id=OuterRef('record_id'), current_version=OuterRef('version'))
    from django.db.models import Exists
    from records.service import medical_time_view
    from verification.models import FieldVerificationState

    current_versions = RecordVersion.objects.filter(patient=patient).filter(Exists(current_records))
    fields = list(RecordField.objects.filter(patient=patient, concept_code=concept_code, record_version_id__in=current_versions.values('id')))
    if field_codes is not None:
        fields = [field for field in fields if field.field_code in field_codes]
    time_by_id = {t.id: t for t in MedicalTime.objects.filter(id__in=[f.medical_time_id for f in fields if f.medical_time_id])}
    # 不用 UUID 推断先后：按医学时间的 instant/local_date 客观排序，未知时间排后。
    fields.sort(key=lambda f: _sort_key(f, time_by_id))
    def time_key(field):
        value = time_by_id.get(field.medical_time_id)
        if value is None or value.precision == 'unknown':
            return None
        if value.instant is not None:
            return ('instant', value.instant)
        if value.local_datetime is not None:
            return (value.precision, value.timezone_name, value.local_datetime)
        if value.local_date is not None:
            return (value.precision, value.timezone_name, value.local_date)
        return None

    counts = Counter(key for field in fields if (key := time_key(field)) is not None)
    versions = {version.id: version for version in current_versions}
    states = {}
    for state in FieldVerificationState.objects.filter(patient_id=patient.id, field_id__in=[field.id for field in fields]):
        states.setdefault(state.field_id, []).append({'purpose': state.purpose, 'status': state.status, 'version': str(state.version)})
    trend = []
    for index, field in enumerate(fields):
        entry = {
            "field_id": str(field.id),
            "record_version_id": str(field.record_version_id),
            "raw_numeric": str(field.raw_numeric) if field.raw_numeric is not None else None,
            "raw_unit": field.raw_unit,
            "method": field.method,
            "value_state": field.value_state,
            "qualifier": field.qualifier,
            "original_name": field.original_name,
            "raw_text": field.raw_text,
            "medical_time": medical_time_view(field.medical_time_id, patient),
            "medical_time_id": str(field.medical_time_id) if field.medical_time_id else None,
            "direction": None,
            "not_comparable_reason": None,
            "comparison_reasons": [],
            "origin_type": versions[field.record_version_id].origin_type,
            "source_organization": versions[field.record_version_id].source_organization,
            "verification_states": states.get(field.id, []),
        }
        if index > 0:
            conflicting_time = counts.get(time_key(fields[index - 1]), 0) > 1 or counts.get(time_key(field), 0) > 1
            direction = None if conflicting_time else _comparable_direction(fields[index - 1], field, time_by_id)
            if direction is None:
                entry["not_comparable_reason"] = "unit_or_method_or_precision_mismatch"
                entry["comparison_reasons"] = comparison_reasons(fields[index - 1], field, time_by_id)
                if conflicting_time and 'same_time' not in entry['comparison_reasons']:
                    entry['comparison_reasons'].append('same_time')
            else:
                entry["direction"] = direction
        trend.append(entry)
    return {"patient_id": str(patient.id), "concept_code": concept_code, "trend": trend}


def history_search(*, patient, from_date=None, to_date=None, scope_timezone=None, limit=20) -> dict:
    times = MedicalTime.objects.filter(patient=patient).order_by("instant", "id")
    entries = []
    for time_row in times[:limit]:
        field_count = RecordField.objects.filter(patient=patient, medical_time_id=time_row.id).count()
        entries.append(
            {
                "medical_time_id": str(time_row.id),
                "precision": time_row.precision,
                "raw_text": time_row.raw_text,
                "local_date": time_row.local_date.isoformat() if time_row.local_date else None,
                "instant": time_row.instant.isoformat() if time_row.instant else None,
                "resolution_status": time_row.resolution_status,
                "field_count": field_count,
            }
        )
    return {"entries": entries}
