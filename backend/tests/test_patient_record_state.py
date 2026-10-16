"""Synthetic HTTP regressions for the family non-active patient boundary.

The real record/grant/relation/serialization paths run against PostgreSQL.
Only the read-only snapshot bootstrap is patched, because Django TestCase has
already opened its outer transaction. test_snapshot_closeout separately covers
actual repeatable-read HTTP transactions and physical concurrent revocation.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from records.models import RecordField
from tests import test_record_metadata_scope as metadata
from tests import test_record_access_boundaries as boundaries


@override_settings(SJIA_ENVIRONMENT='test')
class PatientStatusHTTPTests(TestCase):
    _grant = boundaries.RecordAccessBoundaryTest._grant
    _file = boundaries.RecordAccessBoundaryTest._file
    _record = metadata.RecordMetadataScopeTests._record
    _version = metadata.RecordMetadataScopeTests._version
    _grant_reads = metadata.RecordMetadataScopeTests._grant_reads
    _paths = metadata.RecordMetadataScopeTests._paths

    def setUp(self):
        boundaries.RecordAccessBoundaryTest.setUp(self)
        context = patch('records.snapshot.begin_record_snapshot')
        context.start()
        self.addCleanup(context.stop)
        self.record, self.version = self._record(('allowed',))
        self.grants = self._grant_reads()

    def _responses(self):
        return [(path, self.client.get(path))
            for path in self._paths(self.record, self.version)]

    def _set_status(self, value):
        self.patient.status = value
        self.patient.save(update_fields=['status'])

    def _assert_denied(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertEqual(set(response.json()),
            {'code', 'message', 'retryable', 'recovery', 'field_errors'})
        self.assertIn(response.json()['code'], {'PATIENT_UNAVAILABLE', 'CASE_ACTION_DENIED'})
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertNotIn(str(self.record.id), response.content.decode())
        self.assertNotIn(str(self.version.id), response.content.decode())
        self.assertNotIn('SYNTHETIC-METADATA-', response.content.decode())

    def _all_read_responses(self):
        field = RecordField.objects.get(record_version_id=self.version.id, field_code='allowed')
        field.concept_code = 'synthetic-allowed'
        field.save(update_fields=['concept_code'])
        pairs = self._responses()
        source = f'/api/record-versions/{self.version.id}/fields/{field.id}/source'
        pairs.append(('source', self.client.get(source)))
        for suffix in ('records/search', 'history/search', 'analytes/synthetic-allowed/search'):
            pairs.append((suffix, self.client.post(
                f'/api/patients/{self.patient.id}/{suffix}', {}, content_type='application/json')))
        return pairs

    def test_active_patient_current_version_and_history_remain_readable(self):
        for path, response in self._responses():
            with self.subTest(path=path):
                self.assertEqual(response.status_code, 200)
                self.assertIn(str(self.record.id), response.content.decode())

    def test_restricted_patient_with_unchanged_relation_and_grants_is_denied(self):
        self._set_status('restricted')
        self.relation.refresh_from_db()
        self.assertEqual(self.relation.status, 'active')
        for grant in self.grants:
            grant.refresh_from_db()
            self.assertIsNone(grant.revoked_at)
        relations = self.client.get('/api/relations')
        self.assertEqual(relations.status_code, 200)
        self.assertEqual([row['actions'] for row in relations.json()['relations']], [[]])
        for path, response in self._responses():
            with self.subTest(path=path):
                self._assert_denied(response)

    def test_revoked_relation_with_active_patient_remains_denied(self):
        self.relation.status = 'revoked'
        self.relation.revoked_at = timezone.now()
        self.relation.save(update_fields=['status', 'revoked_at'])
        for path, response in self._responses():
            with self.subTest(path=path):
                self._assert_denied(response)

    def test_restricted_patient_rejects_every_family_record_query_entry(self):
        self._set_status('restricted')
        for path, response in self._all_read_responses():
            with self.subTest(path=path):
                self._assert_denied(response)

    def test_disposed_patient_rejects_reads_even_without_content_tombstone(self):
        self._set_status('disposed')
        for path, response in self._all_read_responses():
            with self.subTest(path=path):
                self._assert_denied(response)

    def test_revoked_grants_still_reject_every_family_record_query(self):
        for grant in self.grants:
            grant.revoked_at = timezone.now()
            grant.save(update_fields=['revoked_at'])
        for path, response in self._all_read_responses():
            with self.subTest(path=path):
                self._assert_denied(response)

    def test_active_patient_all_record_queries_keep_working(self):
        for path, response in self._all_read_responses():
            with self.subTest(path=path):
                self.assertEqual(response.status_code, 200)

    def test_restricted_patient_cannot_read_a_pre_correction_version(self):
        old_version = self.version
        self.version = self._version(self.record, ('allowed',))
        old_path = f'/api/record-versions/{old_version.id}'
        self.assertEqual(self.client.get(old_path).status_code, 200)
        self._set_status('restricted')
        self._assert_denied(self.client.get(old_path))
        for path, response in self._responses():
            with self.subTest(path=path):
                self._assert_denied(response)
