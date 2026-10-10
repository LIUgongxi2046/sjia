import json
import os
import unittest
from hashlib import sha256

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

django.setup()

from django.db import connection, transaction
from django.utils import timezone
from django.urls import reverse

from configurations.models import ConfigApproval, ConfigVersion
from contacts.models import ContactVersion
from api.views_publication import contact_management_view, directory_item, publication_release_view
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from family_content.qualification import release_qualification_reason
from requests.controlled import CONTROLLED_OPERATIONS, controlled_target
from tests.verify_v2_migration import ROOT, require, snapshot


class PublicationUIRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atomic = transaction.atomic()
        cls.atomic.__enter__()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cls.before = snapshot(connection.connection)
        cls.counts = {'anonymous_http_requests': 0, 'contact_projections': 0, 'release_projections': 0, 'directory_items': 0}
        cls.sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/api/urls_v2.py', 'backend/api/urls.py', 'backend/api/views_publication.py', 'backend/api/views_family2.py',
            'backend/api/views_family.py', 'backend/requests/controlled.py',
            'backend/api/views_family3.py', 'backend/family_content/policies.py', 'backend/family_content/qualification.py',
            'backend/family_content/alert_detail.py', 'backend/summaries/service.py', 'backend/configurations/schemas.py',
            'backend-design/publication.schema.json', 'backend/tests/verify_publication_ui_runtime.py')}
        cls.schema = json.loads((ROOT / 'backend-design/publication.schema.json').read_text())
        cls.protocol = json.loads((ROOT / 'backend-design/protocol.schema.json').read_text())
        cls.registry = Registry().with_resource('protocol.schema.json', Resource.from_contents(cls.protocol))
        cls.passed = True
        cls.routes = []

    def tearDown(self):
        if self._outcome.result.failures or self._outcome.result.errors:
            type(self).passed = False

    @classmethod
    def tearDownClass(cls):
        require(snapshot(connection.connection) == cls.before, '发布界面检查改变已保存业务资料。')
        report = {'checked_at': timezone.now().isoformat(), 'status': 'PASS' if cls.passed else 'FAIL',
            'scope': 'ACTUAL_PUBLICATION_UI_HTTP', 'counts': cls.counts, 'preserved_tables': len(cls.before),
            'business_writes': 0, 'source_sha256': cls.sources, 'routes': cls.routes, 'positive_publication': 'NOT_RUN', 'visual': 'NOT_RUN'}
        (ROOT / '.planning-work/t51-ui-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        cls.atomic.__exit__(None, None, None)
        print(json.dumps({key: value for key, value in report.items() if key != 'source_sha256'}, ensure_ascii=False))

    def test_publication_reads_reject_anonymous_identity_before_lookup(self):
        config, contact = ConfigVersion.objects.order_by('id').first(), ContactVersion.objects.order_by('id').first()
        self.assertIsNotNone(config)
        self.assertIsNotNone(contact)
        release, approval = ReleaseBundle.objects.order_by('id').first(), ConfigApproval.objects.order_by('id').first()
        self.assertIsNotNone(release)
        self.assertIsNotNone(approval)
        paths = ['/api/v2/support/publication-directory?collection=release&action=publish_config',
            f'/api/v2/support/publication-configs/{config.id}?action=manage_configs',
            f'/api/v2/support/publication-contacts/{contact.id}?action=manage_contacts',
            f'/api/v2/support/publication-releases/{release.id}', f'/api/v2/support/publication-approvals/{approval.id}',
            f'/api/requests/{release.id}?lookup=request_id&operation_id=release_enable&target_key=/api/support/releases/{release.id}/enable']
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10) as client:
            for path in paths:
                response = client.get(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                self.assertIn('no-store', response.headers['cache-control'])
                self.counts['anonymous_http_requests'] += 1
            response = client.post(f'/api/write-intents/{release.id}/cancel', json={})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
            self.assertIn('no-store', response.headers['cache-control'])
            self.counts['anonymous_http_requests'] += 1

    def validate(self, name, value):
        Draft202012Validator({**self.schema, '$ref': f'#/$defs/{name}'},
            registry=self.registry, format_checker=FormatChecker()).validate(value)

    def test_existing_contacts_keep_exact_input_and_unavailable_verification(self):
        for contact in ContactVersion.objects.order_by('id'):
            value = contact_management_view(contact)
            self.validate('ContactDetail', value)
            self.assertEqual(value['contact']['approval_config_id'], str(contact.approval_config_id))
            self.assertEqual(value['contact']['channel_value'], contact.channel_value)
            self.assertEqual(value['verified_at'], contact.verified_at.isoformat() if contact.verified_at else None)
            self.counts['contact_projections'] += 1

    def test_existing_releases_show_current_qualification_without_enabling(self):
        for release in ReleaseBundle.objects.order_by('id'):
            gate = EnvironmentGate.objects.get(environment=release.environment)
            value = publication_release_view(release, gate)
            self.validate('ReleaseDetail', value)
            self.assertEqual(value['resource']['release_id'], str(release.id))
            self.assertEqual(len(value['resource']['release']['components']), ReleaseComponent.objects.filter(release=release).count())
            self.assertEqual(len(set(item['key'] for item in value['readiness'])), 6)
            self.assertEqual(value['qualification_reason'], release_qualification_reason(release, require_active=release.status == 'enabled'))
            if value['qualification_reason'] is not None or gate.active_release_id is not None:
                self.assertFalse(value['may_enable'])
            self.counts['release_projections'] += 1

    def test_saved_directory_metadata_uses_actual_resource_identity(self):
        for collection, model in (('config', ConfigVersion), ('release', ReleaseBundle), ('contact', ContactVersion)):
            for row in model.objects.order_by('id'):
                value = directory_item(collection, row)
                self.validate('DirectoryItem', value)
                self.assertEqual(value['id'], str(row.id))
                self.assertEqual(value['version'], str(row.version))
                self.counts['directory_items'] += 1

    def test_controlled_targets_use_current_routes_and_saved_resource_ids(self):
        identities = {'config_id': ConfigVersion.objects.order_by('id').first().id,
            'approval_id': ConfigApproval.objects.order_by('id').first().id,
            'contact_version_id': ContactVersion.objects.order_by('id').first().id,
            'release_id': ReleaseBundle.objects.order_by('id').first().id}
        for operation in CONTROLLED_OPERATIONS:
            key = 'approval_id' if operation == 'config_approval_revoke' else 'config_id' if operation.startswith('config_') else 'contact_version_id' if operation.startswith('contact_') else 'release_id'
            kwargs = {} if operation.endswith('_create') else {key: identities[key]}
            target = reverse(operation, kwargs=kwargs)
            self.assertEqual(controlled_target(operation, target), kwargs)
            self.routes.append({'operation': operation, 'resource_id': str(identities[key]) if kwargs else None, 'target': target})


if __name__ == '__main__':
    unittest.main(verbosity=2)
