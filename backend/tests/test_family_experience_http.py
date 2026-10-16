"""Real family HTTP/DTO readback in an isolated test DB, never a user session."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings

from tests import test_family_experience_seed as seed_tests
from tests.isolated_rows import IsolatedSchemaRows
from tests.test_home_read_snapshot import validator


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='synthetic-browser-demo')
class FamilyExperienceHTTPTests(TransactionTestCase):
    def setUp(self):
        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        seed_tests.FamilyExperienceSeedTests.setUp(self)
        # Seeder and reader are separate OS processes. Its evidence/object
        # directories remain after the management command has fully exited.
        from files.storage import LocalFileStore
        store = LocalFileStore(settings.FILE_OBJECT_ROOT)
        for name in ('files.service.object_store', 'files.purpose.object_store'):
            replacement = patch(name, store)
            replacement.start()
            self.addCleanup(replacement.stop)
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.development',
            'SJIA_ENVIRONMENT': settings.SJIA_ENVIRONMENT, 'SJIA_DEBUG': '1',
            'SJIA_DB_NAME': connection.settings_dict['NAME'],
            'SJIA_CONFIG_EVIDENCE_ROOT': str(settings.CONFIG_EVIDENCE_ROOT),
            'SJIA_CONFIG_EVIDENCE_MAX_BYTES': str(settings.CONFIG_EVIDENCE_MAX_BYTES),
            'SJIA_FILE_OBJECT_ROOT': str(settings.FILE_OBJECT_ROOT)}
        result = subprocess.run([sys.executable, 'manage.py', 'seed_family_experience',
            '--username', 'family-experience', '--principal-id', str(self.actor.id),
            '--acknowledge-test-only'], cwd=Path(__file__).parents[1], env=env,
            capture_output=True, text=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.result = json.loads(result.stdout)
        from configurations.models import ConfigVersion
        from configurations.qualification import configuration_qualification_reason
        from sources.models import SourceRevision
        root = Path(settings.CONFIG_EVIDENCE_ROOT).resolve() / 'family-experience-v1'
        self.assertTrue(root.is_dir())
        for revision in SourceRevision.objects.filter(source__source_key__startswith='family-experience-v1'):
            self.assertTrue(Path(revision.source.location).resolve().is_relative_to(root))
            self.assertTrue(Path(revision.source.location).is_file())
        for config in ConfigVersion.objects.filter(source_revision__source__source_key='family-experience-v1'):
            self.assertIsNone(configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT))
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        self.addCleanup(lambda: session.delete(session.session_key))

    def get_json(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_after_seed_process_exit_family_menus_return_real_data_and_original_bytes(self):
        from archives.search_schemas import VALIDATORS
        from files.models import FileObject
        from records.models import Record, RecordVersion
        from summaries.models import Summary

        session = self.get_json('/api/session')
        self.assertEqual(session['principal_id'], str(self.actor.id))
        relations = self.get_json('/api/relations')['relations']
        self.assertEqual({row['patient_id'] for row in relations},
            {row['id'] for row in self.result['patients'].values()})
        self.assertEqual(len(relations), 2)
        for patient in self.result['patients'].values():
            patient_id = patient['id']
            home = self.get_json(f'/api/patients/{patient_id}/home')
            self.assertFalse(list(validator('HomeView').iter_errors(home)))
            self.assertTrue(home['recent_records'])
            self.assertEqual(len(home['recent_records']), 5)
            recent = self.client.post(f'/api/patients/{patient_id}/records/search',
                {'limit': 5}, content_type='application/json')
            self.assertEqual(recent.status_code, 200, recent.content)
            self.assertEqual([row['record_id'] for row in home['recent_records']],
                [row['record_id'] for row in recent.json()['records']])
            self.assertNotIn('clinical', home)
            self.assertIsNone(home['current_alert_id'])
            self.assertEqual(home['assessment_state'], 'not_evaluated')
            drafts = self.get_json(f'/api/patients/{patient_id}/drafts')['drafts']
            self.assertEqual(len(drafts), 1)
            summaries = self.get_json(f'/api/patients/{patient_id}/summaries')['summaries']
            self.assertEqual(len(summaries), 1)
            summary = self.get_json(f"/api/summaries/{summaries[0]['summary_id']}")
            self.assertTrue(summary['items'])
            self.assertFalse(summary['needs_regeneration'])
            self.assertNotIn('clinical', summary)
            self.assertEqual(Summary.objects.get(id=summary['summary_id']).patient_id.hex, patient_id.replace('-', ''))
            for kind in ('observation', 'report', 'therapy'):
                response = self.client.post(f'/api/patients/{patient_id}/records/search',
                    {'kind': kind}, content_type='application/json')
                self.assertEqual(response.status_code, 200, response.content)
                found = response.json()['records']
                self.assertEqual(len(found), Record.objects.filter(patient_id=patient_id, kind=kind).count())
                for item in found:
                    record = self.get_json(f"/api/records/{item['record_id']}")
                    self.assertTrue(record['fields'])
                    self.assertEqual(record['record_version_id'], item['detail']['record_version_id'])
            # The normal archive menu now has a genuine manual document, with
            # family-proposed visits kept separate from ClinicalEpisode rows.
            response = self.client.post(f'/api/v2/patients/{patient_id}/archives/search',
                {}, content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
            archive = response.json()
            self.assertFalse(list(VALIDATORS['ArchiveSearchView'].iter_errors(archive)))
            self.assertEqual(archive['patient_id'], patient_id)
            self.assertEqual(len(archive['items']), 1)
            item = archive['items'][0]
            document = self.get_json(f"/api/v2/archive-documents/{item['document']['document_id']}")
            self.assertEqual(document['grouping_status'], 'pending')
            self.assertEqual(document['episode_ids'], [])
            self.assertEqual(item['episodes'], [])
            self.assertEqual(len(document['proposed_metadata']['episodes']), 2)
            batch = self.get_json(f"/api/v2/archive-batches/{document['batch_id']}")
            self.assertEqual(batch['intake_mode'], 'manual')
            self.assertEqual(batch['budget_status'], 'unknown')
            for page in item['pages']:
                response = self.client.get(f"/api/files/{page['file_id']}/content")
                self.assertEqual(response.status_code, 200)
                content = b''.join(response.streaming_content) if response.streaming else response.content
                import io
                from PIL import Image
                with Image.open(io.BytesIO(content)) as image:
                    source = json.loads(image.info['demo-content'])
                self.assertEqual(source['confirmed_text'], document['confirmed_text'])
                self.assertEqual(source['proposed_episodes'], document['proposed_metadata']['episodes'])
                self.assertFalse(source['ocr_performed'])
                self.assertFalse(source['clinical_document'])
        for file in FileObject.objects.filter(purpose='original', status='available'):
            response = self.client.get(f'/api/files/{file.id}/content')
            self.assertEqual(response.status_code, 200)
            content = b''.join(response.streaming_content) if response.streaming else response.content
            self.assertTrue(content.startswith(b'\x89PNG\r\n\x1a\n'))
        for record in Record.objects.filter(kind='report'):
            version = RecordVersion.objects.get(record_id=record.id, version=record.current_version)
            originals = self.get_json(f'/api/record-versions/{version.id}/originals')
            self.assertTrue(originals['originals'])

