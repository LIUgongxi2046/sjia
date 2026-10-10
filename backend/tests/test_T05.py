"""T05 提交意图、幂等与结果恢复逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T05 --keepdb
"""
import uuid
from datetime import datetime, timezone

from django.test import TestCase

from identity.models import Patient, Principal
from requests.models import WriteIntent, WriteRequest
from requests.service import (
    IdempotencyConflict,
    IntentRejected,
    commit_intent,
    fail_intent,
    prepare_intent,
    query_intent,
    request_recent_account,
    retry_intent,
)
from retention.models import RetentionPolicy


def _now():
    return datetime.now(timezone.utc)


class WriteIntentTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T05", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="intent", version=1, data_class="request", rules={}, schema_version="r-1", status="active")
        self.request_id = uuid.uuid4()
        self.payload = {"kind": "observation", "fields": [{"field_code": "temp", "raw_numeric": "38.0"}]}

    def _prepare(self, payload=None, request_id=None):
        return prepare_intent(
            actor=self.principal,
            operation_id="record_create",
            target_key="/records",
            request_id=request_id or self.request_id,
            payload=payload or self.payload,
            schema_version="back-record-1",
            retention_policy=self.retention,
            patient=self.patient,
        )

    def test_prepare_creates_prepared_intent(self):
        intent = self._prepare()
        self.assertEqual(intent.status, "prepared")
        self.assertEqual(intent.generation, 1)

    def test_same_key_same_payload_reuses(self):
        first = self._prepare()
        second = self._prepare()
        self.assertEqual(first.id, second.id)
        self.assertEqual(WriteIntent.objects.count(), 1)

    def test_same_key_different_payload_conflicts(self):
        self._prepare()
        with self.assertRaises(IdempotencyConflict):
            self._prepare(payload={"kind": "therapy"})

    def test_commit_creates_write_request_and_marks_committed(self):
        intent = self._prepare()
        resource_id = uuid.uuid4()
        write_request = commit_intent(intent=intent, action="record_create", resource_type="record", resource_id=resource_id)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "committed")
        self.assertEqual(intent.write_request_id, write_request.id)
        self.assertEqual(WriteRequest.objects.count(), 1)

    def test_commit_requires_prepared(self):
        intent = self._prepare()
        commit_intent(intent=intent, action="record_create", resource_type="record", resource_id=uuid.uuid4())
        with self.assertRaises(IntentRejected):
            commit_intent(intent=intent, action="record_create", resource_type="record", resource_id=uuid.uuid4())

    def test_fail_and_retry_generation(self):
        intent = self._prepare()
        fail_intent(intent=intent, error_code="REQUEST_EXPIRED")
        intent.refresh_from_db()
        self.assertEqual(intent.status, "failed")
        retry_intent(intent=intent, expected_generation=1)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "prepared")
        self.assertEqual(intent.generation, 2)

    def test_retry_generation_mismatch(self):
        intent = self._prepare()
        fail_intent(intent=intent, error_code="REQUEST_EXPIRED")
        with self.assertRaises(IntentRejected):
            retry_intent(intent=intent, expected_generation=99)

    def test_query_intent(self):
        intent = self._prepare()
        found = query_intent(self.principal.id, "record_create", "/records", self.request_id)
        self.assertEqual(found.id, intent.id)

    def test_request_recent_account_filters_patient_null(self):
        prepare_intent(actor=self.principal, operation_id="application_create", target_key="/associations/applications", request_id=uuid.uuid4(), payload={}, schema_version="back-relation-1", retention_policy=self.retention, patient=None)
        accounts = request_recent_account(self.principal.id, ["application_create", "application_withdraw"])
        self.assertEqual(accounts.count(), 1)

    def test_invalid_target_key_rejected(self):
        from requests.service import prepare_intent as pi

        with self.assertRaises(IntentRejected):
            pi(actor=self.principal, operation_id="record_create", target_key="https://evil.example/x", request_id=uuid.uuid4(), payload={}, schema_version="back-record-1", retention_policy=self.retention)
