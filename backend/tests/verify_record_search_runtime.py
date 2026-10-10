import argparse
import json
from datetime import datetime, timezone

from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from django.core import signing
from django.db.models import Exists, OuterRef
from jsonschema import Draft202012Validator, FormatChecker
from identity.models import Patient
from recognition.models import CandidateConfirmation
from records.models import Record, RecordField, RecordVersion
from records.queries import analyte_search, record_search, record_version_read, record_history_read, source_resolve
from records.service import read_record
from records.correction import medical_time_for_correction
from files.models import FileObject, FilePage
from records.search_input import RecordSearchInput


schema = json.loads((ROOT / 'backend/records/search.schema.json').read_text())
Draft202012Validator.check_schema(schema)
page_validator = Draft202012Validator({**schema, '$ref': '#/$defs/RecordSearchPage'}, format_checker=FormatChecker())
checks = []
queried_pages = []
read_projections = []
arguments = argparse.ArgumentParser()
arguments.add_argument('--frontend-json', action='store_true')
arguments.add_argument('--route-json', action='store_true')
options = arguments.parse_args()
read_validators = {name: Draft202012Validator({**schema, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
                   for name in ['RecordView', 'RecordHistoryView', 'AnalyteTrendView']}
correction_field_validator = Draft202012Validator({**schema, '$ref': '#/$defs/RecordCorrectionField'}, format_checker=FormatChecker())
correction_fields = tuple(schema['$defs']['RecordCorrectionField']['properties'])


def validate_read(name, value):
    errors = list(read_validators[name].iter_errors(value))
    require(not errors, '记录读取结果未通过 Schema 校验：' + '; '.join('/'.join(str(part) for part in error.absolute_path) for error in errors[:10]))
    if options.frontend_json:
        read_projections.append({'schema': name, 'response': value})


def search(patient, **conditions):
    principal = conditions.pop('principal', None)
    payload = RecordSearchInput.model_validate(conditions).model_dump()
    response = record_search(patient=patient, principal=principal, **payload)
    errors = list(page_validator.iter_errors(response))
    require(not errors, '记录返回资料未通过 Schema 校验：' + '; '.join(
        '/'.join(str(part) for part in error.absolute_path) for error in errors[:10]))
    if options.frontend_json:
        queried_pages.append({'input': payload, 'response': response})
    return response


def identifiers(page):
    return [item['record_id'] for item in page['records']]


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    require(set(RecordSearchInput.model_fields) == set(schema['$defs']['RecordSearchInput']['properties']), '查询输入字段不一致。')
    for patient in Patient.objects.order_by('id'):
        baseline = search(patient, limit=100)
        require(baseline['next_cursor'] is None, '当前检查需要读取全部现有记录。')
        expected = set(str(value) for value in Record.objects.filter(patient=patient, status='active').values_list('id', flat=True))
        require(set(identifiers(baseline)) == expected, '查询缺少当前有效记录。')
        pages, cursor_value = [], None
        while True:
            page = search(patient, limit=1, cursor=cursor_value)
            pages.extend(identifiers(page))
            cursor_value = page['next_cursor']
            if cursor_value is None:
                break
            require(len(pages) <= len(expected), '查询分页未正常结束。')
        require(pages == identifiers(baseline), '查询分页顺序或记录内容不一致。')
        current_records = Record.objects.filter(patient=patient, status='active', id=OuterRef('record_id'), current_version=OuterRef('version'))
        versions = RecordVersion.objects.filter(patient=patient).filter(Exists(current_records))
        organization_count = 0
        for organization in versions.exclude(source_organization__isnull=True).exclude(source_organization='').values_list('source_organization', flat=True).distinct():
            require(organization.strip() and len(organization) <= 120, '现有来源医院不能作为有效筛选条件。')
            actual = search(patient, source_organization=organization, limit=100)
            expected_organizations = set(str(value) for value in versions.filter(source_organization__icontains=organization).values_list('record_id', flat=True))
            require(set(identifiers(actual)) == expected_organizations, '来源医院筛选未使用当前记录版本。')
            organization_count += 1
        author_count = 0
        for version in versions.select_related('author').order_by('author_id').distinct('author_id'):
            principal = version.author
            confirmations = CandidateConfirmation.objects.filter(patient=patient, confirmed_by=principal, decision__in=['accepted', 'modified', 'unknown'])
            confirmed_versions = RecordField.objects.filter(patient=patient, confirmation_id__in=confirmations.values('id')).values_list('record_version_id', flat=True)
            expected_self = set(str(value) for value in versions.filter(author=principal, capture_method='manual').values_list('record_id', flat=True))
            expected_self.update(str(value) for value in versions.filter(id__in=confirmed_versions).values_list('record_id', flat=True))
            actual = search(patient, principal=principal, confirmed_by_self=True, limit=100)
            require(set(identifiers(actual)) == expected_self, '本人确认筛选与实际登记或确认人员不一致。')
            original = search(patient, principal=principal, limit=1)
            if original['next_cursor']:
                scope = signing.loads(original['next_cursor'], salt='record-medical-order', max_age=3600)['scope']
                require(scope['principal'] == str(principal.id) and scope['confirmed_by_self'] is False,
                        '分页标识未绑定实际查询人员与确认条件。')
            author_count += 1
        checks.append({'records': len(expected), 'pagination': 'PASS', 'response_schema': 'PASS',
                       'organization_conditions': organization_count, 'confirmation_principals': author_count})
    analyte_count, analyte_field_count, scoped_analyte_count = 0, 0, 0
    route_analyte = None
    for patient in Patient.objects.order_by('id'):
        current_records = Record.objects.filter(patient=patient, status='active', id=OuterRef('record_id'), current_version=OuterRef('version'))
        versions = RecordVersion.objects.filter(patient=patient).filter(Exists(current_records))
        fields = RecordField.objects.filter(patient=patient, record_version_id__in=versions.values('id'))
        concepts = fields.exclude(concept_code__isnull=True).exclude(concept_code='').values_list('concept_code', flat=True).distinct().order_by('concept_code')
        for concept in concepts:
            actual = analyte_search(patient=patient, concept_code=concept)
            validate_read('AnalyteTrendView', actual)
            require(actual['patient_id'] == str(patient.id) and actual['concept_code'] == concept, '历次检验返回的患儿与项目不一致。')
            saved = {str(field.id): field for field in fields.filter(concept_code=concept)}
            require({item['field_id'] for item in actual['trend']} == set(saved), '历次检验没有准确使用当前有效版本。')
            require(len(actual['trend']) == len(saved), '历次检验重复采用同一字段。')
            for item in actual['trend']:
                original = saved[item['field_id']]
                require(item['record_version_id'] == str(original.record_version_id)
                        and item['raw_numeric'] == (str(original.raw_numeric) if original.raw_numeric is not None else None)
                        and item['raw_text'] == original.raw_text and item['raw_unit'] == original.raw_unit
                        and item['method'] == original.method and item['value_state'] == original.value_state
                        and item['qualifier'] == original.qualifier, '历次检验原值、单位、方法或状态发生变化。')
                require(not item['direction'] or not item['comparison_reasons'], '已表达变化的结果同时包含不可比较原因。')
            for field_code in fields.filter(concept_code=concept).values_list('field_code', flat=True).distinct():
                scoped = analyte_search(patient=patient, concept_code=concept, field_codes=[field_code])
                validate_read('AnalyteTrendView', scoped)
                require({item['field_id'] for item in scoped['trend']} == {field_id for field_id, field in saved.items() if field.field_code == field_code},
                        '历次检验未按实际字段范围过滤。')
                scoped_analyte_count += 1
            empty = analyte_search(patient=patient, concept_code=concept, field_codes=[])
            validate_read('AnalyteTrendView', empty)
            require(empty['trend'] == [], '空字段范围仍返回历次检验内容。')
            analyte_count += 1
            analyte_field_count += len(saved)
            if route_analyte is None:
                route_analyte = {'patient_id': str(patient.id), 'analyte_id': concept}
    current_count, version_count, history_count, source_count = 0, 0, 0, 0
    correction_time_count, correction_null_time_count = 0, 0
    route_identity = None
    for record in Record.objects.select_related('patient').order_by('id'):
        current = read_record(record.id, record.patient)
        validate_read('RecordView', current)
        require(current['record_id'] == str(record.id) and current['patient_id'] == str(record.patient_id)
                and current['version'] == record.current_version, '当前记录身份、患儿或版本不一致。')
        current_count += 1
        if route_identity is None:
            require(current['fields'], '当前路由检查记录没有可核验的字段。')
            route_identity = {'record_id': current['record_id'], 'record_version_id': current['record_version_id'], 'field_id': current['fields'][0]['field_id']}
        history = record_history_read(patient=record.patient, record_id=record.id)
        validate_read('RecordHistoryView', history)
        expected_versions = list(RecordVersion.objects.filter(record_id=record.id, patient=record.patient).order_by('version'))
        require([value['record_version_id'] for value in history['versions']] == [str(value.id) for value in expected_versions]
                and history['current_version'] == str(record.current_version), '历史版本集合与当前保存记录不一致。')
        history_count += 1
        for version in expected_versions:
            value = record_version_read(patient=record.patient, record_version_id=version.id)
            validate_read('RecordView', value)
            require(value['patient_id'] == str(record.patient_id) and value['record_version_id'] == str(version.id)
                    and value['record_id'] == str(record.id) and value['version'] == version.version, '来源版本身份或患儿不一致。')
            version_count += 1
            for field in value['fields']:
                projected = {key: field[key] for key in correction_fields}
                require(correction_field_validator.is_valid(projected), '已有字段的更正输入投影未通过 Schema 校验。')
                saved_field = RecordField.objects.get(id=field['field_id'], patient=record.patient, record_version_id=version.id)
                preserved_time = medical_time_for_correction(record.patient, saved_field, projected)
                require((str(preserved_time.id) if preserved_time else None) == field['medical_time_id'],
                        '没有修改的医学时间未保持原引用。')
                correction_time_count += int(preserved_time is not None)
                correction_null_time_count += int(preserved_time is None)
                source = source_resolve(patient=record.patient, record_version_id=version.id, field_id=field['field_id'])
                require(source['field_id'] == field['field_id'] and source['record_version_id'] == str(version.id)
                        and source['field_code'] == field['field_code'], '字段来源引用不一致。')
                if field['source_anchor']:
                    page = FilePage.objects.get(id=field['source_anchor']['page_id'], patient=record.patient)
                    require(field['source_anchor']['page_ordinal'] == page.ordinal
                            and field['source_anchor']['file_id'] == str(page.file_id), '来源页码或原件不一致。')
                source_count += 1
    if options.route_json:
        require(route_analyte is not None, '当前数据库缺少已有检验项目标识。')
        route_identity.update(route_analyte)
        file_id = FileObject.objects.filter(purpose='original', status='available').order_by('id').values_list('id', flat=True).first()
        require(file_id is not None, '当前数据库缺少可用于匿名接口检查的原件标识。')
        route_identity['file_id'] = str(file_id)

report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS',
          'scope': 'CURRENT_DATABASE_READ_ONLY_QUERY', 'checks': checks, 'transaction_read_only': True,
          'clinical_data_created': False, 'credentials_submitted': False, 'provider_called': False,
          'authenticated_http_validation': 'NOT_RUN', 'clinical_business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
report['record_read_projection'] = {'current_records': current_count, 'record_versions': version_count, 'history_lists': history_count,
                                    'field_source_references': source_count, 'restricted_field_http_validation': 'NOT_RUN',
                                    'actual_source_viewer_validation': 'NOT_RUN'}
report['analyte_read_projection'] = {'actual_projects': analyte_count, 'actual_fields': analyte_field_count,
                                    'scoped_field_queries': scoped_analyte_count, 'unknown_and_original_values_preserved': 'PASS',
                                    'clinical_comparability_validation': 'NOT_RUN', 'approved_dictionary_selection': 'NOT_RUN'}
report['correction_read_projection'] = {'actual_fields': source_count, 'unchanged_medical_time_references': correction_time_count,
                                       'unchanged_null_time_references': correction_null_time_count, 'input_field_schema': 'PASS',
                                       'medical_time_rows_created': False, 'actual_correction_submission': 'NOT_RUN',
                                       'draft_restore_validation': 'NOT_RUN', 'concurrent_correction_validation': 'NOT_RUN'}
if options.route_json:
    require(route_identity is not None, '当前数据库缺少可用于匿名路由检查的记录标识。')
    print(json.dumps(route_identity))
else:
    (ROOT / '.planning-work/t54-record-search.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': report, 'pages': queried_pages, 'reads': read_projections} if options.frontend_json else report, ensure_ascii=False))
