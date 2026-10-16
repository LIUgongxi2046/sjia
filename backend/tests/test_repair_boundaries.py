"""Offline software regressions; all fixtures are synthetic, no clinical validation."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from django.test import SimpleTestCase, TestCase

from assessment.classification import evaluate_threshold
from archives.models import MaterialReminder, MaterialReminderNotice
from identity.models import Patient, Principal
from notifications.material_scheduler import _due_candidates, due_candidates


class ThresholdBoundaryRegressionTest(SimpleTestCase):
    def test_full_three_valued_comparison_matrix(self):
        # Each row is below/equal/above threshold, derived from interval logic.
        expected = {
            'gt': {'eq': ['not_met', 'not_met', 'met'],
                   'lt': ['not_met', 'not_met', 'unknown'],
                   'le': ['not_met', 'not_met', 'unknown'],
                   'gt': ['unknown', 'met', 'met'],
                   'ge': ['unknown', 'unknown', 'met']},
            'le': {'eq': ['met', 'met', 'not_met'],
                   'lt': ['met', 'met', 'unknown'],
                   'le': ['met', 'met', 'unknown'],
                   'gt': ['unknown', 'not_met', 'not_met'],
                   'ge': ['unknown', 'unknown', 'not_met']},
        }
        for operator, qualifiers in expected.items():
            for qualifier, outcomes in qualifiers.items():
                for value, outcome in zip(['9', '10', '11'], outcomes, strict=True):
                    with self.subTest(operator=operator, qualifier=qualifier, value=value):
                        self.assertEqual(evaluate_threshold(Decimal(value), qualifier, operator, Decimal('10')), outcome)
                self.assertEqual(evaluate_threshold(None, qualifier, operator, Decimal('10')), 'unknown')
        for operator in expected:
            for value in ['9', '10', '11']:
                self.assertEqual(evaluate_threshold(Decimal(value), None, operator, Decimal('10')),
                    evaluate_threshold(Decimal(value), 'eq', operator, Decimal('10')))


class ReminderStarvationRegressionTest(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 23, tzinfo=timezone.utc)
        self.actor = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.patient = Patient.objects.create(display_id='SIM-REPAIR-SCHEDULER', status='active', created_at=self.now)

    def reminder(self, offset, quiet=True):
        return MaterialReminder.objects.create(patient_id=self.patient.id, owner_id=self.actor.id,
            version=1, target_type='draft', target_ref=uuid4(), target_version=1,
            schedule_at=self.now - timedelta(days=1) + timedelta(seconds=offset), timezone_name='UTC',
            quiet_start=time(22) if quiet else None, quiet_end=time(7) if quiet else None,
            status='enabled', created_at=self.now, updated_at=self.now)

    def test_quiet_prefix_cannot_starve_later_ready_reminders(self):
        for offset in range(5):
            self.reminder(offset)
        ready = [self.reminder(10 + offset, quiet=False) for offset in range(3)]
        rows, scanned = _due_candidates(limit=2, now=self.now)
        self.assertEqual([row.id for row in rows], [row.id for row in ready[:2]])
        self.assertEqual(scanned, 7)

    def test_following_tick_excludes_already_notified_version(self):
        first, second = self.reminder(0, quiet=False), self.reminder(1, quiet=False)
        MaterialReminderNotice.objects.create(patient_id=self.patient.id, reminder_id=first.id,
            reminder_version=1, scheduled_at=first.schedule_at, channel='in_app',
            deduplication_key=str(uuid4()), status='pending', permit_refs={}, receipt_refs=[], created_at=self.now)
        self.assertEqual([row.id for row in due_candidates(limit=1, now=self.now)], [second.id])

    def test_cross_midnight_quiet_end_is_inclusive_for_delivery(self):
        reminder = self.reminder(0)
        self.assertEqual(due_candidates(limit=1, now=self.now), [])
        ending = self.now.replace(hour=7) + timedelta(days=1)
        self.assertEqual([row.id for row in due_candidates(limit=1, now=ending)], [reminder.id])

    def test_current_quiet_hours_apply_even_when_original_schedule_was_allowed(self):
        reminder = self.reminder(0)
        reminder.schedule_at = self.now.replace(hour=12)
        reminder.save(update_fields=['schedule_at'])
        self.assertEqual(due_candidates(limit=1, now=self.now), [])
