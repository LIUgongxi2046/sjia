"""T21 家庭内容批准、联系信息及发布控制逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T21 --keepdb
"""
import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from configurations.models import ConfigVersion
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from family_content.service import (
    ReleaseRejected,
    activate_contact,
    add_release_component,
    approve_release,
    create_contact_version,
    create_release_bundle,
    disable_release,
    enable_release,
    release_available,
)
from identity.models import Principal
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


class ReleaseServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        source = register_source(source_key="c-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        self.revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.contact = create_contact_version(
            contact_id=uuid.uuid4(), contact_name="风湿免疫科", purpose="alert", channel="phone", channel_value="010-12345678",
            owner=self.principal, source_revision=self.revision, approval_config=self._config("contact"), valid_until=now + timedelta(days=30),
        )
        activate_contact(contact=self.contact)
        self.validation_config = self._config("validation")
        self.gate = EnvironmentGate.objects.create(environment="test", version=1, enabled=True, updated_at=now)
        self.release = create_release_bundle(environment="test", contact_version=self.contact, validation_config=self.validation_config)

    def _config(self, key):
        return ConfigVersion.objects.create(config_key=key, kind="rule", version=1, schema_version="rule-1", payload={}, checksum="a" * 64, status="enabled", created_by=self.principal, created_at=_now())

    def _add_components(self):
        for kind in ["rule", "window", "verification", "mapping", "visibility", "content", "notification"]:
            add_release_component(release=self.release, component_kind=kind, config=self._config(kind))

    def test_enable_requires_components(self):
        approve_release(release=self.release)
        with self.assertRaises(ReleaseRejected):
            enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=1)

    def test_enable_success(self):
        self._add_components()
        approve_release(release=self.release)
        enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=1)
        self.release.refresh_from_db()
        self.gate.refresh_from_db()
        self.assertEqual(self.release.status, "enabled")
        self.assertEqual(self.gate.active_release_id, self.release.id)
        self.assertEqual(self.gate.publication_epoch, 1)

    def test_enable_gate_version_mismatch(self):
        self._add_components()
        approve_release(release=self.release)
        with self.assertRaises(ReleaseRejected):
            enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=99)

    def test_enable_requires_approval(self):
        self._add_components()
        with self.assertRaises(ReleaseRejected):
            enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=1)

    def test_disable_release(self):
        self._add_components()
        approve_release(release=self.release)
        enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=1)
        disable_release(release=self.release, environment_gate=self.gate)
        self.release.refresh_from_db()
        self.gate.refresh_from_db()
        self.assertEqual(self.release.status, "disabled")
        self.assertIsNone(self.gate.active_release_id)
        self.assertEqual(self.gate.publication_epoch, 2)
        self.assertFalse(release_available(self.release))

    def test_expired_contact_release_unavailable(self):
        self._add_components()
        approve_release(release=self.release)
        enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=1)
        self.contact.valid_until = _now() - timedelta(days=1)
        self.contact.save(update_fields=["valid_until"])
        self.assertFalse(release_available(self.release))
