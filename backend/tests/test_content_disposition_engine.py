"""Native PostgreSQL proofs for the explicitly gated content disposition slice.

All identities, policy/evidence bytes and clinical canaries are synthetic. These
TransactionTestCase tests refuse any database not named test_*. They perform no
provider calls, install no runtime policy and never waive evidence/auth checks.
The claims are logical current-row erasure and local path absence only.
"""
import copy
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, connection, connections, transaction
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone
from pydantic import ValidationError

from archives.models import ArchiveFact, ArchiveFactVersion, PersonalRecordTask
from authorization.models import AccessGrant, ServiceRoleGrant
from case_details.models import PatientProfileVersion, ClinicalStatement
from configurations.models import ConfigVersion
from dictionaries.models import UnitMapping
from files.models import FileGroup, FileObject, FilePage
from files.storage import LocalFileStore, build_object_key, digest_bytes
from identity.models import Patient, Principal
from medical_time.models import MedicalTime
from recognition.models import CandidateConfirmation, CandidateField, CandidateSet, SourceAnchor
from records.models import NormalizedMeasurement, Record, RecordAttachment, RecordField, RecordVersion, Report
from requests.models import WriteIntent, WriteRequest
from requests.service import commit_intent, prepare_intent
from retention.content_engine import approve_plan, execute_plan, preview_plan, verify_manifest
from retention.content_journal import configured_journal
from retention.content_registry import REGISTRY, REGISTRY_SHA256, PRESERVATION_SHA256, adapter, digest, patient_rows, row_digest, row_values, row_version
from retention.content_schemas import ContentPolicyRules
from retention.disposition import DispositionRejected
from retention.erasure_models import ContentDisposed
from retention.models import ContentAccessRevocation, ContentDispositionHold, ContentDispositionPlan, ContentDispositionPolicy, ContentErasure
from sources.models import DataUseAuthorization
from summaries.models import Summary, SummaryItem
from tasks.models import OutboxEvent, Task, TaskAttempt
from tasks.service import input_digest
from tests.synthetic_fixtures import SyntheticEvidence


CANARY = 'SYNTHETIC_T26_ERASE_ME'
OTHER = 'SYNTHETIC_T26_PRESERVE_ME'
PURPOSE = 'synthetic_content_disposition'


class ContentPolicyContractTests(SimpleTestCase):
    def test_policy_contract_has_no_implicit_mode_or_preservation_defaults(self):
        required = {'mode', 'profile', 'registry_sha256', 'preservation_sha256', 'allowed_adapters',
            'receipt_keep_until', 'metadata_keep_until', 'retention_seconds', 'clock', 'local_objects',
            'all_copies_required', 'access_purpose', 'recheck_at'}
        self.assertTrue(all(ContentPolicyRules.model_fields[name].is_required() for name in required))
        with self.assertRaises(ValidationError):
            ContentPolicyRules.model_validate({'schema_version': 'content-disposition-policy-1'})

    def test_every_registered_model_field_is_explicitly_classified(self):
        # A schema coverage check, not a claim that every adapter has run.
        for label in REGISTRY:
            with self.subTest(adapter=label):
                model, spec = adapter(label)
                self.assertFalse(set(spec['payload_fields']) & set(spec['preserved_fields']))
                self.assertTrue(set(spec['live_required_fields']) <= set(spec['payload_fields']))
                self.assertTrue(all(model._meta.get_field(name).null for name in spec['payload_fields']))


class ContentDispositionNativeTests(TransactionTestCase):
    """True committed transactions and SQL triggers; no TestCase lock bypass."""

    def setUp(self):
        super().setUp()
        if connection.vendor != 'postgresql' or not str(connection.settings_dict['NAME']).startswith('test_'):
            raise RuntimeError('Content disposition fixtures require an explicitly named test_* PostgreSQL database')
        from tests.isolated_rows import IsolatedSchemaRows
        # Django flush does not discover these schema-qualified application
        # tables. Snapshot before the first fixture write, then register exact
        # key cleanup last, even if setup fails, so DB cleanup runs before
        # temporary evidence, object and journal directories are removed.
        rows = IsolatedSchemaRows(require_empty=True)
        try:
            self.create_fixture()
        finally:
            self.addCleanup(rows.cleanup)

    def create_fixture(self):
        self.now = timezone.now()
        self.settings_override = override_settings(SJIA_ENVIRONMENT=f'test-content-{uuid4()}',
            SJIA_CONTENT_DISPOSITION_ENABLED=True)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.actor = Principal.objects.create(account_status='active', created_at=self.now, updated_at=self.now)
        self.patient = Patient.objects.create(display_id=f'SYNTHETIC-T26-{uuid4()}', status='active', created_at=self.now)
        self.other = Patient.objects.create(display_id=f'SYNTHETIC-T26-OTHER-{uuid4()}', status='active', created_at=self.now)
        self.evidence = SyntheticEvidence(self, self.actor)
        self.role = ServiceRoleGrant.objects.create(principal=self.actor, environment=self.evidence.gate,
            action='manage_disposition', granted_by=self.actor, evidence_ref=self.evidence.reference, valid_from=self.now)
        self.grant = AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='read_case',
            field_codes=[], record_scope='all', purpose=PURPOSE, granted_by=self.actor,
            evidence_ref=self.evidence.reference, valid_from=self.now)
        hold_id = uuid4()
        from retention.content_schemas import ContentHoldAttestation
        attestation = ContentHoldAttestation(schema_version='content-disposition-hold-1',hold_id=hold_id,
            patient_id=self.patient.id,hold_version=1,environment=settings.SJIA_ENVIRONMENT,state='clear',
            reviewed_by=self.actor.id,reviewed_at=self.now,valid_until=self.now+timedelta(days=1)).model_dump(mode='json')
        hold_source,_ = self.evidence.evidence(attestation,purposes=['configuration_approval'])
        self.hold = ContentDispositionHold.objects.create(id=hold_id,patient=self.patient, source_revision=hold_source,
            state='clear', reviewed_by=self.actor, reviewed_at=self.now,
            valid_until=self.now + timedelta(days=1), version=1)
        # Three unrelated temp trees: evidence/object store, durable journal and
        # trust checkpoint. No real bytes or configured external stores are used.
        self.journal_directory = TemporaryDirectory(prefix='sjia-t26-journal-')
        self.checkpoint_directory = TemporaryDirectory(prefix='sjia-t26-checkpoint-')
        self.addCleanup(self.journal_directory.cleanup)
        self.addCleanup(self.checkpoint_directory.cleanup)
        self.journal_override = override_settings(SJIA_CONTENT_JOURNAL_ROOT=self.journal_directory.name,
            SJIA_CONTENT_CHECKPOINT_PATH=str(Path(self.checkpoint_directory.name) / 'checkpoint.json'),
            SJIA_RESTORE_ROOT=None)
        self.journal_override.enable()
        self.addCleanup(self.journal_override.disable)
        configured_journal().initialize()
        from files import service as files_service
        previous_store = files_service.object_store
        self.store = LocalFileStore(Path(settings.FILE_OBJECT_ROOT))
        files_service.object_store = self.store
        self.addCleanup(setattr, files_service, 'object_store', previous_store)
        # FK configuration identities are explicit synthetic draft artifacts,
        # never enabled policies or stand-ins for clinical validation.
        config_payload = {'schema_version': 'synthetic-content-fixture-1', 'synthetic': True}
        self.config = ConfigVersion.objects.create(config_key=f'synthetic-content-{uuid4()}', kind='content',
            version=1, schema_version=config_payload['schema_version'], payload=config_payload,
            checksum=digest(config_payload), source_revision=self.evidence.source, status='draft',
            created_by=self.actor, created_at=self.now)
        self.authorization = self.authorization_for(self.patient)
        self.other_authorization = self.authorization_for(self.other)
        self.build_chain()

    def authorization_for(self, patient):
        return DataUseAuthorization.objects.create(patient=patient, source_revision=self.evidence.source,
            purpose=PURPOSE, environment=settings.SJIA_ENVIRONMENT, evidence_ref=self.evidence.reference,
            approved_by=self.actor, approved_at=self.now)

    def time_row(self, patient, text):
        return MedicalTime.objects.create(patient=patient, raw_text=text, precision='date',
            local_date=date(2026, 1, 2), resolution_status='local_only')

    def file_row(self, purpose, *, derived_from=None):
        content = f'{CANARY}:{purpose}:actual synthetic local object bytes'.encode()
        checksum = digest_bytes(content)
        key = build_object_key(self.patient.id, uuid4(), purpose)
        self.store.put_immutable(key, content)
        row = FileObject.objects.create(patient=self.patient, file_group_id=self.group.id,
            derived_from_id=derived_from.id if derived_from else None, object_key=key,
            object_version=checksum, checksum=checksum, size_bytes=len(content), media_type='application/octet-stream',
            purpose=purpose, status='available', retention_policy=self.evidence.retention, created_at=self.now)
        self.object_bytes[row.id] = content
        return row

    def task_row(self, refs, *, task_type='summary'):
        return Task.objects.create(patient=self.patient, actor=self.actor, task_type=task_type,
            schema_version='synthetic-content-snapshot-1', input_refs=refs, input_sha256=input_digest(refs),
            status='running', status_version=1, lease_generation=1, lease_owner='synthetic-worker',
            lease_until=self.now + timedelta(minutes=30), created_at=self.now,
            deadline_at=self.now + timedelta(hours=1))

    def build_chain(self):
        self.object_bytes = {}
        self.group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1,
            status='available', source_revision=self.evidence.source,
            use_authorization_id=self.authorization.id, created_at=self.now)
        self.original = self.file_row('original')
        self.response_file = self.file_row('ocr_response', derived_from=self.original)
        self.page = FilePage.objects.create(patient=self.patient, file_group_id=self.group.id,
            file_id=self.original.id, ordinal=1, width=12, height=18, rotation=0)
        self.anchor = SourceAnchor.objects.create(patient=self.patient, page_id=self.page.id,
            anchor_kind='page', coordinate_system='decoded_pixel', original_text=CANARY + ':anchor',
            row_label=CANARY + ':row', column_label=CANARY + ':column')
        self.ocr_task = self.task_row({'file_id': str(self.original.id), 'clinical_copy': CANARY + ':ocr_input'}, task_type='ocr')
        self.candidate_set = CandidateSet.objects.create(patient=self.patient, file_group_id=self.group.id,
            file_group_version=1, task_id=self.ocr_task.id, provider='synthetic-no-provider-call',
            model_version='synthetic-1', provider_request_id='synthetic-only', schema_version='synthetic-candidates-1',
            raw_response_file_id=self.response_file.id, status='confirmed', created_at=self.now)
        self.candidate = CandidateField.objects.create(patient=self.patient, candidate_set_id=self.candidate_set.id,
            field_path=CANARY + ':field_path', raw_text=CANARY + ':candidate', field_config_version=self.config,
            proposed_field={'raw_text': CANARY + ':proposed'}, anchor_id=self.anchor.id,
            recognition_status='recognized', confidence=Decimal('0.99'))
        self.confirmation = CandidateConfirmation.objects.create(patient=self.patient,
            candidate_field_id=self.candidate.id, confirmed_by=self.actor,
            confirmed_text=CANARY + ':confirmed', decision='modified', confirmed_at=self.now)
        self.medical_time = self.time_row(self.patient, CANARY + ':medical_time')
        self.adjacent_time = self.time_row(self.patient, OTHER + ':adjacent_time')
        self.report = Report.objects.create(patient=self.patient, source_revision=self.evidence.source,
            external_ref=CANARY + ':report_ref', source_organization=CANARY + ':report_org',
            report_status='final', sample_time_id=self.medical_time.id, result_time_id=self.medical_time.id,
            file_group_id=self.group.id, created_at=self.now)
        with transaction.atomic():
            self.record = Record.objects.create(patient=self.patient, kind='report', current_version=2,
                status='active', created_at=self.now)
            self.first = RecordVersion.objects.create(patient=self.patient, record_id=self.record.id, version=1,
                author=self.actor, origin_type='report_transcription', capture_method='ocr_confirmed',
                source_revision=self.evidence.source, source_organization=CANARY + ':version_org',
                report_id=self.report.id, use_authorization_id=self.authorization.id,
                quality_status='valid', quality_reasons=[CANARY + ':quality'], recorded_at=self.now,
                retention_policy=self.evidence.retention)
            self.second = RecordVersion.objects.create(patient=self.patient, record_id=self.record.id, version=2,
                supersedes_version=1, author=self.actor, origin_type='self_report', capture_method='manual',
                source_revision=self.evidence.source, source_organization=OTHER + ':adjacent_org',
                use_authorization_id=self.authorization.id, quality_status='valid', quality_reasons=[],
                correction_reason=OTHER + ':correction', corrected_at=self.now, recorded_at=self.now,
                retention_policy=self.evidence.retention)
        self.field = self.field_row(self.first, self.medical_time, CANARY, source=True)
        self.adjacent_field = self.field_row(self.second, self.adjacent_time, OTHER)
        RecordAttachment.objects.create(record_version_id=self.first.id, patient_id=self.patient.id,
            file_id=self.original.id, ordinal=1)
        # Native archive mirrors required by normal record detail serialization.
        self.fact_rows = []
        for version, field in [(self.first, self.field), (self.second, self.adjacent_field)]:
            with transaction.atomic():
                fact = ArchiveFact.objects.create(patient_id=self.patient.id, current_version=1, created_at=self.now)
                self.fact_rows.append(ArchiveFactVersion.objects.create(patient_id=self.patient.id,
                    fact_id=fact.id, version=1, record_version_id=version.id, field_id=field.id,
                    source_anchor_id=field.anchor_id, medical_time_id=field.medical_time_id,
                    confirmation_id=field.confirmation_id, context_type='unknown', assertion_state='unknown',
                    raw_time_text=CANARY + ':fact_time' if version == self.first else OTHER,
                    time_origin='item', available_at=self.now, availability_status='known', created_at=self.now))
        self.mapping = UnitMapping.objects.create(config=self.config, concept_code='synthetic_measurement',
            source_revision=self.evidence.source, raw_unit='synthetic_unit', normalized_unit='synthetic_unit',
            method_scope='synthetic_only', multiplier=1, additive=0, exact_values_only=True)
        self.normalized = NormalizedMeasurement.objects.create(patient_id=self.patient.id, field_id=self.field.id,
            mapping=self.mapping, normalized_value=Decimal('987.654'), normalized_unit=CANARY + ':normalized_unit',
            created_at=self.now)
        self.profile = PatientProfileVersion.objects.create(patient_id=self.patient.id,
            record_version_id=self.first.id, source_field_id=self.field.id, sex='unknown',
            age_raw_text=CANARY + ':age', age_reference_time_id=self.medical_time.id,
            patient_name=CANARY + ':patient_name', chief_complaint=CANARY + ':complaint')
        Patient.objects.filter(id=self.patient.id).update(current_profile_id=self.profile.id)
        self.statement = ClinicalStatement.objects.create(patient_id=self.patient.id, record_version_id=self.first.id,
            source_field_id=self.field.id, statement_type='symptom', subject='patient', category=CANARY + ':category',
            concept_code=CANARY + ':concept', label=CANARY + ':statement', assertion_status='present',
            status_raw_text=CANARY + ':status', medical_time_id=self.medical_time.id, body_site=CANARY + ':site')
        self.snapshot = {'record_version_id': str(self.first.id), 'field_id': str(self.field.id),
            'clinical_text': CANARY + ':embedded_snapshot', 'nested': {'source_quote': CANARY + ':nested'}}
        self.summary_task = self.task_row(copy.deepcopy(self.snapshot))
        self.attempt = TaskAttempt.objects.create(task=self.summary_task, attempt_number=1, lease_generation=1,
            provider_request_id='synthetic-summary', started_at=self.now, status='running',
            usage_evidence={'clinical_copy': CANARY + ':usage'})
        self.summary = Summary.objects.create(patient=self.patient, requested_by=self.actor,
            from_date=date(2026, 1, 1), to_date=date(2026, 1, 3), scope_timezone='UTC', include_unknown_time=True,
            template=self.config, visibility_policy=self.config, validity='valid', task_id=self.summary_task.id,
            generated_at=self.now, execution_snapshot=copy.deepcopy(self.snapshot), execution_sha256=digest(self.snapshot))
        self.summary_item = SummaryItem.objects.create(patient_id=self.patient.id, summary_id=self.summary.id,
            record_version_id=self.first.id, field_id=self.field.id, ordinal=1, snapshot=copy.deepcopy(self.snapshot))
        self.outbox = OutboxEvent.objects.create(event_type='synthetic.content', schema_version='synthetic-event-1',
            aggregate_id=self.summary.id, aggregate_version=1, occurred_at=self.now, correlation_id=uuid4(),
            task=self.summary_task, resource_refs=copy.deepcopy(self.snapshot), available_at=self.now)
        self.request_payload = {'fields': [{'field_code': 'synthetic_measurement', 'raw_text': CANARY + ':recovery'}]}
        self.intent_args = dict(actor=self.actor, patient=self.patient, operation_id='record_create',
            target_key=f'/api/patients/{self.patient.id}/records', request_id=uuid4(),
            payload=self.request_payload, schema_version='back-json-1', retention_policy=self.evidence.request_retention,
            recovery_payload=self.request_payload, authorization_context={'purpose': PURPOSE, 'note': CANARY + ':auth_copy'})
        self.intent = prepare_intent(**self.intent_args)
        self.request = commit_intent(intent=self.intent, action='record_create', resource_type='record', resource_id=self.record.id)
        self.other_time = self.time_row(self.other, OTHER + ':other_patient_time')

    def add_all_case_domains(self):
        from case_details.domain_import import import_domain_entities
        from tests.synthetic_case_projection import complete_projection
        minimal = {'fields': [{'id': str(self.field.id), 'patient_id': str(self.patient.id),
            'record_version_id': str(self.first.id), 'medical_time_id': str(self.medical_time.id)}],
            'case_details': {}}
        projection, ids = complete_projection(minimal)
        remap = {ids['time']: str(self.medical_time.id), ids['numeric']: str(self.field.id),
            ids['report']: str(self.report.id), ids['file']: str(self.original.id)}
        def copied(value):
            if isinstance(value, dict):
                return {key: copied(item) for key, item in value.items()}
            if isinstance(value, list):
                return [copied(item) for item in value]
            if isinstance(value, str):
                if value in remap:
                    return remap[value]
                if 'synthetic' in value.lower():
                    return CANARY + ':' + value
            return value
        case = copied(projection['case_details'])
        case['profiles'] = []  # The chain already owns the one profile/version.
        with transaction.atomic():
            import_domain_entities(case=case, patient=self.patient, source_revision=self.evidence.source,
                original_files={str(self.original.id): self.original}, now=self.now,
                existing_report_ids={self.report.id})
        return {label for label in REGISTRY if label.startswith('case_details.')}

    def field_row(self, version, medical_time, prefix, source=False):
        return RecordField.objects.create(patient=self.patient, record_version_id=version.id, ordinal=1,
            field_code='synthetic_measurement', concept_code=prefix + ':field_concept', original_name=prefix + ':name',
            value_type='decimal', value_state='known', raw_text=prefix + ':raw', raw_numeric=Decimal('987.654'),
            qualifier='eq', raw_unit=prefix + ':unit', method=prefix + ':method', reference_interval=prefix + ':range',
            report_flag=prefix + ':flag', medical_time_id=medical_time.id, sample_time_origin='item',
            anchor_id=self.anchor.id if source else None, confirmation_id=self.confirmation.id if source else None,
            quality_reasons=[prefix + ':field_quality'])

    def policy(self, mode='erase_selected_content', **changes):
        rules = {'schema_version': 'content-disposition-policy-1', 'mode': mode,
            'profile': 'relationship-skeleton-1', 'registry_sha256': REGISTRY_SHA256,
            'preservation_sha256': PRESERVATION_SHA256, 'allowed_adapters': sorted(REGISTRY),
            'access_purpose': PURPOSE, 'retention_seconds': 0, 'clock': 'recorded_at',
            'receipt_keep_until': (self.now + timedelta(days=30)).isoformat(),
            'metadata_keep_until': (self.now + timedelta(days=30)).isoformat(),
            'recheck_at': (self.now + timedelta(days=1)).isoformat(), 'local_objects': True,
            'all_copies_required': False, **changes}
        ContentPolicyRules.model_validate(rules)
        from retention.content_schemas import ContentPolicyAttestation
        policy_id=uuid4()
        attestation=ContentPolicyAttestation(schema_version='content-disposition-policy-approval-1',
            policy_id=policy_id,policy_version=1,environment=settings.SJIA_ENVIRONMENT,
            rules_sha256=digest(rules),approved_by=self.actor.id,approved_at=self.now,
            execution_enabled=True).model_dump(mode='json')
        source, _ = self.evidence.evidence(attestation, purposes=['configuration_approval'])
        return ContentDispositionPolicy.objects.create(id=policy_id, version=1, environment=settings.SJIA_ENVIRONMENT,
            rules=rules, checksum=digest(rules), source_revision=source, approved_by=self.actor,
            approved_at=self.now, enabled=True, created_at=self.now)

    def target(self, row):
        return {'kind': 'database', 'adapter': row._meta.label_lower, 'object_id': str(row.pk), 'version': row_version(row)}

    def preview(self, policy=None, roots=None):
        return preview_plan(actor=self.actor, auth_epoch=self.actor.auth_epoch, patient_id=self.patient.id,
            policy_id=(policy or self.policy()).id, data={'schema_version': 'content-disposition-request-1',
                'targets': [self.target(row) for row in (roots or [self.first, self.second])],
                'hold_id': str(self.hold.id), 'hold_version': self.hold.version})

    def approve(self, plan):
        source, _ = self.evidence.evidence(plan.manifest, purposes=['configuration_approval'])
        self.assertEqual(source.checksum, plan.manifest_sha256)
        return approve_plan(plan_id=plan.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            expected_manifest_sha256=plan.manifest_sha256, source_revision_id=source.id)

    def execute(self, plan):
        return execute_plan(plan_id=plan.id, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            expected_manifest_sha256=plan.manifest_sha256)

    def envelope(self, plan):
        return {'schema_version': 'content-disposition-journal-1', 'plan_id': str(plan.id),
            'manifest_sha256': plan.manifest_sha256, 'manifest': plan.manifest, 'approval': plan.approval}

    def assert_rejected(self, code, function, *args, **kwargs):
        with self.assertRaises(DispositionRejected) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.reason_code, code)

    def assert_payload_erased(self, plan):
        for item in plan.manifest['items']:
            if item['kind'] == 'database':
                model, spec = adapter(item['adapter'])
                row = model.all_objects.get(pk=item['object_id'])
                with self.subTest(adapter=item['adapter'], identity=item['object_id']):
                    self.assertIsNotNone(row.content_erased_at)
                    self.assertEqual(row.content_disposition_id, plan.id)
                    for field in spec['payload_fields']:
                        self.assertIsNone(getattr(row, field), field)
                    self.assertFalse(model.objects.filter(pk=row.pk).exists())
                    self.assertTrue(ContentErasure.objects.filter(adapter=item['adapter'], object_id=row.pk,
                        plan_id=plan.id, proof_sha256=digest(item)).exists())
            else:
                row = FileObject.objects.get(pk=item['file_id'])
                self.assertEqual(row.status, 'disposed')
                self.assertFalse(self.store.exists(row.object_key))
        self.assertTrue(verify_manifest(self.envelope(plan)))

    def test_native_chain_canaries_real_bytes_erased_and_relationships_preserved(self):
        from records.queries import record_version_read
        case_adapters = self.add_all_case_domains()
        before = record_version_read(patient=self.patient, record_version_id=self.first.id)
        self.assertEqual(before['fields'][0]['raw_text'], CANARY + ':raw')
        self.assertEqual(before['fields'][0]['source_anchor']['original_text'], CANARY + ':anchor')
        plan = self.approve(self.preview())
        expected_adapters = {'records.recordversion', 'records.recordfield', 'records.report',
            'records.normalizedmeasurement', 'medical_time.medicaltime', 'recognition.sourceanchor',
            'recognition.candidateconfirmation', 'recognition.candidatefield', 'recognition.candidateset',
            'case_details.patientprofileversion', 'case_details.clinicalstatement', 'archives.archivefactversion',
            'tasks.task', 'tasks.taskattempt', 'tasks.outboxevent', 'summaries.summary', 'summaries.summaryitem',
            'requests.writeintent', 'requests.writerequest'}
        expected_adapters |= case_adapters
        self.assertTrue(expected_adapters <= {item.get('adapter') for item in plan.manifest['items']})
        self.assertEqual(len(expected_adapters), 35)
        self.assertEqual({item['file_id'] for item in plan.manifest['items'] if item['kind'] == 'local_object'},
            {str(self.original.id), str(self.response_file.id)})
        preserved = {}
        for item in plan.manifest['items']:
            if item['kind'] == 'database':
                model, spec = adapter(item['adapter'])
                row = model.all_objects.get(pk=item['object_id'])
                preserved[(item['adapter'], item['object_id'])] = {
                    name: getattr(row, model._meta.get_field(name).attname) for name in spec['preserved_fields']}
        plan = self.execute(plan)
        self.assertEqual(plan.status, 'logical_erasure_complete')
        self.assert_payload_erased(plan)
        for (label, identity), values in preserved.items():
            model, _ = adapter(label)
            row = model.all_objects.get(pk=identity)
            self.assertEqual(values, {name: getattr(row, model._meta.get_field(name).attname) for name in values})
        self.record.refresh_from_db()
        self.assertEqual(self.record.current_version, 2)
        self.assertEqual(RecordVersion.all_objects.get(id=self.second.id).supersedes_version, 1)
        self.assertTrue(RecordAttachment.objects.filter(record_version_id=self.first.id, file_id=self.original.id).exists())
        self.assertEqual(MedicalTime.objects.get(id=self.other_time.id).raw_text, OTHER + ':other_patient_time')
        self.assertTrue(plan.receipt['logical_payload_erased'])
        self.assertFalse(plan.receipt['all_copies_erased'])
        self.assertEqual(set(plan.receipt['unverified_copy_obligations']),
            {'physical_database_storage', 'backup_copies', 'external_provider_copies'})
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'], 1)
        self.assertNotIn(CANARY, json.dumps([row_values(row) for row in patient_rows(self.patient.id)]))
        for path in Path(self.store.root).rglob('*'):
            if path.is_file():
                self.assertNotIn(CANARY.encode(), path.read_bytes(), str(path))

    def test_every_registered_adapter_executes_native_cross_domain_closure(self):
        from tests.synthetic_content_disposition import add_cross_domain_canaries
        self.add_all_case_domains()
        rows = add_cross_domain_canaries(self, CANARY)
        plan = self.approve(self.preview())
        actual = {item['adapter'] for item in plan.manifest['items'] if item['kind'] == 'database'}
        self.assertEqual(actual, set(REGISTRY), 'Every registered type must have an actual manifest row')
        for label,spec in REGISTRY.items():
            if spec['payload_fields']:
                model,_=adapter(label)
                ids=[item['object_id'] for item in plan.manifest['items'] if item.get('adapter')==label]
                self.assertTrue(any(any(getattr(row,name) is not None for name in spec['payload_fields'])
                    for row in model.all_objects.filter(pk__in=ids)),f'{label}: positive erasure needs actual non-null payload')
        expected_rows = {(row._meta.label_lower, str(row.pk)) for row in rows}
        manifest_rows = {(item['adapter'], item['object_id']) for item in plan.manifest['items'] if item['kind'] == 'database'}
        self.assertTrue(expected_rows <= manifest_rows, expected_rows - manifest_rows)
        result = self.execute(plan)
        self.assert_payload_erased(result)
        self.assertNotIn(CANARY, json.dumps([row_values(row) for row in patient_rows(self.patient.id)]))
        task = Task.all_objects.get(id=self.cross_domain['evaluation_task'].id)
        self.assertIsNone(task.patient_id)
        self.assertIsNone(task.input_refs)
        self.assertTrue(TaskAttempt.all_objects.filter(task=task, usage_evidence__isnull=True).exists())
        self.assertFalse(result.receipt['all_copies_erased'])

    def test_shared_evaluation_run_is_refused_before_any_patient_payload_changes(self):
        from archives.models import EvaluationPatientPartition
        from tests.synthetic_content_disposition import add_cross_domain_canaries
        add_cross_domain_canaries(self, CANARY)
        foreign = EvaluationPatientPartition.objects.create(patient_id=self.other.id,
            dataset_version_id=self.cross_domain['dataset'].id, partition='validation',
            use_authorization_id=self.other_authorization.id, source_refs=[{'body': OTHER}])
        self.assert_rejected('CONTENT_SHARED_EVALUATION_PARTITION_REQUIRED', self.preview)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertFalse(ContentDispositionPlan.objects.exists())
        self.assertEqual(EvaluationPatientPartition.objects.get(id=foreign.id).source_refs, [{'body': OTHER}])
        self.assertEqual(RecordField.objects.get(id=self.field.id).raw_text, CANARY + ':raw')

    def test_typed_summary_task_owner_closes_copy_without_source_ids_in_task_json(self):
        body = {'clinical_body': CANARY + ':copy_without_source_identity'}
        Task.objects.filter(id=self.summary_task.id).update(input_refs=body, input_sha256=input_digest(body))
        plan = self.approve(self.preview())
        keys = {(item.get('adapter'), item.get('object_id')) for item in plan.manifest['items']}
        self.assertIn(('tasks.task', str(self.summary_task.id)), keys)
        self.assertIn(('tasks.taskattempt', str(self.attempt.id)), keys)
        self.assert_payload_erased(self.execute(plan))

    def test_explicit_retain_policy_preserves_same_chain_without_erasure_proof(self):
        plan = self.approve(self.preview(self.policy('retain')))
        hashes = {(item['adapter'], item['object_id']): item['row_sha256']
            for item in plan.manifest['items'] if item['kind'] == 'database'}
        result = self.execute(plan)
        self.assertEqual(result.status, 'retained')
        self.assertFalse(result.receipt['logical_payload_erased'])
        self.assertFalse(ContentErasure.objects.exists())
        self.assertFalse(ContentAccessRevocation.objects.exists())
        for (label, identity), checksum in hashes.items():
            model, _ = adapter(label)
            self.assertEqual(row_digest(model.objects.get(pk=identity)), checksum)
        self.assertEqual(self.store.get(self.original.object_key), self.object_bytes[self.original.id])
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'], 0)

    def test_explicit_revoke_access_hides_content_without_claiming_or_performing_erasure(self):
        plan = self.approve(self.preview(self.policy('revoke_access')))
        result = self.execute(plan)
        self.assertEqual(result.status, 'access_revoked')
        self.assertFalse(result.receipt['logical_payload_erased'])
        self.assertFalse(ContentErasure.objects.exists())
        self.assertTrue(ContentAccessRevocation.objects.exists())
        self.assertFalse(RecordField.objects.filter(id=self.field.id).exists())
        self.assertEqual(RecordField.all_objects.get(id=self.field.id).raw_text, CANARY + ':raw')
        self.assertEqual(self.store.get(self.original.object_key), self.object_bytes[self.original.id])
        self.assertTrue(verify_manifest(self.envelope(result)))

    def test_missing_policy_and_runtime_disabled_cannot_erase(self):
        self.assertFalse(ContentDispositionPolicy.objects.exists())
        with self.assertRaises((ContentDispositionPolicy.DoesNotExist, DispositionRejected)):
            preview_plan(actor=self.actor, auth_epoch=self.actor.auth_epoch, patient_id=self.patient.id,
                policy_id=uuid4(), data={'schema_version': 'content-disposition-request-1',
                    'targets': [self.target(self.first)], 'hold_id': str(self.hold.id), 'hold_version': 1})
        plan = self.approve(self.preview())
        with override_settings(SJIA_CONTENT_DISPOSITION_ENABLED=False):
            self.assert_rejected('CONTENT_EXECUTION_DISABLED', self.execute, plan)
        self.assertEqual(RecordField.objects.get(id=self.field.id).raw_text, CANARY + ':raw')
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'], 0)

    def test_hold_unknown_expired_or_authority_revoked_cannot_execute(self):
        plan = self.approve(self.preview())
        ContentDispositionHold.objects.filter(id=self.hold.id).update(state='unresolved')
        self.assert_rejected('CONTENT_HOLD_UNRESOLVED', self.execute, plan)
        ContentDispositionHold.objects.filter(id=self.hold.id).update(state='clear', valid_until=self.now - timedelta(seconds=1))
        self.assert_rejected('CONTENT_HOLD_UNRESOLVED', self.execute, plan)
        ContentDispositionHold.objects.filter(id=self.hold.id).update(valid_until=self.now + timedelta(days=1))
        self.role.revoked_at = timezone.now()
        self.role.save(update_fields=['revoked_at'])
        from authorization.policy import Denied
        with self.assertRaises(Denied):
            self.execute(plan)
        self.assertFalse(ContentErasure.objects.exists())

    def test_policy_checksum_and_approval_source_bytes_must_match(self):
        policy = self.policy()
        changed = {**policy.rules, 'retention_seconds': 100}
        ContentDispositionPolicy.objects.filter(id=policy.id).update(rules=changed)
        self.assert_rejected('CONTENT_POLICY_CONTRACT_UNAVAILABLE', self.preview, policy)
        plan = self.preview()
        unrelated, _ = self.evidence.evidence({'synthetic': 'wrong manifest'}, purposes=['configuration_approval'])
        self.assert_rejected('CONTENT_APPROVAL_EVIDENCE_UNAVAILABLE', approve_plan, plan_id=plan.id,
            actor=self.actor, auth_epoch=self.actor.auth_epoch, expected_manifest_sha256=plan.manifest_sha256,
            source_revision_id=unrelated.id)
        self.assertFalse(ContentErasure.objects.exists())

    def test_wrong_target_version_or_unknown_adapter_is_not_an_executable_plan(self):
        policy = self.policy()
        data = {'schema_version': 'content-disposition-request-1', 'targets': [self.target(self.first)],
            'hold_id': str(self.hold.id), 'hold_version': 1}
        data['targets'][0]['version'] = '999'
        self.assert_rejected('CONTENT_VERSION_CONFLICT', preview_plan, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            patient_id=self.patient.id, policy_id=policy.id, data=data)
        data['targets'][0]['adapter'] = 'arbitrary.unregistered'
        self.assert_rejected('CONTENT_TARGET_UNAVAILABLE', preview_plan, actor=self.actor, auth_epoch=self.actor.auth_epoch,
            patient_id=self.patient.id, policy_id=policy.id, data=data)

    def test_exact_adjacent_version_leaves_other_version_patient_and_files_intact(self):
        first_hash = row_digest(self.first)
        plan = self.execute(self.approve(self.preview(roots=[self.second])))
        self.assert_payload_erased(plan)
        self.assertEqual(row_digest(RecordVersion.objects.get(id=self.first.id)), first_hash)
        self.assertEqual(RecordField.objects.get(id=self.field.id).raw_text, CANARY + ':raw')
        self.assertEqual(MedicalTime.objects.get(id=self.other_time.id).raw_text, OTHER + ':other_patient_time')
        self.assertEqual(self.store.get(self.original.object_key), self.object_bytes[self.original.id])

    def test_shared_medical_time_requires_explicit_adjacent_version_scope(self):
        RecordField.objects.filter(id=self.adjacent_field.id).update(medical_time_id=self.medical_time.id)
        self.assert_rejected('CONTENT_SHARED_CANONICAL_SCOPE', self.preview, roots=[self.first])
        self.assertFalse(ContentDispositionPlan.objects.exists())
        self.assertEqual(MedicalTime.objects.get(id=self.medical_time.id).raw_text, CANARY + ':medical_time')

    def test_shared_local_original_requires_explicit_adjacent_version_scope(self):
        RecordAttachment.objects.create(patient_id=self.patient.id, record_version_id=self.second.id,
            file_id=self.original.id, ordinal=1)
        self.assert_rejected('CONTENT_SHARED_LOCAL_OBJECT_SCOPE', self.preview, roots=[self.first])
        self.assertEqual(self.store.get(self.original.object_key), self.object_bytes[self.original.id])

    def test_new_embedded_copy_after_approval_invalidates_the_exact_manifest(self):
        plan = self.approve(self.preview())
        SummaryItem.objects.create(patient_id=self.patient.id, summary_id=self.summary.id,
            record_version_id=self.second.id, field_id=self.adjacent_field.id, ordinal=2,
            snapshot={'record_version_id': str(self.first.id), 'late_copy': CANARY + ':late_copy'})
        self.assert_rejected('CONTENT_CLOSURE_CHANGED', self.execute, plan)
        self.assertEqual(RecordField.objects.get(id=self.field.id).raw_text, CANARY + ':raw')
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'], 0)

    def test_mutated_payload_after_approval_invalidates_the_exact_manifest(self):
        plan = self.approve(self.preview())
        CandidateField.objects.filter(id=self.candidate.id).update(raw_text=CANARY + ':changed')
        self.assert_rejected('CONTENT_MANIFEST_CHANGED', self.execute, plan)
        self.assertFalse(ContentErasure.objects.exists())

    def test_disconnected_explicit_roots_remain_bound_during_execution(self):
        todo = PersonalRecordTask.objects.create(patient_id=self.patient.id, owner_id=self.actor.id,
            title=CANARY + ':todo', description=CANARY + ':todo_body', version=1, status='pending',
            retention_policy=self.evidence.retention, created_at=self.now, updated_at=self.now)
        independent_task = self.task_row({'synthetic_body': CANARY + ':independent_task'})
        plan = self.approve(self.preview(self.policy(clock='created_at'), roots=[independent_task, todo]))
        self.assertEqual({root['object_id'] for root in plan.manifest['roots']},
            {str(independent_task.id), str(todo.id)})
        self.assert_payload_erased(self.execute(plan))
        self.assertEqual(RecordField.objects.get(id=self.field.id).raw_text, CANARY + ':raw')

    def test_all_copy_requirement_cannot_be_satisfied_by_local_or_simulated_proof(self):
        self.assert_rejected('CONTENT_ALL_COPIES_PROTOCOL_UNAVAILABLE', self.preview,
            self.policy(all_copies_required=True))
        self.assertFalse(ContentErasure.objects.exists())

    def test_reexecution_reuses_exact_journal_and_receipt_identity(self):
        plan = self.execute(self.approve(self.preview()))
        first_receipt = copy.deepcopy(plan.receipt)
        count = ContentErasure.objects.count()
        repeated = self.execute(plan)
        self.assertEqual(repeated.receipt, first_receipt)
        self.assertEqual(ContentErasure.objects.count(), count)
        journal = configured_journal().verify()
        self.assertEqual(len(journal['entries']), 1)
        self.assertEqual(journal['checkpoint']['sequence'], 1)
        self.assert_payload_erased(repeated)

    def test_old_request_replay_is_a_disposed_identity_not_a_new_write(self):
        self.execute(self.approve(self.preview()))
        before = WriteIntent.all_objects.count()
        with self.assertRaises(ContentDisposed):
            prepare_intent(**self.intent_args)
        self.assertEqual(WriteIntent.all_objects.count(), before)
        retained = WriteIntent.all_objects.get(id=self.intent.id)
        self.assertIsNone(retained.recovery_payload)
        self.assertEqual(retained.payload_sha256, self.intent.payload_sha256)
        self.assertEqual(retained.write_request_id, self.request.id)

    def test_late_worker_completion_and_failure_cannot_repopulate_attempt_evidence(self):
        from tasks.service import complete_task, fail_task, finish_attempt, heartbeat
        self.execute(self.approve(self.preview()))
        for callback in [lambda: finish_attempt(self.attempt, 'completed', usage_evidence={'late': CANARY}),
                         lambda: finish_attempt(self.attempt, 'failed', usage_evidence={'late': CANARY})]:
            with self.assertRaises(ContentDisposed):
                callback()
        self.assertEqual(complete_task(self.summary_task.id, 1, 'summary', self.summary.id), 0)
        self.assertEqual(fail_task(self.summary_task.id, 1, 'SYNTHETIC_LATE_FAILURE'), 0)
        self.assertEqual(heartbeat(self.summary_task.id, 1, timezone.now() + timedelta(minutes=1)), 0)
        self.assertIsNone(TaskAttempt.all_objects.get(id=self.attempt.id).usage_evidence)

    def test_live_constraints_stay_real_and_erased_rows_are_immutable(self):
        for change in [{'correction_reason': None}, {'supersedes_version': 77}]:
            with self.assertRaises(IntegrityError), transaction.atomic():
                RecordVersion.objects.filter(id=self.second.id).update(**change)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NormalizedMeasurement.objects.filter(id=self.normalized.id).update(normalized_value=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecordField.objects.filter(id=self.field.id).update(record_version_id=uuid4())
        plan = self.execute(self.approve(self.preview()))
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecordField.all_objects.filter(id=self.field.id).update(raw_text=CANARY + ':resurrected')
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecordVersion.all_objects.filter(id=self.first.id).update(source_organization=CANARY + ':resurrected')
        self.assert_payload_erased(plan)

    def test_new_nested_copy_referencing_erased_identity_is_rejected_by_database(self):
        self.execute(self.approve(self.preview()))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.task_row({'nested': [{'source': f'urn:sjia:record-version:{self.first.id}', 'clinical': CANARY}]})
        with self.assertRaises(IntegrityError), transaction.atomic():
            RecordVersion.objects.create(patient=self.patient, record_id=self.record.id, version=3,
                supersedes_version=2, author=self.actor, origin_type='self_report', capture_method='manual',
                use_authorization_id=self.authorization.id, quality_status='valid', quality_reasons=[],
                correction_reason=CANARY, corrected_at=self.now, recorded_at=self.now,
                retention_policy=self.evidence.retention)

    def test_read_paths_and_stale_in_memory_version_cannot_expose_erased_content(self):
        from records.queries import record_version_read, source_resolve
        from records.service import read_version
        self.execute(self.approve(self.preview()))
        with self.assertRaises(RecordVersion.DoesNotExist):
            RecordVersion.objects.get(id=self.first.id)
        with self.assertRaises(RecordVersion.DoesNotExist):
            RecordVersion.objects.filter(patient=self.other).get(id=self.first.id)
        for read in [lambda: record_version_read(patient=self.patient, record_version_id=self.first.id),
                     lambda: source_resolve(patient=self.patient, record_version_id=self.first.id, field_id=self.field.id),
                     lambda: read_version(self.first, self.record)]:
            with self.assertRaises(ContentDisposed):
                read()

    def test_real_http_version_read_returns_content_then_typed_disposition(self):
        from api.views_family import record_version_read_view
        from relations.models import Relation, RelationApplication
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor, case=self)
        application = RelationApplication.objects.create(applicant=self.actor, patient=self.patient,
            version=1, evidence_ref=self.evidence.reference, status='approved', checked_by=self.actor,
            checked_at=self.now, created_at=self.now)
        relation = Relation.objects.create(patient=self.patient, guardian=self.actor, application=application,
            status='active', version=1, checked_by=self.actor, checked_at=self.now, valid_from=self.now)
        AccessGrant.objects.create(principal=self.actor, patient=self.patient, relation_id=relation.id,
            action='read_history', field_codes=['synthetic_measurement'], record_scope='all',
            purpose='family_observation', granted_by=self.actor, evidence_ref=self.evidence.reference,
            valid_from=self.now)
        def request_version():
            request = RequestFactory().get(f'/api/record-versions/{self.first.id}')
            request.session = {'_sjia_principal_id': str(self.actor.id),
                '_sjia_auth_epoch': self.actor.auth_epoch, '_sjia_audience': 'family'}
            return record_version_read_view(request, record_version_id=self.first.id)
        before = request_version()
        self.assertEqual(before.status_code, 200, before.data)
        self.assertEqual(before.data['fields'][0]['raw_text'], CANARY + ':raw')
        self.execute(self.approve(self.preview()))
        after = request_version()
        self.assertEqual(after.status_code, 410, after.data)
        self.assertEqual(after.data['code'], 'CONTENT_DISPOSED')
        self.assertNotIn(CANARY, json.dumps(after.data))
        self.assertEqual(after['Cache-Control'], 'no-store, private')

    def test_active_reader_blocks_erasure_until_response_lifetime_finishes(self):
        from retention.content_fences import content_reader
        plan = self.approve(self.preview())
        started, finished = threading.Event(), threading.Event()
        result = {}
        def erase_in_other_connection():
            try:
                worker = connections['default']
                with worker.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    result['pid'] = cursor.fetchone()[0]
                started.set()
                result['plan'] = self.execute(plan)
            except BaseException as exc:
                result['error'] = exc
            finally:
                connections['default'].close()
                finished.set()
        thread = threading.Thread(target=erase_in_other_connection, daemon=True)
        try:
            with content_reader():
                thread.start()
                self.assertTrue(started.wait(10), 'worker did not open an independent PostgreSQL connection')
                deadline = time.monotonic() + 10
                blocked = False
                while time.monotonic() < deadline and not finished.is_set():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted)",
                            [result['pid']])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    time.sleep(0.02)
                self.assertTrue(blocked, f'expected an observed PostgreSQL advisory-lock wait, got {result}')
                self.assertFalse(finished.is_set())
                self.assertEqual(RecordField.objects.get(id=self.field.id).raw_text, CANARY + ':raw')
        finally:
            thread.join(timeout=30)
        self.assertTrue(finished.is_set(), 'erasure did not continue when reader released its lock')
        if 'error' in result:
            raise result['error']
        self.assert_payload_erased(result['plan'])

    def test_hold_clearance_is_bound_to_patient_review_and_expiry_bytes(self):
        plan = self.approve(self.preview())
        self.hold.valid_until += timedelta(days=30)
        self.hold.save(update_fields=['valid_until'])
        with self.assertRaisesRegex(DispositionRejected,'CONTENT_HOLD_EVIDENCE_UNAVAILABLE'):
            self.execute(plan)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertTrue(self.store.get(self.original.object_key))


    def http_execute(self, plan):
        from api.views_content_disposition import content_disposition_execute
        from django.middleware.csrf import _get_new_csrf_string
        csrf=_get_new_csrf_string()
        request=RequestFactory().post(f'/api/v2/support/content-disposition/plans/{plan.id}/execute',
            data=json.dumps({'expected_manifest_sha256':plan.manifest_sha256}),content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf)
        request.COOKIES[settings.CSRF_COOKIE_NAME]=csrf
        request.session={'_sjia_principal_id':str(self.actor.id),'_sjia_auth_epoch':self.actor.auth_epoch,'_sjia_audience':'support'}
        if settings.CSRF_USE_SESSIONS:
            from django.middleware.csrf import CSRF_SESSION_KEY
            request.session[CSRF_SESSION_KEY]=csrf
        return content_disposition_execute(request,plan_id=plan.id)

    def test_http_outcome_audit_failure_does_not_rollback_fences_after_unlink(self):
        from unittest.mock import patch
        from audit import api as audit_api
        from audit.models import AuditEvent
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor,case=self)
        plan=self.approve(self.preview())
        original_record=audit_api._record
        observed=[]
        def record(request,operation,route,response,**kwargs):
            if operation=='content_disposition_execute' and not kwargs.get('admission'):
                observed.append((response.status_code,response.data))
                raise audit_api.AuditUnavailable()
            return original_record(request,operation,route,response,**kwargs)
        with patch.object(audit_api,'_record',side_effect=record):
            response=self.http_execute(plan)
        self.assertEqual(response.status_code,503,response.data)
        self.assertEqual(response.data['code'],'AUDIT_UNAVAILABLE')
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0][0],200,observed)
        self.assertTrue(AuditEvent.objects.filter(action='content_disposition_execute',object_type='plan',
            object_id=plan.id,reason_code='ADMISSION').exists())
        plan.refresh_from_db()
        self.assertEqual(plan.status,'logical_erasure_complete')
        self.assert_payload_erased(plan)
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'],1)
        repeated=self.http_execute(plan)
        self.assertEqual(repeated.status_code,200,repeated.data)
        self.assertEqual(repeated.data['manifest_sha256'],plan.manifest_sha256)
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'],1)

    def test_http_audit_admission_failure_leaves_payload_and_journal_unchanged(self):
        from unittest.mock import patch
        from audit.api import AuditUnavailable
        plan=self.approve(self.preview())
        with patch('audit.api._record',side_effect=AuditUnavailable):
            response=self.http_execute(plan)
        self.assertEqual(response.status_code,503)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertEqual(self.store.get(self.original.object_key),self.object_bytes[self.original.id])
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'],0)

    def test_outer_transaction_cannot_hide_local_unlink_commit(self):
        plan=self.approve(self.preview())
        with transaction.atomic():
            self.assert_rejected('CONTENT_OUTER_TRANSACTION_FORBIDDEN',self.execute,plan)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertTrue(self.store.get(self.original.object_key))

    def test_policy_identity_environment_approver_and_enablement_are_evidence_bound(self):
        policy=self.policy()
        for change in [{'version':2},{'approved_at':self.now-timedelta(seconds=1)},{'enabled':False}]:
            with self.subTest(change=change):
                ContentDispositionPolicy.objects.filter(id=policy.id).update(**change)
                self.assert_rejected('CONTENT_POLICY_EVIDENCE_UNAVAILABLE',self.preview,policy)
                ContentDispositionPolicy.objects.filter(id=policy.id).update(version=1,approved_at=self.now,enabled=True)
        cloned=ContentDispositionPolicy.objects.create(version=policy.version,environment=policy.environment,
            rules=policy.rules,checksum=policy.checksum,source_revision=policy.source_revision,
            approved_by=self.actor,approved_at=self.now,enabled=True,created_at=self.now)
        self.assert_rejected('CONTENT_POLICY_EVIDENCE_UNAVAILABLE',self.preview,cloned)
        self.assertFalse(ContentDispositionPlan.objects.exists())

    def test_tied_or_newer_hold_review_refuses_clearance(self):
        plan=self.approve(self.preview())
        for version in [self.hold.version,self.hold.version+1]:
            review=ContentDispositionHold.objects.create(patient=self.patient,source_revision=self.evidence.source,
                state='held',reviewed_by=self.actor,reviewed_at=self.now,valid_until=self.now+timedelta(days=1),version=version)
            self.assert_rejected('CONTENT_HOLD_REVIEW_SUPERSEDED',self.execute,plan)
            review.delete()
        self.assertFalse(ContentErasure.objects.exists())

    def alias_file(self,patient):
        group=self.group if patient==self.patient else FileGroup.objects.create(patient=patient,owner=self.actor,
            version=1,status='available',source_revision=self.evidence.source,
            use_authorization_id=self.other_authorization.id,created_at=self.now)
        return FileObject.objects.create(patient=patient,file_group_id=group.id,object_key=self.original.object_key,
            object_version='synthetic-alias-version',checksum=self.original.checksum,size_bytes=self.original.size_bytes,
            media_type=self.original.media_type,purpose=self.original.purpose,status='available',
            retention_policy=self.evidence.retention,created_at=self.now)

    def test_unselected_same_path_alias_in_same_or_other_patient_blocks_erase(self):
        for patient in [self.patient,self.other]:
            alias=self.alias_file(patient)
            self.assert_rejected('CONTENT_SHARED_LOCAL_PATH_SCOPE',self.preview)
            self.assertEqual(self.store.get(alias.object_key),self.object_bytes[self.original.id])
            alias.delete()
        self.assertFalse(ContentErasure.objects.exists())

    def test_same_path_alias_added_after_approval_blocks_execution(self):
        plan=self.approve(self.preview())
        self.alias_file(self.other)
        self.assert_rejected('CONTENT_SHARED_LOCAL_PATH_SCOPE',self.execute,plan)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertEqual(configured_journal().verify()['checkpoint']['sequence'],0)

    def test_new_same_path_alias_after_erasure_is_blocked_by_native_write_fence(self):
        self.execute(self.approve(self.preview()))
        with self.assertRaises(IntegrityError),transaction.atomic():
            self.alias_file(self.other)

    def test_unrelated_authenticated_user_cannot_probe_erased_original_or_file_status(self):
        from api.views_originals import record_originals_read
        from api.views_purpose import purpose_copy_options
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor,case=self)
        outsider=Principal.objects.create(account_status='active',created_at=self.now,updated_at=self.now)
        ServiceRoleGrant.objects.create(principal=outsider,environment=self.evidence.gate,action='manage_data_use',
            granted_by=self.actor,evidence_ref=self.evidence.reference,valid_from=self.now)
        def read(view,audience,**kwargs):
            request=RequestFactory().get('/synthetic-authorized-route')
            request.session={'_sjia_principal_id':str(outsider.id),'_sjia_auth_epoch':outsider.auth_epoch,'_sjia_audience':audience}
            return view(request,**kwargs)
        before_original=read(record_originals_read,'family',record_version_id=self.first.id)
        before_file=read(purpose_copy_options,'support',file_id=self.original.id,purpose='external_recognition')
        self.assertGreaterEqual(before_original.status_code,400)
        self.assertGreaterEqual(before_file.status_code,400)
        self.assertNotEqual(before_file.status_code,422,before_file.data)
        self.execute(self.approve(self.preview()))
        after_original=read(record_originals_read,'family',record_version_id=self.first.id)
        after_file=read(purpose_copy_options,'support',file_id=self.original.id,purpose='external_recognition')
        for before,after in [(before_original,after_original),(before_file,after_file)]:
            self.assertEqual(after.status_code,before.status_code,(before.data,after.data))
            self.assertEqual(after.data['code'],before.data['code'])
            self.assertNotIn(after.data['code'],['CONTENT_DISPOSED','CONTENT_ACCESS_REVOKED'])

    def test_due_review_reports_finite_deadlines_without_deleting_receipts_or_fences(self):
        from retention.content_lifecycle import lifecycle
        plan=self.execute(self.approve(self.preview()))
        before=ContentErasure.objects.count()
        state=lifecycle(plan,now=self.now+timedelta(days=31))
        self.assertTrue(state['review_due'])
        self.assertTrue(state['receipt_expiry_review_due'])
        self.assertTrue(state['metadata_expiry_review_due'])
        self.assertEqual(state['expiry_disposal_status'],'blocked_pending_backup_and_retry_window_proof')
        self.assertEqual({o['kind'] for o in state['unresolved_copy_obligations']},set(plan.manifest['obligations']))
        self.assertTrue(all(o['review_due_at']==state['next_recheck_at'] and not o['all_copies_erased'] for o in state['unresolved_copy_obligations']))
        plan.refresh_from_db()
        self.assertIsNotNone(plan.receipt)
        self.assertEqual(ContentErasure.objects.count(),before)

    def test_sourced_review_changes_recheck_only_and_is_idempotent(self):
        from retention.content_lifecycle import lifecycle,record_review
        from retention.content_schemas import ContentReviewAttestation
        from retention.models import ContentDispositionReview
        plan=self.execute(self.approve(self.preview(self.policy('retain'))))
        body=ContentReviewAttestation(schema_version='content-disposition-review-1',plan_id=plan.id,
            manifest_sha256=plan.manifest_sha256,policy_sha256=plan.manifest['policy_sha256'],review_version=1,
            reviewed_by=self.actor.id,reviewed_at=self.now,next_recheck_at=self.now+timedelta(days=2),
            reason_code='approved_retention_continues').model_dump(mode='json')
        source,_=self.evidence.evidence(body,purposes=['configuration_approval'])
        def review(data):
            return record_review(plan_id=plan.id,actor=self.actor,auth_epoch=self.actor.auth_epoch,
                data=data,source_revision_id=source.id)
        row=review(body)
        self.assertEqual(review(body).id,row.id)
        self.assertEqual(ContentDispositionReview.objects.count(),1)
        self.assertEqual(lifecycle(plan)['review_version'],1)
        self.assertEqual(lifecycle(plan)['next_recheck_at'],body['next_recheck_at'])
        self.assert_rejected('CONTENT_REVIEW_EVIDENCE_UNAVAILABLE',review,{**body,'review_version':2})
        self.assertFalse(ContentErasure.objects.exists())
        self.assertEqual(self.store.get(self.original.object_key),self.object_bytes[self.original.id])

    def test_authorized_choices_and_history_are_metadata_only_with_no_default_policy(self):
        from api.views_content_disposition import content_disposition_patients,content_disposition_options,content_disposition_history
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        from retention.content_schemas import ContentPatientsView,ContentOptionsView,ContentHistoryView
        ensure_synthetic_audit_policy(self.actor,case=self)
        plan=self.preview()
        def get(view,**kwargs):
            request=RequestFactory().get('/synthetic-content-control')
            request.session={'_sjia_principal_id':str(self.actor.id),'_sjia_auth_epoch':self.actor.auth_epoch,'_sjia_audience':'support'}
            return view(request,**kwargs)
        patients=get(content_disposition_patients)
        self.assertEqual(patients.status_code,200,patients.data)
        ContentPatientsView.model_validate(patients.data)
        self.assertEqual([row['patient_id'] for row in patients.data['patients']],[str(self.patient.id)])
        choices=get(content_disposition_options,patient_id=self.patient.id)
        self.assertEqual(choices.status_code,200,choices.data)
        ContentOptionsView.model_validate(choices.data)
        self.assertIsNone(choices.data['default_policy'])
        self.assertEqual(choices.data['policies'][0]['policy_id'],str(plan.policy_id))
        self.assertTrue(choices.data['hold']['eligible'])
        self.assertNotIn(CANARY,json.dumps(choices.data))
        history=get(content_disposition_history,patient_id=self.patient.id)
        self.assertEqual(history.status_code,200,history.data)
        ContentHistoryView.model_validate(history.data)
        self.assertEqual(history.data['plans'][0]['plan_id'],str(plan.id))
        self.assertNotIn(CANARY,json.dumps(history.data))
        self.grant.revoked_at=timezone.now();self.grant.save(update_fields=['revoked_at'])
        denied=get(content_disposition_options,patient_id=self.patient.id)
        self.assertEqual(denied.status_code,403,denied.data)
        self.assertNotIn('targets',denied.data)


    def test_new_same_path_alias_after_access_revocation_is_blocked(self):
        self.execute(self.approve(self.preview(self.policy('revoke_access'))))
        self.assertEqual(self.store.get(self.original.object_key),self.object_bytes[self.original.id])
        with self.assertRaises(IntegrityError),transaction.atomic():
            self.alias_file(self.other)

    def test_revoked_renewal_evidence_remains_historical_but_cannot_hide_due_review(self):
        from retention.content_lifecycle import lifecycle,record_review
        from retention.content_schemas import ContentReviewAttestation
        from retention.models import ContentDispositionReview
        plan=self.execute(self.approve(self.preview(self.policy('retain'))))
        body=ContentReviewAttestation(schema_version='content-disposition-review-1',plan_id=plan.id,
            manifest_sha256=plan.manifest_sha256,policy_sha256=plan.manifest['policy_sha256'],review_version=1,
            reviewed_by=self.actor.id,reviewed_at=self.now,next_recheck_at=self.now+timedelta(days=10),
            reason_code='approved_retention_continues').model_dump(mode='json')
        source,_=self.evidence.evidence(body,purposes=['configuration_approval'])
        kwargs={'plan_id':plan.id,'actor':self.actor,'auth_epoch':self.actor.auth_epoch,'data':body,'source_revision_id':source.id}
        review=record_review(**kwargs)
        self.assertFalse(lifecycle(plan)['review_due'])
        source.status='revoked';source.save(update_fields=['status'])
        state=lifecycle(plan)
        self.assertTrue(state['review_due'])
        self.assertEqual(state['review_evidence_status'],'invalid_or_revoked')
        self.assertEqual(state['next_recheck_at'],ContentPolicyRules.model_validate(plan.policy.rules).model_dump(mode='json')['recheck_at'])
        self.assertTrue(ContentDispositionReview.objects.filter(id=review.id).exists())
        self.assert_rejected('CONTENT_REVIEW_EVIDENCE_UNAVAILABLE',record_review,**kwargs)
        self.assertFalse(ContentErasure.objects.exists())

    def test_patient_choices_skip_expired_purposes_and_revoked_other_patient(self):
        from api.views_content_disposition import content_disposition_patients,content_disposition_options
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor,case=self)
        for patient,purpose in [(self.patient,'aaa_expired_purpose'),(self.other,'zzz_other_revoked')]:
            AccessGrant.objects.create(principal=self.actor,patient=patient,action='read_case',field_codes=[],
                record_scope='all',purpose=purpose,granted_by=self.actor,evidence_ref=self.evidence.reference,
                valid_from=self.now-timedelta(days=2),valid_until=self.now-timedelta(days=1))
        self.policy(access_purpose='aaa_expired_purpose')
        current=self.policy()
        def get(view,**kwargs):
            request=RequestFactory().get('/synthetic-content-control')
            request.session={'_sjia_principal_id':str(self.actor.id),'_sjia_auth_epoch':self.actor.auth_epoch,'_sjia_audience':'support'}
            return view(request,**kwargs)
        patients=get(content_disposition_patients)
        self.assertEqual(patients.status_code,200,patients.data)
        self.assertEqual([row['patient_id'] for row in patients.data['patients']],[str(self.patient.id)])
        options=get(content_disposition_options,patient_id=self.patient.id)
        self.assertEqual(options.status_code,200,options.data)
        self.assertEqual([row['policy_id'] for row in options.data['policies']],[str(current.id)])


    def test_equivalent_noncanonical_object_keys_are_rejected_before_path_alias_can_exist(self):
        from copy import copy
        for key in [self.original.object_key.replace('/','/./',1),self.original.object_key.replace('/','//',1),
                './'+self.original.object_key,self.original.object_key+'/']:
            alias=copy(self.original);alias.id=uuid4();alias.object_key=key;alias.object_version='synthetic-alias'
            with self.assertRaises(IntegrityError),transaction.atomic():
                alias.save(force_insert=True)
        self.assertEqual(self.store.get(self.original.object_key),self.object_bytes[self.original.id])
        self.assert_payload_erased(self.execute(self.approve(self.preview())))

    def test_registered_symlink_alias_cannot_make_unrelated_path_ownership_ambiguous(self):
        from copy import copy
        from files.deletion import object_path
        alias=copy(self.original);alias.id=uuid4();alias.object_key=build_object_key(self.other.id,uuid4(),'original')
        alias.object_version='synthetic-symlink-alias'
        # A legacy operator-created symlink is unsupported storage state. The
        # actual synthetic filesystem alias must be detected before any erasure.
        path=Path(self.store.root)/alias.object_key
        path.parent.mkdir(parents=True,exist_ok=True)
        path.symlink_to(object_path(self.store,self.original.object_key))
        alias.save(force_insert=True)
        self.assert_rejected('CONTENT_LOCAL_PATH_INVENTORY_UNVERIFIED',self.preview)
        self.assertFalse(ContentErasure.objects.exists())
        self.assertEqual(path.read_bytes(),self.object_bytes[self.original.id])
