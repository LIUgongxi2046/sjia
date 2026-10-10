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
        create = self.client.post("/api/relation-applications", {"evidence_ref": "demo"}, content_type="application/json")
        self.assertEqual(create.status_code, 201)
        application_id = create.json()["application_id"]
        application_read = self.client.get(f"/api/relation-applications/{application_id}")
        self.assertEqual(application_read.status_code, 200)
        self.assertEqual(application_read.json()["application_id"], application_id)
        relation = self.client.get("/api/relations")
        relation_id = relation.json()["relations"][0]["relation_id"]
        relation_read = self.client.get(f"/api/relations/{relation_id}")
        self.assertEqual(relation_read.status_code, 200)
        self.assertEqual(relation_read.json()["patient_id"], str(self.patient.id))

    def test_record_history_and_correct(self):
        record = post_record(self.client, self.patient, {"kind": "observation", "capture_method": "manual", "fields": [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}]})
        self.assertEqual(record.status_code, 201)
        record_id = record.json()["record_id"]
        history = self.client.get(f"/api/records/{record_id}/history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()["versions"]), 1)
        correct = self.client.post(f"/api/records/{record_id}/corrections", {"expected_version": 1, "reason": "更正", "fields": [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "37.8", "raw_unit": "C", "sample_time_origin": "observation"}]}, content_type="application/json")
        self.assertEqual(correct.status_code, 201)
        self.assertEqual(correct.json()["version"], 2)

    def test_request_recent(self):
        response = self.client.get(f"/api/patients/{self.patient.id}/requests")
        self.assertEqual(response.status_code, 200)
        self.assertIn("requests", response.json())

    def test_grants_create_and_revoke(self):
        grant = self.client.post("/api/support/access-grants", {"patient_id": str(self.patient.id), "principal_id": str(self.principal.id), "action": "read_raw", "field_codes": ["temp"], "record_scope": "all", "purpose": "family_observation"}, content_type="application/json")
        self.assertEqual(grant.status_code, 201)
        grant_id = grant.json()["grant_id"]
        revoke = self.client.post(f"/api/support/access-grants/{grant_id}/revoke", {}, content_type="application/json")
        self.assertEqual(revoke.status_code, 200)
        role_grant = self.client.post("/api/support/role-grants", {"principal_id": str(self.principal.id), "action": "verify_field"}, content_type="application/json")
        self.assertEqual(role_grant.status_code, 201)
        role_grant_id = role_grant.json()["role_grant_id"]
        role_revoke = self.client.post(f"/api/support/role-grants/{role_grant_id}/revoke", {}, content_type="application/json")
        self.assertEqual(role_revoke.status_code, 200)

    def test_verification_request_and_read(self):
        record = post_record(self.client, self.patient, {"kind": "observation", "capture_method": "manual", "fields": [{"field_code": "temp", "value_type": "decimal", "value_state": "known", "raw_numeric": "38.0", "raw_unit": "C", "sample_time_origin": "observation"}]})
        self.assertEqual(record.status_code, 201)
        record_id = record.json()["record_id"]
        from records.models import RecordVersion

        record_version_id = RecordVersion.objects.filter(record_id=record_id, patient=self.patient).first().id
        request_response = self.client.post("/api/support/verification-requests", {"patient_id": str(self.patient.id), "record_version_id": str(record_version_id), "purpose": "assessment"}, content_type="application/json")
        self.assertEqual(request_response.status_code, 201)
        verification_request_id = request_response.json()["verification_request_id"]
        read_response = self.client.get(f"/api/support/verification-requests/{verification_request_id}")
        self.assertEqual(read_response.status_code, 200)

    def test_config_lifecycle_and_validation(self):
        create = self.client.post("/api/support/configs", {"kind": "rule", "config_key": "r-flow4", "schema_version": "rule-1", "payload": {"x": 1}}, content_type="application/json")
        self.assertEqual(create.status_code, 201)
        config_id = create.json()["config_id"]
        read_response = self.client.get(f"/api/support/configs/{config_id}")
        self.assertEqual(read_response.status_code, 200)
        submit = self.client.post(f"/api/support/configs/{config_id}/submit", {}, content_type="application/json")
        self.assertEqual(submit.status_code, 200)
        approve = self.client.post(f"/api/support/configs/{config_id}/approvals", {"approval_role": "clinical", "evidence_ref": "demo", "effective_scope": {}}, content_type="application/json")
        self.assertEqual(approve.status_code, 200)
        validate = self.client.post(f"/api/support/configs/{config_id}/validations", {"artifact_sha256": "a" * 64, "evidence_ref": "demo", "evidence_sha256": "a" * 64, "outcome": "pass"}, content_type="application/json")
        self.assertEqual(validate.status_code, 201)
        enable = self.client.post(f"/api/support/configs/{config_id}/enable", {}, content_type="application/json")
        self.assertEqual(enable.status_code, 200)

    def test_contact_disable_and_runtime(self):
        contact = self.client.post("/api/support/contacts", {"contact_name": "监护人", "purpose": "alert", "channel": "phone", "channel_value": "13800000000"}, content_type="application/json")
        self.assertEqual(contact.status_code, 201)
        contact_id = contact.json()["contact_version_id"]
        disable = self.client.post(f"/api/support/contacts/{contact_id}/disable", {}, content_type="application/json")
        self.assertEqual(disable.status_code, 200)
        runtime_config = self.client.post("/api/support/configs", {"kind": "provider", "config_key": "runtime-flow4", "schema_version": "backend-runtime-1", "payload": {"x": 1}}, content_type="application/json")
        runtime_config_id = runtime_config.json()["config_id"]
        runtime = self.client.post("/api/support/environments/test/runtime", {"runtime_config_id": runtime_config_id}, content_type="application/json")
        self.assertEqual(runtime.status_code, 200)

    def test_source_revoke_and_retention_activate(self):
        from sources.models import SourceRevision
        from retention.models import RetentionPolicy

        revision = SourceRevision.objects.first()
        revoke = self.client.post(f"/api/support/source-revisions/{revision.id}/revoke", {}, content_type="application/json")
        self.assertEqual(revoke.status_code, 200)
        policy = RetentionPolicy.objects.filter(status="active").first()
        activate = self.client.post(f"/api/support/retention-policies/{policy.id}/activate", {}, content_type="application/json")
        self.assertEqual(activate.status_code, 200)

    def test_use_authorization_revoke(self):
        from sources.models import DataUseAuthorization

        authorization = DataUseAuthorization.objects.filter(patient=self.patient).first()
        response = self.client.post(f"/api/support/use-authorizations/{authorization.id}/revoke", {}, content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_case_search_and_import(self):
        import_response = self.client.post(f"/api/support/patients/{self.patient.id}/case/import", {}, content_type="application/json")
        self.assertEqual(import_response.status_code, 201)
        search = self.client.post(f"/api/support/patients/{self.patient.id}/case/search", {}, content_type="application/json")
        self.assertEqual(search.status_code, 200)
        self.assertFalse(search.json()["clinical_rule_eligible"])

    def test_alert_evaluation_create_and_read(self):
        create = self.client.post(f"/api/support/patients/{self.patient.id}/alert-evaluations", {"mode": "current"}, content_type="application/json")
        self.assertEqual(create.status_code, 202)
        evaluation_id = create.json()["evaluation_id"]
        read = self.client.get(f"/api/support/alert-evaluations/{evaluation_id}")
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["mode"], "current")

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

    def test_in_app_message_stats(self):
        from alerting.models import AlertEvent, AlertRecipient
        from configurations.models import ConfigVersion
        from contacts.models import ContactVersion
        from family_content.models import ReleaseBundle
        from relations.models import Relation
        from sources.models import SourceRevision

        evaluation_response = self.client.post(f"/api/support/patients/{self.patient.id}/alert-evaluations", {"mode": "current"}, content_type="application/json")
        evaluation_id = evaluation_response.json()["evaluation_id"]
        config = ConfigVersion.objects.filter(kind="content").first()
        revision = SourceRevision.objects.first()
        relation = Relation.objects.filter(patient=self.patient).first()
        contact = ContactVersion.objects.create(
            contact_id=uuid.uuid4(), version=1, contact_name="监护人", purpose="alert",
            channel="in_app", channel_value="", owner=self.principal,
            source_revision=revision, approval_config=config,
            verified_at=timezone.now(), updated_at=timezone.now(),
            valid_until=timezone.now() + timedelta(days=30), status=ContactVersion.Status.ACTIVE,
        )
        release = ReleaseBundle.objects.create(
            environment="test", version=1, status=ReleaseBundle.Status.ENABLED,
            contact_version=contact, validation_config=config,
        )
        event = AlertEvent.objects.create(
            patient=self.patient, evaluation_id=uuid.UUID(evaluation_id), release=release,
            trigger_key="inapp", trigger_policy=config, input_snapshot={},
            validity=AlertEvent.Validity.VALID, created_at=timezone.now(),
        )
        AlertRecipient.objects.create(
            patient_id=self.patient.id, alert_id=event.id, relation_id=relation.id,
            channel="in_app", delivery_status=AlertRecipient.DeliveryStatus.DELIVERED,
        )
        stats = self.client.get(f"/api/patients/{self.patient.id}/alerts/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["unread"], 1)
        alerts = self.client.get(f"/api/patients/{self.patient.id}/alerts")
        self.assertIsNone(alerts.json()["alerts"][0]["read_at"])
        mark = self.client.post(f"/api/alerts/{event.id}/read", {}, content_type="application/json")
        self.assertEqual(mark.status_code, 200)
        self.assertIsNotNone(mark.json()["read_at"])
        stats_after = self.client.get(f"/api/patients/{self.patient.id}/alerts/stats")
        self.assertEqual(stats_after.json()["unread"], 0)

    def test_provider_callback(self):
        from alerting.models import AlertEvent, AlertRecipient
        from configurations.models import ConfigVersion
        from contacts.models import ContactVersion
        from family_content.models import ReleaseBundle
        from relations.models import Relation
        from sources.models import SourceRevision

        evaluation_response = self.client.post(f"/api/support/patients/{self.patient.id}/alert-evaluations", {"mode": "current"}, content_type="application/json")
        self.assertEqual(evaluation_response.status_code, 202)
        evaluation_id = evaluation_response.json()["evaluation_id"]
        config = ConfigVersion.objects.filter(kind="content").first()
        revision = SourceRevision.objects.first()
        relation = Relation.objects.filter(patient=self.patient).first()
        contact = ContactVersion.objects.create(
            contact_id=uuid.uuid4(), version=1, contact_name="监护人", purpose="alert",
            channel="phone", channel_value="13800000000", owner=self.principal,
            source_revision=revision, approval_config=config,
            verified_at=timezone.now(), updated_at=timezone.now(),
            valid_until=timezone.now() + timedelta(days=30), status=ContactVersion.Status.ACTIVE,
        )
        release = ReleaseBundle.objects.create(
            environment="test", version=1, status=ReleaseBundle.Status.ENABLED,
            contact_version=contact, validation_config=config,
        )
        event = AlertEvent.objects.create(
            patient=self.patient, evaluation_id=uuid.UUID(evaluation_id), release=release,
            trigger_key="flow4", trigger_policy=config, input_snapshot={},
            validity=AlertEvent.Validity.VALID, created_at=timezone.now(),
        )
        recipient = AlertRecipient.objects.create(
            patient_id=self.patient.id, alert_id=event.id, relation_id=relation.id,
            channel="phone", delivery_status=AlertRecipient.DeliveryStatus.NOT_SENT,
        )
        attempt = authorize_attempt(
            patient_id=self.patient.id, recipient_id=recipient.id,
            provider_idempotency_key="flow4", permit_expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self.client.post("/api/integrations/local-sms/receipts", {"attempt_id": str(attempt.send_permit_id), "provider_event_id": "evt-1", "status": "accepted", "evidence_sha256": "a" * 64}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
