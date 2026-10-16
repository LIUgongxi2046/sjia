"""Synthetic local disposition lifecycle, including actual temporary-file receipts.

External provider deletion is a separate opt-in integration scope. This suite
never reads, deletes, or fabricates clinical inputs or external-provider receipts.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from django.test import TestCase

from authorization.models import AccessGrant, ServiceRoleGrant
from files.models import FileGroup, FileObject
from files.purpose_schemas import PurposeDispositionApprovalInput
from files.service import object_store
from files.storage import digest_bytes
from identity.models import Patient, Principal
from retention.disposition import (
    DispositionRejected, approve_disposition_request, create_disposition_item,
    create_disposition_request, execute_local_item, mark_item_awaiting_provider,
    mark_item_deleted, mark_item_retained, scope_sha256,
)
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tests.synthetic_fixtures import SyntheticEvidence, synthetic_png


def _now():
    return datetime.now(timezone.utc)


class DispositionServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-T26', status='active', created_at=now)
        self.synthetic = SyntheticEvidence(self, self.principal)
        self.policy = RetentionPolicy.objects.create(policy_key=f'synthetic-dispose-{uuid4()}', version=1,
            data_class='file', rules={'local_delete': {'schema_version': 'local-disposition-2',
                'target_types': ['file_object'], 'access_purpose': 'family_observation',
                'receipt_policy_id': str(self.synthetic.retention.id)}}, schema_version='retention-1', status='active',
            approved_by=self.principal, approved_at=now, evidence_ref='synthetic-test-only')
        ServiceRoleGrant.objects.create(principal=self.principal, environment=self.synthetic.gate,
            action='manage_disposition', granted_by=self.principal, evidence_ref='synthetic-test-only', valid_from=now)
        AccessGrant.objects.create(patient=self.patient, principal=self.principal, relation_id=None,
            action='read_file', field_codes=[], record_scope='all', purpose='family_observation',
            granted_by=self.principal, evidence_ref='synthetic-test-only', valid_from=now)
        authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.synthetic.source,
            purpose='family_observation', environment='test', evidence_ref='synthetic-test-only',
            approved_by=self.principal, approved_at=now)
        group = FileGroup.objects.create(patient=self.patient, owner=self.principal, version=1, status='available',
            source_revision=self.synthetic.source, use_authorization_id=authorization.id, created_at=now)
        data = synthetic_png()
        key = f'synthetic-t26/{uuid4()}/original'
        object_store.put_immutable(key, data)
        self.file = FileObject.objects.create(patient=self.patient, file_group_id=group.id, object_key=key,
            object_version=digest_bytes(data), checksum=digest_bytes(data), size_bytes=len(data), media_type='image/png',
            purpose='original', status='available', retention_policy=self.policy, created_at=now)
        self.target = {'target_type': 'file_object', 'target_id': str(self.file.id), 'target_version': self.file.object_version}

    def _request(self):
        return create_disposition_request(patient=self.patient, requested_by=self.principal, policy=self.policy,
            evidence_ref='synthetic-test-only', scope={'targets': [self.target]})

    def _approve(self, request):
        return approve_disposition_request(request=request, approved_by=self.principal,
            auth_epoch=self.principal.auth_epoch, payload=PurposeDispositionApprovalInput.model_validate({
                'policy_version': str(self.policy.version), 'expected_scope_sha256': scope_sha256(request.scope),
                'evidence_ref': 'synthetic-test-only'}))

    def _item(self, request):
        return create_disposition_item(request=request, **self.target)

    def test_create_request_pending(self):
        request = self._request()
        self.assertEqual(request.status, 'pending')
        self.assertEqual(request.scope['targets'], [self.target])

    def test_approve_request(self):
        request = self._request()
        self._approve(request)
        request.refresh_from_db()
        self.assertEqual(request.status, 'approved')
        self.assertIsNotNone(request.approved_at)
        self.assertEqual(request.scope['approval_context']['file_versions'][0]['file_id'], str(self.file.id))

    def test_create_item_requires_approved(self):
        request = self._request()
        with self.assertRaises(DispositionRejected):
            self._item(request)

    def test_retained_requires_evidence(self):
        request = self._request()
        self._approve(request)
        with self.assertRaisesMessage(DispositionRejected, 'RETAINED_REQUIRES_EVIDENCE'):
            mark_item_retained(item=self._item(request), retention_evidence_ref='', recheck_at=None,
                executed_by=self.principal, auth_epoch=self.principal.auth_epoch)

    def test_retained_with_evidence(self):
        request = self._request()
        self._approve(request)
        item = self._item(request)
        mark_item_retained(item=item, retention_evidence_ref='synthetic-test-only',
            recheck_at=_now() + timedelta(days=30), executed_by=self.principal, auth_epoch=self.principal.auth_epoch)
        item.refresh_from_db()
        self.assertEqual(item.status, 'retained')
        self.assertIsNotNone(item.recheck_at)
        self.assertTrue(object_store.exists(self.file.object_key))

    def test_deleted_requires_receipt(self):
        request = self._request()
        self._approve(request)
        with self.assertRaisesMessage(DispositionRejected, 'DELETED_REQUIRES_RECEIPT'):
            mark_item_deleted(item=self._item(request), receipt_ref='', receipt_sha256=None,
                executed_by=self.principal, auth_epoch=self.principal.auth_epoch)
        self.assertTrue(object_store.exists(self.file.object_key))

    def test_deleted_with_real_synthetic_object_receipt(self):
        request = self._request()
        self._approve(request)
        item = self._item(request)
        execute_local_item(item=item, expected_target_version=self.file.object_version,
            policy_version=str(self.policy.version), executed_by=self.principal, auth_epoch=self.principal.auth_epoch)
        item.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(item.status, 'deleted')
        self.assertEqual(request.status, 'completed')
        self.assertFalse(object_store.exists(self.file.object_key))
        self.assertTrue(object_store.exists(item.receipt_ref))
        self.assertEqual(digest_bytes(object_store.get(item.receipt_ref)), item.receipt_sha256)

    def test_local_object_cannot_be_marked_awaiting_provider(self):
        request = self._request()
        self._approve(request)
        item = self._item(request)
        with self.assertRaisesMessage(DispositionRejected, 'DISPOSITION_PROVIDER_SCOPE_DENIED'):
            mark_item_awaiting_provider(item=item)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
