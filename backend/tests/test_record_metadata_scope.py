"""Synthetic HTTP regressions for zero-visible-field record metadata disclosure.

Only transaction bootstrap is patched: Django TestCase has already opened its
outer transaction. Session, grants, relation checks, serializers and HTTP error
handling remain real. The existing snapshot suite checks actual read-only
repeatable-read transactions separately.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings

from archives.models import ArchiveFact, ArchiveFactVersion
from files.models import FilePage
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import Record, RecordAttachment, RecordField, RecordVersion, Report
from tests import test_record_access_boundaries as boundary_tests


@override_settings(SJIA_ENVIRONMENT='test')
class RecordMetadataScopeTests(TestCase):
    # Reuse fixture methods through their module, without importing/subclassing
    # the TestCase itself and accidentally discovering its tests a second time.
    _grant = boundary_tests.RecordAccessBoundaryTest._grant
    _file = boundary_tests.RecordAccessBoundaryTest._file

    def setUp(self):
        boundary_tests.RecordAccessBoundaryTest.setUp(self)
        snapshot = patch('records.snapshot.begin_record_snapshot')
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def _record(self, codes=('hidden',)):
        record = Record.objects.create(patient=self.patient, kind='report', current_version=1,
            status='active', created_at=self.now)
        return record, self._version(record, codes)

    def _version(self, record, codes):
        number = RecordVersion.objects.filter(record_id=record.id, patient=self.patient).count() + 1
        marker = f'SYNTHETIC-METADATA-{record.id}-{number}'
        sample = MedicalTime.objects.create(patient=self.patient, precision='date',
            local_date=date(2026, 1, 1), resolution_status='local_only', raw_text=f'{marker}-sample')
        result = MedicalTime.objects.create(patient=self.patient, precision='date',
            local_date=date(2026, 1, 2), resolution_status='local_only', raw_text=f'{marker}-result')
        file = self._file()
        report = Report.objects.create(patient=self.patient, source_revision=self.use_auth.source_revision,
            external_ref=f'{marker}-external', source_organization=f'{marker}-report-organization',
            report_status='final', sample_time_id=sample.id, result_time_id=result.id,
            file_group_id=file.file_group_id, created_at=self.now)
        version = RecordVersion.objects.create(patient=self.patient, record_id=record.id, version=number,
            supersedes_version=number - 1 if number > 1 else None, author=self.principal,
            origin_type='report_transcription', capture_method='manual',
            source_revision=self.use_auth.source_revision, source_organization=f'{marker}-organization',
            report_id=report.id, use_authorization_id=self.use_auth.id,
            quality_status='valid', quality_reasons=[], recorded_at=self.now, retention_policy=self.retention,
            correction_reason=f'{marker}-correction' if number > 1 else None,
            corrected_at=self.now if number > 1 else None)
        RecordAttachment.objects.create(record_version_id=version.id, patient_id=self.patient.id,
            file_id=file.id, ordinal=1)
        page = FilePage.objects.create(patient=self.patient, file_group_id=file.file_group_id,
            file_id=file.id, ordinal=1, width=100, height=100, rotation=0)
        for ordinal, code in enumerate(codes, start=1):
            anchor = SourceAnchor.objects.create(patient=self.patient, page_id=page.id,
                anchor_kind='page', coordinate_system='decoded_pixel', original_text=f'{marker}-{code}-source')
            field = RecordField.objects.create(patient=self.patient, record_version_id=version.id,
                ordinal=ordinal, field_code=code, value_type='text', value_state='known',
                raw_text=f'{marker}-{code}-value', medical_time_id=sample.id,
                sample_time_origin='report', anchor_id=anchor.id, quality_reasons=[])
            # Explicit stored snapshots let historical versions have a different
            # visible field set. This fixture does not test the correction writer.
            fact = ArchiveFact.objects.create(patient_id=self.patient.id, current_version=1, created_at=self.now)
            ArchiveFactVersion.objects.create(patient_id=self.patient.id, fact_id=fact.id, version=1,
                record_version_id=version.id, field_id=field.id, source_anchor_id=anchor.id,
                context_type='unknown', assertion_state='unknown', medical_time_id=sample.id,
                raw_time_text=sample.raw_text, time_origin='unknown', available_at=self.now,
                availability_status='known', created_at=self.now)
        record.current_version = number
        record.save(update_fields=['current_version'])
        return version

    def _grant_reads(self, codes=('allowed',)):
        return [self._grant(action=action, codes=codes) for action in ('read_raw', 'read_history')]

    def _paths(self, record, version):
        return [f'/api/records/{record.id}', f'/api/record-versions/{version.id}',
            f'/api/records/{record.id}/history']

    def _assert_denied(self, response):
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json(), {'code': 'CASE_ACTION_DENIED', 'message': 'CASE_ACTION_DENIED',
            'retryable': False, 'recovery': 'none', 'field_errors': []})
        self.assertEqual(response['Cache-Control'], 'no-store, private')

    def _assert_metadata(self, data, version):
        report = Report.objects.get(id=version.report_id)
        self.assertEqual(data['source_organization'], version.source_organization)
        self.assertEqual(data['correction_reason'], version.correction_reason)
        self.assertEqual(data['corrected_at'], version.corrected_at.isoformat() if version.corrected_at else None)
        self.assertEqual(data['report']['external_ref'], report.external_ref)
        self.assertEqual(data['report']['sample_time']['raw_text'],
            MedicalTime.objects.get(id=report.sample_time_id).raw_text)
        self.assertEqual(data['report']['result_time']['raw_text'],
            MedicalTime.objects.get(id=report.result_time_id).raw_text)
        attachment = RecordAttachment.objects.get(record_version_id=version.id)
        self.assertEqual(data['attachments'], [{'file_id': str(attachment.file_id), 'ordinal': 1}])

    def test_current_read_denies_zero_visible_fields_without_metadata_payload(self):
        record, _ = self._record()
        self._version(record, ('hidden',))
        self._grant(action='read_raw')
        self._assert_denied(self.client.get(f'/api/records/{record.id}'))

    def test_historical_read_denies_zero_visible_fields_without_metadata_payload(self):
        record, _ = self._record()
        hidden = self._version(record, ('hidden',))
        self._version(record, ('allowed',))
        self._grant_reads()
        self._assert_denied(self.client.get(f'/api/record-versions/{hidden.id}'))
        self.assertEqual(self.client.get(f'/api/records/{record.id}').status_code, 200)

    def test_history_omits_hidden_version_ids_and_keeps_actual_current_number(self):
        record, first = self._record()
        visible = self._version(record, ('allowed', 'hidden'))
        current = self._version(record, ('hidden',))
        self._grant_reads()
        response = self.client.get(f'/api/records/{record.id}/history')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['current_version'], '3')
        self.assertEqual([row['record_version_id'] for row in response.json()['versions']], [str(visible.id)])
        self.assertEqual(response.json()['versions'][0]['version'], '2')
        self.assertNotIn(str(first.id), response.content.decode())
        self.assertNotIn(str(current.id), response.content.decode())
        # A directory header is not permission to read its hidden current version.
        self._assert_denied(self.client.get(f'/api/records/{record.id}'))
        self._assert_denied(self.client.get(f'/api/record-versions/{current.id}'))

    def test_history_denies_empty_visible_directory_without_any_version_metadata(self):
        record, _ = self._record()
        self._version(record, ('hidden',))
        self._grant(action='read_history')
        self._assert_denied(self.client.get(f'/api/records/{record.id}/history'))

    def test_partial_current_and_historical_reads_keep_authorized_record_metadata(self):
        record, _ = self._record(('allowed', 'hidden'))
        version = self._version(record, ('allowed', 'hidden'))
        self._grant_reads()
        for path in self._paths(record, version)[:2]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.content)
                data = response.json()
                self.assertEqual([field['field_code'] for field in data['fields']], ['allowed'])
                self.assertEqual([ref['field_id'] for ref in data['fact_refs']], [data['fields'][0]['field_id']])
                self.assertNotIn('-hidden-value', response.content.decode())
                self.assertNotIn('-hidden-source', response.content.decode())
                self._assert_metadata(data, version)

    def test_empty_field_grant_is_not_wildcard_for_current_history_or_version(self):
        record, version = self._record(('allowed', 'hidden'))
        self._grant_reads(codes=())
        for path in self._paths(record, version):
            with self.subTest(path=path):
                self._assert_denied(self.client.get(path))

    def test_whole_resource_plus_field_grants_preserve_partial_metadata_contract(self):
        record, version = self._record(('allowed', 'hidden'))
        self._grant_reads(codes=())
        self._grant_reads()
        for path in self._paths(record, version)[:2]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual([field['field_code'] for field in response.json()['fields']], ['allowed'])
                self._assert_metadata(response.json(), version)
        history = self.client.get(self._paths(record, version)[2])
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual([row['record_version_id'] for row in history.json()['versions']], [str(version.id)])

    def test_grant_narrowing_applies_to_next_current_history_and_version_read(self):
        record, version = self._record(('allowed', 'hidden'))
        grants = self._grant_reads()
        for path in self._paths(record, version):
            self.assertEqual(self.client.get(path).status_code, 200)
        for grant in grants:
            grant.field_codes = ['unrelated']
            grant.save(update_fields=['field_codes'])
        for path in self._paths(record, version):
            with self.subTest(path=path):
                self._assert_denied(self.client.get(path))

    def test_revoke_with_other_active_field_grants_cannot_leave_metadata_access(self):
        record, version = self._record(('allowed', 'hidden'))
        grants = self._grant_reads()
        self._grant_reads(codes=('unrelated',))
        for path in self._paths(record, version):
            self.assertEqual(self.client.get(path).status_code, 200)
        for grant in grants:
            grant.revoked_at = self.now
            grant.save(update_fields=['revoked_at'])
        for path in self._paths(record, version):
            with self.subTest(path=path):
                self._assert_denied(self.client.get(path))

    def test_unrelated_history_field_grant_does_not_advertise_history_action(self):
        record, _ = self._record(('allowed',))
        self._grant(action='read_raw')
        self._grant(action='read_history', codes=('unrelated',))
        response = self.client.get(f'/api/records/{record.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('read_history', response.json()['allowed_actions'])
        self._assert_denied(self.client.get(f'/api/records/{record.id}/history'))

    def test_history_action_allows_visible_older_version_without_current_overlap(self):
        record, old = self._record(('history_allowed',))
        self._version(record, ('raw_allowed',))
        self._grant(action='read_raw', codes=('raw_allowed',))
        self._grant(action='read_history', codes=('history_allowed',))
        response = self.client.get(f'/api/records/{record.id}')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('read_history', response.json()['allowed_actions'])
        history = self.client.get(f'/api/records/{record.id}/history')
        self.assertEqual(history.status_code, 200, history.content)
        self.assertEqual([row['record_version_id'] for row in history.json()['versions']], [str(old.id)])

    def test_history_action_requires_family_purpose_and_live_relation(self):
        record, _ = self._record(('allowed',))
        self._grant(action='read_raw')
        grant = self._grant(action='read_history')
        for purpose, relation_id in [('clinical', self.relation.id), ('family_observation', None)]:
            with self.subTest(purpose=purpose, relation_id=relation_id):
                grant.purpose, grant.relation_id = purpose, relation_id
                grant.save(update_fields=['purpose', 'relation_id'])
                response = self.client.get(f'/api/records/{record.id}')
                self.assertEqual(response.status_code, 200, response.content)
                self.assertNotIn('read_history', response.json()['allowed_actions'])
                self._assert_denied(self.client.get(f'/api/records/{record.id}/history'))

    def test_archive_fact_direct_read_already_requires_its_specific_field(self):
        record, version = self._record(('hidden',))
        self._grant_reads()
        fact = ArchiveFactVersion.objects.get(record_version_id=version.id)
        self._assert_denied(self.client.get(f'/api/v2/archive-facts/{fact.fact_id}'))
        self._grant(action='read_raw', codes=('hidden',))
        response = self.client.get(f'/api/v2/archive-facts/{fact.fact_id}')
        self.assertEqual(response.status_code, 200, response.content)

