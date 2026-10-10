from datetime import datetime, timezone

from django.test import SimpleTestCase

from api.base import ApiError
from assessment.medical_intervals import medical_interval
from medical_time.models import MedicalTime
from records.medical_times import time_values
from records.period_schemas import checked_period


def local_time(value, precision='second', zone='Asia/Shanghai', offset=None):
    return {'precision': precision, 'raw_text': value, 'local_date': value[:10], 'local_datetime': value,
        'timezone_name': zone, 'utc_offset_minutes': offset, 'instant': None, 'resolution_status': 'local_only',
        'local_year': None, 'local_month': None, 'end_year': None, 'end_month': None, 'relative_text': None}


class MedicalTimeResolutionTests(SimpleTestCase):
    def test_explicit_zone_retains_original_and_actual_utc(self):
        original = local_time('2026-10-06T03:00:00')
        values = time_values(original)
        self.assertEqual(values['instant'], datetime(2026, 10, 5, 19, tzinfo=timezone.utc))
        self.assertEqual(values['utc_offset_minutes'], 480)
        self.assertEqual(original['instant'], None)
        actual = medical_interval(MedicalTime(**values))
        self.assertEqual(actual.lower, actual.upper)
        self.assertTrue(actual.upper_inclusive)

    def test_minute_precision_retains_full_minute_range(self):
        values = time_values(local_time('2026-10-06T09:00:00', precision='minute'))
        actual = medical_interval(MedicalTime(**values))
        self.assertEqual((actual.upper - actual.lower).total_seconds(), 60)
        self.assertFalse(actual.upper_inclusive)
        with self.assertRaises(ApiError):
            time_values(local_time('2026-10-06T09:00:30', precision='minute'))

    def test_explicit_offset_resolves_without_invented_zone(self):
        values = time_values(local_time('2026-10-06T03:00:00', zone=None, offset=480))
        self.assertIsNone(values['timezone_name'])
        self.assertEqual(values['utc_offset_minutes'], 480)
        actual = medical_interval(MedicalTime(**values))
        self.assertEqual(actual.lower, datetime(2026, 10, 5, 19, tzinfo=timezone.utc))

    def test_dst_ambiguity_and_nonexistent_time_remain_unknown(self):
        for value, status in (('2026-11-01T01:30:00', 'ambiguous'), ('2026-03-08T02:30:00', 'invalid')):
            values = time_values(local_time(value, zone='America/New_York'))
            self.assertEqual(values['resolution_status'], status)
            self.assertIsNone(values['instant'])

    def test_inconsistent_date_or_offset_is_rejected(self):
        inconsistent = local_time('2026-10-06T03:00:00'); inconsistent['local_date'] = '2026-10-05'
        with self.assertRaises(ApiError):
            time_values(inconsistent)
        with self.assertRaises(ApiError):
            time_values(local_time('2026-10-06T03:00:00', offset=60))
        with self.assertRaises(ApiError):
            time_values(local_time('2026-10-06T03:00:00+08:00'))

    def test_date_and_unknown_precision_do_not_generate_instants(self):
        value = local_time('2026-10-06T00:00:00', precision='date'); value['local_datetime'] = None
        values = time_values(value)
        actual = medical_interval(MedicalTime(**values))
        self.assertEqual((actual.upper - actual.lower).total_seconds(), 86400)
        self.assertIsNone(values['instant'])
        unknown = {**value, 'precision': 'unknown', 'raw_text': '未知医学时间', 'local_date': None,
            'timezone_name': None, 'resolution_status': 'unknown'}
        checked_period('Input', {'expected_version': None, 'start': unknown, 'end': value,
            'label': None, 'episode_id': None, 'reason': '本人保留未知边界'})
        self.assertIsNone(time_values(unknown)['instant'])
