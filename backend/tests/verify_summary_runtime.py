import json
import argparse
import hashlib
from datetime import datetime, timezone
from unittest import TestCase

from tests.verify_v2_migration import ROOT, require

from django.db import connection, transaction
from summaries.models import Summary, SummaryItem
from summaries.service import read_summary, list_summaries, SummaryRejected
from summaries.submission import summary_task_view, summary_request_view
from tasks.models import Task
from configurations.service import compute_checksum
from requests.models import WriteIntent
from identity.models import Patient, Principal
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from django.urls import reverse, resolve


arguments = argparse.ArgumentParser()
arguments.add_argument('--frontend-json', action='store_true')
arguments.add_argument('--route-json', action='store_true')
options = arguments.parse_args()
schema = json.loads((ROOT / 'backend/summaries/read.schema.json').read_text())
record_schema = json.loads((ROOT / 'backend/records/search.schema.json').read_text())
registry = Registry().with_resource('record-search.schema.json', Resource.from_contents(record_schema))
Draft202012Validator.check_schema(schema)
validators = {name: Draft202012Validator({**schema, '$ref': f'#/$defs/{name}'}, registry=registry, format_checker=FormatChecker())
    for name in ['SummaryView', 'SummaryList', 'SummaryTaskView', 'SummaryRequestView', 'SummaryRequestList']}
responses, verified, rejected, source_count, unavailable_details = [], 0, 0, 0, 0
route = None


def validate(name, value):
    errors = list(validators[name].iter_errors(value))
    diagnostics = '; '.join('/'.join(str(part) for part in error.absolute_path) + ':' + error.validator
        + (':' + ','.join(key for key in error.validator_value if key not in error.instance) if error.validator == 'required' else '')
        for error in errors[:10])
    require(not errors, '摘要资料未通过 Schema：' + diagnostics)
    if options.frontend_json:
        responses.append({'schema': name, 'response': value})


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    summaries = list(Summary.objects.select_related('patient').order_by('id'))
    require(bool(summaries), '当前数据库没有可验证的实际摘要。')
    fields = sorted({key for item in SummaryItem.objects.all() for key in item.snapshot})
    saved_items_before = list(SummaryItem.objects.order_by('id').values())
    saved_items_sha256 = hashlib.sha256(json.dumps(saved_items_before, sort_keys=True, default=str,
        ensure_ascii=False).encode()).hexdigest()
    tasks = [Task.objects.get(id=summary.task_id) for summary in summaries]
    metadata = {'existing_summaries': len(summaries), 'existing_items': SummaryItem.objects.count(), 'snapshot_properties': fields,
        'task_input_properties': sorted({key for task in tasks for key in task.input_refs}),
        'task_checksum_matches': sum(compute_checksum(task.input_refs) == task.input_sha256 for task in tasks)}
    if not options.route_json:
        (ROOT / '.planning-work/t54-summary-source-metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    for summary in summaries:
        task = Task.objects.get(id=summary.task_id)
        validate('SummaryTaskView', summary_task_view(task))
        if compute_checksum(task.input_refs) != task.input_sha256:
            with TestCase().assertRaises(SummaryRejected) as failure:
                read_summary(summary.id, summary.patient)
            require(failure.exception.reason_code == 'SUMMARY_TASK_INPUT_INVALID', '校验失败摘要必须准确拒绝。')
            rejected += 1
            continue
        result = read_summary(summary.id, summary.patient)
        validate('SummaryView', result)
        require(result.get('patient_id') == str(summary.patient_id), '摘要响应缺少准确患儿身份。')
        require(result['summary_id'] == str(summary.id), '摘要响应与当前请求对象不一致。')
        require(result['task_id'] == str(task.id), '摘要没有指向原有生成任务。')
        source = {str(item.field_id): item for item in SummaryItem.objects.filter(summary_id=summary.id)}
        if result['needs_regeneration']:
            require(not result['items'], '失效摘要不能返回当前不符合资格的正文。')
            unavailable_details += 1
        else:
            require(len(result['items']) == len(source), '符合当前资格的摘要没有完整保留已有项目。')
        for item in result['items']:
            saved = source[item['field_id']]
            actual = item['snapshot']
            require(item['ordinal'] == saved.ordinal and item['record_version_id'] == str(saved.record_version_id), '摘要来源顺序或版本不一致。')
            for key, value in saved.snapshot.items():
                require(value == (actual[key] if key in actual else actual['field'][key]), '摘要项目改变了原始保存内容。')
            if 'confirmation_id' not in saved.snapshot:
                require(actual['confirmation_snapshot_state'] == 'not_recorded' and actual['field']['confirmation_id'] is None,
                    '历史确认缺失应当保持未知。')
            if 'source_anchor' not in saved.snapshot:
                require(actual['source_anchor_snapshot_state'] == 'not_recorded' and actual['field']['source_anchor'] is None,
                    '历史来源页码缺失应当保持未知。')
            source_count += 1
        for key in ['record_kinds', 'include_source_status', 'environment', 'template_checksum', 'visibility_checksum']:
            require(result[key] == task.input_refs.get(key), '摘要范围元数据没有来自原始任务。')
        target = reverse('summary_create', kwargs={'patient_id': summary.patient_id})
        require(target == f'/api/patients/{summary.patient_id}/summaries' and resolve(target).url_name == 'summary_create', '摘要目标路由不准确。')
        verified += 1
        if route is None:
            route = {'summary_id': str(summary.id), 'task_id': str(task.id), 'patient_id': str(summary.patient_id)}
    for patient in Patient.objects.order_by('id'):
        listing = list_summaries(patient)
        validate('SummaryList', listing)
        require(all(row['patient_id'] == str(patient.id) for row in listing['summaries']), '摘要目录越过当前患儿范围。')
    intents = list(WriteIntent.objects.filter(operation_id='summary_create').select_related('actor', 'write_request'))
    for intent in intents:
        validate('SummaryRequestView', summary_request_view(intent))
    for principal in Principal.objects.order_by('id'):
        for patient in Patient.objects.order_by('id'):
            owned = [summary_request_view(intent) for intent in intents if intent.actor_id == principal.id and intent.patient_id == patient.id]
            validate('SummaryRequestList', {'patient_id': str(patient.id), 'requests': owned})
    require(saved_items_before == list(SummaryItem.objects.order_by('id').values()), '只读摘要检查改变了原有保存项目。')

report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS', 'scope': 'CURRENT_SUMMARY_READ_ONLY_PROJECTION',
    **metadata, 'verified_details': verified, 'rejected_invalid_task_inputs': rejected, 'original_items_verified': source_count,
    'unavailable_details': unavailable_details, 'saved_items_sha256': saved_items_sha256, 'saved_items_preserved': True,
    'actual_summary_intents': len(intents), 'actual_projections': len(responses), 'transaction_read_only': True, 'summaries_created': False,
    'actual_generation_and_recovery': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
if not options.route_json:
    (ROOT / '.planning-work/t54-summary-read.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
if options.route_json:
    require(route is not None, '当前没有可核验的摘要路由。')
    print(json.dumps(route))
elif options.frontend_json:
    print(json.dumps({'report': report, 'responses': responses}))
else:
    print(json.dumps(report, ensure_ascii=False))
