"""Synthetic-only coverage metadata tests. No deletion, providers or patient files."""
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from assessment.qualified_facts import QualificationRejected
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import Denied
from dependencies.models import DependencyEdge, DependencyNode
from drafts.models import Draft
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from recognition.models import CandidateField
from retention.coverage import (capability_catalog, disposition_coverage, inventory_models,
    metadata_columns, object_metadata, policy_metadata)
from retention.models import RetentionPolicy


SCHEMA = json.loads((Path(__file__).resolve().parents[2] / 'backend-design/disposition-coverage.schema.json').read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class CoveragePureTests(SimpleTestCase):
    def test_catalog_distinguishes_byte_execution_and_unsupported_categories(self):
        normal, simulation = capability_catalog('test'), capability_catalog('simulation')
        self.assertEqual(normal['registered_target_types'], ['file_object', 'purpose_copy', 'provider_copy'])
        self.assertFalse(normal['executors'][2]['environment_supported'])
        self.assertTrue(simulation['executors'][2]['environment_supported'])
        self.assertEqual(len(normal['unsupported_categories']), 7)
        self.assertTrue(all(not row['executor_registered'] for row in normal['unsupported_categories']))

    def test_projection_never_selects_payload_credentials_or_file_keys(self):
        from files.models import FileObject
        from requests.models import WriteIntent
        from tasks.models import Task
        for model, forbidden in [(CandidateField, ('raw_text', 'proposed_field', 'confidence')),
                (FileObject, ('object_key',)), (WriteIntent, ('recovery_payload', 'authorization_context')),
                (Task, ('input_refs', 'lease_owner'))]:
            with self.subTest(model=model):
                columns = metadata_columns(model)[0]
                self.assertFalse(set(columns) & set(forbidden))

    def test_extra_input_keys_cannot_escape_projection(self):
        row = {'id': uuid4(), 'patient_id': uuid4(), 'candidate_set_id': uuid4(),
            'field_config_version_id': uuid4(), 'anchor_id': None, 'raw_text': 'SYNTHETIC PRIVATE',
            'proposed_field': {'secret': 'SYNTHETIC PRIVATE'}}
        result = object_metadata(CandidateField, row)
        self.assertNotIn('SYNTHETIC PRIVATE', json.dumps(result))
        self.assertEqual(result['version_binding'], 'row_identity_only')

    def test_model_inventory_excludes_account_credentials_and_global_sessions(self):
        labels = {model._meta.label_lower for model, _ in inventory_models()}
        for expected in ('records.recordversion', 'recognition.candidateset', 'tasks.consumerreceipt',
                'assessment.assessmentoutput', 'notifications.notificationreceipt', 'case_details.statementevidence'):
            self.assertIn(expected, labels)
        self.assertFalse(labels & {'identity.principalcredential', 'identity.principalidentity',
            'identity.principal', 'sessions.session', 'contacts.contactversion'})

    def test_policy_metadata_does_not_invent_approval_or_disposition_clock(self):
        now, actor = timezone.now(), uuid4()
        policy = SimpleNamespace(id=uuid4(), version=1, status='active', approved_at=now,
            approved_by_id=actor, evidence_ref=f'urn:sjia:source-revision:{uuid4()}',
            schema_version='retention-1', rules={'retention_days': None, 'disposition': 'delete'})
        value = policy_metadata(policy, {actor}, now)
        self.assertIn('FINITE_RETENTION_PERIOD_UNRESOLVED', value['unresolved_policy_inputs'])
        self.assertFalse(value['eligible_for_execution'])
        self.assertEqual(value['evidence_qualification'], 'not_checked')
        self.assertEqual(value['retention_clock'], 'unresolved_per_object')
        policy.approved_at = now + timedelta(seconds=1)
        self.assertFalse(policy_metadata(policy, {actor}, now)['approval_metadata_current'])
        policy.rules = {'unexpected': 'SYNTHETIC PRIVATE'}
        self.assertNotIn('SYNTHETIC PRIVATE', json.dumps(policy_metadata(policy, {actor}, now)))

    def test_schema_forbids_execution_claim_and_extra_clinical_payload(self):
        Draft202012Validator.check_schema(SCHEMA)
        catalog = capability_catalog('test')
        Draft202012Validator({'$defs': SCHEMA['$defs'], '$ref': '#/$defs/Catalog'}).validate(catalog)
        self.assertFalse(Draft202012Validator({'$defs': SCHEMA['$defs'], '$ref': '#/$defs/Object'}).is_valid({
            **object_metadata(CandidateField, {'id': uuid4()}), 'raw_text': 'SYNTHETIC PRIVATE'}))


class CoverageDatabaseTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.patient = Patient.objects.create(display_id=f'SYNTHETIC-COVERAGE-{uuid4()}', status='active', created_at=self.now)
        self.other = Patient.objects.create(display_id=f'SYNTHETIC-COVERAGE-OTHER-{uuid4()}', status='active', created_at=self.now)
        self.gate = EnvironmentGate.objects.create(environment=f'coverage-{uuid4()}', version=1, enabled=True, updated_at=self.now)
        self.role = ServiceRoleGrant.objects.create(principal=self.actor, environment=self.gate,
            action='manage_disposition', granted_by=self.actor, evidence_ref='synthetic-only', valid_from=self.now)
        self.grant = AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='read_case',
            field_codes=[], record_scope='all', purpose='synthetic_disposition_review', granted_by=self.actor,
            evidence_ref='synthetic-only', valid_from=self.now)
        self.policy = RetentionPolicy.objects.create(policy_key=f'synthetic-coverage-{uuid4()}', version=1,
            data_class='draft', schema_version='retention-1', status='draft',
            rules={'retention_days': None, 'disposition': 'retain'}, evidence_ref='SYNTHETIC PRIVATE EVIDENCE')
        self.draft = Draft.objects.create(patient=self.patient, owner=self.actor, form_type='observation', version=3,
            schema_version='draft-1', payload={'note': 'SYNTHETIC PRIVATE CLINICAL'}, status='editing',
            saved_at=self.now, retention_policy=self.policy)
        self.other_draft = Draft.objects.create(patient=self.other, owner=self.actor, form_type='observation', version=1,
            schema_version='draft-1', payload={}, status='editing', saved_at=self.now, retention_policy=self.policy)

    def read(self, **changes):
        return disposition_coverage(**{'actor': self.actor, 'auth_epoch': self.actor.auth_epoch,
            'audience': 'support', 'patient_id': self.patient.id, 'purpose': self.grant.purpose,
            'environment': self.gate.environment, **changes})

    def test_inventory_has_exact_patient_versions_and_no_payload_or_mutation(self):
        statements = []
        def observe(execute, sql, params, many, context):
            statements.append(sql.lstrip().split(' ', 1)[0].upper())
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe), patch('files.service.object_store.get') as file_read:
            value = self.read()
        VALIDATOR.validate(value)
        self.assertFalse({'INSERT', 'UPDATE', 'DELETE'} & set(statements))
        file_read.assert_not_called()
        self.assertFalse(value['whole_data_disposal_supported'])
        self.assertFalse(value['execution_authorized'])
        objects = {row['id']: row for row in value['objects']}
        self.assertEqual(objects[str(self.draft.id)]['versions']['version'], '3')
        self.assertNotIn(str(self.other_draft.id), objects)
        self.assertNotIn('SYNTHETIC PRIVATE', json.dumps(value))
        self.assertEqual(value['policies'][0]['policy_id'], str(self.policy.id))
        self.assertFalse(value['policies'][0]['approval_metadata_current'])

    def test_service_role_alone_or_partial_case_grant_cannot_enumerate(self):
        self.grant.field_codes = ['only-one-field']; self.grant.save(update_fields=['field_codes'])
        with self.assertRaises(QualificationRejected): self.read()
        self.grant.field_codes = []; self.grant.record_scope = 'own'; self.grant.save()
        with self.assertRaises(QualificationRejected): self.read()
        self.grant.delete()
        with self.assertRaises(QualificationRejected): self.read()

    def test_audience_scope_revocation_and_session_epoch_fail_closed(self):
        with self.assertRaises(Denied): self.read(audience='family')
        with self.assertRaises(Denied): self.read(auth_epoch=self.actor.auth_epoch + 1)
        with self.assertRaises(QualificationRejected): self.read(purpose='different_purpose')
        with self.assertRaises(QualificationRejected): self.read(patient_id=self.other.id)
        self.role.revoked_at = self.now; self.role.save(update_fields=['revoked_at'])
        with self.assertRaises(Denied): self.read()

    def test_registered_graph_never_expands_into_foreign_patient(self):
        own = DependencyNode.objects.create(patient=self.patient, resource_type='draft', resource_id=self.draft.id,
            resource_version='3', validity='valid')
        shared = DependencyNode.objects.create(patient=None, resource_type='retention_policy', resource_id=self.policy.id,
            resource_version='1', validity='valid')
        foreign = DependencyNode.objects.create(patient=self.other, resource_type='draft', resource_id=self.other_draft.id,
            resource_version='1', validity='valid')
        DependencyEdge.objects.create(dependent=own, prerequisite=shared, edge_type='policy')
        # Low-level synthetic malformed graph deliberately exercises safe projection.
        DependencyEdge.objects.create(dependent=own, prerequisite=foreign, edge_type='source')
        value = self.read()
        self.assertTrue(value['dependencies']['cross_patient_edge_rejected'])
        self.assertEqual(len(value['dependencies']['edges']), 1)
        self.assertEqual(value['dependencies']['edges'][0]['prerequisite']['scope'], 'shared')
        self.assertNotIn(str(foreign.id), json.dumps(value))
        self.assertNotIn(str(self.other_draft.id), json.dumps(value))

    def test_inventory_limit_is_explicit_and_never_claims_full_disposal(self):
        value = self.read(limit=1)
        self.assertEqual(len(value['objects']), 1)
        self.assertTrue(value['inventory_truncated'])
        self.assertFalse(value['whole_data_disposal_supported'])
        self.assertIn('BACKUP_CATALOG_UNAVAILABLE', value['unresolved_coverage'])

    def test_http_requires_exact_query_and_is_schema_valid(self):
        from django.conf import settings
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor, case=self)
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'support'
        session.save()
        path = f'/api/v2/support/patients/{self.patient.id}/disposition-coverage'
        with self.settings(SJIA_ENVIRONMENT=self.gate.environment):
            response = self.client.get(path, {'purpose': self.grant.purpose})
            self.assertEqual(response.status_code, 200, response.content)
            VALIDATOR.validate(response.json())
            for suffix in ('', '?purpose=a&purpose=b', '?purpose=a&extra=b'):
                response = self.client.get(path + suffix)
                self.assertEqual(response.status_code, 422, response.content)
