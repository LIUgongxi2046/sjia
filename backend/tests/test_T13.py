"""T13 OCR、本人确认与报告登记逻辑验证。

真实 OCR Provider 依赖 D03，本文件验证候选、确认与报告登记服务层逻辑。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T13 --keepdb
"""
import uuid
from datetime import datetime, timezone

from django.test import TestCase

from configurations.models import ConfigVersion
from files.models import FileGroup, FileObject
from identity.models import Patient, Principal
from recognition.models import CandidateConfirmation, CandidateField, CandidateSet
from recognition.service import (
    RecognitionRejected,
    add_candidate_field,
    confirm_candidate,
    confirmed_report_fields,
    create_candidate_set,
    finalize_candidate_set,
    register_report,
)
from records.models import Record, RecordAttachment, Report
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


class RecognitionServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T13", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="report", version=1, data_class="report", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="rep-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        self.revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)
        self.field_config = ConfigVersion.objects.create(config_key="fc", kind="content", version=1, schema_version="field-config-1", payload={}, checksum="a" * 64, status="enabled", created_by=self.principal, created_at=now)
        self.group = FileGroup.objects.create(patient=self.patient, owner=self.principal, version=1, status="available", source_revision=self.revision, use_authorization_id=self.use_auth.id, created_at=now)
        from tasks.models import Task

        self.task = Task.objects.create(actor=self.principal, patient=self.patient, task_type="ocr", schema_version="task-1", input_refs={}, input_sha256="a" * 64, status="completed", status_version=1, created_at=now, deadline_at=now)

    def _candidate_set(self):
        return create_candidate_set(
            patient=self.patient, file_group_id=self.group.id, file_group_version=1, task_id=self.task.id,
            provider="ocr-provider", model_version="v1", provider_request_id="req-1", schema_version="ocr-1",
        )

    def _candidate(self, candidate_set, field_path="/fields/temp", proposed=None):
        return add_candidate_field(
            candidate_set=candidate_set, field_path=field_path, field_config_version=self.field_config,
            recognition_status="recognized", raw_text="38.0", proposed_field=proposed or {"field_code": "temp", "value_type": "decimal", "raw_numeric": "38.0", "raw_unit": "C"},
        )

    def test_create_candidate_set(self):
        candidate_set = self._candidate_set()
        self.assertEqual(candidate_set.status, "pending_confirmation")

    def test_confirm_and_finalize(self):
        candidate_set = self._candidate_set()
        candidate = self._candidate(candidate_set)
        confirm_candidate(candidate_field=candidate, confirmed_by=self.principal, decision="accepted")
        self.assertEqual(CandidateConfirmation.objects.count(), 1)
        finalize_candidate_set(candidate_set=candidate_set)
        candidate_set.refresh_from_db()
        self.assertEqual(candidate_set.status, "confirmed")

    def test_finalize_requires_all_decided(self):
        candidate_set = self._candidate_set()
        self._candidate(candidate_set)
        with self.assertRaises(RecognitionRejected):
            finalize_candidate_set(candidate_set=candidate_set)

    def test_confirmed_fields_filter_excluded_unknown(self):
        candidate_set = self._candidate_set()
        c1 = self._candidate(candidate_set, "/fields/temp")
        c2 = self._candidate(candidate_set, "/fields/crp", proposed={"field_code": "crp", "value_type": "decimal", "raw_numeric": "12.0"})
        c3 = self._candidate(candidate_set, "/fields/platelet", proposed={"field_code": "platelet", "value_type": "decimal", "raw_numeric": "90.0"})
        confirm_candidate(candidate_field=c1, confirmed_by=self.principal, decision="accepted")
        confirm_candidate(candidate_field=c2, confirmed_by=self.principal, decision="excluded")
        confirm_candidate(candidate_field=c3, confirmed_by=self.principal, decision="unknown")
        fields = confirmed_report_fields(candidate_set)
        self.assertEqual([f["field_code"] for f in fields], ["temp"])

    def test_register_report(self):
        candidate_set = self._candidate_set()
        candidate = self._candidate(candidate_set)
        confirm_candidate(candidate_field=candidate, confirmed_by=self.principal, decision="accepted")
        finalize_candidate_set(candidate_set=candidate_set)
        result = register_report(
            patient=self.patient, actor=self.principal, candidate_set=candidate_set, source_revision=self.revision,
            report_status="final", retention_policy=self.retention, use_authorization_id=self.use_auth.id,
        )
        self.assertEqual(Report.objects.count(), 1)
        record = Record.objects.get(id=result["record_id"])
        self.assertEqual(record.kind, "report")
        self.assertEqual(record.current_version, 1)

    def test_register_report_with_attachment(self):
        candidate_set = self._candidate_set()
        candidate = self._candidate(candidate_set)
        confirm_candidate(candidate_field=candidate, confirmed_by=self.principal, decision="accepted")
        finalize_candidate_set(candidate_set=candidate_set)
        # 建立同患儿文件组与对象，验证 record_attachment 建立
        group = FileGroup.objects.create(patient=self.patient, owner=self.principal, version=1, status="available", source_revision=self.revision, use_authorization_id=self.use_auth.id, created_at=_now())
        FileObject.objects.create(patient=self.patient, file_group_id=group.id, object_key="k", object_version="v", checksum="b" * 64, size_bytes=10, media_type="image/png", purpose="original", status="available", retention_policy=self.retention, created_at=_now())
        candidate_set.file_group_id = group.id
        candidate_set.save(update_fields=["file_group_id"])
        result = register_report(patient=self.patient, actor=self.principal, candidate_set=candidate_set, source_revision=self.revision, report_status="final", retention_policy=self.retention, use_authorization_id=self.use_auth.id)
        self.assertEqual(RecordAttachment.objects.filter(record_version_id=result["record_version_id"]).count(), 1)
