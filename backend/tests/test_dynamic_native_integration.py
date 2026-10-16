"""Real synthetic dynamic-task → publication → native delivery integration.

No clinical/governance admission, task runner, publication or signing function is
patched. Only outbound network is blocked; all files and identities are synthetic.
This establishes software behavior, not clinical validity or external delivery.
"""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from audit.models import AuditEvent
from authorization.models import AccessGrant
from notifications import native
from notifications.models import NotificationAttempt, NotificationReceipt
from tests.synthetic_dynamic import synthetic_dynamic_native
from tests.synthetic_fixtures import ensure_synthetic_audit_policy


class DynamicNativeIntegrationTest(TestCase):
    def setUp(self):
        network = patch('urllib.request.urlopen', side_effect=AssertionError('No external delivery permitted'))
        network.start()
        self.addCleanup(network.stop)
        self.flow = synthetic_dynamic_native(self)
        ensure_synthetic_audit_policy(self.flow.actor, case=self)
        AccessGrant.objects.create(patient=self.flow.patient, principal=self.flow.actor,
            relation_id=self.flow.relation.id, action='receive_alert', field_codes=[], record_scope='all',
            purpose='family_observation', granted_by=self.flow.actor,
            evidence_ref='synthetic family message read', valid_from=timezone.now())
        session = self.client.session
        session['_sjia_principal_id'] = str(self.flow.actor.id)
        session['_sjia_auth_epoch'] = self.flow.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()

    def test_current_dynamic_proof_authorizes_native_accept_and_family_delivery(self):
        attempt, task = native.authorize_native(self.flow.recipient.id)
        self.assertEqual(attempt.status, 'authorized')
        self.assertEqual(native.checked_permit(attempt).id, task.id)
        native.submit_native(attempt.id)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'accepted')
        path = f'/api/v2/notification-records/{self.flow.recipient.id}'
        message = self.client.get(path + '/message')
        self.assertEqual(message.status_code, 200, message.content)
        self.assertEqual(message.json()['body']['approved_text'], 'SYNTHETIC TEST CONTENT; NO CLINICAL APPROVAL')
        delivered = self.client.post(path + '/delivered', {'delivery_ticket': message.json()['delivery_ticket']},
            content_type='application/json')
        self.assertEqual(delivered.status_code, 200, delivered.content)
        self.assertEqual(delivered.json()['delivery_status'], 'delivered')
        repeated = self.client.post(path + '/delivered', {'delivery_ticket': message.json()['delivery_ticket']},
            content_type='application/json')
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(NotificationAttempt.objects.count(), 1)
        self.assertEqual([row.status for row in native.checked_receipts(attempt, task)], ['accepted', 'delivered'])
        self.assertEqual(NotificationReceipt.objects.filter(status='delivered').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='notification_delivered').count(), 1)

    def test_revoked_verified_fact_blocks_native_permit_with_zero_receipts(self):
        from assessment.qualified_facts import QualificationRejected
        from notifications.errors import NotificationRejected
        self.flow.verification.revoked_at = timezone.now()
        self.flow.verification.save(update_fields=['revoked_at'])
        with self.assertRaises((QualificationRejected, NotificationRejected)):
            native.authorize_native(self.flow.recipient.id)
        self.assertFalse(NotificationAttempt.objects.exists())
        self.assertFalse(NotificationReceipt.objects.exists())
