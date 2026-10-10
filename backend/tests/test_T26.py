"""T26 保存、删除申请及逐对象处置逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T26 --keepdb
"""
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from identity.models import Patient, Principal
from retention.disposition import (
    DispositionRejected,
    approve_disposition_request,
    create_disposition_item,
    create_disposition_request,
    mark_item_awaiting_provider,
    mark_item_deleted,
    mark_item_retained,
)
from retention.models import RetentionPolicy


def _now():
    return datetime.now(timezone.utc)


class DispositionServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T26", status="active", created_at=now)
        self.policy = RetentionPolicy.objects.create(policy_key="dispose", version=1, data_class="file", rules={}, schema_version="r-1", status="active")

    def _request(self):
        return create_disposition_request(patient=self.patient, requested_by=self.principal, policy=self.policy, evidence_ref="proof", scope={"targets": ["file"]})

    def test_create_request_pending(self):
        request = self._request()
        self.assertEqual(request.status, "pending")

    def test_approve_request(self):
        request = self._request()
        approve_disposition_request(request=request, approved_by=self.principal)
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")
        self.assertIsNotNone(request.approved_at)

    def test_create_item_requires_approved(self):
        request = self._request()
        with self.assertRaises(DispositionRejected):
            create_disposition_item(request=request, target_type="file_object", target_id="t", target_version="v")

    def test_retained_requires_evidence(self):
        request = self._request()
        approve_disposition_request(request=request, approved_by=self.principal)
        item = create_disposition_item(request=request, target_type="file_object", target_id="t", target_version="v")
        with self.assertRaises(DispositionRejected):
            mark_item_retained(item=item, retention_evidence_ref="", recheck_at=None, executed_by=self.principal)

    def test_retained_with_evidence(self):
        request = self._request()
        approve_disposition_request(request=request, approved_by=self.principal)
        item = create_disposition_item(request=request, target_type="file_object", target_id="t", target_version="v")
        mark_item_retained(item=item, retention_evidence_ref="evidence", recheck_at=_now() + timedelta(days=30), executed_by=self.principal)
        item.refresh_from_db()
        self.assertEqual(item.status, "retained")
        self.assertIsNotNone(item.recheck_at)

    def test_deleted_requires_receipt(self):
        request = self._request()
        approve_disposition_request(request=request, approved_by=self.principal)
        item = create_disposition_item(request=request, target_type="file_object", target_id="t", target_version="v")
        with self.assertRaises(DispositionRejected):
            mark_item_deleted(item=item, receipt_ref="", receipt_sha256=None, executed_by=self.principal)

    def test_deleted_with_receipt(self):
        request = self._request()
        approve_disposition_request(request=request, approved_by=self.principal)
        item = create_disposition_item(request=request, target_type="file_object", target_id="t", target_version="v")
        mark_item_deleted(item=item, receipt_ref="receipt", receipt_sha256="a" * 64, executed_by=self.principal)
        item.refresh_from_db()
        self.assertEqual(item.status, "deleted")

    def test_awaiting_provider(self):
        request = self._request()
        approve_disposition_request(request=request, approved_by=self.principal)
        item = create_disposition_item(request=request, target_type="provider_copy", target_id="t", target_version="v")
        mark_item_awaiting_provider(item=item)
        item.refresh_from_db()
        self.assertEqual(item.status, "awaiting_provider")
