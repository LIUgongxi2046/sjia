"""Deterministic fictional originals are readable and match manual source text."""
import io
import json
from datetime import date
from uuid import uuid4

from django.test import SimpleTestCase, override_settings
from PIL import Image

from core.demo_visit_files import demo_visit_note, render_demo_visit_png, ensure_demo_visit_attachment


class DemoVisitFileTests(SimpleTestCase):
    def test_notes_have_two_explicit_self_reported_visits(self):
        for child in ('child_a', 'child_b'):
            note = demo_visit_note(child_key=child, as_of=date(2026, 10, 12))
            self.assertEqual(len(note['proposed_episodes']), 2)
            self.assertEqual(note['confirmed_text'], '\n'.join(note['lines']))
            for episode in note['proposed_episodes']:
                self.assertIn(episode['start_time']['local_date'], note['confirmed_text'])
                self.assertIn(episode['description'], note['confirmed_text'])
            self.assertIn('未经专业核对', note['confirmed_text'])
            from archives.inputs import VALIDATORS
            payload = {'batch_id': str(uuid4()), 'page_ids': [str(uuid4())],
                'document_type': 'outpatient', 'organization': note['organization'],
                'documented_time': note['documented_time'], 'episode_ids': [],
                'confirmed_text': note['confirmed_text'], 'proposed_metadata': {
                    'episodes': note['proposed_episodes'], 'grouping_note': '家庭补充，待专业核对。'}}
            self.assertFalse(list(VALIDATORS['DocumentInput'].iter_errors(payload)))
            content = render_demo_visit_png(note)
            self.assertEqual(content, render_demo_visit_png(note))
            with Image.open(io.BytesIO(content)) as image:
                payload = json.loads(image.info['demo-content'])
                self.assertEqual(payload['confirmed_text'], note['confirmed_text'])
                self.assertEqual(payload['proposed_episodes'], note['proposed_episodes'])
                self.assertFalse(payload['clinical_document'])
                self.assertFalse(payload['ocr_performed'])
                image.verify()

    def test_reject_mismatched_text_or_unknown_patient_key(self):
        with self.assertRaises(ValueError):
            demo_visit_note(child_key='real-child', as_of=date(2026, 10, 12))
        note = demo_visit_note(child_key='child_a', as_of=date(2026, 10, 12))
        note['confirmed_text'] = 'not in original'
        with self.assertRaisesMessage(ValueError, 'DEMO_VISIT_TEXT_INVALID'):
            render_demo_visit_png(note)

    @override_settings(DEBUG=False, SJIA_ENVIRONMENT='production')
    def test_attachment_rejects_production_before_writes(self):
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, 'DEMO_REQUIRES_EXPLICIT_DEBUG_TEST_DATABASE'):
            ensure_demo_visit_attachment(actor=None, patient=None, child_key='child_a',
                as_of=date(2026, 10, 12), source_revision=None, use_authorization=None,
                retention_policy=None, request_retention_policy=None)
