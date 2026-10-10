import argparse
import json
from datetime import datetime, timezone

import httpx
from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from archives.models import ArchiveFact, ArchiveFactVersion


arguments = argparse.ArgumentParser()
arguments.add_argument('--endpoint-only', action='store_true')
arguments.add_argument('--projection-only', action='store_true')
arguments.add_argument('--frontend-json', action='store_true')
options = arguments.parse_args()
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    fact = ArchiveFact.objects.order_by('id').first()
    require(fact is not None, '当前数据库没有可验证的实际事实。')
    if not options.projection_only:
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for path in [f'/api/v2/archive-facts/{fact.id}', f'/api/v2/archive-facts/{fact.id}/versions']:
                response = client.get(path)
                require(response.status_code == 401, '事实读取接口没有准确拒绝匿名请求。')
                require(response.json()['code'] == 'AUTH_REQUIRED', '事实读取没有返回实际认证状态。')
                require('private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control'],
                    '事实读取缺少私有资料缓存限制。')

if options.endpoint_only:
    print(json.dumps({'status': 'PASS', 'scope': 'FACT_ANONYMOUS_HTTP', 'checks': 2, 'clinical_acceptance': 'NOT_RUN'}))
else:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    from archives.queries import fact_read, fact_versions
    from records.models import RecordField
    from records.service import medical_time_view
    from identity.models import Patient
    from configurations.service import compute_checksum
    from unittest import TestCase
    from records.queries import QueryRejected
    from datetime import timedelta

    schemas = {}
    for name in ['backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json', 'data-design/dataset.schema.json']:
        uri = (ROOT / name).as_uri()
        document = json.loads((ROOT / name).read_text())
        document['$id'] = uri
        Draft202012Validator.check_schema(document)
        schemas[uri] = document
    registry = Registry().with_resources((uri, Resource.from_contents(document)) for uri, document in schemas.items())
    schema = schemas[(ROOT / 'backend-design/v2-protocol.schema.json').as_uri()]
    validators = {name: Draft202012Validator({**schema, '$ref': f'#/$defs/{name}'},
        registry=registry, format_checker=FormatChecker()) for name in ['FactView', 'FactList']}
    checks, source_anchors, unknown_availability, missing_quality_reasons, fields_seen = 0, 0, 0, 0, set()
    responses = []
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        rows = list(ArchiveFactVersion.objects.order_by('id'))
        facts = {fact.id: fact for fact in ArchiveFact.objects.order_by('id')}
        patients = {patient.id: patient for patient in Patient.objects.order_by('id')}
        before = compute_checksum([{'id': str(row.id), 'field_id': str(row.field_id), 'version': row.version,
            'context_type': row.context_type, 'assertion_state': row.assertion_state,
            'available_at': row.available_at.isoformat() if row.available_at is not None else None} for row in rows])
        for row in rows:
            patient, fact = patients[row.patient_id], facts[row.fact_id]
            field = RecordField.objects.get(id=row.field_id, record_version_id=row.record_version_id, patient=patient)
            value = fact_read(fact=fact, patient=patient, version=row)
            errors = list(validators['FactView'].iter_errors(value))
            require(not errors, '事实详情未通过正式 Schema：' + ';'.join(
                '/'.join(str(part) for part in error.absolute_path) + ':' + error.validator for error in errors[:10]))
            actual = value['field']['field']
            reason_missing = field.value_state in ('unknown', 'invalid') and not field.quality_reasons
            require(value['field']['quality_reasons_snapshot_state'] == ('not_recorded' if reason_missing else 'recorded'),
                '事实质量原因缺少状态没有来自实际保存字段。')
            missing_quality_reasons += int(reason_missing)
            original_field_validator = Draft202012Validator({'$ref': (ROOT / 'data-design/dataset.schema.json').as_uri() + '#/$defs/clinicalField'},
                registry=registry, format_checker=FormatChecker())
            original_errors = list(original_field_validator.iter_errors(actual))
            require(not original_errors if not reason_missing else bool(original_errors) and all(
                list(error.absolute_path) == ['quality_reasons'] and error.validator == 'minItems' for error in original_errors),
                '事实读取结构改变了新资料输入完整性要求：' + ';'.join(
                    '/'.join(str(part) for part in error.absolute_path) + ':' + error.validator for error in original_errors[:10]))
            for name in ['ordinal', 'field_code', 'concept_code', 'original_name', 'value_type', 'value_state', 'raw_text',
                'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval', 'report_flag', 'sample_time_origin', 'quality_reasons']:
                require(actual[name] == getattr(field, name), '事实投影改变了原有字段内容。')
            require(actual['raw_numeric'] == (str(field.raw_numeric) if field.raw_numeric is not None else None), '事实数值没有来自准确字段。')
            require(actual['medical_time'] == medical_time_view(field.medical_time_id, patient), '事实医学时间与准确字段不一致。')
            require(value['context']['raw_time_text'] == row.raw_time_text and value['context']['time_origin'] == row.time_origin,
                '事实时间文字或来源没有来自保存版本。')
            require(value['context']['context_type'] == row.context_type and value['context']['assertion_state'] == row.assertion_state,
                '事实语境改变了保存状态。')
            require(value['available_at'] == (row.available_at.isoformat() if row.available_at is not None else None), '事实取得时间没有来自保存版本。')
            page = fact_versions(fact=fact, patient=patient, scope={'purpose': 'READ_ONLY_TECHNICAL_CHECK'}, limit=1,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60))
            errors = list(validators['FactList'].iter_errors(page))
            require(not errors, '事实历史未通过正式 Schema。')
            require(page['items'][0]['fact_id'] == str(fact.id) and page['items'][0]['patient_id'] == str(patient.id), '事实历史越过准确对象。')
            if options.frontend_json:
                responses.extend([{'schema': 'FactView', 'response': value}, {'schema': 'FactList', 'response': page}])
            other = next(value for value in patients.values() if value.id != patient.id)
            with TestCase().assertRaises(QueryRejected) as failure:
                fact_read(fact=fact, patient=other, version=row)
            require(failure.exception.reason_code == 'FACT_PATIENT_INVALID', '跨患儿事实读取没有准确拒绝。')
            source_anchors += int(row.source_anchor_id is not None)
            unknown_availability += int(row.availability_status == 'unknown')
            fields_seen.add(row.field_id)
            checks += 1
        require(len(fields_seen) == RecordField.objects.count(), '事实检查没有覆盖全部保存字段。')
        after = compute_checksum([{'id': str(row.id), 'field_id': str(row.field_id), 'version': row.version,
            'context_type': row.context_type, 'assertion_state': row.assertion_state,
            'available_at': row.available_at.isoformat() if row.available_at is not None else None}
            for row in ArchiveFactVersion.objects.order_by('id')])
        require(before == after, '事实只读检查改变了保存内容。')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS', 'scope': 'CURRENT_FACT_READ_ONLY_PROJECTION',
        'actual_facts': len(facts), 'actual_versions': checks, 'actual_projections': checks * 2,
        'actual_fields': len(fields_seen), 'source_anchors': source_anchors, 'unknown_availability': unknown_availability,
        'missing_quality_reasons': missing_quality_reasons,
        'anonymous_http_checks': 0 if options.projection_only else 2, 'transaction_read_only': True,
        'new_facts_created': False, 'clinical_acceptance': 'NOT_RUN', 'authenticated_business': 'NOT_RUN',
        'model_confirmation_and_correction': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
    (ROOT / '.planning-work/t41-fact-read.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': report, 'responses': responses} if options.frontend_json else report, ensure_ascii=False))
