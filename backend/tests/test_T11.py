"""T11 服务端草稿、恢复与提交衔接逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T11 --keepdb
"""
from datetime import datetime, timezone

from django.test import TestCase

from drafts.models import Draft
from drafts.service import DraftRejected, abandon_draft, create_draft, list_restorable_drafts, read_draft, submit_draft, update_draft
from identity.models import Patient, Principal
from requests.models import WriteRequest
from retention.models import RetentionPolicy


def _now():
    return datetime.now(timezone.utc)


class DraftServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.owner = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T11", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="draft", version=1, data_class="draft", rules={}, schema_version="r-1", status="active")

    def _draft(self, form_type="observation"):
        return create_draft(patient=self.patient, owner=self.owner, form_type=form_type, schema_version="backend-draft-1", payload={"temp": "38.0"}, retention_policy=self.retention)

    def test_create_draft(self):
        draft = self._draft()
        self.assertEqual(draft.status, "editing")
        self.assertEqual(draft.version, 1)

    def test_update_draft_increments_version(self):
        draft = self._draft()
        update_draft(draft=draft, expected_version=1, payload={"temp": "39.0"})
        draft.refresh_from_db()
        self.assertEqual(draft.version, 2)
        self.assertEqual(draft.payload, {"temp": "39.0"})

    def test_update_version_conflict(self):
        draft = self._draft()
        with self.assertRaises(DraftRejected):
            update_draft(draft=draft, expected_version=99, payload={})

    def test_abandon_draft(self):
        draft = self._draft()
        abandon_draft(draft=draft, expected_version=1)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "abandoned")

    def test_abandon_version_conflict(self):
        draft = self._draft()
        with self.assertRaises(DraftRejected):
            abandon_draft(draft=draft, expected_version=2)

    def test_submit_draft_requires_request(self):
        draft = self._draft()
        write_request = WriteRequest.objects.create(
            actor=self.owner, patient=self.patient, action="record_create", target_key="/records",
            request_id=draft.id, payload_sha256="a" * 64, normalization_version="back-json-1",
            status="committed", resource_type="record", resource_id=draft.id, created_at=_now(), completed_at=_now(),
        )
        submit_draft(draft=draft, expected_version=1, submitted_request=write_request)
        draft.refresh_from_db()
        self.assertEqual(draft.status, "submitted")
        self.assertEqual(draft.submitted_request_id, write_request.id)

    def test_submitted_draft_not_restorable(self):
        draft = self._draft()
        write_request = WriteRequest.objects.create(
            actor=self.owner, patient=self.patient, action="record_create", target_key="/records",
            request_id=draft.id, payload_sha256="a" * 64, normalization_version="back-json-1",
            status="committed", resource_type="record", resource_id=draft.id, created_at=_now(), completed_at=_now(),
        )
        submit_draft(draft=draft, expected_version=1, submitted_request=write_request)
        self.assertEqual(list_restorable_drafts(owner=self.owner, patient=self.patient).count(), 0)

    def test_list_restorable_drafts(self):
        self._draft()
        self._draft(form_type="report")
        drafts = list_restorable_drafts(owner=self.owner, patient=self.patient)
        self.assertEqual(drafts.count(), 2)
        self.assertEqual(list_restorable_drafts(owner=self.owner, patient=self.patient, form_type="report").count(), 1)

    def test_read_draft_scoped_to_owner_patient(self):
        draft = self._draft()
        self.assertEqual(read_draft(draft.id, owner=self.owner, patient=self.patient).id, draft.id)
        other = Principal.objects.create(account_status="active", created_at=_now(), updated_at=_now())
        self.assertIsNone(read_draft(draft.id, owner=other, patient=self.patient))
