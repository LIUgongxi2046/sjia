import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django
import httpx

django.setup()

from django.db import connection
from django.utils import timezone

from authorization.policy import Denied, evaluate
from core.management.commands.seed_simulation import require_simulation
from identity.models import Patient, PrincipalCredential


class SimulationPermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_simulation()
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
                raise RuntimeError('权限检查数据库身份不匹配')
        cls.fixture = json.loads((ROOT / '.simulation-work/t29-business-fixture.json').read_text())
        cls.patient = Patient.objects.get(id=cls.fixture['patient_id'])
        cls.owner = PrincipalCredential.objects.get(username='simulation-family-b').principal
        cls.other = PrincipalCredential.objects.get(username='simulation-family').principal
        cls.responses = []

    @classmethod
    def tearDownClass(cls):
        report = {'mode': 'SIMULATION_ONLY', 'checked_at': timezone.now().isoformat(),
            'patient_id': str(cls.patient.id), 'checks': cls.responses,
            'scope': '真实HTTP身份、账号隔离、字段和会话拒绝；完整业务组合及撤权竞争另行运行',
            'full_business_regression': 'NOT_RUN', 'clinical_approval': 'NOT_RUN', 'visual': 'NOT_RUN',
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        target = ROOT / '.planning-work/coordination/root/t56-permissions.json'
        text = json.dumps(report, ensure_ascii=False, indent=2)
        target.write_text(text)
        if json.loads(target.read_text()) != report:
            raise RuntimeError('权限报告保存重读不一致')

    def response(self, client, path, expected, code=None):
        result = client.get(path)
        self.responses.append({'path': path, 'status': result.status_code,
            'code': result.json().get('code') if result.headers.get('content-type', '').startswith('application/json') else None,
            'body_sha256': hashlib.sha256(result.content).hexdigest(), 'expected_status': expected})
        self.assertEqual(result.status_code, expected, (path, result.text[:300]))
        self.assertIn('no-store', result.headers['cache-control'])
        if code is not None:
            self.assertEqual(result.json()['code'], code)
        return result

    def login(self, client, username):
        bootstrap = client.get('/api/auth/bootstrap')
        self.assertEqual(bootstrap.status_code, 200)
        client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
        result = client.post('/api/auth/login', json={'username': username,
            'password': os.environ['SJIA_SIMULATION_PASSWORD']})
        self.assertEqual(result.status_code, 200, result.text[:300])
        client.headers['X-CSRFToken'] = result.json()['csrf_token']

    def paths(self):
        record = self.fixture['records'][1]
        return [f'/api/records/{record["record_id"]}',
            f'/api/record-versions/{record["record_version_id"]}',
            f'/api/files/{self.fixture["file_ids"][1]}/content',
            f'/api/patients/{self.patient.id}/drafts',
            f'/api/patients/{self.patient.id}/summaries',
            f'/api/v2/patients/{self.patient.id}/notification-records']

    def test_anonymous_access_requires_session(self):
        with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
            for path in self.paths():
                self.response(client, path, 401, 'AUTH_REQUIRED')

    def test_family_account_cannot_read_another_patient(self):
        with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
            self.login(client, 'simulation-family')
            for path in self.paths():
                self.response(client, path, 403)

    def test_owner_record_original_and_logout(self):
        with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
            self.login(client, 'simulation-family-b')
            for path in self.paths()[:2]:
                self.response(client, path, 200)
            original = self.response(client, self.paths()[2], 200)
            self.assertIn(b'SIMULATION ONLY', original.content)
            self.assertIn('独立模拟就诊2'.encode('utf-8'), original.content)
            for path in self.paths()[3:]:
                self.response(client, path, 200)
            denied = f'/api/v2/support/patients/{self.patient.id}/assessment-options?mode=current&known_at={timezone.now().isoformat()}'
            self.response(client, denied, 403, 'SUPPORT_AUDIENCE_REQUIRED')
            logout = client.post('/api/session/logout')
            self.assertEqual(logout.status_code, 200)
            for path in self.paths():
                self.response(client, path, 401, 'AUTH_REQUIRED')

    def test_field_and_session_policy_boundaries(self):
        decision = evaluate(principal=self.owner, patient=self.patient, action='read_raw',
            purpose='family_observation', environment='simulation', session_auth_epoch=self.owner.auth_epoch,
            field_codes=['ferritin'])
        self.assertTrue(decision.allowed)
        with self.assertRaises(Denied) as missing_field:
            evaluate(principal=self.owner, patient=self.patient, action='read_raw', purpose='family_observation',
                environment='simulation', session_auth_epoch=self.owner.auth_epoch,
                field_codes=['SIMULATION_UNGRANTED_FIELD'])
        self.assertEqual(missing_field.exception.reason_code, 'CASE_ACTION_DENIED')
        with self.assertRaises(Denied) as invalid_session:
            evaluate(principal=self.owner, patient=self.patient, action='read_raw', purpose='family_observation',
                environment='simulation', session_auth_epoch=self.owner.auth_epoch + 1)
        self.assertEqual(invalid_session.exception.reason_code, 'AUTH_EPOCH_MISMATCH')
        with self.assertRaises(Denied) as invalid_purpose:
            evaluate(principal=self.owner, patient=self.patient, action='read_raw', purpose='SIMULATION_UNGRANTED_PURPOSE',
                environment='simulation', session_auth_epoch=self.owner.auth_epoch)
        self.assertEqual(invalid_purpose.exception.reason_code, 'CASE_ACTION_DENIED')


if __name__ == '__main__':
    unittest.main(verbosity=2)
