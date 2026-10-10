import os
import unittest

import httpx

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from sources.models import SourceRevision


class EvaluationRequestRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.existing_source_id = SourceRevision.objects.order_by('id').values_list('id', flat=True).first()
        if cls.existing_source_id is None:
            raise RuntimeError('当前检查需要已有来源身份。')

    def test_dataset_list_requires_current_identity(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            response = client.post('/api/v2/support/datasets/search', json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
        self.assertIn('no-store', response.headers['cache-control'])

    def test_dataset_create_requires_current_identity(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            response = client.post('/api/v2/support/datasets', json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
        self.assertIn('no-store', response.headers['cache-control'])

    def test_dataset_operations_require_identity_before_resource_lookup(self):
        path = f'/api/v2/support/datasets/{self.existing_source_id}'
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, endpoint in [('GET', path), ('POST', path + '/partitions'),
                    ('POST', path + '/labels'), ('POST', path + '/freeze')]:
                with self.subTest(method=method, path=endpoint):
                    response = client.request(method, endpoint, **({'json': {}} if method == 'POST' else {}))
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
