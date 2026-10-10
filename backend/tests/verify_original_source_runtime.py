import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.verify_v2_migration import ROOT, require
from django.db import connection, transaction
from files.models import FileObject, FilePage
from files.originals import original_source_view
from records.models import RecordAttachment, RecordField, RecordVersion
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


parser = argparse.ArgumentParser()
parser.add_argument('--frontend-json', action='store_true')
options = parser.parse_args()
schema = json.loads((ROOT / 'backend/files/original-read.schema.json').read_text())
record_schema = json.loads((ROOT / 'backend/records/search.schema.json').read_text())
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema, registry=Registry().with_resource('record-search.schema.json', Resource.from_contents(record_schema)), format_checker=FormatChecker())
responses = []
counts = {'record_versions': 0, 'original_references': 0, 'page_references': 0, 'document_references': 0, 'copy_references': 0, 'empty_original_versions': 0}
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    for version in RecordVersion.objects.select_related('patient').order_by('id'):
        codes = list(RecordField.objects.filter(patient=version.patient, record_version_id=version.id).values_list('field_code', flat=True))
        result = original_source_view(version, codes)
        validator.validate(result)
        require(result['patient_id'] == str(version.patient_id) and result['record_version_id'] == str(version.id)
            and result['record_id'] == str(version.record_id) and result['record_version'] == version.version, '原件投影对象不一致。')
        attachments = list(RecordAttachment.objects.filter(patient_id=version.patient_id, record_version_id=version.id).order_by('ordinal'))
        require([(item['file_id'], item['ordinal']) for item in result['originals']] == [(str(item.file_id), item.ordinal) for item in attachments], '原件顺序或标识没有来自保存版本。')
        for item in result['originals']:
            actual = FileObject.objects.get(id=item['file_id'], patient=version.patient)
            require(actual.purpose == 'original' and item['object_version'] == actual.object_version and item['checksum'] == actual.checksum
                and item['created_at'] == actual.created_at.isoformat() and item['status'] == actual.status, '原件版本、时间或状态改变。')
            pages = FilePage.objects.filter(patient=version.patient, file_id=actual.id).order_by('ordinal')
            require(item['pages'] == [{'page_id': str(page.id), 'ordinal': page.ordinal} for page in pages], '原件页面没有来自准确保存对象。')
            counts['page_references'] += len(item['pages'])
            counts['document_references'] += len(item['documents'])
            counts['copy_references'] += len(item['purpose_copies'])
        counts['record_versions'] += 1
        counts['original_references'] += len(result['originals'])
        counts['empty_original_versions'] += not result['originals']
        responses.append(result)
report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS',
    'scope': 'EXISTING_ORIGINAL_METADATA_READ_ONLY', 'counts': counts, 'transaction_read_only': True,
    'clinical_data_created': False, 'original_bytes_read': False, 'authenticated_business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
print(json.dumps({'report': report, 'responses': responses} if options.frontend_json else report, ensure_ascii=False))
