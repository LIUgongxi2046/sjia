"""Synthetic permission-marker checks; no credentials, providers or clinical use.

The central isolated runner owns database execution. ScopeCanonicalTests also
runs without a database to validate canonicalization and both response schemas.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone as dt_timezone
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from jsonschema import Draft202012Validator, ValidationError

from authorization.models import AccessGrant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from records.models import Record, RecordVersion, Report
from relations.models import Relation, RelationApplication
from relations.queries import relation_list_read, relation_view
from relations.scope import scope_actions, scope_fingerprint, scope_valid_until, scoped_relations


class ScopeCanonicalTests(SimpleTestCase):
    def test_next_boundary_includes_future_grants_and_linked_relation_revocation(self):
        now = datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc)
        def window(minutes):
            return {'valid_from': (now - timedelta(days=1)).isoformat(),
                'valid_until': (now + timedelta(minutes=minutes)).isoformat(), 'revoked_at': None}
        scope = {'relation': window(60), 'grants': [window(30) | {'relation': window(20)},
            window(40) | {'valid_from': (now + timedelta(minutes=10)).isoformat(), 'relation': None}]}
        self.assertEqual(scope_valid_until(scope, now), (now + timedelta(minutes=10)).isoformat())
        scope['grants'][0]['relation']['revoked_at'] = (now + timedelta(minutes=5)).isoformat()
        self.assertEqual(scope_valid_until(scope, now), (now + timedelta(minutes=5)).isoformat())
        self.assertIsNone(scope_valid_until(scope, now + timedelta(minutes=60)))

    def test_grant_and_field_order_are_not_permission_changes(self):
        scope = {'relation': {'id': 'r'}, 'grants': [
            {'id': 'a', 'action': 'read_raw', 'field_codes': ['b', 'a', 'a']},
            {'id': 'b', 'action': 'read_file', 'field_codes': []}]}
        reordered = deepcopy(scope)
        reordered['grants'].reverse()
        reordered['grants'][1]['field_codes'] = ['a', 'b']
        self.assertRegex(scope_fingerprint(scope), r'^[0-9a-f]{64}$')
        self.assertEqual(scope_fingerprint(scope), scope_fingerprint(reordered))
        self.assertEqual(scope['grants'][0]['field_codes'], ['b', 'a', 'a'])

    def test_denying_actor_patient_or_environment_never_advertises_actions(self):
        scope = {'principal': {'account_status': 'active'}, 'patient': {'status': 'active'},
            'environment': {'gate': {'enabled': True}}, 'relation': {'id': 'r', 'active': True},
            'grants': [{'relation_id': 'r', 'action': 'read_raw', 'active': True}]}
        self.assertEqual(scope_actions(scope), ['read_raw'])
        for area, key, value in [('principal', 'account_status', 'disabled'),
                ('patient', 'status', 'restricted'), ('relation', 'active', False),
                ('environment', 'gate', None), ('environment', 'gate', {'enabled': False})]:
            changed = deepcopy(scope)
            changed[area][key] = value
            self.assertEqual(scope_actions(changed), [])

    def test_both_read_contracts_accept_only_optional_nullable_sha256_marker(self):
        root = Path(__file__).resolve().parents[2]
        item = {'relation_id': str(uuid4()), 'patient_id': str(uuid4()), 'display_id': 'synthetic',
            'status': 'active', 'version': '1', 'valid_from': '2026-01-01T00:00:00+00:00',
            'valid_until': None, 'actions': ['read_raw']}
        for path, definition in [(root / 'backend/relations/read.schema.json', 'RelationItem'),
                (root / 'backend-design/protocol.schema.json', 'RelationView')]:
            schema = json.loads(path.read_text())
            validator = Draft202012Validator({'$ref': f'#/$defs/{definition}', '$defs': schema['$defs']})
            validator.validate(item)
            for marker in (None, 'a' * 64):
                validator.validate(dict(item, authorization_scope_fingerprint=marker))
            for marker in ('', 'A' * 64, 'a' * 63, 42, {}, ['a' * 64]):
                with self.assertRaises(ValidationError):
                    validator.validate(dict(item, authorization_scope_fingerprint=marker))


@override_settings(SJIA_ENVIRONMENT='test')
class RelationAuthorizationScopeTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc)
        self.actor = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.other = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.patient = Patient.objects.create(display_id=f'synthetic-scope-{uuid4()}',
            status='active', created_at=self.now)
        self.gate = EnvironmentGate.objects.create(environment='test', version=1,
            enabled=True, updated_at=self.now)
        self.relation = self._relation()
        self.grant = self._grant()

    def _relation(self, *, status='active'):
        application = RelationApplication.objects.create(version=1, applicant=self.actor,
            patient=self.patient, evidence_ref='synthetic-only', status='approved',
            checked_by=self.actor, checked_at=self.now, created_at=self.now)
        return Relation.objects.create(patient=self.patient, guardian=self.actor, application=application,
            status=status, version=1, checked_by=self.actor, checked_at=self.now,
            valid_from=self.now - timedelta(days=1))

    def _grant(self, **changes):
        values = {'patient': self.patient, 'principal': self.actor, 'relation_id': self.relation.id,
            'action': 'read_raw', 'field_codes': ['synthetic-a', 'synthetic-b'], 'record_scope': 'all',
            'purpose': 'family_observation', 'granted_by': self.actor, 'evidence_ref': 'synthetic-only',
            'valid_from': self.now - timedelta(days=1)}
        return AccessGrant.objects.create(**(values | changes))

    def _view(self, *, relation=None, principal=None, at=None):
        with patch('relations.scope.timezone.now', return_value=at or self.now):
            return relation_view(relation or self.relation, principal or self.actor)

    def _marker(self, **kwargs):
        return self._view(**kwargs)['authorization_scope_fingerprint']

    def test_list_and_detail_are_one_statement_and_serialize_same_scope(self):
        with patch('relations.scope.timezone.now', return_value=self.now):
            with self.assertNumQueries(1):
                listed = relation_list_read(self.actor)['relations']
            with self.assertNumQueries(1):
                detailed = relation_view(self.relation, self.actor, detail=True)
        self.assertEqual(listed, [{key: value for key, value in detailed.items() if key != 'auth_epoch'}])
        self.assertEqual(detailed['actions'], ['read_raw'])
        self.assertRegex(detailed['authorization_scope_fingerprint'], r'^[0-9a-f]{64}$')
        self.assertEqual(set(detailed), {'relation_id', 'patient_id', 'display_id', 'status', 'version',
            'valid_from', 'valid_until', 'actions', 'auth_epoch', 'authorization_scope_fingerprint',
            'authorization_scope_valid_until'})
        self.assertIsNone(detailed['authorization_scope_valid_until'])

    def test_same_actions_changed_purpose_or_field_scope_changes_marker(self):
        before = self._view()
        for change in ({'purpose': 'synthetic-other-purpose'}, {'field_codes': ['synthetic-a']}):
            AccessGrant.objects.filter(id=self.grant.id).update(**change)
            after = self._view()
            self.assertEqual(before['actions'], after['actions'])
            self.assertEqual(before['version'], after['version'])
            self.assertNotEqual(before['authorization_scope_fingerprint'], after['authorization_scope_fingerprint'])
            before = after

    def test_grant_replacement_revocation_and_removal_change_even_with_same_actions(self):
        before = self._marker()
        replacement = self._grant()
        after_replacement = self._marker()
        self.assertNotEqual(before, after_replacement)
        AccessGrant.objects.filter(id=self.grant.id).update(revoked_at=self.now)
        after_revoke = self._marker()
        self.assertNotEqual(after_replacement, after_revoke)
        self.assertEqual(self._view()['actions'], ['read_raw'])
        self.grant.delete()
        self.assertNotEqual(after_revoke, self._marker())
        replacement.delete()
        self.assertEqual(self._view()['actions'], [])

    def test_record_scope_and_assignment_change_marker(self):
        before = self._marker()
        AccessGrant.objects.filter(id=self.grant.id).update(record_scope='own')
        self.assertNotEqual(before, self._marker())
        records = [Record.objects.create(patient=self.patient, kind='observation', current_version=1,
            status='active', created_at=self.now) for _ in range(2)]
        # The current-version FK is deferred, but still requires a complete
        # synthetic record/version/source authorization fixture at teardown.
        from retention.models import RetentionPolicy
        from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
        retention = RetentionPolicy.objects.create(policy_key=f'synthetic-scope-{uuid4()}',
            version=1, data_class='observation', rules={}, schema_version='retention-1', status='active')
        source = SourceRegistry.objects.create(source_key=f'synthetic-scope-{uuid4()}',
            owner='synthetic', location='synthetic-only', purpose='software_test',
            sensitivity='synthetic', update_frequency='never', status='approved')
        revision = SourceRevision.objects.create(source=source, version='1', checksum='a' * 64,
            license='synthetic-only', allowed_use=['family_observation'],
            captured_at=self.now, status='approved')
        authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision,
            purpose='family_observation', environment='test', evidence_ref='synthetic-only',
            approved_by=self.actor, approved_at=self.now)
        for record in records:
            RecordVersion.objects.create(patient=self.patient, record_id=record.id, version=1,
                author=self.actor, origin_type='self_report', capture_method='manual',
                source_revision=revision, use_authorization_id=authorization.id,
                quality_status='valid', quality_reasons=[], recorded_at=self.now, retention_policy=retention)
        before = self._marker()
        for record in records:
            AccessGrant.objects.filter(id=self.grant.id).update(record_scope='assigned', assigned_record_id=record.id)
            after = self._marker()
            self.assertNotEqual(before, after)
            before = after

    def test_relation_version_epoch_and_identity_are_permission_inputs(self):
        before = self._marker()
        for change in ({'version': 2}, {'auth_epoch': 1}):
            Relation.objects.filter(id=self.relation.id).update(**change)
            after = self._marker()  # The caller's relation object is intentionally stale.
            self.assertNotEqual(before, after)
            before = after
        Relation.objects.filter(id=self.relation.id).update(status='revoked', revoked_at=self.now)
        new = self._relation()
        self._grant(relation_id=new.id)
        self.assertNotEqual(before, self._marker(relation=new))
        self.assertEqual(self._view(relation=new)['actions'], ['read_raw'])

    def test_relationless_and_other_relation_grants_are_included_conservatively(self):
        before = self._marker()
        relationless = self._grant(relation_id=None)
        self.assertNotEqual(before, self._marker())
        before = self._marker()
        AccessGrant.objects.filter(id=relationless.id).update(field_codes=['synthetic-c'])
        self.assertNotEqual(before, self._marker())
        old = self._relation(status='revoked')
        self._grant(relation_id=old.id)
        before = self._marker()
        Relation.objects.filter(id=old.id).update(auth_epoch=3)
        self.assertNotEqual(before, self._marker())
        self.assertEqual(self._view()['actions'], ['read_raw'])

    def test_unrelated_principal_or_patient_permissions_do_not_change_marker(self):
        before = self._marker()
        self._grant(principal=self.other, relation_id=None, field_codes=['unrelated'])
        another_patient = Patient.objects.create(display_id=f'synthetic-other-{uuid4()}',
            status='active', created_at=self.now)
        self._grant(patient=another_patient, relation_id=None, field_codes=['unrelated'])
        self.assertEqual(before, self._marker())

    def test_actor_patient_environment_denials_clear_actions_and_change_marker(self):
        for model, identity, changes, reset in (
                (Principal, self.actor.id, {'account_status': 'disabled'}, {'account_status': 'active'}),
                (Patient, self.patient.id, {'status': 'restricted'}, {'status': 'active'}),
                (EnvironmentGate, self.gate.pk, {'enabled': False}, {'enabled': True})):
            before = self._marker()
            model.objects.filter(pk=identity).update(**changes)
            self.assertNotEqual(before, self._marker())
            self.assertEqual(self._view()['actions'], [])
            model.objects.filter(pk=identity).update(**reset)
        before = self._marker()
        Principal.objects.filter(id=self.actor.id).update(auth_epoch=1)
        self.assertNotEqual(before, self._marker())
        before = self._marker()
        EnvironmentGate.objects.filter(pk=self.gate.pk).update(version=2)
        self.assertNotEqual(before, self._marker())
        with override_settings(SJIA_ENVIRONMENT='synthetic-missing-environment'):
            self.assertEqual(self._view()['actions'], [])
            self.assertNotEqual(before, self._marker())

    def test_temporal_boundaries_change_marker_but_query_time_does_not(self):
        boundary = self.now + timedelta(hours=1)
        before_boundary = boundary - timedelta(seconds=1)
        for model, identity in ((AccessGrant, self.grant.id), (Relation, self.relation.id)):
            for field in ('valid_from', 'valid_until', 'revoked_at'):
                defaults = {'valid_from': self.now - timedelta(days=1), 'valid_until': None, 'revoked_at': None}
                model.objects.filter(id=identity).update(**(defaults | {field: boundary}))
                first = self._view(at=self.now)
                self.assertEqual(first['authorization_scope_valid_until'], boundary.isoformat())
                self.assertEqual(first, self._view(at=before_boundary))
                changed = self._view(at=boundary)
                self.assertIsNone(changed['authorization_scope_valid_until'])
                self.assertNotEqual(first['authorization_scope_fingerprint'], changed['authorization_scope_fingerprint'])
                self.assertEqual(changed['actions'], ['read_raw'] if field == 'valid_from' else [])
                model.objects.filter(id=identity).update(**defaults)

    def test_next_boundary_uses_inactive_relationless_and_other_relation_grants(self):
        relationless = self._grant(relation_id=None, valid_from=self.now + timedelta(minutes=30))
        old = self._relation(status='revoked')
        self._grant(relation_id=old.id)
        Relation.objects.filter(id=old.id).update(valid_until=self.now + timedelta(minutes=20))
        self.assertEqual(self._view()['authorization_scope_valid_until'],
            (self.now + timedelta(minutes=20)).isoformat())
        AccessGrant.objects.filter(id=relationless.id).update(revoked_at=self.now + timedelta(minutes=10))
        self.assertEqual(self._view()['authorization_scope_valid_until'],
            (self.now + timedelta(minutes=10)).isoformat())

    def test_field_order_and_unrelated_clinical_or_descriptive_updates_do_not_change_scope(self):
        before = self._marker()
        AccessGrant.objects.filter(id=self.grant.id).update(field_codes=['synthetic-b', 'synthetic-a'],
            evidence_ref='synthetic-changed-evidence-description')
        Patient.objects.filter(id=self.patient.id).update(display_id=f'synthetic-renamed-{uuid4()}',
            data_epoch=3, verification_epoch=2, evaluation_sequence=7)
        Principal.objects.filter(id=self.actor.id).update(updated_at=self.now + timedelta(minutes=5))
        Relation.objects.filter(id=self.relation.id).update(reason='synthetic-note-not-permission')
        report = Report.objects.create(patient=self.patient, report_status='unknown',
            external_ref='synthetic-clinical-value-a', created_at=self.now)
        self.assertEqual(before, self._marker())
        Report.objects.filter(id=report.id).update(external_ref='synthetic-clinical-value-b')
        self.assertEqual(before, self._marker())
        with patch('relations.scope.timezone.now', return_value=self.now):
            scope = scoped_relations(self.actor).get(id=self.relation.id)._authorization_scope
        serialized = json.dumps(scope)
        for text in ('synthetic-clinical-value', 'synthetic-note-not-permission',
                'synthetic-changed-evidence-description', 'data_epoch', 'verification_epoch', 'evaluation_sequence'):
            self.assertNotIn(text, serialized)

    def test_other_caller_cannot_read_scope_and_client_marker_does_not_authorize(self):
        with self.assertRaises(ValueError):
            self._view(principal=self.other)
        self.assertEqual(relation_list_read(self.other), {'relations': []})
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor)
        marker = self._marker()
        response = self.client.get(f'/api/relations/{self.relation.id}',
            {'authorization_scope_fingerprint': marker})
        self.assertEqual(response.status_code, 401)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.other.id)
        session['_sjia_auth_epoch'] = self.other.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()
        response = self.client.get(f'/api/relations/{self.relation.id}',
            {'authorization_scope_fingerprint': marker})
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(marker, response.content.decode())
