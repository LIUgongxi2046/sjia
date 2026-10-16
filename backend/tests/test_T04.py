"""T04 来源、配置、字典与单位映射逻辑验证。

验证配置登记不可变、批准/验证/启用分离、来源登记、单位换算与字段配置读取。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T04 --keepdb
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from tests.synthetic_fixtures import SyntheticEvidence

from django.test import TestCase

from configurations.field_config import FieldConfigInvalid, read_field_config
from configurations.models import ConfigVersion
from configurations.service import ConfigRejected, approve_config, compute_checksum, enable_config, register_config, validate_config
from dictionaries.models import UnitMapping
from dictionaries.service import MappingNotFound, convert_measurement
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from sources.models import SourceRegistry, SourceRevision
from sources.service import approve_revision, create_use_authorization, register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


class ConfigServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T04", status="active", created_at=now)
        self.env = EnvironmentGate.objects.create(environment="test", version=1, enabled=True, updated_at=now)
        self.source = register_source(source_key="cfg", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        self.revision = register_revision(source=self.source, version="v1", checksum="c" * 64, license="l", allowed_use=["use1"])
        self.synthetic = SyntheticEvidence(self, self.principal)

    def payload(self, codes=('temp',)):
        return {'schema_version': 'summary-1', 'environment': 'test', 'purpose': 'software_test',
            'field_codes': list(codes), 'evidence_refs': [self.synthetic.reference]}

    def test_register_config_immutable_creates_new_version(self):
        c1 = register_config(kind="summary", config_key="r1", schema_version="summary-1", payload=self.payload(), created_by=self.principal)
        c2 = register_config(kind="summary", config_key="r1", schema_version="summary-1", payload=self.payload(("rash",)), created_by=self.principal)
        self.assertEqual(c1.version, 1)
        self.assertEqual(c2.version, 2)
        self.assertEqual(c1.status, "draft")
        self.assertNotEqual(c1.checksum, c2.checksum)

    def test_register_config_unregistered_schema_rejected(self):
        with self.assertRaises(ConfigRejected):
            register_config(kind="rule", config_key="r1", schema_version="unknown-1", payload={}, created_by=self.principal)

    def test_register_config_unregistered_kind_rejected(self):
        with self.assertRaises(ConfigRejected):
            register_config(kind="bogus", config_key="r1", schema_version="x", payload={}, created_by=self.principal)

    def test_checksum_deterministic(self):
        self.assertEqual(compute_checksum({"b": 1, "a": 2}), compute_checksum({"a": 2, "b": 1}))

    def test_enable_requires_approval_and_validation(self):
        config = register_config(kind="summary", config_key="r2", schema_version="summary-1",
            payload=self.payload(), created_by=self.principal)
        def enable():
            self.env.refresh_from_db()
            return enable_config(config=config, environment=self.env, actor=self.principal,
                expected_gate_version=self.env.version, reason='synthetic lifecycle check',
                expected_checksum=config.checksum)
        with self.assertRaises(ConfigRejected):
            enable()
        self.synthetic.qualify(config)
        validation = config.validations.get()
        validation.delete()
        config.status = 'approved'
        config.save(update_fields=['status'])
        with self.assertRaises(ConfigRejected):
            enable()
        validation.save(force_insert=True)
        old_version = self.env.version
        enable()
        self.env.refresh_from_db()
        config.refresh_from_db()
        self.assertEqual(self.env.version, old_version + 1)
        self.assertEqual(config.status, "enabled")


class SourceServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-SRC", status="active", created_at=now)

    def test_register_and_approve_revision(self):
        source = register_source(source_key="s1", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["use1"])
        self.assertEqual(revision.status, "pending")
        synthetic = SyntheticEvidence(self, self.principal)
        revision.retention_policy = synthetic.retention
        revision.save(update_fields=['retention_policy'])
        approve_revision(revision=revision, evidence_ref=synthetic.reference)
        revision.refresh_from_db()
        self.assertEqual(revision.status, "approved")

    def test_approve_revision_requires_evidence(self):
        source = register_source(source_key="s2", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["use1"])
        from sources.service import SourceRejected

        with self.assertRaises(SourceRejected):
            approve_revision(revision=revision, evidence_ref="")

    def test_create_use_authorization(self):
        source = register_source(source_key="s3", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["use1"])
        auth = create_use_authorization(patient=self.patient, source_revision=revision, purpose="use1", environment="test", evidence_ref="e", approved_by=self.principal)
        self.assertIsNotNone(auth.id)


class UnitConversionTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.synthetic = SyntheticEvidence(self, self.principal)
        self.revision = self.synthetic.source
        self.config = self.synthetic.configuration('mapping', {'schema_version': 'mapping-1',
            'mappings': [{'mapping_id': str(uuid4()), 'concept_code': 'temp',
                'source_revision_id': str(self.revision.id), 'raw_unit': 'F', 'normalized_unit': 'C',
                'method_scope': 'all', 'multiplier': '0.5555555555555556',
                'additive': '-17.77777777777778', 'exact_values_only': False}],
            'evidence_refs': [self.synthetic.reference]})

    def test_convert_known_mapping(self):
        value, unit = convert_measurement(raw_numeric=Decimal("100.4"), raw_unit="F", concept_code="temp", config=self.config, source_revision=self.revision, method_scope="all")
        self.assertEqual(unit, "C")
        self.assertTrue(abs(value - Decimal("38.0")) < Decimal("0.001"))

    def test_unknown_mapping_not_converted(self):
        with self.assertRaises(MappingNotFound):
            convert_measurement(raw_numeric=Decimal("38"), raw_unit="unknown-unit", concept_code="temp", config=self.config, source_revision=self.revision, method_scope="all")


class FieldConfigReadTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-FC", status="active", created_at=now)

    def _payload(self):
        return {
            "schema_version": "field-config-1",
            "kind": "observation",
            "fields": [
                {"field_code": "temp", "label": "体温", "value_type": "decimal", "required": True, "writable": True, "allowed_values": [], "allowed_units": ["C"], "dictionary_category": None, "minimum": "30.0", "maximum": "45.0", "max_length": None, "max_occurrences": 1, "time_required": True},
                {"field_code": "rash", "label": "皮疹", "value_type": "categorical", "required": False, "writable": True, "allowed_values": ["yes", "no"], "allowed_units": [], "dictionary_category": None, "minimum": None, "maximum": None, "max_length": None, "max_occurrences": 1, "time_required": False},
            ],
            "watch_field_codes": ["temp"],
        }

    def test_disabled_without_config(self):
        view = read_field_config(self.patient, "observation")
        self.assertEqual(view["state"], "disabled")
        self.assertEqual(view["fields"], [])

    def test_available_with_enabled_config(self):
        config = register_config(kind="content", config_key="fc1", schema_version="field-config-1", payload=self._payload(), created_by=self.principal)
        synthetic = SyntheticEvidence(self, self.principal)
        synthetic.qualify(config)
        view = read_field_config(self.patient, "observation")
        self.assertEqual(view["state"], "available")
        self.assertEqual([f["field_code"] for f in view["fields"]], ["temp", "rash"])
        self.assertEqual(view["watch_field_codes"], ["temp"])

    def test_read_only_for_non_enabled_version(self):
        config = register_config(kind="content", config_key="fc2", schema_version="field-config-1", payload=self._payload(), created_by=self.principal)
        view = read_field_config(self.patient, "observation", version=config.id)
        self.assertEqual(view["state"], "read_only")

    def test_validation_rejects_watch_field_not_in_fields(self):
        payload = self._payload()
        payload["watch_field_codes"] = ["nonexistent"]
        with self.assertRaises(FieldConfigInvalid):
            from configurations.field_config import validate_definition

            validate_definition(payload)
