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
        from tests.synthetic_fixtures import SyntheticEvidence, synthetic_publication
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.synthetic = SyntheticEvidence(self, self.principal)
        self.release, self.contact, self.components = synthetic_publication(self.synthetic,
            add_components=False, enable=False)
        self.gate = self.synthetic.gate
        self.validation_config = self.components['validation']
        self.initial_version = self.gate.version
        self.initial_epoch = self.gate.publication_epoch

    def _add_components(self):
        for kind in ["rule", "window", "verification", "mapping", "visibility", "content", "notification"]:
            add_release_component(release=self.release, component_kind=kind, config=self.components[kind])

    def test_enable_requires_components(self):
        with self.assertRaisesMessage(ReleaseRejected, "RELEASE_COMPONENTS_INCOMPLETE"):
            approve_release(release=self.release)

    def test_enable_success(self):
        self._add_components()
        approve_release(release=self.release)
        enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=self.initial_version)
        self.release.refresh_from_db()
        self.gate.refresh_from_db()
        self.assertEqual(self.release.status, "enabled")
        self.assertEqual(self.gate.active_release_id, self.release.id)
        self.assertEqual(self.gate.publication_epoch, self.initial_epoch + 1)

    def test_enable_gate_version_mismatch(self):
        self._add_components()
        approve_release(release=self.release)
        with self.assertRaises(ReleaseRejected):
            enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=99)

    def test_enable_requires_approval(self):
        self._add_components()
        with self.assertRaises(ReleaseRejected):
            enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=self.initial_version)

    def test_disable_release(self):
        self._add_components()
        approve_release(release=self.release)
        enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=self.initial_version)
        disable_release(release=self.release, environment_gate=self.gate)
        self.release.refresh_from_db()
        self.gate.refresh_from_db()
        self.assertEqual(self.release.status, "disabled")
        self.assertIsNone(self.gate.active_release_id)
        self.assertEqual(self.gate.publication_epoch, self.initial_epoch + 2)
        self.assertFalse(release_available(self.release))

    def test_expired_contact_release_unavailable(self):
        self._add_components()
        approve_release(release=self.release)
        enable_release(release=self.release, environment_gate=self.gate, expected_gate_version=self.initial_version)
        self.contact.valid_until = _now() - timedelta(days=1)
        self.contact.save(update_fields=["valid_until"])
        self.assertFalse(release_available(self.release))
