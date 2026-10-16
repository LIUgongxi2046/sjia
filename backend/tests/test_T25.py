"""T25 结构化就医摘要、准确范围与重新生成逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T25 --keepdb
"""
import uuid
from datetime import date, datetime, timezone

from django.test import TestCase

from configurations.models import ConfigVersion
from identity.models import Patient, Principal
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source
from summaries.models import Summary
from summaries.service import SummaryRejected, add_summary_item, create_summary, needs_regeneration, read_summary


def _now():
    return datetime.now(timezone.utc)


class SummaryServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T25", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="obs", version=1, data_class="observation", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="sum-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)
        self.template = ConfigVersion.objects.create(config_key="tpl", kind="summary", version=1, schema_version="summary-1", payload={}, checksum="a" * 64, status="enabled", created_by=self.principal, created_at=now)
        self.visibility = ConfigVersion.objects.create(config_key="vis", kind="visibility", version=1, schema_version="visibility-1", payload={}, checksum="b" * 64, status="enabled", created_by=self.principal, created_at=now)
        from tasks.models import Task

        self.task = Task.objects.create(actor=self.principal, patient=self.patient, task_type="summary", schema_version="task-1", input_refs={}, input_sha256="a" * 64, status="completed", status_version=1, created_at=now, deadline_at=now)

    def _summary(self):
        return create_summary(
            patient=self.patient, requested_by=self.principal, from_date=date(2026, 9, 1), to_date=date(2026, 9, 30),
            scope_timezone="Asia/Shanghai", include_unknown_time=False, template=self.template, visibility_policy=self.visibility,
            task_id=self.task.id,
        )

    def test_create_summary(self):
        summary = self._summary()
        self.assertEqual(summary.validity, "valid")
        self.assertEqual(summary.data_epoch, self.patient.data_epoch)

    def test_date_range_invalid(self):
        with self.assertRaises(SummaryRejected):
            create_summary(patient=self.patient, requested_by=self.principal, from_date=date(2026, 9, 30), to_date=date(2026, 9, 1), scope_timezone="Asia/Shanghai", include_unknown_time=False, template=self.template, visibility_policy=self.visibility, task_id=uuid.uuid4())

    def test_needs_regeneration_after_data_change(self):
        summary = self._summary()
        self.assertFalse(needs_regeneration(summary, self.patient))
        # 模拟新资料使 data_epoch 变化
        self.patient.data_epoch = self.patient.data_epoch + 1
        self.patient.save(update_fields=["data_epoch"])
        self.assertTrue(needs_regeneration(summary, self.patient))

    def test_read_summary_with_items(self):
        from tests.synthetic_fixtures import SyntheticEvidence
        from tests.integration.helpers import activate_summary_configs
        from summaries.service import generate_summary
        self.synthetic = SyntheticEvidence(self, self.principal)
        activate_summary_configs(self)
        create_record(patient=self.patient, actor=self.principal, kind='observation', capture_method='manual',
            origin_type='self_report', retention_policy=self.retention, use_authorization_id=self.use_auth.id,
            fields=[{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known',
                'raw_numeric': '38.0', 'raw_unit': 'C', 'sample_time_origin': 'observation',
                'medical_time': {'precision': 'date', 'local_date': '2026-09-15', 'resolution_status': 'local_only'}}])
        self.patient.refresh_from_db()
        summary = generate_summary(patient=self.patient, requested_by=self.principal, from_date=date(2026,9,1),
            to_date=date(2026,9,30), scope_timezone='Asia/Shanghai', include_unknown_time=False,
            field_codes=['temp'], environment='test')
        read = read_summary(summary.id, self.patient)
        self.assertFalse(read['needs_regeneration'])
        self.assertEqual(len(read['items']), 1)
        self.assertEqual(read['items'][0]['snapshot']['field']['raw_numeric'], '38.0')
        self.assertEqual(read['items'][0]['snapshot']['field']['field_code'], 'temp')

    def test_read_summary_scoped_to_patient(self):
        summary = self._summary()
        other = Patient.objects.create(display_id="P-OTHER", status="active", created_at=_now())
        with self.assertRaises(SummaryRejected):
            read_summary(summary.id, other)
