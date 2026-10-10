import json
from unittest import TestCase

from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from records.models import RecordField, RecordVersion
from records.service import RecordRejected, _validate_field_input
from recognition.models import SourceAnchor
from recognition.schemas import REPORT_SCHEMA_VERSION, ReportCandidates, ReportNumeric
from recognition.jobs import REPORT_VALUE
from medical_time.models import MedicalTime
from files.models import FilePage
from jsonschema import Draft202012Validator


schema = json.loads((ROOT / 'backend/recognition/report.schema.json').read_text())
Draft202012Validator.check_schema(schema)
Draft202012Validator.check_schema(ReportCandidates.model_json_schema())
require(set(schema['$defs']['reportItem']['required']) == set(ReportCandidates.model_json_schema()['$defs']['ReportItem']['required']), '报告候选必填字段不一致')
decimal_schema = json.loads((ROOT / 'data-design/dataset.schema.json').read_text())['$defs']['decimal']
require(ReportNumeric.model_json_schema()['pattern'] == decimal_schema['pattern'], '报告原值数值规范不一致')
require(schema['$defs']['decimal'] == decimal_schema, '报告客户端数值规范不一致')
numeric_validator = Draft202012Validator(decimal_schema)

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    fields = list(RecordField.objects.order_by('id'))
    anchors = {value.id: value for value in SourceAnchor.objects.filter(id__in=[field.anchor_id for field in fields if field.anchor_id])}
    pages = {value.id: value for value in FilePage.objects.filter(id__in=[anchor.page_id for anchor in anchors.values()])}
    times = {value.id: value for value in MedicalTime.objects.filter(id__in=[field.medical_time_id for field in fields if field.medical_time_id])}
    versions = {value.id: value for value in RecordVersion.objects.filter(id__in=[field.record_version_id for field in fields])}
    values = []
    guard_checks = 0
    numeric_checks = 0
    for field in fields:
        raw = str(field.raw_numeric) if field.raw_numeric is not None else field.raw_text
        if isinstance(raw, str):
            require(isinstance(REPORT_VALUE.validate_python(raw), ReportNumeric) == numeric_validator.is_valid(raw), '已有原值的数值分类与规范不一致')
            numeric_checks += 1
        source = anchors.get(field.anchor_id)
        if source is None:
            continue
        require(source.patient_id == field.patient_id, '已有来源与字段所属患儿不一致')
        page = pages[source.page_id]
        require(page.patient_id == field.patient_id, '已有原件与字段所属患儿不一致')
        value = {'field_code': field.field_code, 'value_type': field.value_type, 'value_state': field.value_state,
            'raw_text': field.raw_text, 'raw_numeric': str(field.raw_numeric) if field.raw_numeric is not None else None,
            'categorical_value': field.categorical_value, 'qualifier': field.qualifier,
            'source_file_id': str(page.file_id), 'source_text': source.original_text}
        _validate_field_input(value)
        with TestCase().assertRaises(RecordRejected) as rejected:
            _validate_field_input({key: item for key, item in value.items() if key != 'source_text'})
        require(rejected.exception.reason_code == 'FIELD_SOURCE_TEXT_REQUIRED', '缺少来源原文时没有准确拒绝')
        guard_checks += 1
        medical_time = times.get(field.medical_time_id)
        sample_time = None
        if medical_time is not None and medical_time.precision in ('date', 'month', 'minute'):
            require(medical_time.patient_id == field.patient_id, '已有来源字段的医学时间所属患儿不一致')
            if medical_time.precision == 'date':
                require(medical_time.local_date is not None, '已有日期缺少原始日期')
                sample_time = {'precision': 'date', 'value': medical_time.local_date.isoformat(), 'timezone': medical_time.timezone_name or ''}
            elif medical_time.precision == 'minute':
                require(medical_time.local_datetime is not None, '已有准确时间缺少原始时间')
                sample_time = {'precision': 'minute', 'value': medical_time.local_datetime.isoformat(), 'timezone': medical_time.timezone_name or ''}
            else:
                require(medical_time.local_year is not None and medical_time.local_month is not None, '已有月份缺少年月')
                sample_time = {'precision': 'month', 'value': f'{medical_time.local_year:04d}-{medical_time.local_month:02d}', 'timezone': '',
                    'localYear': medical_time.local_year, 'localMonth': medical_time.local_month}
        values.append({'name': field.original_name if field.original_name is not None else field.field_code,
            'value': raw, 'comparator': {'eq': '=', 'lt': '<', 'le': '≤', 'gt': '>', 'ge': '≥', None: None}[field.qualifier],
            'unit': field.raw_unit, 'reference': field.reference_interval, 'method': field.method,
            'source_text': source.original_text, 'photo_id': str(page.file_id), 'sampleTime': sample_time,
            'existing_capture_method': versions[field.record_version_id].capture_method})
    require(values and guard_checks, '当前没有可用于来源检查的已保存字段')

print(json.dumps({'values': values, 'source_guard_checks': guard_checks, 'numeric_checks': numeric_checks,
    'candidate_schema_version': REPORT_SCHEMA_VERSION,
    'transaction_read_only': True, 'clinical_data_created': False, 'report_recognition_validation': 'NOT_RUN'}, ensure_ascii=False))
