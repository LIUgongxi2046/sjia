"""Example archive guardrails; real source bytes and native receipt replay."""
import ast
import io
import json
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from archives.grouping import save_document
from archives.models import ArchiveDocument, ArchiveDocumentVersion
from core.demo_archives import DemoArchivesRejected, ensure_demo_manual_archive
from core.demo_records import demo_identity
from files.models import FileGroup, FileObject
from files.purpose import checked_bytes
from identity.models import Patient
from tests import test_family_experience_seed as family_seed_tests


class DemoArchiveGuardTests(SimpleTestCase):
    @override_settings(DEBUG=False, SJIA_ENVIRONMENT='production')
    def test_production_rejected_before_any_identity_or_file_access(self):
        with self.assertRaisesMessage(DemoArchivesRejected, 'DEMO_ARCHIVE_TEST_CONTEXT_REQUIRED'):
            ensure_demo_manual_archive(actor=None, patient=None, file_group=None, proposed_episodes=[],
                documented_time=None, organization=None, confirmed_text='', request_key='example')

    @override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
    def test_non_test_database_rejected_without_connecting(self):
        with patch.dict(connection.settings_dict, NAME='sjia_real'):
            with self.assertRaisesMessage(DemoArchivesRejected, 'DEMO_ARCHIVE_TEST_CONTEXT_REQUIRED'):
                ensure_demo_manual_archive(actor=None, patient=None, file_group=None, proposed_episodes=[],
                    documented_time=None, organization=None, confirmed_text='', request_key='example')

    def test_helper_has_no_login_provider_or_clinical_entity_writer(self):
        tree = ast.parse((Path(__file__).parents[1] / 'core' / 'demo_archives.py').read_text())
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(names & {'Session', 'PrincipalCredential', 'ClinicalEpisode', 'CandidateSet',
            'CandidateConfirmation', 'Verification', 'Task', 'TaskAttempt', 'RequestFactory',
            'establish_session', 'authenticate_account', 'import_case'})


@override_settings(DEBUG=True, SJIA_ENVIRONMENT='test')
class DemoArchiveReplayTests(TestCase):
    setUp = family_seed_tests.FamilyExperienceSeedTests.setUp
    seed = family_seed_tests.FamilyExperienceSeedTests.seed

    def existing(self):
        self.seed()
        patient = Patient.objects.get(id=demo_identity('patient:child_a'))
        document = ArchiveDocument.objects.get(patient_id=patient.id)
        version = ArchiveDocumentVersion.objects.get(document_id=document.id, version=1)
        for original in FileObject.objects.filter(patient=patient, purpose='original'):
            with Image.open(io.BytesIO(checked_bytes(original))) as image:
                metadata = json.loads(image.info.get('demo-content', '{}'))
            if metadata.get('schema_version') == 'example-family-visit-1':
                return patient, document, version, FileGroup.objects.get(id=original.file_group_id), metadata
        self.fail('Seed must contain a real family visit note original')

    def test_initial_receipt_and_metadata_are_real_unverified_input(self):
        patient, document, version, _, note = self.existing()
        self.assertEqual(version.confirmed_text, note['confirmed_text'])
        self.assertEqual(version.proposed_metadata['episodes'], note['proposed_episodes'])
        self.assertEqual(version.grouping_status, 'pending')
        self.assertEqual(document.current_version, 1)
        self.assertEqual(len(version.proposed_metadata['episodes']), 2)

    def test_seed_replay_preserves_later_user_document_version(self):
        from archives.models import ArchiveDocumentPage
        patient, document, version, group, note = self.existing()
        payload = {'batch_id': str(version.batch_id), 'page_ids': [str(row.page_id) for row in
            ArchiveDocumentPage.objects.filter(document_version_id=version.id).order_by('page_order')],
            'document_type': version.document_type, 'organization': version.organization,
            'documented_time': None, 'episode_ids': [], 'proposed_metadata': version.proposed_metadata,
            'confirmed_text': '本人后来补充的说明', 'expected_version': '1', 'reason': '本人修改示例文书'}
        save_document(patient=patient, actor=self.actor, payload=payload, document=document)
        result = ensure_demo_manual_archive(actor=self.actor, patient=patient, file_group=group,
            proposed_episodes=note['proposed_episodes'], documented_time=note['documented_time'],
            organization=note['organization'], confirmed_text=note['confirmed_text'],
            request_key='child_a-family-visits')
        self.assertEqual(result['version'], '1')
        self.assertEqual(result['current_version'], '2')
        document.refresh_from_db()
        self.assertEqual(document.current_version, 2)
        self.assertEqual(ArchiveDocumentVersion.objects.get(document_id=document.id, version=2).confirmed_text,
            '本人后来补充的说明')

    def test_metadata_mismatch_fails_before_creating_other_documents(self):
        patient, _, _, group, note = self.existing()
        count = ArchiveDocument.objects.count()
        with self.assertRaisesMessage(DemoArchivesRejected, 'DEMO_ARCHIVE_VISIT_CONTENT_MISMATCH'):
            ensure_demo_manual_archive(actor=self.actor, patient=patient, file_group=group,
                proposed_episodes=note['proposed_episodes'], documented_time=note['documented_time'],
                organization=note['organization'], confirmed_text='not in the original', request_key='changed')
        self.assertEqual(ArchiveDocument.objects.count(), count)
