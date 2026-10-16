"""本地端到端流程集成测试四：权限、核对、配置、病例、评估与回执（虚拟数据）。"""
import uuid
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from identity.models import Patient, Principal
from notifications.models import NotificationAttempt
from notifications.service import authorize_attempt
from tests.integration.helpers import post_record


class EndpointCoverageIntegrationTest(TestCase):
    def setUp(self):
        call_command("seed_demo", verbosity=0)
        self.principal = Principal.objects.filter(account_status="active").first()
        self.patient = Patient.objects.filter(display_id="demo-child").first()
        from tests.synthetic_fixtures import qualify_demo
        qualify_demo(self, self.principal, self.patient)
        session = self.client.session
        session["_sjia_principal_id"] = str(self.principal.id)
        session["_sjia_auth_epoch"] = self.principal.auth_epoch
        session["_sjia_audience"] = "family"
        session.save()

    def test_auth_flow_and_capabilities(self):
        complete = self.client.post('/api/auth/login', {'username': 'guardian-1', 'password': 'demo-pass-1'}, content_type='application/json')
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["principal_id"], str(self.principal.id))
        capabilities = self.client.get("/api/capabilities")
        self.assertEqual(capabilities.status_code, 200)
        self.assertIn("record:write", capabilities.json()["capabilities"])

    def test_application_and_relation_read(self):
        from tests.integration.helpers import post_intended
        create = post_intended(self.client, '/api/relation-applications', 'application_create',
            {'evidence_ref': 'synthetic application only', 'note': None, 'requested_access': 'read_write'})
        self.assertEqual(create.status_code, 201, create.content)
        application_id = create.json()['resource_id']
        application_read = self.client.get(f'/api/relation-applications/{application_id}')
        self.assertEqual(application_read.status_code, 200, application_read.content)
        self.assertEqual(application_read.json()['application_id'], application_id)
        relation_id = self.client.get('/api/relations').json()['relations'][0]['relation_id']
        relation_read = self.client.get(f'/api/relations/{relation_id}')
        self.assertEqual(relation_read.status_code, 200)
        self.assertEqual(relation_read.json()['patient_id'], str(self.patient.id))


    def test_record_history_and_correct(self):
        record = post_record(self.client, self.patient, {"kind": "observation", "capture_method": "manual", "fields": [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}]})
        self.assertEqual(record.status_code, 201)
        record_id = record.json()["record_id"]
        history = self.client.get(f"/api/records/{record_id}/history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["versions"]), 1)
        from tests.integration.helpers import correction_fields, post_intended
        correct = post_intended(self.client, f"/api/records/{record_id}/corrections", 'record_correct',
            {"expected_version": 1, "reason": "更正", "fields": correction_fields(self.patient, record_id, {"temp": {"raw_numeric": "37.8"}})},
            schema_version='back-json-1')
        self.assertEqual(correct.status_code, 201, correct.content)
        self.assertEqual(correct.json()["version"], 2)

    def test_request_recent(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/requests")
        self.assertEqual(response.status_code, 200)
        self.assertIn("requests", response.json())

    def test_grants_create_and_revoke(self):
        from tests.integration.helpers import support_session
        from relations.models import Relation
        support_session(self.client)
        relation = Relation.objects.get(patient=self.patient, guardian=self.principal)
        grant = self.client.post('/api/support/access-grants', {'patient_id': str(self.patient.id),
            'principal_id': str(self.principal.id), 'relation_id': str(relation.id), 'action': 'read_raw',
            'field_codes': ['temp'], 'record_scope': 'all', 'assigned_record_id': None,
            'purpose': 'family_observation', 'evidence_ref': 'synthetic-test-only',
            'valid_from': timezone.now().isoformat(), 'valid_until': None}, content_type='application/json')
        self.assertEqual(grant.status_code, 201, grant.content)
        grant_id = grant.json()['grant_id']
        revoke = self.client.post(f'/api/support/access-grants/{grant_id}/revoke', {}, content_type='application/json')
        self.assertEqual(revoke.status_code, 200, revoke.content)
        self.assertIsNotNone(revoke.json()['revoked_at'])
        role = self.client.post('/api/support/role-grants', {'principal_id': str(self.principal.id),
            'environment': 'test', 'action': 'verify_field', 'evidence_ref': 'synthetic-test-only',
            'valid_from': timezone.now().isoformat(), 'valid_until': None}, content_type='application/json')
        self.assertEqual(role.status_code, 201, role.content)
        role_id = role.json()['role_grant_id']
        revoked = self.client.post(f'/api/support/role-grants/{role_id}/revoke', {'reason': 'synthetic revocation'}, content_type='application/json')
        self.assertEqual(revoked.status_code, 200, revoked.content)
        self.assertIsNotNone(revoked.json()['revoked_at'])


    def test_legacy_verification_requests_require_v2(self):
        from tests.integration.helpers import support_session
        support_session(self.client)
        response = self.client.post('/api/support/verification-requests', {}, content_type='application/json')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'VERIFICATION_API_VERSION_REQUIRED')
        read = self.client.get(f'/api/support/verification-requests/{uuid.uuid4()}')
        self.assertEqual(read.status_code, 422)
        self.assertEqual(read.json()['code'], 'VERIFICATION_API_VERSION_REQUIRED')


    def test_config_lifecycle_and_validation(self):
        from configurations.evidence import deployed_artifact_checksum
        from tests.integration.helpers import create_and_approve_synthetic_config, post_intended
        config = create_and_approve_synthetic_config(self, 'synthetic-flow4')
        artifact = deployed_artifact_checksum(config)
        executed_at = timezone.now()
        report = {'schema_version': 'config-validation-1', 'config_id': str(config.id),
            'config_checksum': config.checksum, 'environment': 'test', 'artifact_sha256': artifact,
            'dataset_revision_id': str(self.synthetic.source.id), 'outcome': 'pass',
            'executed_by': str(self.principal.id), 'executed_at': executed_at.isoformat()}
        revision, reference = self.synthetic.evidence(report, purposes=['configuration_validation'])
        response = post_intended(self.client, f'/api/support/configs/{config.id}/validations', 'config_validate',
            {'config_checksum': config.checksum, 'environment': 'test', 'artifact_sha256': artifact,
                'dataset_revision_id': str(self.synthetic.source.id), 'evidence_ref': reference,
                'evidence_sha256': revision.checksum})
        self.assertEqual(response.status_code, 201, response.content)
        self.synthetic.gate.refresh_from_db()
        enabled = post_intended(self.client, f'/api/support/configs/{config.id}/enable', 'config_enable',
            {'expected_gate_version': str(self.synthetic.gate.version), 'reason': 'synthetic enable'})
        self.assertEqual(enabled.status_code, 200, enabled.content)
        config.refresh_from_db()
        self.assertEqual(config.status, 'enabled')


    def test_contact_disable_and_runtime(self):
        from authorization.models import ServiceRoleGrant
        from tests.integration.helpers import post_intended, support_session
        support_session(self.client)
        ServiceRoleGrant.objects.create(principal=self.principal, environment=self.synthetic.gate,
            action='manage_contacts', granted_by=self.principal, evidence_ref='synthetic-test-only', valid_from=timezone.now())
        payload = {'contact_id': str(uuid.uuid4()), 'contact_name': 'SYNTHETIC CONTACT', 'purpose': 'software_test',
            'channel': 'in_app', 'channel_value': 'synthetic-only', 'source_revision_id': str(self.synthetic.source.id),
            'valid_until': (timezone.now() + timedelta(days=1)).isoformat()}
        config = self.synthetic.configuration('content', {**payload, 'owner_id': str(self.principal.id),
            'environment': 'test'}, schema_version='contact-1')
        created = post_intended(self.client, '/api/support/contacts', 'contact_create',
            {**payload, 'approval_config_id': str(config.id)})
        self.assertEqual(created.status_code, 201, created.content)
        contact_id = created.json()['resource_id']
        disabled = post_intended(self.client, f'/api/support/contacts/{contact_id}/disable', 'contact_disable',
            {'expected_state': 'draft', 'expected_version': '1', 'reason': 'synthetic disable'})
        self.assertEqual(disabled.status_code, 200, disabled.content)
        self.synthetic.gate.refresh_from_db()
        runtime = self.client.post('/api/support/environments/test/runtime',
            {'runtime_config_id': str(self.synthetic.gate.runtime_config_id)}, content_type='application/json')
        self.assertEqual(runtime.status_code, 200, runtime.content)
        self.assertEqual(runtime.json()['runtime_config_id'], str(self.synthetic.gate.runtime_config_id))


    def test_source_revoke_and_retention_activate(self):
        from sources.models import SourceRevision
        from retention.models import RetentionPolicy
        from tests.integration.helpers import post_intended, support_session
        support_session(self.client)
        revision = SourceRevision.objects.get(source__source_key='demo-source')
        revoked = post_intended(self.client, f'/api/support/source-revisions/{revision.id}/revoke', 'source_revision_revoke',
            {'expected_state': 'approved', 'reason': 'synthetic revoke'})
        self.assertEqual(revoked.status_code, 200, revoked.content)
        revision.refresh_from_db()
        self.assertEqual(revision.status, 'revoked')
        policy = RetentionPolicy.objects.create(policy_key='synthetic-flow4-new-retention', version=1,
            data_class='software_test', rules={'retention_days': 1, 'disposition': 'retain'}, schema_version='retention-1',
            status='draft', evidence_ref=self.synthetic.reference)
        activated = post_intended(self.client, f'/api/support/retention-policies/{policy.id}/activate', 'retention_policy_activate',
            {'expected_state': 'draft', 'expected_version': '1', 'reason': 'synthetic activate'})
        self.assertEqual(activated.status_code, 200, activated.content)
        policy.refresh_from_db()
        self.assertEqual(policy.status, 'active')


    def test_use_authorization_revoke(self):
        from tests.integration.helpers import support_session
        support_session(self.client)
        from sources.models import DataUseAuthorization

        authorization = DataUseAuthorization.objects.filter(patient=self.patient).first()
        response = self.client.post(f"/api/support/use-authorizations/{authorization.id}/revoke", {}, content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_family_cannot_invoke_support_case_import(self):
        response = self.client.post(f'/api/support/patients/{self.patient.id}/case/import', {}, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'SUPPORT_AUDIENCE_REQUIRED')


    def test_legacy_alert_evaluation_create_requires_v2(self):
        response = self.client.post(f'/api/support/patients/{self.patient.id}/alert-evaluations', {'mode': 'current'}, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'API_V2_REQUIRED')
        self.assertNotIn('evaluation_id', response.json())


    def test_file_group_revise_and_recognition(self):
        group = self.client.post(f"/api/patients/{self.patient.id}/uploads", {}, content_type="application/json")
        self.assertEqual(group.status_code, 201)
        group_id = group.json()["group_id"]
        revise = self.client.post(f"/api/uploads/{group_id}/revisions", {}, content_type="application/json")
        self.assertEqual(revise.status_code, 201)
        self.assertEqual(revise.json()["version"], "2")
        recognition = self.client.post("/api/recognition-jobs", {"file_group_id": group_id, "file_group_version": "1"}, content_type="application/json")
        self.assertEqual(recognition.status_code, 409)
        self.assertEqual(recognition.json()['code'], 'FILE_GROUP_NOT_AVAILABLE')

    def test_self_built_register_and_login(self):
        register = self.client.post("/api/auth/register", {"username": "guardian-2", "password": "password123"}, content_type="application/json")
        self.assertEqual(register.status_code, 201)
        principal_id = register.json()["principal_id"]
        self.client.post("/api/session/logout", {}, content_type="application/json")
        login = self.client.post("/api/auth/login", {"username": "guardian-2", "password": "password123"}, content_type="application/json")
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["principal_id"], principal_id)
        self.client.post("/api/session/logout", {}, content_type="application/json")
        bad = self.client.post("/api/auth/login", {"username": "guardian-2", "password": "wrong-password"}, content_type="application/json")
        self.assertEqual(bad.status_code, 401)
        dup = self.client.post("/api/auth/register", {"username": "guardian-2", "password": "password456"}, content_type="application/json")
        self.assertEqual(dup.status_code, 400)

    def test_approved_content_seeded(self):
        from configurations.models import ConfigVersion
        from dictionaries.models import DictionaryEntry, UnitMapping
        from retention.models import RetentionPolicy

        self.assertGreaterEqual(DictionaryEntry.objects.filter(concept_code__in=["temp", "platelet", "ferritin", "triglycerides", "fibrinogen", "cns"]).count(), 6)
        self.assertTrue(UnitMapping.objects.filter(concept_code="triglycerides", raw_unit="mmol/L", normalized_unit="mg/dL").exists())
        self.assertTrue(RetentionPolicy.objects.filter(policy_key="retention-observation", status="active").exists())
        visibility = ConfigVersion.objects.filter(config_key="visibility-family", kind="visibility").first()
        self.assertIsNotNone(visibility)
        self.assertFalse(visibility.payload["visible_score"])
        self.assertEqual(visibility.payload["alert_disclaimer"], "本提示用于预警，不构成诊断，请及时就医。")

    def test_legacy_alert_evaluation_read_requires_v2(self):
        response = self.client.get(f'/api/support/alert-evaluations/{uuid.uuid4()}')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'API_V2_REQUIRED')


    def test_unconfigured_external_callback_cannot_create_receipts(self):
        from notifications.models import NotificationAttempt, NotificationReceipt
        before = (NotificationAttempt.objects.count(), NotificationReceipt.objects.count())
        response = self.client.post('/api/integrations/local-sms/receipts',
            {'provider_event_id': 'synthetic-unapproved'}, content_type='application/json')
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'NOTIFICATION_PROVIDER_UNAVAILABLE')
        self.assertEqual((NotificationAttempt.objects.count(), NotificationReceipt.objects.count()), before)
