import os
import unittest
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from notifications import reminders
from notifications.errors import NotificationRejected


class ReminderScheduleTest(unittest.TestCase):
    def test_cross_midnight_quiet_hours(self):
        zone = ZoneInfo('Asia/Shanghai')
        scheduled = datetime(2026, 10, 8, 23, 30, tzinfo=zone)
        result = reminders.next_permitted_time(scheduled, 'Asia/Shanghai', time(22), time(7))
        self.assertEqual(result, datetime(2026, 10, 9, 7, tzinfo=zone))

    def test_quiet_start_inclusive_and_end_exclusive(self):
        zone = ZoneInfo('Asia/Shanghai')
        for hour, expected_day, expected_hour in [(22, 9, 7), (7, 8, 7), (12, 8, 12)]:
            with self.subTest(hour=hour):
                result = reminders.next_permitted_time(datetime(2026, 10, 8, hour, tzinfo=zone),
                    'Asia/Shanghai', time(22), time(7))
                self.assertEqual(result, datetime(2026, 10, expected_day, expected_hour, tzinfo=zone))

    def test_spring_transition_resolves_nonexistent_end(self):
        zone = ZoneInfo('America/New_York')
        result = reminders.next_permitted_time(datetime(2026, 3, 8, 1, 30, tzinfo=zone),
            'America/New_York', time(1), time(2, 30))
        self.assertEqual(result, datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc))

    def test_autumn_transition_waits_for_second_end(self):
        zone = ZoneInfo('America/New_York')
        result = reminders.next_permitted_time(datetime(2026, 11, 1, 0, 30, tzinfo=zone),
            'America/New_York', time(0), time(1, 30))
        self.assertEqual(result, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))

    def test_overdue_schedule_rechecks_current_quiet_hours(self):
        zone = ZoneInfo('Asia/Shanghai')
        scheduled = datetime(2026, 10, 8, 12, tzinfo=zone)
        current = datetime(2026, 10, 8, 23, tzinfo=zone)
        result = reminders.next_permitted_time(scheduled, 'Asia/Shanghai', time(22), time(7), not_before=current)
        self.assertEqual(result, datetime(2026, 10, 9, 7, tzinfo=zone))

    def test_no_quiet_hours_preserves_instant(self):
        current = datetime.now(timezone.utc)
        result = reminders.next_permitted_time(current, 'Asia/Shanghai', None, None)
        self.assertEqual(result, current)

    def test_invalid_schedule_fails_before_calculation(self):
        with self.assertRaises(NotificationRejected) as rejected:
            reminders.next_permitted_time(datetime.now(), 'Asia/Shanghai', None, None)
        self.assertEqual(rejected.exception.reason_code, 'REMINDER_SCHEDULE_INVALID')

    def test_lifecycle_states_stop_invalid_plans(self):
        cases = [('enabled', 'completed', None, 'completed'),
            ('disabled', 'completed', None, 'completed'),
            ('paused', 'cancelled', None, 'cancelled'),
            ('enabled', 'pending', 'REMINDER_SCOPE_DENIED', 'cancelled'),
            ('enabled', 'changed', None, 'paused'),
            ('enabled', 'unavailable', None, 'paused'),
            ('disabled', 'pending', None, 'disabled'),
            ('paused', 'pending', None, 'paused'),
            ('completed', 'pending', None, 'completed'),
            ('cancelled', 'pending', None, 'cancelled'),
            ('enabled', 'pending', 'REMINDER_ENVIRONMENT_UNAVAILABLE', 'enabled')]
        for status, target, reason, expected in cases:
            with self.subTest(status=status, target=target, reason=reason):
                self.assertEqual(reminders.reminder_transition(status, target, reason), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
