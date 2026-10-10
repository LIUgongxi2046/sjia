"""T09 身份登录、关联申请、核验与撤销逻辑验证。

真实 IdentityProvider 与获准账号由 T29/T30 依据 D02 验证；本文件验证服务层逻辑。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T09 --keepdb
"""
from datetime import datetime, timezone

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from identity.models import Principal, PrincipalIdentity
from identity.service import disable_account, resolve_principal
from identity.session import bootstrap_csrf, clear_session, establish_session, read_session_principal
from relations.models import Relation, RelationApplication
from relations.service import RelationRejected, create_application, decide_application, revoke_relation, withdraw_application


def _now():
    return datetime.now(timezone.utc)


class IdentityServiceTest(TestCase):
    def test_resolve_principal_creates_then_reuses(self):
        p1 = resolve_principal(provider="idp", provider_subject="sub-1")
        self.assertEqual(PrincipalIdentity.objects.count(), 1)
        p2 = resolve_principal(provider="idp", provider_subject="sub-1")
        self.assertEqual(p1.id, p2.id)
        self.assertEqual(PrincipalIdentity.objects.count(), 1)

    def test_disable_account_increments_epoch(self):
        principal = resolve_principal(provider="idp", provider_subject="sub-2")
        before = principal.auth_epoch
        disable_account(principal)
        principal.refresh_from_db()
        self.assertEqual(principal.account_status, "disabled")
        self.assertEqual(principal.auth_epoch, before + 1)


class RelationApplicationTest(TestCase):
    def setUp(self):
        now = _now()
        self.applicant = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.checker = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        from identity.models import Patient

        self.patient = Patient.objects.create(display_id="P-T09", status="active", created_at=now)

    def test_create_and_withdraw_application(self):
        application = create_application(applicant=self.applicant, evidence_ref="proof", patient=self.patient)
        self.assertEqual(application.status, "pending")
        self.assertEqual(application.version, 1)
        withdraw_application(application=application, expected_version=1)
        application.refresh_from_db()
        self.assertEqual(application.status, "withdrawn")
        self.assertEqual(application.version, 2)

    def test_withdraw_version_conflict(self):
        application = create_application(applicant=self.applicant, evidence_ref="proof", patient=self.patient)
        with self.assertRaises(RelationRejected):
            withdraw_application(application=application, expected_version=99)

    def test_decide_approve_creates_relation_and_grant(self):
        from authorization.models import AccessGrant

        application = create_application(applicant=self.applicant, evidence_ref="proof", patient=self.patient)
        relation = decide_application(
            application=application, decision="approved", checked_by=self.checker, expected_version=1,
            action="read_raw", field_codes=["temp"], purpose="family_observation",
        )
        self.assertIsNotNone(relation)
        self.assertEqual(Relation.objects.filter(guardian=self.applicant, patient=self.patient, status="active").count(), 1)
        self.assertEqual(AccessGrant.objects.filter(principal=self.applicant, patient=self.patient, action="read_raw").count(), 1)

    def test_decide_reject_no_relation(self):
        application = create_application(applicant=self.applicant, evidence_ref="proof", patient=self.patient)
        relation = decide_application(application=application, decision="rejected", checked_by=self.checker, expected_version=1)
        self.assertIsNone(relation)
        self.assertEqual(Relation.objects.count(), 0)

    def test_revoke_relation(self):
        application = create_application(applicant=self.applicant, evidence_ref="proof", patient=self.patient)
        relation = decide_application(application=application, decision="approved", checked_by=self.checker, expected_version=1, action="read_raw", field_codes=["temp"])
        revoke_relation(relation=relation, reason="revoked by guardian", revoked_by=self.checker)
        relation.refresh_from_db()
        self.assertEqual(relation.status, "revoked")
        self.assertEqual(relation.auth_epoch, 1)

    def test_revoke_requires_reason(self):
        application = create_application(applicant=self.applicant, evidence_ref="proof", patient=self.patient)
        relation = decide_application(application=application, decision="approved", checked_by=self.checker, expected_version=1)
        with self.assertRaises(RelationRejected):
            revoke_relation(relation=relation, reason="", revoked_by=self.checker)


class SessionBootstrapTest(TestCase):
    def _request(self):
        factory = RequestFactory()
        request = factory.get("/auth/bootstrap")
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()
        return request

    def test_bootstrap_and_session_lifecycle(self):
        request = self._request()
        token = bootstrap_csrf(request)
        self.assertTrue(token)
        establish_session(request, principal_id="00000000-0000-0000-0000-000000000001", auth_epoch=3, audience="family")
        principal_id, epoch, audience = read_session_principal(request)
        self.assertEqual(principal_id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(epoch, 3)
        self.assertEqual(audience, "family")
        clear_session(request)
        principal_id, epoch, audience = read_session_principal(request)
        self.assertIsNone(principal_id)
