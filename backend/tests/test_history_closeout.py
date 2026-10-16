"""Synthetic T16/FR-03 history contract regressions; no external services."""
from datetime import date

from django.core import signing
from django.test import TestCase, override_settings
from jsonschema import Draft202012Validator, FormatChecker

from medical_time.models import MedicalTime
from records.history import checked_query
from records.models import RecordField
from records.queries import QueryRejected, history_search
from tests.test_record_access_boundaries import RecordAccessBoundaryTest


@override_settings(SJIA_ENVIRONMENT='test')
class HistoryCloseoutTest(TestCase):
    setUp = RecordAccessBoundaryTest.setUp
    _grant = RecordAccessBoundaryTest._grant
    _record = RecordAccessBoundaryTest._record
    _permissions = RecordAccessBoundaryTest._permissions

    def _add(self, precision='date', *, codes=('allowed',), **values):
        time = MedicalTime.objects.create(patient=self.patient, precision=precision,
            resolution_status=values.pop('resolution_status', 'local_only'), **values)
        self._record(codes=codes, times=[time] * len(codes))
        return time

    def _search(self, **query):
        return history_search(patient=self.patient, principal=self.principal,
            permission_scopes=self._permissions(), **query)

    def test_date_filter_happens_before_page_limit_and_sources_are_scoped(self):
        self._grant()
        self._add(local_date=date(2026, 1, 1))
        selected = self._add(local_date=date(2026, 2, 1), codes=('hidden', 'allowed'))
        result = self._search(from_date='2026-02-01', to_date='2026-02-01',
            scope_timezone='Asia/Shanghai', limit=1)
        self.assertEqual([item['medical_time_id'] for item in result['items']], [str(selected.id)])
        self.assertEqual(result['entries'][0]['field_count'], 1)
        sources = result['items'][0]['sources']
        self.assertEqual(len(sources), 1)
        self.assertEqual(RecordField.objects.get(id=sources[0]['field_id']).field_code, 'allowed')

    def test_year_month_range_intersections_preserve_original_precision(self):
        self._grant()
        year = self._add('year', local_year=2026)
        month = self._add('month', local_year=2026, local_month=2)
        span = self._add('month_range', local_year=2026, local_month=1, end_year=2026, end_month=3)
        self._add('unknown', resolution_status='unknown', raw_text='unspecified')
        self._add('relative', resolution_status='unknown', relative_text='three days earlier')
        result = self._search(from_date='2026-02-10', to_date='2026-02-10', scope_timezone='UTC')
        self.assertEqual({item['medical_time_id'] for item in result['items']}, {str(year.id), str(month.id), str(span.id)})
        for item in result['items']:
            self.assertIsNone(item['medical_time']['instant'])
            self.assertIsNone(item['medical_time']['local_date'])

    def test_timezone_window_and_half_open_boundary(self):
        from records.medical_times import create_medical_time

        self._grant()
        inside = create_medical_time(self.patient, {'precision': 'minute',
            'local_datetime': '2026-02-02T00:30:00', 'timezone_name': 'Asia/Tokyo', 'resolution_status': 'local_only'})
        outside = create_medical_time(self.patient, {'precision': 'second',
            'local_datetime': '2026-02-02T00:00:00', 'timezone_name': 'UTC', 'resolution_status': 'local_only'})
        self._record(times=[inside])
        self._record(times=[outside])
        result = self._search(from_date='2026-02-01', to_date='2026-02-01', scope_timezone='UTC')
        self.assertEqual([item['medical_time_id'] for item in result['items']], [str(inside.id)])

    def test_unknown_relative_and_kind_can_be_queried_without_date_fabrication(self):
        from records.models import Record

        self._grant()
        known = self._add(local_date=date(2026, 1, 1))
        unknown = self._add('unknown', resolution_status='unknown', raw_text='not provided')
        relative = self._add('relative', resolution_status='unknown', relative_text='last winter')
        self.assertEqual(self._search(precision='unknown')['items'][0]['medical_time_id'], str(unknown.id))
        self.assertEqual(self._search(precision='relative')['items'][0]['medical_time_id'], str(relative.id))
        Record.objects.filter(patient=self.patient).update(kind='report')
        self.assertEqual(self._search(kind='observation')['items'], [])
        self.assertEqual(len(self._search(kind='report')['items']), 3)
        self.assertEqual(self._search(precision='date')['items'][0]['medical_time_id'], str(known.id))

    def test_cursor_pages_and_binds_query_actor_permissions_and_epochs(self):
        self._grant()
        for day in range(1, 5):
            self._add(local_date=date(2026, 1, day))
        first = self._search(limit=2)
        second = self._search(limit=2, cursor=first['next_cursor'])
        self.assertEqual(len(first['items']) + len(second['items']), 4)
        self.assertIsNone(second['next_cursor'])
        self.assertFalse({item['medical_time_id'] for item in first['items']} &
            {item['medical_time_id'] for item in second['items']})
        for changed in ({'precision': 'date'}, {'limit': 3}):
            with self.subTest(changed=changed), self.assertRaisesRegex(QueryRejected, 'CURSOR_STALE'):
                self._search(**{'limit': 2, 'cursor': first['next_cursor'], **changed})
        self.principal.auth_epoch += 1
        with self.assertRaisesRegex(QueryRejected, 'CURSOR_STALE'):
            self._search(limit=2, cursor=first['next_cursor'])
        self.principal.auth_epoch -= 1
        self.patient.data_epoch += 1
        with self.assertRaisesRegex(QueryRejected, 'CURSOR_STALE'):
            self._search(limit=2, cursor=first['next_cursor'])

    def test_malformed_cursor_and_invalid_date_scope_rejected(self):
        self._grant()
        for query in ({'from_date': '2026-01-01'}, {'scope_timezone': 'UTC'},
            {'from_date': '2026-02-30', 'to_date': '2026-03-01', 'scope_timezone': 'UTC'},
            {'from_date': '2026-02-02', 'to_date': '2026-02-01', 'scope_timezone': 'UTC'},
            {'from_date': '2026-02-01', 'to_date': '2026-02-01', 'scope_timezone': 'not-a-zone'},
            {'limit': True}, {'limit': 101}, {'unknown_field': 'value'}):
            with self.subTest(query=query), self.assertRaises(QueryRejected):
                checked_query(query)
        with self.assertRaises(signing.BadSignature):
            self._search(cursor='modified')
        with self.assertRaisesRegex(QueryRejected, 'CURSOR_INVALID'):
            self._search(cursor=signing.dumps([], salt='medical-time-directory-v2'))

    def test_http_body_is_applied_and_result_matches_history_schema(self):
        from archives.inputs import REGISTRY, ROOT, SCHEMAS

        self._grant()
        self._add(local_date=date(2026, 1, 1))
        selected = self._add(local_date=date(2026, 2, 1))
        path = f'/api/patients/{self.patient.id}/history/search'
        response = self.client.post(path, {'precision': 'date', 'from_date': '2026-02-01',
            'to_date': '2026-02-01', 'scope_timezone': 'UTC'}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['items'][0]['medical_time_id'], str(selected.id))
        schema = SCHEMAS[(ROOT / 'backend-design/protocol.schema.json').as_uri()]
        Draft202012Validator({**schema, '$ref': '#/$defs/HistoryList'}, registry=REGISTRY,
            format_checker=FormatChecker()).validate(response.json())
        invalid = self.client.post(path, {'from_date': '2026-01-01'}, content_type='application/json')
        self.assertEqual(invalid.status_code, 422)
