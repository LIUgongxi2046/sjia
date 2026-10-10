import argparse
import json
from datetime import datetime, timezone

import httpx
from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from archives.models import ArchiveDocument, ArchiveDocumentVersion
from identity.models import Patient


arguments = argparse.ArgumentParser()
arguments.add_argument('--endpoint-only', action='store_true')
options = arguments.parse_args()
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    patient = Patient.objects.order_by('id').first()
    require(patient is not None, '当前数据库缺少可以核查的患儿身份。')
    document = ArchiveDocument.objects.order_by('id').first()
    identifier = document.id if document is not None else patient.id
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
        for path in [f'/api/v2/archive-documents/{identifier}', f'/api/v2/archive-documents/{identifier}/versions']:
            response = client.get(path)
            require(response.status_code == 401, '文书读取接口没有准确拒绝匿名请求。')
            require(response.json()['code'] == 'AUTH_REQUIRED', '文书读取没有返回实际认证状态。')
            require('private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control'],
                '文书读取缺少私有资料缓存限制。')

if options.endpoint_only:
    print(json.dumps({'status': 'PASS', 'scope': 'DOCUMENT_ANONYMOUS_HTTP', 'checks': 2,
        'authenticated_business_validation': 'NOT_RUN', 'clinical_data_created': False}))
else:
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    from archives.documents import document_read
    from archives.models import ArchiveDocumentPage, ArchiveDocumentEpisode
    from records.service import medical_time_view

    schemas = {}
    for name in ['backend-design/v2-protocol.schema.json', 'backend-design/protocol.schema.json', 'data-design/dataset.schema.json']:
        uri = (ROOT / name).as_uri()
        schema = json.loads((ROOT / name).read_text())
        schema['$id'] = uri
        Draft202012Validator.check_schema(schema)
        schemas[uri] = schema
    registry = Registry().with_resources((uri, Resource.from_contents(schema)) for uri, schema in schemas.items())
    schema = schemas[(ROOT / 'backend-design/v2-protocol.schema.json').as_uri()]
    validator = Draft202012Validator({**schema, '$ref': '#/$defs/DocumentView'}, registry=registry,
        format_checker=FormatChecker())
    counts = {'documents': 0, 'versions': 0, 'page_references': 0, 'episode_references': 0}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        documents = {row.id: row for row in ArchiveDocument.objects.order_by('id')}
        patients = {row.id: row for row in Patient.objects.order_by('id')}
        counts['documents'] = len(documents)
        for version in ArchiveDocumentVersion.objects.order_by('id'):
            patient = patients[version.patient_id]
            value = document_read(document=documents[version.document_id], patient=patient, version=version)
            validator.validate(value)
            require(value['version_id'] == str(version.id) and value['version'] == str(version.version),
                '文书读取没有保留准确版本。')
            require(value['documented_time'] == medical_time_view(version.documented_time_id, patient),
                '文书形成时间与准确保存内容不一致。')
            pages = list(ArchiveDocumentPage.objects.filter(document_version_id=version.id, patient_id=patient.id)
                .order_by('page_order', 'id'))
            require(value['page_ids'] == [str(row.page_id) for row in pages], '文书原始页面顺序不一致。')
            episodes = list(ArchiveDocumentEpisode.objects.filter(document_version_id=version.id, patient_id=patient.id)
                .order_by('episode_id'))
            require(value['episode_ids'] == [str(row.episode_id) for row in episodes], '文书多次就诊引用不一致。')
            require(value['confirmed_text'] == version.confirmed_text and not value['allowed_actions'],
                '读取不能生成确认文字或授予操作权限。')
            counts['versions'] += 1
            counts['page_references'] += len(pages)
            counts['episode_references'] += len(episodes)
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS',
        'scope': 'EXISTING_DOCUMENT_METADATA_READ_ONLY', 'counts': counts, 'anonymous_denial_checks': 2,
        'transaction_read_only': True, 'clinical_data_created': False, 'original_bytes_read': False,
        'document_projection_validation': 'PASS' if counts['versions'] else 'NOT_RUN',
        'authenticated_business_validation': 'NOT_RUN', 'grouping_write_validation': 'NOT_RUN',
        'visual_validation': 'NOT_RUN'}
    target = ROOT / '.planning-work/t43-document-read.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
