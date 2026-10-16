from datetime import date, datetime, timedelta, timezone
import hashlib
from uuid import uuid4

from django.test import TestCase

from tests import test_T16 as record_tests
from records.queries import record_search, record_version_read
from records.models import Record, RecordVersion
from records.correction import correct_record
from configurations.field_config import read_field_config
from configurations.models import ConfigVersion
from tests import test_T22 as alert_tests
from alerting.models import AlertEvent
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.alert_detail import alert_content
from tests.integration import test_flow as family_tests
from tests.integration.helpers import activate_summary_configs, post_record
from drafts.models import Draft
from records.service import create_record, RecordRejected
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from summaries.service import generate_summary, read_summary, SummaryRejected
from tests.integration.test_flow3 import _png


class MedicalPaginationTest(TestCase):
    setUp = record_tests.RecordQueryTest.setUp
    _temp_record = record_tests.RecordQueryTest._temp_record

    def test_medical_order_with_unknown_and_cursor(self):
        recent = self._temp_record('38', medical_time={'precision': 'date', 'local_date': '2026-10-03', 'resolution_status': 'local_only'})
        earlier = self._temp_record('37', medical_time={'precision': 'date', 'local_date': '2026-09-01', 'resolution_status': 'local_only'})
        unknown = self._temp_record('36')
        first = record_search(patient=self.patient, limit=1)
        self.assertEqual(first['records'][0]['record_id'], recent['record_id'])
        second = record_search(patient=self.patient, limit=1, cursor=first['next_cursor'])
        self.assertEqual(second['records'][0]['record_id'], earlier['record_id'])
        third = record_search(patient=self.patient, limit=1, cursor=second['next_cursor'])
        self.assertEqual(third['records'][0]['record_id'], unknown['record_id'])
        self.assertIsNone(third['next_cursor'])

    def test_history_includes_time_fields_and_attachments(self):
        result = self._temp_record('38', medical_time={'precision': 'date', 'local_date': '2026-10-03', 'resolution_status': 'local_only'})
        content = record_version_read(patient=self.patient, record_version_id=result['record_version_id'])
        self.assertEqual(content['fields'][0]['medical_time']['local_date'], '2026-10-03')
        self.assertEqual(content['fields'][0]['verification_states'], [])
        self.assertEqual(content['attachments'], [])
        self.assertEqual(content['capture_method'], 'manual')

    def test_corrected_version_preserves_source_type(self):
        result = self._temp_record('38')
        record = Record.objects.get(id=result['record_id'])
        correct_record(record=record, expected_version=1, actor=self.principal, reason='核对测量原文', fields=[{'field_code': 'temp', 'concept_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '37', 'raw_unit': 'C'}], use_authorization_id=self.use_auth.id, retention_policy=self.retention)
        before = record_version_read(patient=self.patient, record_version_id=result['record_version_id'])
        after = RecordVersion.objects.get(record_id=record.id, version=2)
        self.assertEqual(before['fields'][0]['raw_numeric'], '38')
        self.assertEqual(after.correction_reason, '核对测量原文')


class FieldKindTest(TestCase):
    def test_report_cannot_return_observation_definition(self):
        from identity.models import Patient, Principal

        now = datetime.now(timezone.utc)
        actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        patient = Patient.objects.create(display_id='QA02-config', status='active', created_at=now)
        ConfigVersion.objects.create(config_key='field-config-observation', kind='content', version=1, schema_version='field-config-1', payload={'schema_version': 'field-config-1', 'kind': 'observation', 'fields': [{'field_code': 'rash', 'value_type': 'categorical', 'allowed_values': ['yes', 'no']}], 'watch_field_codes': []}, checksum='a' * 64, status='enabled', created_by=actor, created_at=now)
        result = read_field_config(patient, 'report')
        self.assertEqual(result['kind'], 'report')
        self.assertEqual(result['fields'], [])
        self.assertEqual(result['state'], 'disabled')


class AlertContentTest(TestCase):
    setUp = alert_tests.ReadReceiptTest.setUp

    def publish(self):
        from tests.synthetic_fixtures import SyntheticEvidence, synthetic_publication
        from alerting.models import AlertEpisode, AlertEvaluation
        event = AlertEvent.objects.get(id=self.recipient.alert_id)
        event.release.status = 'disabled'
        event.release.save(update_fields=['status'])
        fixture = SyntheticEvidence(self, self.principal)
        release, _, configs = synthetic_publication(fixture)
        fixture.gate.refresh_from_db()
        evaluation = AlertEvaluation.objects.get(id=event.evaluation_id)
        evaluation.policy = configs['rule']
        evaluation.window_policy = configs['window']
        evaluation.publication_epoch = fixture.gate.publication_epoch
        evaluation.save(update_fields=['policy', 'window_policy', 'publication_epoch'])
        episode = AlertEpisode.objects.create(patient=self.patient, rule_family_key='synthetic-test',
            status='open', opened_at=event.created_at, last_evaluation_id=evaluation.id,
            last_event_id=event.id, state_version=1, last_assessment_at=evaluation.assessment_at,
            last_context_sequence=evaluation.context_sequence)
        event.release = release
        event.trigger_policy = configs['rule']
        event.input_snapshot = {'schema_version': 'dynamic-family-event-2',
            'publication': configs['rule'].payload['publication'], 'episode_id': str(episode.id),
            'evaluation_id': str(evaluation.id), 'input_digest': evaluation.input_digest,
            **{key: str(getattr(evaluation, key)) for key in
                ('context_sequence', 'data_epoch', 'verification_epoch', 'publication_epoch')}}
        event.save(update_fields=['release', 'trigger_policy', 'input_snapshot'])
        return event, configs['content']

    def test_current_content_and_contact(self):
        event, _ = self.publish()
        result = alert_content(event, ['temp'])
        self.assertEqual(result['content_state'], 'available')
        self.assertEqual(result['content']['approved_text'], 'SYNTHETIC TEST CONTENT; NO CLINICAL APPROVAL')
        self.assertEqual(result['content']['contact']['state'], 'available')

    def test_changed_data_blocks_content(self):
        event, _ = self.publish()
        event.patient.data_epoch += 1
        self.assertEqual(alert_content(event, ['temp'])['content'], None)
        self.assertEqual(alert_content(event, ['temp'])['validity'], 'invalid')

    def test_disabled_content_does_not_return_text(self):
        event, content = self.publish()
        content.status = 'disabled'
        content.save(update_fields=['status'])
        self.assertEqual(alert_content(event, ['temp'])['content_state'], 'unavailable')
        self.assertIsNone(alert_content(event, ['temp'])['content'])


class DraftTransactionTest(TestCase):
    setUp = family_tests.FamilyFlowIntegrationTest.setUp

    def record(self):
        result = post_record(self.client, self.patient, {'kind': 'observation', 'capture_method': 'manual', 'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '38', 'raw_unit': 'C'}]})
        self.assertEqual(result.status_code, 201)
        return result.json()['record_id']

    def draft(self, record_id):
        result = self.client.post(f'/api/patients/{self.patient.id}/drafts', {'form_type': 'correction', 'payload': {'recordId': record_id, 'version': 1, 'fields': [], 'reason': '模拟核对'}}, content_type='application/json')
        self.assertEqual(result.status_code, 201)
        return result.json()['draft_id']

    def correct(self, record_id, draft_id, draft_version=1):
        from tests.integration.helpers import correction_fields, post_intended
        fields = correction_fields(self.patient, record_id, {"temp": {"raw_numeric": "37"}})
        return post_intended(self.client, f'/api/records/{record_id}/corrections', 'record_correct',
            {'expected_version': 1, 'reason': '模拟核对', 'fields': fields, 'draft_id': draft_id, 'draft_version': str(draft_version)},
            schema_version='back-json-1')

    def test_stale_draft_correction_preserves_record_and_draft(self):
        record_id = self.record()
        draft_id = self.draft(record_id)
        self.assertEqual(self.correct(record_id, draft_id, 2).status_code, 409)
        self.assertEqual(Record.objects.get(id=record_id).current_version, 1)
        self.assertEqual(RecordVersion.objects.filter(record_id=record_id).count(), 1)
        self.assertEqual(Draft.objects.get(id=draft_id).status, 'editing')

    def test_current_draft_correction_consumes_same_transaction(self):
        record_id = self.record()
        draft_id = self.draft(record_id)
        self.assertEqual(self.correct(record_id, draft_id).status_code, 201)
        self.assertEqual(Record.objects.get(id=record_id).current_version, 2)
        self.assertEqual(Draft.objects.get(id=draft_id).status, 'submitted')
        self.assertEqual(self.client.get(f'/api/drafts/{draft_id}').status_code, 409)

    def test_expired_draft_cannot_create_correction(self):
        record_id = self.record()
        draft_id = self.draft(record_id)
        Draft.objects.filter(id=draft_id).update(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(self.correct(record_id, draft_id).status_code, 409)
        self.assertEqual(Record.objects.get(id=record_id).current_version, 1)

    def test_record_change_invalidates_cursor(self):
        self.record()
        self.record()
        first = self.client.post(f'/api/patients/{self.patient.id}/records/search', {'limit': 1}, content_type='application/json').json()
        self.record()
        next_page = self.client.post(f'/api/patients/{self.patient.id}/records/search', {'limit': 1, 'cursor': first['next_cursor']}, content_type='application/json')
        self.assertEqual(next_page.status_code, 409)
        self.assertEqual(next_page.json()['code'], 'CURSOR_STALE')

    def test_report_page_source_survives_correction_and_requires_attachment(self):
        image = _png()
        group = self.client.post(f'/api/patients/{self.patient.id}/uploads', {}, content_type='application/json').json()
        uploaded = self.client.put(f"/api/uploads/{group['group_id']}/files/{uuid4()}?checksum={hashlib.sha256(image).hexdigest()}&size={len(image)}&media_type=image/png", image, content_type='application/octet-stream')
        self.assertEqual(uploaded.status_code, 201)
        file_id = uploaded.json()['file_id']
        field = {'field_code': 'report_item', 'original_name': '模拟项目', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '150', 'raw_text': '150', 'raw_unit': 'ng/mL', 'source_file_id': file_id, 'source_text': '模拟项目 150 ng/mL'}
        arguments = {'patient': self.patient, 'actor': self.principal, 'kind': 'report', 'capture_method': 'ocr_confirmed', 'origin_type': 'report_transcription', 'retention_policy': RetentionPolicy.objects.first(), 'use_authorization_id': DataUseAuthorization.objects.get(patient=self.patient).id, 'fields': [field]}
        with self.assertRaises(RecordRejected) as rejected:
            create_record(**arguments)
        self.assertEqual(rejected.exception.reason_code, 'FIELD_SOURCE_NOT_ATTACHED')
        result = create_record(**arguments, file_ids=[file_id])
        before = record_version_read(patient=self.patient, record_version_id=result['record_version_id'])
        self.assertEqual(before['fields'][0]['source_anchor']['file_id'], file_id)
        corrected = correct_record(record=Record.objects.get(id=result['record_id']), expected_version=1, actor=self.principal, reason='模拟原文核对', fields=[{**field, 'raw_numeric': '151', 'raw_text': '151'}], use_authorization_id=arguments['use_authorization_id'], retention_policy=arguments['retention_policy'])
        after = record_version_read(patient=self.patient, record_version_id=corrected['record_version_id'])
        self.assertEqual(after['fields'][0]['raw_numeric'], '151')
        self.assertEqual(after['fields'][0]['source_anchor'], before['fields'][0]['source_anchor'])
        self.assertEqual(after['attachments'], before['attachments'])


class TherapySummaryScopeTest(TestCase):
    setUp = family_tests.FamilyFlowIntegrationTest.setUp

    def therapy(self, status_value, end):
        fields = [
            {'field_code': 'medication_name', 'value_type': 'text', 'value_state': 'known', 'raw_text': '模拟药名'},
            {'field_code': 'therapy_status', 'value_type': 'categorical', 'value_state': 'known' if status_value else 'unknown', 'categorical_value': status_value},
            {'field_code': 'therapy_start', 'value_type': 'time', 'value_state': 'known', 'medical_time': {'precision': 'date', 'local_date': '2026-08-01', 'timezone_name': 'Asia/Shanghai', 'resolution_status': 'local_only'}},
            {'field_code': 'therapy_end', 'value_type': 'time', 'value_state': 'known' if end else 'unknown', 'medical_time': {'precision': 'date', 'local_date': end, 'timezone_name': 'Asia/Shanghai', 'resolution_status': 'local_only'} if end else None},
        ]
        create_record(patient=self.patient, actor=self.principal, kind='therapy', capture_method='manual', origin_type='self_report', retention_policy=RetentionPolicy.objects.first(), use_authorization_id=DataUseAuthorization.objects.get(patient=self.patient).id, fields=fields)
        activate_summary_configs(self, ['medication_name', 'therapy_start', 'therapy_end', 'therapy_status'])

    def generate(self):
        return generate_summary(patient=self.patient, requested_by=self.principal, from_date=date(2026, 9, 1), to_date=date(2026, 9, 30), scope_timezone='Asia/Shanghai', include_unknown_time=False, field_codes=['medication_name', 'therapy_start', 'therapy_end', 'therapy_status'], environment='test')

    def test_therapy_spanning_scope_includes_name_with_unknown_field_time(self):
        self.therapy('ended', '2026-10-01')
        summary = self.generate()
        content = read_summary(summary.id, self.patient)
        medication = next(item['snapshot']['field'] for item in content['items'] if item['snapshot']['field']['field_code'] == 'medication_name')
        self.assertEqual(medication['raw_text'], '模拟药名')
        self.assertIsNone(medication['medical_time'])

    def test_explicit_ongoing_therapy_includes_name(self):
        self.therapy('ongoing', None)
        self.assertTrue(read_summary(self.generate().id, self.patient)['items'])

    def test_unknown_end_does_not_infer_ongoing_therapy(self):
        self.therapy(None, None)
        with self.assertRaises(SummaryRejected) as result:
            self.generate()
        self.assertEqual(result.exception.reason_code, 'SUMMARY_SCOPE_EMPTY')
