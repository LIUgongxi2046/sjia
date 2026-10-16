"""Synthetic-only regressions. No clinical approval, provider call or production write.

Release qualification is intentionally mocked in home integration tests to isolate
current-event selection. Those tests do not prove clinical publication eligibility.
"""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from alerting.models import AlertEpisode, AlertEvaluation, AlertEvent
from api.family_home import home_projection
from assessment.classification import classify_2016
from assessment.qualified_facts import QualificationRejected
from audit.models import AuditEvent
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import Denied
from configurations.models import ConfigVersion
from contacts.models import ContactVersion
from family_content.alert_detail import alert_content, current_event_reason, select_current_event
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from identity.models import Patient, Principal
from records.correction import CorrectionRejected
from records.models import Record, RecordField, RecordVersion
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from tasks.models import Task
from verification.corrections import (correction_payload, create_correction_request,
    read_correction_request, resolve_correction_request)
from verification.models import CorrectionRequest


class ClassificationEvidenceTest(SimpleTestCase):
    def test_premise_evidence_uses_three_valued_states(self):
        inputs = {key: {'value': None} for key in ('ferritin', 'platelet', 'ast', 'triglycerides', 'fibrinogen')}
        for premise, expected in [({'state': 'known', 'met': True}, 'met'),
                ({'state': 'known', 'met': False}, 'not_met'), ({'state': 'unknown'}, 'unknown')]:
            with self.subTest(expected=expected):
                _, conditions = classify_2016(premise=premise, **inputs)
                self.assertEqual(conditions['BR-C00'], expected)


class EventValidityTest(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.patient = NS(id=uuid4(), data_epoch=3, verification_epoch=4)
        self.gate = NS(environment='test', enabled=True, active_release_id=uuid4(), publication_epoch=5)
        self.policy = NS(id=uuid4(), payload={'rule_key': 'synthetic-rule',
            'episode': {'rule_family_key': 'synthetic-family'}, 'publication': {'valid_for_seconds': 60, 'priority': 1}})
        self.evaluation = NS(id=uuid4(), patient_id=self.patient.id, mode='current', environment='test',
            status='completed', decision='met', validity='valid', policy_id=self.policy.id,
            data_epoch=3, verification_epoch=4, publication_epoch=5, input_digest='a' * 64, context_sequence=1)
        self.event = NS(id=uuid4(), patient=self.patient, patient_id=self.patient.id, validity='valid',
            release_id=self.gate.active_release_id, trigger_policy=self.policy, trigger_policy_id=self.policy.id,
            created_at=self.now - timedelta(seconds=30))
        self.episode = NS(id=uuid4(), patient_id=self.patient.id, status='open', last_event_id=self.event.id,
            rule_family_key='synthetic-family')
        self.event.input_snapshot = {'schema_version': 'dynamic-family-event-2', 'episode_id': str(self.episode.id),
            'evaluation_id': str(self.evaluation.id), 'input_digest': self.evaluation.input_digest,
            'publication': deepcopy(self.policy.payload['publication']), 'context_sequence': '1',
            'data_epoch': '3', 'verification_epoch': '4', 'publication_epoch': '5'}

    def reason(self, now=None):
        return current_event_reason(self.event, self.evaluation, self.gate, self.episode, now or self.now)

    def test_approved_expiry_boundary_has_no_implicit_extension(self):
        expiry = self.event.created_at + timedelta(seconds=60)
        self.assertIsNone(self.reason(expiry - timedelta(microseconds=1)))
        self.assertEqual(self.reason(expiry), 'ALERT_EXPIRED')

    def test_absent_or_invalid_lifetime_is_never_defaulted(self):
        for lifetime in (None, 0, -1, True, '60', 10**100):
            with self.subTest(lifetime=lifetime):
                self.policy.payload['publication']['valid_for_seconds'] = lifetime
                self.event.input_snapshot['publication']['valid_for_seconds'] = lifetime
                self.assertEqual(self.reason(), 'ALERT_PUBLICATION_PROOF_INVALID')

    def test_replay_and_invalid_evaluation_never_publish(self):
        for field, bad in [('mode', 'replay'), ('status', 'insufficient'), ('decision', 'unknown'),
                ('validity', 'invalid'), ('data_epoch', 2), ('verification_epoch', 3), ('publication_epoch', 4)]:
            with self.subTest(field=field):
                before = getattr(self.evaluation, field)
                setattr(self.evaluation, field, bad)
                self.assertEqual(self.reason(), 'ALERT_CURRENT_DEPENDENCY_CHANGED')
                setattr(self.evaluation, field, before)

    def test_disabled_release_or_policy_mismatch_fails_closed(self):
        self.gate.enabled = False
        self.assertEqual(self.reason(), 'ALERT_CURRENT_DEPENDENCY_CHANGED')
        self.gate.enabled = True
        self.evaluation.policy_id = uuid4()
        self.assertEqual(self.reason(), 'ALERT_CURRENT_DEPENDENCY_CHANGED')

    def test_closed_invalid_or_replaced_episode_is_not_current(self):
        for status in ('closed', 'invalid'):
            self.episode.status = status
            self.assertEqual(self.reason(), 'ALERT_EPISODE_NOT_CURRENT')
        self.episode.status = 'open'
        self.episode.last_event_id = uuid4()
        self.assertEqual(self.reason(), 'ALERT_EPISODE_NOT_CURRENT')

    def test_mutated_snapshot_is_not_current(self):
        self.event.input_snapshot['publication']['priority'] = 2
        self.assertEqual(self.reason(), 'ALERT_PUBLICATION_PROOF_INVALID')

    def test_future_event_cannot_publish(self):
        self.event.created_at = self.now + timedelta(seconds=1)
        self.assertEqual(self.reason(), 'ALERT_EXPIRED')

    def test_priority_direction_is_never_guessed(self):
        other = deepcopy(self.event)
        other.id = uuid4()
        other.trigger_policy.payload['publication']['priority'] = 2
        event, reason = select_current_event([self.event, other])
        self.assertIsNone(event)
        self.assertEqual(reason, 'ALERT_PRIORITY_ORDER_UNCONFIGURED')

    def test_equal_priority_rule_tie_is_stable(self):
        other = deepcopy(self.event)
        other.id = uuid4()
        other.trigger_policy.payload['rule_key'] = 'a-rule'
        self.assertEqual(select_current_event([self.event, other]), (other, None))
        self.assertEqual(select_current_event([other, self.event]), (other, None))


class SyntheticClinicalFixture:
    def setUp(self):
        self.now = timezone.now() - timedelta(minutes=2)
        self.owner = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.reviewer = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.patient = Patient.objects.create(display_id='SIM-CLOSEOUT-' + str(uuid4()), status='active', created_at=self.now)
        self.gate = EnvironmentGate.objects.create(environment='test', version=1, enabled=True, updated_at=self.now)
        self.retention = RetentionPolicy.objects.create(policy_key='synthetic-closeout', version=1, data_class='professional',
            rules={}, schema_version='retention-1', status='active', approved_by=self.reviewer,
            approved_at=self.now, evidence_ref='synthetic-test-only://retention')
        self.source = SourceRegistry.objects.create(source_key='synthetic-' + str(uuid4()), owner='synthetic', location='test',
            purpose='synthetic', sensitivity='synthetic', update_frequency='test', status='approved')
        self.revision = SourceRevision.objects.create(source=self.source, version='1', checksum='a' * 64, license='synthetic',
            allowed_use=['family_observation'], rights_evidence_ref='synthetic-test-only://rights', captured_at=self.now, status='approved')
        self.authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.revision,
            purpose='family_observation', environment='test', evidence_ref='synthetic-test-only://use',
            approved_by=self.reviewer, approved_at=self.now)
        self.record = Record.objects.create(patient=self.patient, kind='professional', current_version=1,
            status='active', created_at=self.now)
        self.original = self.version(1)
        self.field = self.add_field(self.original)
        application = RelationApplication.objects.create(version=1, applicant=self.owner, patient=self.patient,
            evidence_ref='synthetic-test-only://relation', status='approved', checked_by=self.reviewer,
            checked_at=self.now, created_at=self.now)
        self.relation = Relation.objects.create(patient=self.patient, guardian=self.owner, application=application,
            status='active', version=1, checked_by=self.reviewer, checked_at=self.now, valid_from=self.now)
        self.family_grant = self.grant(self.owner, 'request_correction', relation=self.relation)

    def version(self, number, **changes):
        defaults = dict(patient=self.patient, record_id=self.record.id, version=number, author=self.reviewer,
            origin_type='authorized_clinical', capture_method='manual', source_revision=self.revision,
            use_authorization_id=self.authorization.id, quality_status='pending', quality_reasons=[],
            recorded_at=self.now if number == 1 else timezone.now(), retention_policy=self.retention)
        if number > 1:
            defaults.update(supersedes_version=number - 1, correction_reason='synthetic correction', corrected_at=timezone.now())
        defaults.update(changes)
        return RecordVersion.objects.create(**defaults)

    def add_field(self, version, code='clinical-variable'):
        return RecordField.objects.create(patient=self.patient, record_version_id=version.id, ordinal=1, field_code=code,
            concept_code=code, value_type='text', value_state='known', raw_text='synthetic-only',
            sample_time_origin='unknown', quality_reasons=[])

    def grant(self, actor, action, relation=None, codes=None, **changes):
        defaults = dict(patient=self.patient, principal=actor, action=action, field_codes=codes or ['clinical-variable'],
            record_scope='assigned', assigned_record_id=self.record.id, relation_id=relation.id if relation else None,
            purpose='family_observation', granted_by=self.reviewer, evidence_ref='synthetic-test-only://permission', valid_from=self.now)
        defaults.update(changes)
        return AccessGrant.objects.create(**defaults)

    def role(self, action='verify_field'):
        return ServiceRoleGrant.objects.create(principal=self.reviewer, environment=self.gate, action=action,
            granted_by=self.reviewer, evidence_ref='synthetic-test-only://role', valid_from=self.now)


@override_settings(SJIA_ENVIRONMENT='test')
class CorrectionWorkflowTest(SyntheticClinicalFixture, TestCase):
    def create(self, **changes):
        payload = {'record_version_id': str(self.original.id), 'field_ids': [str(self.field.id)],
            'reason': 'Please verify synthetic value', 'proposed_text': None}
        payload.update(changes)
        return create_correction_request(record_id=self.record.id, actor=self.owner, epoch=0, environment='test', payload=payload)

    def authorize_resolution(self):
        self.role()
        self.grant(self.reviewer, 'verify_field')
        self.grant(self.reviewer, 'correct_record')

    def resolve(self, row, **changes):
        payload = {'expected_version': '1', 'decision': 'rejected', 'reason': 'Synthetic evidence reviewed',
            'resolution_record_version_id': None}
        payload.update(changes)
        return resolve_correction_request(request_id=row.id, actor=self.reviewer, epoch=0, environment='test', payload=payload)

    def test_creation_binds_exact_version_fields_and_deduplicates(self):
        first, second = self.create(), self.create()
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.record_version_id, self.original.id)
        self.assertEqual(first.field_ids, [self.field.id])
        self.assertEqual(CorrectionRequest.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='correction_request_create').count(), 1)

    def test_creation_rejects_blank_duplicate_and_foreign_fields(self):
        for changes in [{'reason': ' '}, {'field_ids': []}, {'field_ids': [str(self.field.id)] * 2},
                {'field_ids': [str(uuid4())]}, {'proposed_text': ' '}]:
            with self.subTest(changes=changes), self.assertRaises(CorrectionRejected):
                self.create(**changes)
        self.assertEqual(CorrectionRequest.objects.count(), 0)

    def test_creation_rejects_stale_record_version(self):
        replacement = self.version(2)
        self.record.current_version = replacement.version
        self.record.save(update_fields=['current_version'])
        with self.assertRaisesMessage(CorrectionRejected, 'VERSION_CONFLICT'):
            self.create()

    def test_creation_requires_current_relationship_and_field_grant(self):
        self.family_grant.field_codes = ['other']
        self.family_grant.save(update_fields=['field_codes'])
        with self.assertRaises(QualificationRejected):
            self.create()
        self.family_grant.field_codes = ['clinical-variable']
        self.family_grant.save(update_fields=['field_codes'])
        self.relation.status = 'revoked'
        self.relation.revoked_at = timezone.now()
        self.relation.save(update_fields=['status', 'revoked_at'])
        with self.assertRaises(QualificationRejected):
            self.create()

    def test_read_rechecks_field_scope(self):
        row = self.create()
        self.family_grant.revoked_at = timezone.now()
        self.family_grant.save(update_fields=['revoked_at'])
        with self.assertRaises(QualificationRejected):
            read_correction_request(request_id=row.id, actor=self.owner, epoch=0, environment='test')

    def test_configuration_admin_cannot_resolve(self):
        row = self.create()
        self.role('manage_config')
        with self.assertRaisesMessage(Denied, 'SERVICE_ACTION_DENIED'):
            self.resolve(row)
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')

    def test_professional_role_without_exact_case_grants_cannot_resolve(self):
        row = self.create()
        self.role()
        self.grant(self.reviewer, 'verify_field', codes=['other'])
        self.grant(self.reviewer, 'correct_record')
        with self.assertRaises(QualificationRejected):
            self.resolve(row)

    def test_version_conflict_does_not_mutate_request(self):
        row = self.create()
        self.authorize_resolution()
        with self.assertRaisesMessage(CorrectionRejected, 'VERSION_CONFLICT'):
            self.resolve(row, expected_version='2')
        row.refresh_from_db()
        self.assertEqual((row.status, row.version), ('pending', 1))

    def test_rejected_resolution_is_audited_and_identical_retry_is_stable(self):
        row = self.create()
        self.authorize_resolution()
        first = self.resolve(row)
        timestamp = first.resolved_at
        second = self.resolve(row)
        self.assertEqual((second.status, second.version, second.resolved_at), ('rejected', 2, timestamp))
        self.assertEqual(AuditEvent.objects.filter(action='correction_request_resolve').count(), 1)
        audit = AuditEvent.objects.get(action='correction_request_resolve')
        self.assertEqual((audit.outcome, audit.reason_code), ('committed', 'CORRECTION_REJECTED'))
        with self.assertRaises(CorrectionRejected):
            self.resolve(row, reason='Changed resolution')

    def test_resolved_requires_saved_new_current_correction(self):
        row = self.create()
        self.authorize_resolution()
        with self.assertRaises(CorrectionRejected):
            self.resolve(row, decision='resolved')
        with self.assertRaises(CorrectionRejected):
            self.resolve(row, decision='resolved', resolution_record_version_id=str(self.original.id))
        corrected = self.version(2)
        self.add_field(corrected)
        self.record.current_version = 2
        self.record.save(update_fields=['current_version'])
        result = self.resolve(row, decision='resolved', resolution_record_version_id=str(corrected.id))
        self.assertEqual((result.status, result.version, result.resolution_record_version_id), ('resolved', 2, corrected.id))
        self.assertEqual(RecordVersion.objects.count(), 2)  # resolution itself never edits clinical records

    def test_stale_corrected_result_cannot_resolve(self):
        row = self.create()
        self.authorize_resolution()
        old = self.version(2)
        self.add_field(old)
        self.version(3)
        self.record.current_version = 3
        self.record.save(update_fields=['current_version'])
        with self.assertRaises(CorrectionRejected):
            self.resolve(row, decision='resolved', resolution_record_version_id=str(old.id))

    def test_resolution_cannot_reference_another_record(self):
        row = self.create()
        self.authorize_resolution()
        other = Record.objects.create(patient=self.patient, kind='professional', current_version=1,
            status='active', created_at=timezone.now())
        foreign = self.version(1, record_id=other.id)
        with self.assertRaises(RecordVersion.DoesNotExist):
            self.resolve(row, decision='resolved', resolution_record_version_id=str(foreign.id))
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')

    def test_resolving_requires_correction_scope_in_addition_to_verification(self):
        row = self.create()
        self.role()
        self.grant(self.reviewer, 'verify_field')
        with self.assertRaises(QualificationRejected):
            self.resolve(row)

    def test_revoked_source_authorization_blocks_resolution(self):
        from verification.service import VerificationRejected
        row = self.create()
        self.authorize_resolution()
        self.authorization.revoked_at = timezone.now()
        self.authorization.save(update_fields=['revoked_at'])
        with self.assertRaises(VerificationRejected):
            self.resolve(row)

    def test_rejected_cannot_reference_resolution_record(self):
        with self.assertRaises(CorrectionRejected):
            correction_payload('CorrectionResolution', {'expected_version': '1', 'decision': 'rejected',
                'reason': 'synthetic', 'resolution_record_version_id': str(self.original.id)})


@override_settings(SJIA_ENVIRONMENT='test')
class HomeCurrentEventTest(SyntheticClinicalFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.point = timezone.now()
        self.policy = ConfigVersion.objects.create(config_key='synthetic-alert', kind='rule', version=1,
            schema_version='alert-trigger-1', payload={'rule_key': 'synthetic-rule', 'episode': {'rule_family_key': 'synthetic-family'},
                'publication': {'priority': 1, 'valid_for_seconds': 600}}, checksum='b' * 64,
            status='enabled', created_by=self.reviewer, created_at=self.now)
        contact = ContactVersion.objects.create(contact_id=uuid4(), version=1, contact_name='synthetic', purpose='synthetic',
            channel='phone', channel_value='synthetic', owner=self.reviewer, source_revision=self.revision,
            approval_config=self.policy, verified_at=self.now, updated_at=self.now,
            valid_until=self.point + timedelta(days=1), status='active')
        self.release = ReleaseBundle.objects.create(environment='test', version=1, publication_epoch=0,
            status='enabled', contact_version=contact, validation_config=self.policy)
        ReleaseComponent.objects.create(release=self.release, component_kind='rule', config=self.policy)
        self.gate.active_release = self.release
        self.gate.save(update_fields=['active_release'])
        self.evaluation = self.evaluate(1, self.point - timedelta(seconds=20))
        self.episode = AlertEpisode.objects.create(patient=self.patient, rule_family_key='synthetic-family', status='open',
            opened_at=self.point, last_evaluation_id=self.evaluation.id, last_assessment_at=self.evaluation.assessment_at,
            last_context_sequence=1, state_version=1)
        self.event = AlertEvent.objects.create(patient=self.patient, evaluation_id=self.evaluation.id,
            release=self.release, trigger_key='synthetic', trigger_policy=self.policy, validity='valid',
            created_at=self.point - timedelta(seconds=10), input_snapshot={
                'schema_version': 'dynamic-family-event-2', 'episode_id': str(self.episode.id),
                'evaluation_id': str(self.evaluation.id), 'input_digest': 'c' * 64, 'context_sequence': '1',
                'publication': self.policy.payload['publication'], 'data_epoch': '0', 'verification_epoch': '0', 'publication_epoch': '0'})
        self.episode.last_event_id = self.event.id
        self.episode.save(update_fields=['last_event_id'])

    def evaluate(self, sequence, at, status='completed', decision='met', mode='current', task_status='completed'):
        task = Task.objects.create(actor=self.reviewer, patient=self.patient, task_type='alert_evaluation',
            schema_version='dynamic-evaluation-task-2', input_refs={'payload': {'mode': mode},
                'authorization_context': {'environment': 'test'}}, input_sha256='a' * 64, status=task_status,
            status_version=1, created_at=at, deadline_at=at + timedelta(minutes=5))
        return AlertEvaluation.objects.create(patient=self.patient, task_id=task.id, environment='test', policy=self.policy,
            assessment_at=at, known_at=at, context_sequence=sequence, mode=mode, status=status, decision=decision,
            data_epoch=0, verification_epoch=0, publication_epoch=0, authorization_version=1, lease_generation=1,
            use_authorization_id=self.authorization.id, input_digest='c' * 64, missing_items=[], computed_at=at, validity='valid')

    def home(self):
        with patch('api.family_home.release_available', return_value=True):
            return home_projection(patient=self.patient, principal=self.owner,
                field_codes=['clinical-variable'], environment='test')

    def test_newer_insufficient_does_not_hide_valid_open_event(self):
        self.evaluate(2, self.point, status='insufficient', decision='unknown')
        value = self.home()
        self.assertEqual(value['assessment_state'], 'insufficient')
        self.assertEqual(value['current_alert_id'], str(self.event.id))

    def test_continued_met_without_new_event_keeps_existing_event(self):
        self.evaluate(2, self.point)
        self.assertEqual(self.home()['current_alert_id'], str(self.event.id))

    def test_replay_failure_does_not_change_current_processing_state(self):
        self.evaluate(2, self.point, mode='replay', task_status='failed')
        value = self.home()
        self.assertEqual(value['processing_state'], 'idle')
        self.assertEqual(value['assessment_state'], 'current')
        self.assertEqual(value['current_alert_id'], str(self.event.id))

    def test_expired_event_is_hidden_from_home_and_content(self):
        self.event.created_at = self.point - timedelta(seconds=601)
        self.event.save(update_fields=['created_at'])
        self.assertIsNone(self.home()['current_alert_id'])
        with patch('family_content.alert_detail.release_available') as release_check:
            content = alert_content(self.event, ['clinical-variable'])
        self.assertEqual(content['content_state'], 'unavailable')
        self.assertIsNone(content['content'])
        release_check.assert_not_called()

    def test_closed_or_invalid_episode_event_is_hidden(self):
        for state in ('closed', 'invalid'):
            self.episode.status = state
            self.episode.save(update_fields=['status'])
            self.assertIsNone(self.home()['current_alert_id'])

    def test_revoked_epoch_or_release_hides_old_event(self):
        self.patient.verification_epoch = 1
        self.patient.save(update_fields=['verification_epoch'])
        self.assertIsNone(self.home()['current_alert_id'])
        self.patient.verification_epoch = 0
        self.patient.save(update_fields=['verification_epoch'])
        self.gate.enabled = False
        self.gate.save(update_fields=['enabled'])
        self.assertIsNone(self.home()['current_alert_id'])

    def test_unknown_assessment_without_event_never_looks_current(self):
        self.evaluate(2, self.point, status='insufficient', decision='unknown')
        self.event.validity = 'invalid'
        self.event.save(update_fields=['validity'])
        value = self.home()
        self.assertEqual(value['assessment_state'], 'insufficient')
        self.assertIsNone(value['current_alert_id'])


class RetentionRegistrationContractTest(SimpleTestCase):
    def test_canonical_retention_routes_use_controlled_source_views(self):
        from django.urls import resolve
        for path in ['/api/support/retention-policies', f'/api/support/retention-policies/{uuid4()}/activate']:
            route = resolve(path)
            self.assertEqual(route.func.__module__, 'api.views_sources')

    def test_retention_rules_reject_arbitrary_fields_and_bad_types(self):
        from pydantic import ValidationError
        from sources.operations import RetentionRules
        for payload in [{}, {'arbitrary': True}, {'retention_days': -1, 'disposition': 'delete'},
                {'retention_days': True, 'disposition': 'delete'}]:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                RetentionRules.model_validate(payload)
        self.assertEqual(RetentionRules.model_validate({'retention_days': None, 'disposition': 'retain'}).disposition, 'retain')


@override_settings(SJIA_ENVIRONMENT='test')
class RetentionLifecycleContractTest(SyntheticClinicalFixture, TestCase):
    def payload(self):
        return {'policy_key': 'synthetic-new-policy', 'data_class': 'file', 'schema_version': 'retention-1',
            'rules': {'retention_days': None, 'disposition': 'retain'},
            'evidence_ref': 'urn:sjia:source-revision:' + str(self.revision.id)}

    def test_registration_creates_draft_without_approval_fields(self):
        from sources.operations import perform_source_operation
        with patch('sources.operations.read_approved_evidence', return_value=(self.revision, b'synthetic')):
            policy, version = perform_source_operation(self.reviewer, self.gate, 'retention_policy_create', {}, self.payload())
        self.assertEqual((policy.status, version), ('draft', 1))
        self.assertIsNone(policy.approved_by_id)
        self.assertIsNone(policy.approved_at)
        self.assertEqual(policy.evidence_ref, self.payload()['evidence_ref'])

    def test_missing_actual_evidence_cannot_activate_policy(self):
        from configurations.service import ConfigRejected
        from sources.operations import perform_source_operation
        policy = RetentionPolicy.objects.create(**self.payload(), version=1, status='draft')
        with self.assertRaises(ConfigRejected):
            perform_source_operation(self.reviewer, self.gate, 'retention_policy_activate', {'policy_id': policy.id},
                {'expected_state': 'draft', 'expected_version': '1', 'reason': 'Synthetic activation'})
        policy.refresh_from_db()
        self.assertEqual(policy.status, 'draft')
        self.assertIsNone(policy.approved_at)

    def test_explicit_activation_requires_matching_current_state_and_version(self):
        from configurations.service import ConfigRejected
        from sources.operations import perform_source_operation
        policy = RetentionPolicy.objects.create(**self.payload(), version=1, status='draft')
        with patch('sources.operations.read_approved_evidence', return_value=(self.revision, b'synthetic')):
            with self.assertRaises(ConfigRejected):
                perform_source_operation(self.reviewer, self.gate, 'retention_policy_activate', {'policy_id': policy.id},
                    {'expected_state': 'draft', 'expected_version': '2', 'reason': 'Synthetic activation'})
            current, _ = perform_source_operation(self.reviewer, self.gate, 'retention_policy_activate', {'policy_id': policy.id},
                {'expected_state': 'draft', 'expected_version': '1', 'reason': 'Synthetic activation'})
        self.assertEqual(current.status, 'active')
        self.assertEqual(current.approved_by_id, self.reviewer.id)
        self.assertIsNotNone(current.approved_at)
