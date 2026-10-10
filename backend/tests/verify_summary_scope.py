import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from records.models import Record
from records.service import read_record
from summaries.models import Summary, SummaryItem
from summaries.service import field_date, summary_record_fields
from configurations.service import compute_checksum


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    originals = list(SummaryItem.objects.order_by('id').values('id', 'snapshot'))
    before = compute_checksum([{**row, 'id': str(row['id'])} for row in originals])
    summaries = list(Summary.objects.order_by('id'))
    records = [read_record(record.id, record.patient) for record in Record.objects.filter(status='active').select_related('patient')]
    require(bool(summaries) and bool(records), '当前数据库缺少可检查的实际摘要范围或记录。')
    checks, overlapping, excluded, unknown_times, crossing_boundaries = 0, 0, 0, 0, 0
    for summary in summaries:
        zone = ZoneInfo(summary.scope_timezone)
        for record in records:
            if record['patient_id'] != str(summary.patient_id):
                continue
            permitted = {field['field_code'] for field in record['fields']}
            ranges = {(summary.from_date, summary.to_date)}
            if record['kind'] == 'therapy':
                boundaries = {field['field_code']: field_date(field, zone) for field in record['fields']
                    if field['field_code'] in ('therapy_start', 'therapy_end')}
                for date in boundaries.values():
                    if date is not None:
                        ranges.add((date, date))
            for start_date, end_date in sorted(ranges):
                selected = summary_record_fields(record, from_date=start_date, to_date=end_date, zone=zone,
                    include_unknown_time=summary.include_unknown_time, permitted=permitted)
                expected = []
                therapy_overlap = False
                if record['kind'] == 'therapy':
                    start, end = boundaries.get('therapy_start'), boundaries.get('therapy_end')
                    ongoing = any(field['field_code'] == 'therapy_status' and field['value_state'] == 'known'
                        and field['categorical_value'] == 'ongoing' for field in record['fields'])
                    therapy_overlap = bool((start is not None and end is not None and start <= end_date and end >= start_date)
                        or (start is not None and start_date <= start <= end_date)
                        or (end is not None and start_date <= end <= end_date)
                        or (ongoing and start is not None and start <= end_date))
                for field in record['fields']:
                    date = field_date(field, zone)
                    within_range = date is not None and start_date <= date <= end_date
                    if therapy_overlap or within_range or (date is None and summary.include_unknown_time):
                        expected.append(field)
                        unknown_times += int(date is None)
                        crossing_boundaries += int(therapy_overlap and date is not None and not within_range)
                    else:
                        excluded += 1
                require(selected == expected, '治疗区间相交时没有完整采用实际治疗字段。')
                require(all(field in record['fields'] for field in selected), '摘要范围检查改变了原始字段。')
                restricted = summary_record_fields(record, from_date=start_date, to_date=end_date, zone=zone,
                    include_unknown_time=summary.include_unknown_time, permitted=set())
                require(restricted == [], '治疗范围不能扩大允许字段。')
                overlapping += int(therapy_overlap)
                checks += 1
    require(crossing_boundaries > 0, '实际资料没有覆盖范围外治疗日期，当前检查无法证明该场景。')
    after = compute_checksum([{**row, 'id': str(row['id'])}
        for row in SummaryItem.objects.order_by('id').values('id', 'snapshot')])
    require(after == before, '范围检查改变了原有摘要项目。')

report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS',
    'scope': 'CURRENT_RECORD_SUMMARY_SCOPE_READ_ONLY', 'actual_records': len(records), 'actual_summaries': len(summaries),
    'checked_scopes': checks, 'therapy_overlaps': overlapping, 'crossing_boundary_fields': crossing_boundaries,
    'excluded_fields': excluded, 'included_unknown_time_fields': unknown_times,
    'original_summary_items': len(originals), 'original_summary_items_sha256': before,
    'transaction_read_only': True, 'clinical_acceptance': 'NOT_RUN', 'generation_and_recovery': 'NOT_RUN',
    'visual_validation': 'NOT_RUN', 'source_sha256': hashlib.sha256((ROOT / 'backend/summaries/service.py').read_bytes()).hexdigest()}
(ROOT / '.planning-work/t25-summary-scope.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
