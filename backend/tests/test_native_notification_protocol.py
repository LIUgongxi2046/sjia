"""Native in-app signing/state UNIT tests with explicit pre-admitted scope.

Only locked_scope (clinical/governance admission) and its duplicate content read
are injected. Permit signing, hash binding, persistence, audit, idempotency,
mailbox tickets and receipt verification execute their real implementations.
These tests do not prove dynamic clinical evaluation or publication admission.
No network provider or real credentials are used.
"""
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.signing import BadSignature
from django.test import TestCase
from django.utils import timezone

from assessment.provenance import entity_reference
from audit.models import AuditEvent
from family_content.models import EnvironmentGate
from identity.models import Principal
from notifications import native
from notifications.errors import NotificationRejected
from notifications.models import NotificationAttempt, NotificationReceipt
from relations.models import Relation
from retention.models import RetentionPolicy
from tasks.models import Task, TaskAttempt
from tests import test_T23


class NativeNotificationProtocolUnitTest(TestCase):
    def setUp(self):
        test_T23.NotificationServiceTest.setUp(self)
        from alerting.models import AlertEvent
        self.event = AlertEvent.objects.get(id=self.recipient.alert_id)
        self.recipient.channel = 'in_app'
        self.recipient.save(update_fields=['channel'])
        self.gate = EnvironmentGate.objects.create(environment='test', enabled=True, version=1,
            publication_epoch=0, updated_at=timezone.now())
        self.retention = RetentionPolicy.objects.create(policy_key=f'synthetic-native-{uuid4()}', version=1,
            data_class='audit', schema_version='retention-1', status='active', rules={},
            approved_by=self.principal, approved_at=timezone.now(), evidence_ref='synthetic-protocol-unit')
        self.runtime = SimpleNamespace(payload={'api': {'max_json_bytes': 1048576},
            'tasks': [{'task_type': 'notification', 'max_concurrency': 2}]})
        self.provider = SimpleNamespace(id=uuid4())
        self.policy = SimpleNamespace(permit_seconds=300, delivery_ticket_seconds=120,
            max_recipients_per_event=1, max_daily_messages=3, timezone='UTC',
            signing_secret_ref='env:SJIA_SYNTHETIC_NOTIFICATION_UNIT_KEY')
        self.channels = SimpleNamespace(max_attempts=1, retry_delays_seconds=[])
        self.content = {'validity': 'valid', 'content_state': 'available', 'content': {
            'approved_text': 'SYNTHETIC PROTOCOL UNIT TEXT', 'content_id': str(uuid4()),
            'content_version': '1', 'contact': {'state': 'available'}}}
        self.context = {'gate': entity_reference(self.gate), 'fixture_scope': 'pre-admitted synthetic protocol unit'}
        secret = patch.dict(os.environ, {'SJIA_SYNTHETIC_NOTIFICATION_UNIT_KEY': 'synthetic-test-only-' + 'x' * 48})
        secret.start()
        self.addCleanup(secret.stop)
        scope = patch('notifications.native.locked_scope', side_effect=self.scope)
        scope.start()
        self.addCleanup(scope.stop)
        content = patch('notifications.native.alert_content', return_value=self.content)
        content.start()
        self.addCleanup(content.stop)
        network = patch('urllib.request.urlopen', side_effect=AssertionError('No external delivery permitted'))
        network.start()
        self.addCleanup(network.stop)
        expiry = timezone.now() + timedelta(hours=1)
        self.request = SimpleNamespace(session=SimpleNamespace(get_expiry_date=lambda: expiry))

    def scope(self, recipient_id):
        self.assertEqual(recipient_id, self.recipient.id)
        self.gate.refresh_from_db()
        self.recipient.refresh_from_db()
        return (self.recipient, self.event, self.principal, self.principal, self.gate, self.runtime,
            self.provider, self.policy, self.retention, self.channels, self.content, self.context)

    def accepted(self):
        attempt, task = native.authorize_native(self.recipient.id)
        self.assertEqual(attempt.status, 'authorized')
        self.assertEqual(native.checked_permit(attempt).id, task.id)
        native.submit_native(attempt.id)
        attempt.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(attempt.status, 'accepted')
        self.assertEqual(task.status, 'completed')
        self.assertEqual([row.status for row in native.checked_receipts(attempt, task)], ['accepted'])
        return attempt, task

    def test_authorize_accept_deliver_signed_receipts_are_idempotent(self):
        attempt, task = self.accepted()
        repeated, repeated_task = native.authorize_native(self.recipient.id)
        self.assertEqual((repeated.id, repeated_task.id), (attempt.id, task.id))
        native.submit_native(attempt.id)
        self.assertEqual(NotificationAttempt.objects.count(), 1)
        self.assertEqual(TaskAttempt.objects.filter(task=task).count(), 1)
        message = native.delivery_message(self.request, self.principal, self.recipient.id)
        self.assertEqual(message['body']['approved_text'], self.content['content']['approved_text'])
        native.acknowledge_delivery(self.request, self.principal, self.recipient.id, message['delivery_ticket'])
        native.acknowledge_delivery(self.request, self.principal, self.recipient.id, message['delivery_ticket'])
        attempt.refresh_from_db()
        self.recipient.refresh_from_db()
        self.assertEqual(attempt.status, 'accepted')
        self.assertEqual(self.recipient.delivery_status, 'delivered')
        self.assertEqual([row.status for row in native.checked_receipts(attempt, task)], ['accepted', 'delivered'])
        self.assertEqual(NotificationReceipt.objects.filter(status='delivered').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='notification_delivered').count(), 1)

    def test_corrupted_permit_hash_cannot_accept(self):
        attempt, task = native.authorize_native(self.recipient.id)
        task.input_refs['body']['approved_text'] = 'tampered'
        task.save(update_fields=['input_refs'])
        with self.assertRaisesMessage(NotificationRejected, 'NOTIFICATION_PERMIT_PROOF_MISMATCH'):
            native.submit_native(attempt.id)
        self.assertFalse(NotificationReceipt.objects.exists())
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'authorized')

    def test_forged_delivery_ticket_cannot_mark_delivered(self):
        self.accepted()
        message = native.delivery_message(self.request, self.principal, self.recipient.id)
        token = message['delivery_ticket'][:-1] + ('A' if message['delivery_ticket'][-1] != 'A' else 'B')
        with self.assertRaises(BadSignature):
            native.acknowledge_delivery(self.request, self.principal, self.recipient.id, token)
        self.assertFalse(NotificationReceipt.objects.filter(status='delivered').exists())

    def test_other_actor_cannot_read_mailbox_or_acknowledge(self):
        self.accepted()
        now = timezone.now()
        other = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        with self.assertRaisesMessage(NotificationRejected, 'NOTIFICATION_RECIPIENT_SCOPE_DENIED'):
            native.delivery_message(self.request, other, self.recipient.id)
        message = native.delivery_message(self.request, self.principal, self.recipient.id)
        with self.assertRaisesMessage(NotificationRejected, 'NOTIFICATION_RECIPIENT_SCOPE_DENIED'):
            native.acknowledge_delivery(self.request, other, self.recipient.id, message['delivery_ticket'])
        self.assertFalse(NotificationReceipt.objects.filter(status='delivered').exists())

    def test_relation_epoch_change_invalidates_exact_delivery_ticket(self):
        self.accepted()
        message = native.delivery_message(self.request, self.principal, self.recipient.id)
        relation = Relation.objects.get(id=self.recipient.relation_id)
        relation.auth_epoch += 1
        relation.save(update_fields=['auth_epoch'])
        with self.assertRaisesMessage(NotificationRejected, 'NOTIFICATION_DELIVERY_TICKET_CHANGED'):
            native.acknowledge_delivery(self.request, self.principal, self.recipient.id, message['delivery_ticket'])
        self.assertFalse(NotificationReceipt.objects.filter(status='delivered').exists())
