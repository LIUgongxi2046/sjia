import os
import unittest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from api.views_releases import PROTOCOL, release_view
from configurations.models import ConfigApproval, ConfigValidation
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from family_content.service import release_available


class CurrentReleaseQualificationTest(unittest.TestCase):
    def test_anonymous_release_requests_are_rejected(self):
        release = ReleaseBundle.objects.order_by('id').first()
        self.assertIsNotNone(release, '当前数据库需要已有发布记录。')
        paths = [('GET', f'/api/support/releases/{release.id}'), ('POST', '/api/support/releases')]
        paths.extend(('POST', f'/api/support/releases/{release.id}/{action}')
            for action in ('approve', 'enable', 'disable'))
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, path in paths:
                with self.subTest(method=method, path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])
                    self.assertIn('private', response.headers['cache-control'])

    def test_saved_release_projection_matches_protocol(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            releases = list(ReleaseBundle.objects.all())
            self.assertTrue(releases, '当前数据库需要已有发布记录。')
            validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/ReleaseView'}, format_checker=FormatChecker())
            for release in releases:
                result = release_view(release)
                validator.validate(result)
                self.assertEqual(result['release_id'], str(release.id))
                self.assertEqual(result['release']['validation_config_id'], str(release.validation_config_id))
                self.assertEqual(result['release']['contact_version_id'], str(release.contact_version_id))
                self.assertEqual(len(result['release']['components']), ReleaseComponent.objects.filter(release=release).count())

    def test_current_release_requires_matching_approval_and_validation(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            now = timezone.now()
            releases = list(ReleaseBundle.objects.select_related('contact_version__approval_config', 'validation_config'))
            self.assertTrue(releases, '当前数据库需要已有发布记录。')
            for release in releases:
                components = list(ReleaseComponent.objects.filter(release=release).select_related('config'))
                qualified = bool(components)
                configurations = [(item.config, item.component_kind) for item in components]
                configurations.append((release.validation_config, 'validation'))
                configurations.append((release.contact_version.approval_config, None))
                for config, kind in configurations:
                    approvals = ConfigApproval.objects.filter(config=config, revoked_at__isnull=True,
                        approved_at__lte=now).filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
                    approved = any(item.evidence_ref.strip() and isinstance(item.effective_scope, dict)
                        and item.effective_scope.get('environment') == release.environment
                        and item.effective_scope.get('config_checksum') == config.checksum for item in approvals)
                    validated = ConfigValidation.objects.filter(config=config, environment_id=release.environment,
                        config_checksum=config.checksum, outcome='pass', revoked_at__isnull=True,
                        executed_at__lte=now).exists()
                    qualified = qualified and config.status == 'enabled' and (kind is None or config.kind == kind)
                    qualified = qualified and compute_checksum(config.payload) == config.checksum and approved and validated
                gate = EnvironmentGate.objects.filter(environment=release.environment).first()
                qualified = qualified and bool(gate and gate.enabled and gate.active_release_id == release.id
                    and gate.publication_epoch == release.publication_epoch)
                if not qualified:
                    with self.subTest(release_id=str(release.id)):
                        self.assertFalse(release_available(release, now), '不完整的发布资格必须阻止当前内容使用。')


if __name__ == '__main__':
    unittest.main(verbosity=2)
