import json
import os
import sys
from datetime import datetime
from pathlib import Path

import django


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from archives.models import ArchiveDocument, ArchiveDocumentPage, ArchiveDocumentVersion, ArchiveFactVersion, ArchivePage
from case_details.models import ClinicalEpisode
from records.models import Record, RecordVersion, RecordField


PATIENT_ID = '405514ce-9335-4705-a750-d811148bda4c'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-document-provenance-read.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433), 'Database identity differs from approved simulation.')
        records = list(Record.objects.filter(patient_id=PATIENT_ID, status='active').order_by('id'))
        episodes = list(ClinicalEpisode.objects.filter(patient_id=PATIENT_ID).order_by('id').values(
            'id', 'record_version_id', 'source_field_id', 'episode_type', 'label'))
        versions = []
        for record in records:
            version = RecordVersion.objects.get(record_id=record.id, patient_id=PATIENT_ID,
                version=record.current_version)
            fields = list(RecordField.objects.filter(record_version_id=version.id, patient_id=PATIENT_ID).order_by('id'))
            facts = list(ArchiveFactVersion.objects.filter(record_version_id=version.id, patient_id=PATIENT_ID).order_by('id'))
            facts_by_field = {str(fact.field_id): fact for fact in facts}
            source_rows = []
            for field in fields:
                fact = facts_by_field.get(str(field.id))
                require(fact is not None, 'Current field has no current ArchiveFactVersion.')
                document = None
                pages = []
                links = []
                if fact.document_version_id is not None:
                    document_version = ArchiveDocumentVersion.objects.get(id=fact.document_version_id, patient_id=PATIENT_ID)
                    parent = ArchiveDocument.objects.get(id=document_version.document_id, patient_id=PATIENT_ID)
                    pages = list(ArchivePage.objects.filter(id=fact.page_id, patient_id=PATIENT_ID).values(
                        'id', 'status', 'available_at', 'ordinal', 'batch_id')) if fact.page_id else []
                    links = list(ArchiveDocumentPage.objects.filter(document_version_id=document_version.id,
                        page_id=fact.page_id, patient_id=PATIENT_ID).values('id', 'page_id', 'page_order')) if fact.page_id else []
                    document = {'document_id': str(parent.id), 'current_version': parent.current_version,
                        'document_version_id': str(document_version.id), 'version': document_version.version,
                        'batch_id': str(document_version.batch_id), 'grouping_status': document_version.grouping_status,
                        'confirmed_by_id': str(document_version.confirmed_by) if document_version.confirmed_by else None,
                        'confirmed_at': document_version.confirmed_at, 'available_at': document_version.available_at,
                        'page_links': [{**link, 'id': str(link['id']), 'page_id': str(link['page_id'])} for link in links]}
                source_rows.append({'field_id': str(field.id), 'field_code': field.field_code,
                    'record_field_confirmation_id': str(field.confirmation_id) if field.confirmation_id else None,
                    'fact_version_id': str(fact.id), 'fact_available_at': fact.available_at,
                    'fact_availability_status': fact.availability_status,
                    'fact_confirmation_id': str(fact.confirmation_id) if fact.confirmation_id else None,
                    'page_id': str(fact.page_id) if fact.page_id else None,
                    'page_state': [{**page, 'id': str(page['id']), 'batch_id': str(page['batch_id'])} for page in pages],
                    'document': document})
            versions.append({'record_id': str(record.id), 'kind': record.kind,
                'record_version_id': str(version.id), 'version': version.version,
                'quality_status': version.quality_status, 'fields': source_rows})
    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'CURRENT_DOCUMENT_PROVENANCE_READ',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'patient_id': PATIENT_ID, 'records': versions, 'clinical_episode_count': len(episodes),
        'clinical_episodes': [{**row, 'id': str(row['id']), 'record_version_id': str(row['record_version_id']),
            'source_field_id': str(row['source_field_id'])} for row in episodes], 'read_only': True,
        'saved_and_read': True, 'read_at': datetime.now().astimezone().isoformat()}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    REPORT.write_text(encoded, encoding='utf-8')
    require(json.loads(REPORT.read_text(encoding='utf-8')) == json.loads(encoded), 'Saved provenance report differs from its readback.')
    print(json.dumps({'status': report['status'], 'record_count': len(versions),
        'saved_and_read': True, 'report_path': str(REPORT)}, sort_keys=True))


if __name__ == '__main__':
    main()
