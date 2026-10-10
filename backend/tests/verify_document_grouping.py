import hashlib
import json
from datetime import datetime, timezone

import httpx
import psycopg
from django.db import connection, transaction
from django.conf import settings
from django.urls import resolve, reverse
from jsonschema import Draft202012Validator, FormatChecker

from tests.verify_v2_migration import ROOT, database_args, require, snapshot
from archives.inputs import REGISTRY, PROTOCOL, check_medical_time
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion, ArchiveFactVersion, ArchivePage
from archives.record_facts import medical_time_content
from identity.models import Patient
from medical_time.models import MedicalTime
from requests.archive_documents import OPERATIONS
from requests.models import WriteIntent


def database_snapshot():
    with psycopg.connect(**database_args(settings.DATABASES['default']['NAME'])) as database:
        database.execute('SET TRANSACTION READ ONLY')
        return snapshot(database)


before = database_snapshot()
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    patient = Patient.objects.order_by('id').first()
    require(patient is not None, '当前数据库需要已有患儿用于匿名接口检查。')
    document = ArchiveDocument.objects.order_by('id').first()
    intent = WriteIntent.objects.order_by('id').first()
    require(intent is not None, '当前数据库需要已有请求身份用于匿名结果恢复检查。')
    document_identifier = document.id if document is not None else patient.id
    for operation, keys in (('archive_document_create', {'patient_id': patient.id}),
            ('archive_document_group', {'document_id': document_identifier})):
        route = reverse(operation, kwargs=keys)
        require(resolve(route).url_name == operation, '文书写入路由身份不一致。')
        require(OPERATIONS[operation] in ('DocumentInput', 'GroupingInput'), '文书输入定义缺失。')
    medical_validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/MedicalTimeInput'},
        registry=REGISTRY, format_checker=FormatChecker())
    times = list(MedicalTime.objects.filter(instant__isnull=True).exclude(resolution_status='resolved').order_by('id'))
    for time in times:
        content = medical_time_content(time.id, time.patient_id)
        content = {key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in content.items()}
        medical_validator.validate(content)
        check_medical_time(content)
    counts = {'batches': ArchiveBatch.objects.count(), 'pages': ArchivePage.objects.count(),
        'documents': ArchiveDocument.objects.count(), 'document_versions': ArchiveDocumentVersion.objects.count(),
        'fact_versions': ArchiveFactVersion.objects.count(), 'medical_time_inputs': len(times)}
    for name in ('DocumentIntentInput', 'DocumentRequestLookup', 'DocumentInput', 'GroupingInput', 'RequestView', 'WriteReceipt'):
        Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, registry=REGISTRY,
            format_checker=FormatChecker()).check_schema(PROTOCOL)
    paths = [('POST', reverse('archive_document_create', kwargs={'patient_id': patient.id})),
        ('POST', reverse('archive_document_group', kwargs={'document_id': document_identifier})),
        ('POST', '/api/v2/write-intents'), ('GET', f'/api/v2/requests/{intent.id}'),
        ('POST', f'/api/v2/write-intents/{intent.id}/retry'),
        ('GET', f'/api/v2/requests/{intent.request_id}?lookup=request_id&operation_id=archive_document_group&target_key=/archive-documents/{document_identifier}/grouping')]
with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
    for method, path in paths:
        response = client.request(method, path)
        require(response.status_code == 401 and response.json()['code'] == 'AUTH_REQUIRED',
            '文书写入或结果恢复未准确拒绝匿名请求。')
        require('private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control'],
            '文书请求缺少私有资料缓存限制。')
after = database_snapshot()
require(before == after, '匿名文书检查期间已有业务资料发生变化。')
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'PASS',
    'scope': 'DOCUMENT_ROUTE_SCHEMA_AND_ANONYMOUS_HTTP', 'anonymous_denial_checks': len(paths),
    'counts': counts, 'business_tables_preserved': len(before),
    'database_content_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
    'clinical_data_created': False, 'original_bytes_read': False, 'identity_created': False,
    'authenticated_business_validation': 'NOT_RUN', 'grouping_write_validation': 'NOT_RUN',
    'positive_recovery_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
(ROOT / '.planning-work/t43-document-grouping.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
