"""Synthetic HTTP regressions. Only snapshot bootstrap is patched for TestCase's
outer transaction; session, grants, scopes, projections and serializers are real.
No external services or clinical qualification are exercised.
"""
from datetime import datetime, timezone
from unittest.mock import patch
from django.core import signing
from django.test import TestCase, override_settings
from medical_time.models import MedicalTime
from records.models import RecordField, RecordVersion
from tests import test_record_access_boundaries as boundary_tests


@override_settings(SJIA_ENVIRONMENT='test')
class HomeRecordDestinationsTests(TestCase):
    _grant = boundary_tests.RecordAccessBoundaryTest._grant
    _record = boundary_tests.RecordAccessBoundaryTest._record
    _time = boundary_tests.RecordAccessBoundaryTest._time

    def setUp(self):
        boundary_tests.RecordAccessBoundaryTest.setUp(self)
        snapshot = patch('records.snapshot.begin_record_snapshot')
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def search(self, **payload):
        return self.client.post(f'/api/patients/{self.patient.id}/records/search',
            {'kind': 'observation', **payload}, content_type='application/json')

    def mixed(self):
        old = self._record(('temp', 'rash'), times=[self._time(1), self._time(9)])
        new = self._record(('temp',), times=[self._time(5)])
        self._grant(codes=('temp', 'rash'))
        return old, new

    def test_name_match_orders_by_actual_matched_field_and_preserves_full_projection(self):
        old, new = self.mixed()
        response = self.search(original_name='temp', limit=1)
        self.assertEqual(response.status_code, 200, response.content)
        page = response.json()
        self.assertEqual(page['records'][0]['record_id'], str(new.id))
        older = self.search(original_name='temp', limit=1, cursor=page['next_cursor']).json()
        self.assertEqual(older['records'][0]['record_id'], str(old.id))
        self.assertEqual({field['field_code'] for field in older['records'][0]['detail']['fields']}, {'temp', 'rash'})
        self.assertIsNone(older['next_cursor'])

    def test_prechange_signed_cursor_is_stale_after_ordering_revision(self):
        self.mixed()
        response = self.search(original_name='temp', limit=1)
        self.assertEqual(response.status_code, 200, response.content)
        cursor = response.json()['next_cursor']
        payload = signing.loads(cursor, salt='record-medical-order')
        self.assertEqual(payload['scope'].pop('ordering_revision'), 'matched-medical-time-v2')
        old_cursor = signing.dumps(payload, salt='record-medical-order')
        stale = self.search(original_name='temp', limit=1, cursor=old_cursor)
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.json()['code'], 'CURSOR_STALE')
        self.assertNotIn('records', stale.json())
        current = self.search(original_name='temp', limit=1, cursor=cursor)
        self.assertEqual(current.status_code, 200, current.content)
        self.assertEqual(len(current.json()['records']), 1)

    def test_concept_match_orders_by_matching_time_not_unrelated_record_maximum(self):
        old, new = self.mixed()
        response = self.search(concept_code='temp-concept')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([record['record_id'] for record in response.json()['records']], [str(new.id), str(old.id)])

    def test_matching_unknown_time_is_not_replaced_with_other_field_or_save_time(self):
        unknown = self._record(('temp', 'rash'), times=[None, self._time(9)])
        known = self._record(('temp',), times=[self._time(5)])
        self._grant(codes=('temp', 'rash'))
        records = self.search(original_name='temp').json()['records']
        self.assertEqual([row['record_id'] for row in records], [str(known.id), str(unknown.id)])
        field = next(field for field in records[1]['detail']['fields'] if field['field_code'] == 'temp')
        self.assertIsNone(field['medical_time'])

    def test_matching_seconds_and_microseconds_are_not_tied_at_the_minute(self):
        def instant(value):
            at = datetime.fromisoformat(value)
            return MedicalTime.objects.create(patient=self.patient, precision='second',
                instant=at, local_datetime=at.replace(tzinfo=None), timezone_name='UTC',
                utc_offset_minutes=0, resolution_status='resolved')
        newer = self._record(('temp',), times=[instant('2026-01-05T12:34:56.000002+00:00')])
        older = self._record(('temp',), times=[instant('2026-01-05T12:34:56.000001+00:00')])
        # An unrelated save timestamp must not break the microsecond ordering.
        older.created_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        older.save(update_fields=['created_at'])
        self._grant(codes=('temp',))
        response = self.search(original_name='temp')
        self.assertEqual(response.status_code, 200, response.content)
        records = response.json()['records']
        self.assertEqual([row['record_id'] for row in records], [str(newer.id), str(older.id)])
        self.assertEqual(records[0]['detail']['fields'][0]['medical_time']['instant'],
            '2026-01-05T12:34:56.000002+00:00')

    def test_field_scope_cannot_match_hidden_name_or_return_hidden_value(self):
        self._record(('temp', 'rash'), times=[self._time(1), self._time(9)])
        self._grant(codes=('rash',))
        self.assertEqual(self.search(original_name='temp').json()['records'], [])
        response = self.search(original_name='rash')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([field['field_code'] for field in response.json()['records'][0]['detail']['fields']], ['rash'])

    def test_no_match_and_revoked_access_are_distinct(self):
        grant = self._grant(codes=('temp',))
        self.assertEqual(self.search(original_name='temp').json(), {'records': [], 'next_cursor': None})
        grant.revoked_at = self.now
        grant.save(update_fields=['revoked_at'])
        response = self.search(original_name='temp')
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json()['code'], 'CASE_ACTION_DENIED')
        self.assertNotIn('records', response.json())

    def test_same_time_records_keep_distinct_version_and_field_identities(self):
        time = self._time(5)
        first = self._record(('temp',), times=[time])
        second = self._record(('temp',), times=[time])
        self._grant(codes=('temp',))
        records = self.search(original_name='temp').json()['records']
        self.assertEqual({row['record_id'] for row in records}, {str(first.id), str(second.id)})
        self.assertEqual(len({row['detail']['record_version_id'] for row in records}), 2)
        self.assertEqual(len({row['detail']['fields'][0]['field_id'] for row in records}), 2)

    def test_read_only_destination_retains_exact_raw_value_unit_time_and_refs(self):
        record = self._record(('temp',), times=[self._time(5, '合成原文日期')])
        version = RecordVersion.objects.get(record_id=record.id)
        RecordField.objects.filter(record_version_id=version.id).update(value_type='decimal', raw_text=None,
            raw_numeric='37.123456789', raw_unit='SYNTHETIC-UNIT', qualifier='ge')
        self._grant(codes=('temp',))
        result = self.search(original_name='temp').json()['records'][0]['detail']
        detail = self.client.get(f'/api/records/{record.id}')
        self.assertEqual(detail.status_code, 200, detail.content)
        payload = detail.json()
        self.assertEqual(payload['record_version_id'], result['record_version_id'])
        self.assertEqual(payload['fields'], result['fields'])
        field = payload['fields'][0]
        self.assertEqual(field['raw_numeric'], '37.123456789')
        self.assertEqual(field['raw_unit'], 'SYNTHETIC-UNIT')
        self.assertEqual(field['qualifier'], 'ge')
        self.assertEqual(field['medical_time']['raw_text'], '合成原文日期')
        self.assertEqual(payload['allowed_actions'], [])
        self.assertEqual(self.client.get(f'/api/record-versions/{version.id}').status_code, 403)

    def test_destination_rechecks_revoked_read_permission(self):
        record = self._record(('temp',))
        grant = self._grant(codes=('temp',))
        self.assertEqual(self.search(original_name='temp').status_code, 200)
        grant.revoked_at = self.now
        grant.save(update_fields=['revoked_at'])
        response = self.client.get(f'/api/records/{record.id}')
        self.assertEqual(response.status_code, 403, response.content)
        self.assertNotIn('record_version_id', response.json())
