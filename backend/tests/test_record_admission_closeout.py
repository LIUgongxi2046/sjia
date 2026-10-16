"""Synthetic current retention/source admission, no created clinical approvals."""
from uuid import uuid4
from django.test import TestCase
from django.conf import settings
from django.utils import timezone
from configurations.service import ConfigRejected
from identity.models import Patient, Principal
from records.admission import checked_retention, family_record_admission
from records.service import RecordRejected
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tests.synthetic_fixtures import SyntheticEvidence


class RecordAdmissionTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-ADMISSION', status='active', created_at=now)
        self.fixture = SyntheticEvidence(self, self.actor)
        self.policy = RetentionPolicy.objects.create(policy_key=f'synthetic-record-{uuid4()}', version=1,
            data_class='observation', rules={'retention_days': None, 'disposition': 'retain'},
            schema_version='retention-1', status='active', approved_by=self.actor,
            approved_at=now, evidence_ref=self.fixture.reference)
        self.use = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.fixture.source,
            purpose='family_observation', environment=settings.SJIA_ENVIRONMENT, evidence_ref='synthetic purpose evidence',
            approved_by=self.actor, approved_at=now)

    def test_only_qualified_policy_and_exact_current_source_are_adopted(self):
        policy, use = family_record_admission(self.patient, 'observation')
        self.assertEqual((policy.id, use.id), (self.policy.id, self.use.id))

    def test_empty_rules_or_unapproved_policy_cannot_enable_record(self):
        for fields in ({'rules': {}}, {'approved_by': None}, {'schema_version': 'unknown'}):
            old = {key: getattr(self.policy, key) for key in fields}
            for key, value in fields.items(): setattr(self.policy, key, value)
            self.policy.save()
            with self.subTest(fields=fields), self.assertRaises(RecordRejected):
                family_record_admission(self.patient, 'observation')
            for key, value in old.items(): setattr(self.policy, key, value)
            self.policy.save()

    def test_ambiguous_active_policies_do_not_choose_arbitrary_latest(self):
        self.policy.pk = uuid4(); self.policy.policy_key += '-second'; self.policy.save(force_insert=True)
        with self.assertRaisesRegex(RecordRejected, 'RECORD_RETENTION_UNAVAILABLE'):
            family_record_admission(self.patient, 'observation')

    def test_ambiguous_purpose_authorizations_do_not_choose_arbitrary_source(self):
        self.use.pk = uuid4(); self.use.save(force_insert=True)
        with self.assertRaisesRegex(RecordRejected, 'RECORD_SOURCE_AUTHORIZATION_UNAVAILABLE'):
            family_record_admission(self.patient, 'observation')

    def test_source_allowed_use_and_retention_evidence_revocation_are_enforced(self):
        self.fixture.source.allowed_use = ['configuration_approval']
        self.fixture.source.save(update_fields=['allowed_use'])
        with self.assertRaisesRegex(RecordRejected, 'RECORD_SOURCE_AUTHORIZATION_UNAVAILABLE'):
            family_record_admission(self.patient, 'observation')
        self.fixture.source.status = 'revoked'
        self.fixture.source.save(update_fields=['status'])
        with self.assertRaises(ConfigRejected):
            checked_retention(self.policy, data_classes=('observation',))
