"""Synthetic-only core closeout checks; no provider or patient-file access."""
from unittest.mock import patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import DatabaseError, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.response import Response

from audit.api import audited_operation
from audit.models import AuditEvent
from identity.models import Patient, Principal
from identity.session import bootstrap_csrf, establish_session
from retention.models import RetentionPolicy


class CoreAuditTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.policy = RetentionPolicy.objects.create(policy_key='synthetic-core-audit', version=1,
            data_class='audit', rules={}, schema_version='retention-1', status='active',
            approved_by=self.actor, approved_at=now, evidence_ref='synthetic-only audit policy evidence')

    def request(self, method='get'):
        request = getattr(RequestFactory(), method)('/synthetic', data={'private': 'DO-NOT-LOG'})
        request.session = {'_sjia_principal_id': str(self.actor.id), '_sjia_auth_epoch': self.actor.auth_epoch,
            '_sjia_audience': 'family'}
        return request

    def call(self, function, request):
        function.__module__ = 'api.views_synthetic_closeout'
        return audited_operation(function, function)(request)

    def test_successful_read_records_safe_metadata(self):
        def synthetic_read(request):
            return Response({'private': 'DO-NOT-LOG'})
        response = self.call(synthetic_read, self.request())
        self.assertEqual(response.status_code, 200)
        audit = AuditEvent.objects.get(reason_code='HTTP_200')
        self.assertEqual(audit.actor_id, self.actor.id)
        self.assertEqual(audit.action, 'synthetic_read')
        self.assertEqual(audit.outcome, 'allowed')
        self.assertNotIn('DO-NOT-LOG', str(vars(audit)))

    def test_rejected_write_rolls_back_and_persists_denial(self):
        def synthetic_write(request):
            Patient.objects.create(display_id='synthetic-should-rollback', status='active', created_at=timezone.now())
            return Response({'code': 'DENIED', 'message': 'DO-NOT-LOG'}, status=403)
        response = self.call(synthetic_write, self.request('post'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Patient.objects.exists())
        audit = AuditEvent.objects.get()
        self.assertEqual((audit.outcome, audit.reason_code), ('denied', 'HTTP_403'))

    def test_audit_failure_rolls_back_successful_local_write(self):
        def synthetic_write(request):
            Patient.objects.create(display_id='synthetic-should-rollback', status='active', created_at=timezone.now())
            return Response({}, status=201)
        with patch('audit.api.write_audit', side_effect=DatabaseError('synthetic audit outage')):
            response = self.call(synthetic_write, self.request('post'))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Patient.objects.exists())

    def test_unapproved_or_ambiguous_policy_denies_before_operation(self):
        self.policy.approved_at = None
        self.policy.save(update_fields=['approved_at'])
        called = []
        def synthetic_read(request):
            called.append(True)
            return Response({})
        self.assertEqual(self.call(synthetic_read, self.request()).status_code, 503)
        self.assertEqual(called, [])

    def test_public_bootstrap_does_not_need_audit_policy(self):
        self.policy.delete()
        def auth_bootstrap(request):
            return Response({'csrf_token': 'synthetic'})
        self.assertEqual(self.call(auth_bootstrap, self.request()).status_code, 200)

    def test_external_operation_is_not_wrapped_in_added_transaction(self):
        from django.db import connection
        depths = []
        before = len(connection.savepoint_ids)
        def report_ocr(request):
            depths.append(len(connection.savepoint_ids))
            return Response({})
        self.assertEqual(self.call(report_ocr, self.request('post')).status_code, 200)
        self.assertEqual(depths, [before])
        self.assertEqual(AuditEvent.objects.count(), 2)

    def test_patient_selection_module_and_new_route_ids_are_audited(self):
        from uuid import uuid4
        def selection(request, document_id):
            return Response({})
        selection.__module__ = 'api.patient_selection'
        document_id = uuid4()
        response = audited_operation(selection, selection)(self.request('post'), document_id=document_id)
        self.assertEqual(response.status_code, 200)
        audit = AuditEvent.objects.get()
        self.assertEqual((audit.object_type, audit.object_id), ('document', document_id))


class CoreSessionTests(TestCase):
    def test_login_rotates_csrf_secret_and_session_key(self):
        request = RequestFactory().get('/synthetic-session')
        SessionMiddleware(lambda request: None).process_request(request)
        request.session.save()
        bootstrap_csrf(request)
        old_secret, old_key = request.META['CSRF_COOKIE'], request.session.session_key
        establish_session(request, principal_id='00000000-0000-4000-8000-000000000001', auth_epoch=0, audience='family')
        self.assertNotEqual(request.META['CSRF_COOKIE'], old_secret)
        self.assertNotEqual(request.session.session_key, old_key)


class CoreEndpointTests(TestCase):
    def test_recognition_uses_frozen_provider_for_final_copy_check(self):
        import ast
        from pathlib import Path
        from recognition import jobs
        module_source = Path(jobs.__file__).read_text()
        function = next(node for node in ast.parse(module_source).body
            if isinstance(node, ast.FunctionDef) and node.name == '_execute_recognition')
        source = ast.get_source_segment(module_source, function)
        self.assertIn("target=provider.payload['endpoint'].rstrip('/')", source)
        self.assertNotIn('target=settings.ARK_BASE_URL', source)

    def test_expired_unstarted_recognition_is_terminal_and_consumed_without_provider(self):
        from recognition.jobs import execute_recognition
        from recognition.service import RecognitionRejected
        from tasks.models import ConsumerReceipt, TaskAttempt
        from tasks.service import register_task
        from tasks.outbox import emit_event
        now = timezone.now()
        actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        patient = Patient.objects.create(display_id='synthetic-expired-recognition', status='active', created_at=now)
        task = register_task(actor=actor, patient=patient, task_type='ocr', schema_version='recognition-1',
            input_refs={}, deadline_seconds=-1)
        event = emit_event(event_type='recognition.requested', schema_version='recognition-1',
            aggregate_id=task.id, aggregate_version=1, correlation_id=task.id, resource_refs={}, task=task)
        with patch('recognition.jobs.recognize_report_with_evidence') as provider:
            with self.assertRaisesRegex(RecognitionRejected, 'TASK_DEADLINE_EXCEEDED'):
                execute_recognition(event, 'synthetic-expiry-worker')
        provider.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, 'failed')
        self.assertEqual(TaskAttempt.objects.get(task=task).error_code, 'TASK_DEADLINE_EXCEEDED')
        self.assertTrue(ConsumerReceipt.objects.filter(consumer='recognition', event=event).exists())


class CoreImportTests(TestCase):
    def setUp(self):
        import json
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from uuid import uuid4
        from django.test import override_settings
        from authorization.models import AccessGrant, ServiceRoleGrant
        from family_content.models import EnvironmentGate
        from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
        self.environment = f'test-core-import-{uuid4()}'
        settings = override_settings(SJIA_ENVIRONMENT=self.environment)
        settings.enable(); self.addCleanup(settings.disable)
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='synthetic-import-only', status='active', created_at=now)
        self.policy = RetentionPolicy.objects.create(policy_key='synthetic-import-case', version=1,
            data_class='case', rules={}, schema_version='retention-1', status='active',
            approved_by=self.actor, approved_at=now, evidence_ref='synthetic-only case policy')
        RetentionPolicy.objects.create(policy_key='synthetic-import-audit', version=1,
            data_class='audit', rules={}, schema_version='retention-1', status='active',
            approved_by=self.actor, approved_at=now, evidence_ref='synthetic-only audit policy')
        self.gate = EnvironmentGate.objects.create(environment=self.environment, version=1, enabled=True, updated_at=now)
        ServiceRoleGrant.objects.create(principal=self.actor, environment_id=self.environment, action='import_case',
            valid_from=now, granted_by=self.actor, evidence_ref='synthetic import permission')
        AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='read_case',
            field_codes=[], record_scope='all', purpose='case_import', granted_by=self.actor,
            valid_from=now, evidence_ref='synthetic whole case permission')
        source = SourceRegistry.objects.create(source_key='synthetic-import-source', owner='test',
            location='synthetic', purpose='case_import', sensitivity='synthetic', update_frequency='none', status='approved')
        self.revision = SourceRevision.objects.create(source=source, version='synthetic-1', checksum='a'*64,
            license='synthetic test data', allowed_use=['case_import'], rights_evidence_ref='synthetic-only source evidence',
            retention_policy=self.policy, captured_at=now, status='approved')
        self.authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.revision,
            purpose='case_import', environment=self.environment, evidence_ref='synthetic-only use evidence',
            approved_by=self.actor, approved_at=now)
        self.projection = {'dataset_version':'synthetic-1','status':'DESIGN_PROJECTION_VALIDATED',
            'patient_id':str(self.patient.id),'source_sha256':'a'*64,'scope':'SYNTHETIC TEST ONLY',
            'medical_times':[], 'fields':[{'id':str(uuid4()),'patient_id':str(self.patient.id),
                'record_version_id':str(uuid4()),'source_id':'synthetic','original_name':'synthetic_note',
                'raw_text':'synthetic content','value_type':'text','raw_numeric':None,'qualifier':None,
                'raw_unit':None,'report_flag':None,'reference_interval':None}],
            'case_details':{key:[] for key in ('profiles','episodes','groups','statements','imaging_studies',
                'image_links','medication_events','procedures','scores','followups','quality_issues')}}
        directory = TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'synthetic-projection.json'

    def submit(self):
        import hashlib, json
        from case_details.case_import import import_case
        raw = json.dumps(self.projection, sort_keys=True).encode()
        self.path.write_bytes(raw)
        return import_case(patient=self.patient, operator=self.actor, retention_policy=self.policy,
            use_authorization_id=self.authorization.id, source_revision=self.revision,
            projection_path=self.path, expected_projection_sha256=hashlib.sha256(raw).hexdigest(),
            auth_epoch=self.actor.auth_epoch, file_bindings=getattr(self, 'file_bindings', {}))

    def test_synthetic_import_is_idempotent_and_never_fabricates_originals(self):
        from files.models import FileObject, FileGroup
        from records.models import Record, RecordField
        first, repeated = self.submit(), self.submit()
        self.assertFalse(first['replayed']); self.assertTrue(repeated['replayed'])
        self.assertEqual(first['ingestion_run_id'], repeated['ingestion_run_id'])
        self.assertEqual(Record.objects.count(), 1)
        self.assertEqual(RecordField.objects.get().value_state, 'known')
        self.assertEqual((FileObject.objects.count(), FileGroup.objects.count()), (0, 0))
        self.assertFalse(first['clinical_rule_eligible'])

    def test_changed_content_same_source_revision_is_rejected(self):
        from case_details.import_admission import CaseImportRejected
        from records.models import RecordField
        self.submit()
        self.projection['fields'][0]['raw_text']='different synthetic content'
        with self.assertRaisesRegex(CaseImportRejected, 'CASE_IMPORT_IDEMPOTENCY_CONFLICT'):
            self.submit()
        self.assertEqual(RecordField.objects.get().raw_text, 'synthetic content')

    def test_wrong_patient_or_revoked_use_never_writes(self):
        from uuid import uuid4
        from case_details.import_admission import CaseImportRejected
        from records.models import Record
        original = self.projection['patient_id']
        self.projection['patient_id']=str(uuid4())
        with self.assertRaisesRegex(CaseImportRejected, 'CASE_PATIENT_MISMATCH'):
            self.submit()
        self.projection['patient_id']=original
        self.authorization.revoked_at=timezone.now(); self.authorization.save(update_fields=['revoked_at'])
        with self.assertRaisesRegex(CaseImportRejected, 'CASE_SOURCE_AUTHORIZATION_UNAVAILABLE'):
            self.submit()
        self.assertEqual(Record.objects.count(), 0)

    def test_no_implicit_bundled_projection_read(self):
        from case_details.import_admission import CaseImportRejected, load_projection
        with patch('pathlib.Path.read_bytes', side_effect=AssertionError('must not read any file')):
            with self.assertRaisesRegex(CaseImportRejected, 'CASE_EXPLICIT_PROJECTION_REQUIRED'):
                load_projection(None, None, self.patient.id)

    def full_case(self):
        from uuid import uuid4
        from authorization.models import AccessGrant
        from files.models import FileGroup, FileObject
        from files.storage import LocalFileStore, digest_bytes
        from tests.synthetic_case_projection import complete_projection
        from tests.synthetic_fixtures import synthetic_png
        self.projection, ids = complete_projection(self.projection)
        store = LocalFileStore(self.path.parent / 'objects')
        patcher = patch('files.purpose.object_store', store)
        patcher.start(); self.addCleanup(patcher.stop)
        group = FileGroup.objects.create(patient=self.patient, owner=self.actor, version=1, status='available',
            source_revision=self.revision, use_authorization_id=self.authorization.id, created_at=timezone.now())
        content = synthetic_png()
        key = f'synthetic-original/{uuid4()}'
        store.put_immutable(key, content)
        original = FileObject.objects.create(patient=self.patient, file_group_id=group.id, object_key=key,
            object_version=digest_bytes(content), checksum=digest_bytes(content), size_bytes=len(content),
            media_type='image/png', purpose='original', status='available', retention_policy=self.policy, created_at=timezone.now())
        AccessGrant.objects.create(principal=self.actor, patient=self.patient, action='read_file',
            field_codes=[], record_scope='all', purpose='case_import', granted_by=self.actor,
            valid_from=timezone.now(), evidence_ref='synthetic original access')
        self.file_bindings = {ids['file']: str(original.id)}
        return ids, original, store

    def test_all_eighteen_domains_preserve_synthetic_values_times_and_links(self):
        from decimal import Decimal
        from case_details import models
        from medical_time.models import MedicalTime
        from records.models import RecordField, Report
        ids, original, _ = self.full_case()
        result = self.submit()
        names = ('PatientProfileVersion','ClinicalEpisode','RecordFieldGroup','RecordFieldGroupMember',
            'ClinicalStatement','StatementEvidence','ImagingStudy','ImagingStudySite','ImagingAssetLink',
            'MedicationEvent','MedicationEventItem','ProcedureEvent','ExternalScoreObservation',
            'ExternalScoreComponent','FollowupEncounter','FollowupReference','DataQualityIssue','DataQualityIssueReference')
        for name in names:
            self.assertEqual(getattr(models, name).objects.filter(patient_id=self.patient.id).count(), 1, name)
        self.assertEqual(RecordField.objects.get(id=ids['numeric']).raw_numeric, Decimal('1.2500'))
        self.assertEqual(str(RecordField.objects.get(id=ids['numeric']).medical_time_id), ids['time'])
        self.assertEqual(MedicalTime.objects.get(id=ids['time']).raw_text, 'SYNTHETIC 2026-01-02')
        item = models.MedicationEventItem.objects.get(id=ids['medication_item'])
        self.assertEqual((item.dose, item.route_text, item.interval_value), (Decimal('1.2500'),'synthetic route',Decimal('2')))
        self.assertEqual(models.ImagingAssetLink.objects.get(id=ids['image']).file_id, original.id)
        self.assertEqual(Report.objects.get(id=ids['report']).file_group_id, original.file_group_id)
        self.assertEqual(models.StatementEvidence.objects.get().evidence_field_id, RecordField.objects.get(original_name='synthetic_note').id)
        self.assertEqual(str(models.FollowupReference.objects.get().group_id), ids['group'])
        self.assertFalse(result['clinical_rule_eligible'])

    def test_missing_or_corrupt_original_cannot_be_claimed_available(self):
        from case_details.import_admission import CaseImportRejected
        from files.service import FileRejected
        from records.models import Record
        ids, original, store = self.full_case()
        self.file_bindings = {}
        with self.assertRaisesRegex(CaseImportRejected, 'CASE_ORIGINAL_BINDINGS_REQUIRED'):
            self.submit()
        self.file_bindings = {ids['file']:str(original.id)}
        original.checksum = 'b'*64; original.save(update_fields=['checksum'])
        with self.assertRaisesRegex(FileRejected, 'PURPOSE_CHECKSUM_MISMATCH'):
            self.submit()
        self.assertEqual(Record.objects.count(), 0)

    def test_supplied_uuid_collision_never_overwrites_other_patient(self):
        from datetime import date
        from uuid import uuid4
        from django.db import IntegrityError
        from medical_time.models import MedicalTime
        from records.models import Record
        from tests.synthetic_case_projection import complete_projection
        complete, ids = complete_projection(self.projection)
        self.projection['medical_times'] = complete['medical_times']
        other = Patient.objects.create(display_id='synthetic-other-import-target',status='active',created_at=timezone.now())
        original = MedicalTime.objects.create(id=ids['time'],patient=other,precision='date',local_date=date(2025,1,1),
            raw_text='synthetic other patient',resolution_status='local_only')
        with self.assertRaises(IntegrityError):
            self.submit()
        original.refresh_from_db()
        self.assertEqual((original.patient_id,original.raw_text), (other.id,'synthetic other patient'))
        self.assertEqual(Record.objects.filter(patient=self.patient).count(), 0)


class CoreImportConcurrencyTests(TransactionTestCase):
    """Two independent PostgreSQL connections race on one immutable artifact."""
    setUp = CoreImportTests.setUp

    @transaction.atomic
    def tearDown(self):
        from authorization.models import AccessGrant, ServiceRoleGrant
        from family_content.models import EnvironmentGate
        from records.models import Record, RecordField, RecordVersion
        from sources.models import DataUseAuthorization, IngestionRun, SourceRegistry, SourceRevision
        # Exact synthetic ownership only: project schema-qualified tables are
        # not enumerated by Django flush. No broad truncate or shared-data wipe.
        AuditEvent.objects.filter(actor_id=self.actor.id).delete()
        RecordField.objects.filter(patient=self.patient).delete()
        RecordVersion.objects.filter(patient=self.patient).delete()
        Record.objects.filter(patient=self.patient).delete()
        IngestionRun.objects.filter(source_revision=self.revision).delete()
        DataUseAuthorization.objects.filter(id=self.authorization.id).delete()
        AccessGrant.objects.filter(principal=self.actor).delete()
        ServiceRoleGrant.objects.filter(principal=self.actor).delete()
        SourceRevision.objects.filter(id=self.revision.id).delete()
        SourceRegistry.objects.filter(id=self.revision.source_id).delete()
        RetentionPolicy.objects.filter(approved_by=self.actor).delete()
        self.patient.delete()
        self.actor.delete()
        EnvironmentGate.objects.filter(pk=self.gate.pk).delete()
        super().tearDown()

    def test_same_source_and_checksum_converge_to_one_committed_run(self):
        import hashlib, json, threading
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections, connection
        from case_details.case_import import import_case
        from records.models import Record
        from sources.models import IngestionRun
        self.assertEqual(connection.vendor, 'postgresql')
        raw = json.dumps(self.projection, sort_keys=True).encode()
        self.path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        barrier = threading.Barrier(2)

        def execute():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=5)
                return import_case(patient=self.patient, operator=self.actor, retention_policy=self.policy,
                    use_authorization_id=self.authorization.id, source_revision=self.revision,
                    projection_path=self.path, expected_projection_sha256=digest,
                    auth_epoch=self.actor.auth_epoch, file_bindings={})
            finally:
                # Thread-local healthy connections otherwise survive executor shutdown.
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(execute), pool.submit(execute)
            results = [first.result(timeout=15), second.result(timeout=15)]
        self.assertEqual(sorted(result['replayed'] for result in results), [False, True])
        self.assertEqual(len({result['ingestion_run_id'] for result in results}), 1)
        self.assertEqual(IngestionRun.objects.filter(source_revision=self.revision).count(), 1)
        self.assertEqual(Record.objects.filter(patient=self.patient).count(), 1)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.data_epoch, 1)


class CoreDraftPolicyTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.policy = RetentionPolicy.objects.create(policy_key='synthetic-draft-only', version=1,
            data_class='draft', rules={'retention_days':7,'disposition':'delete'}, schema_version='retention-1',
            status='active', approved_by=self.actor, approved_at=now, evidence_ref='synthetic-only draft test')

    def test_expiry_is_derived_only_from_the_approved_draft_rule(self):
        from drafts.policy import draft_retention
        policy, expires_at = draft_retention()
        self.assertEqual(policy.id, self.policy.id)
        self.assertAlmostEqual((expires_at-timezone.now()).total_seconds(),7*86400,delta=2)
        self.policy.rules = {'retention_days':None,'disposition':'retain'}
        self.policy.save(update_fields=['rules'])
        self.assertIsNone(draft_retention()[1])

    def test_wrong_class_unapproved_ambiguous_and_unbounded_delete_fail_closed(self):
        from drafts.policy import draft_retention
        from drafts.service import DraftRejected
        for field, value in [('data_class','case'),('approved_at',None),('rules',{'retention_days':None,'disposition':'delete'})]:
            old = getattr(self.policy, field)
            setattr(self.policy, field, value); self.policy.save(update_fields=[field])
            with self.assertRaisesRegex(DraftRejected,'DRAFT_RETENTION_UNAVAILABLE'):
                draft_retention()
            setattr(self.policy, field, old); self.policy.save(update_fields=[field])
        RetentionPolicy.objects.create(policy_key='synthetic-draft-second',version=1,data_class='draft',
            rules=self.policy.rules,schema_version='retention-1',status='active',approved_by=self.actor,
            approved_at=timezone.now(),evidence_ref='synthetic-only ambiguous test')
        with self.assertRaisesRegex(DraftRejected,'DRAFT_RETENTION_UNAVAILABLE'):
            draft_retention()
