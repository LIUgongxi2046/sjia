"""Synthetic immutable local originals, approved runtime, real task/lease adoption."""
from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from authorization.models import AccessGrant
from authorization.policy import Denied
from files.errors import FileRejected
from files.models import FileGroup, FileObject, FilePage
from files.service import create_file_group, declare_upload_part, store_file_object
from files.storage import LocalFileStore, digest_bytes
from files.validation import (EVENT, ValidationDeferred, cancel_validation, execute_validation,
    prepare_validation_intent, read_validation_task, submit_validation)
from identity.models import Patient, Principal
from sources.models import DataUseAuthorization
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tests.synthetic_fixtures import SyntheticEvidence, synthetic_png


class FileValidationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id='SYNTHETIC-FILE-VALIDATION', status='active', created_at=now)
        self.evidence = SyntheticEvidence(self, self.actor)
        self.runtime = self.evidence.runtime()
        self.grant = AccessGrant.objects.create(patient=self.patient, principal=self.actor, action='write_record',
            field_codes=[], record_scope='own', purpose='family_observation', granted_by=self.actor,
            evidence_ref='SYNTHETIC TEST ONLY', valid_from=now)
        self.authorization = DataUseAuthorization.objects.create(patient=self.patient, source_revision=self.evidence.source,
            purpose='family_observation', environment=settings.SJIA_ENVIRONMENT, evidence_ref=self.evidence.reference,
            approved_by=self.actor, approved_at=now)
        self.store = LocalFileStore(self.evidence.root / 'originals')
        for name in ('files.service.object_store', 'files.purpose.object_store'):
            patched = patch(name, self.store); patched.start(); self.addCleanup(patched.stop)
        self.group, self.original, self.payload, self.target = self.make_group()

    def make_group(self):
        from retention.models import RetentionPolicy
        policy = RetentionPolicy.objects.get(id__in=self.runtime.payload['retention_policy_ids'], data_class='file')
        group = create_file_group(patient=self.patient, owner=self.actor, source_revision=self.evidence.source,
            use_authorization_id=self.authorization.id)
        content = synthetic_png()
        part = declare_upload_part(file_group=group, client_upload_id=uuid4(), ordinal=1,
            declared_checksum=digest_bytes(content), declared_size_bytes=len(content), declared_media_type='image/png')
        original = store_file_object(file_group=group, upload_part=part, data=content, purpose='original',
            retention_policy=policy, limits=self.runtime.payload['limits'])
        return group, original, {'expected_version':'1', 'file_ids':[str(original.id)]}, f'/api/uploads/{group.id}/complete'

    def prepare(self, group=None, payload=None, target=None):
        group, payload, target = group or self.group, payload or self.payload, target or self.target
        return prepare_validation_intent(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            data={'operation_id':'file_group_complete', 'target_key':target, 'request_id':str(group.id),
                'schema_version':'1.0.0', 'payload':payload}, retention_policy=self.evidence.request_retention)

    def submit(self, intent=None, payload=None, target=None):
        intent = intent or self.prepare()
        value = submit_validation(actor=self.actor, auth_epoch=self.actor.auth_epoch, audience='family',
            target=target or self.target, payload=payload or self.payload,
            headers={'X-Write-Intent':str(intent.id),'Idempotency-Key':str(intent.request_id),'X-Intent-Generation':str(intent.generation)})
        task = Task.objects.get(id=value['task_id'])
        return value, task, OutboxEvent.objects.get(task=task,event_type=EVENT)

    def test_admission_and_worker_are_idempotent_and_bound(self):
        intent = self.prepare()
        view, task, event = self.submit(intent)
        self.assertEqual((view['task_type'],view['status']),('file_validation','accepted'))
        self.group.refresh_from_db(); self.assertEqual(self.group.status,'validating')
        refs, digest = task.input_refs, task.input_sha256
        self.assertEqual(self.submit(intent)[1].id,task.id)
        execute_validation(event,'synthetic-worker')
        execute_validation(event,'duplicate-worker')
        task.refresh_from_db(); self.group.refresh_from_db()
        self.assertEqual((task.status,self.group.status),('completed','available'))
        self.assertEqual((task.input_refs,task.input_sha256),(refs,digest))
        self.assertEqual(TaskAttempt.objects.filter(task=task).count(),1)
        self.assertEqual(ConsumerReceipt.objects.filter(event=event,consumer='file_validation').count(),1)
        reread = read_validation_task(task=task,actor=self.actor,auth_epoch=self.actor.auth_epoch,audience='family')
        self.assertEqual((reread['result_type'],reread['result_ref']),('file_group',str(self.group.id)))
        self.assertEqual(self.submit(intent)[0],reread)

    def test_changed_source_metadata_cannot_be_adopted(self):
        _,task,event=self.submit()
        FileObject.objects.filter(id=self.original.id).update(object_version='SYNTHETIC CHANGED')
        with self.assertRaises(FileRejected): execute_validation(event,'synthetic-worker')
        task.refresh_from_db();self.group.refresh_from_db()
        self.assertEqual((task.status,self.group.status),('failed','failed'))

    def test_revoked_grant_cannot_validate_or_poll(self):
        _,task,event=self.submit()
        self.grant.revoked_at=timezone.now();self.grant.save(update_fields=['revoked_at'])
        with self.assertRaises(Denied): execute_validation(event,'synthetic-worker')
        with self.assertRaises(Denied): read_validation_task(task=task,actor=self.actor,auth_epoch=self.actor.auth_epoch,audience='family')
        self.group.refresh_from_db();self.assertEqual(self.group.status,'failed')

    def test_cancel_between_byte_check_and_adoption_prevents_effect(self):
        from files.validation import checked_bytes
        _,task,event=self.submit()
        def cancelled(original):
            value=checked_bytes(original)
            cancel_validation(task=task,actor=self.actor,auth_epoch=self.actor.auth_epoch,audience='family')
            return value
        with patch('files.validation.checked_bytes',side_effect=cancelled): execute_validation(event,'synthetic-worker')
        task.refresh_from_db();self.group.refresh_from_db()
        self.assertEqual((task.status,self.group.status),('cancelled','failed'))
        self.assertEqual(TaskAttempt.objects.get(task=task).status,'cancelled')

    def test_source_revocation_between_byte_check_and_adoption_prevents_effect(self):
        from files.validation import checked_bytes
        _,task,event=self.submit()
        def revoked(original):
            value=checked_bytes(original)
            self.authorization.revoked_at=timezone.now();self.authorization.save(update_fields=['revoked_at'])
            return value
        with patch('files.validation.checked_bytes',side_effect=revoked),self.assertRaises(FileRejected):
            execute_validation(event,'synthetic-worker')
        self.group.refresh_from_db();self.assertEqual(self.group.status,'failed')

    def test_byte_checksum_mismatch_is_terminal_failure(self):
        _,task,event=self.submit()
        with patch.object(self.store,'get',return_value=b'SYNTHETIC WRONG BYTES'),self.assertRaisesRegex(FileRejected,'CHECKSUM_MISMATCH'):
            execute_validation(event,'synthetic-worker')
        task.refresh_from_db();self.assertEqual(task.status,'failed')
        self.assertEqual(TaskAttempt.objects.get(task=task).error_code,'PURPOSE_CHECKSUM_MISMATCH')

    def test_queue_deadline_prevents_byte_access(self):
        _,task,event=self.submit()
        Task.objects.filter(id=task.id).update(created_at=timezone.now()-timedelta(seconds=61))
        with patch('files.validation.checked_bytes') as read,self.assertRaisesRegex(FileRejected,'DEADLINE_EXCEEDED'):
            execute_validation(event,'synthetic-worker')
        read.assert_not_called()
        task.refresh_from_db();self.assertEqual(task.status,'failed')

    def test_approved_runtime_concurrency_budget_defers_same_event(self):
        policy=next(item for item in self.runtime.payload['tasks'] if item['task_type']=='file_validation')
        policy['max_concurrency']=1
        self.runtime.save(update_fields=['payload']);self.evidence.qualify(self.runtime)
        _,first,_=self.submit()
        from tasks.service import acquire_lease
        acquire_lease(first.id,'synthetic-held',timezone.now()+timedelta(seconds=30))
        group,_,payload,target=self.make_group()
        intent=self.prepare(group,payload,target)
        _,second,event=self.submit(intent,payload,target)
        with self.assertRaises(ValidationDeferred) as caught: execute_validation(event,'synthetic-contender')
        self.assertEqual(caught.exception.seconds,policy['heartbeat_seconds'])
        second.refresh_from_db();self.assertEqual(second.status,'accepted')
        self.assertFalse(TaskAttempt.objects.filter(task=second).exists())


    def test_http_intent_receipt_and_poll_use_the_frozen_group(self):
        from tests.synthetic_fixtures import ensure_synthetic_audit_policy
        ensure_synthetic_audit_policy(self.actor, case=self)
        session=self.client.session
        session['_sjia_principal_id']=str(self.actor.id)
        session['_sjia_auth_epoch']=self.actor.auth_epoch
        session['_sjia_audience']='family'
        session.save()
        token=self.client.get('/api/auth/bootstrap').json()['csrf_token']
        prepared=self.client.post('/api/write-intents',{'operation_id':'file_group_complete','target_key':self.target,
            'request_id':str(self.group.id),'schema_version':'1.0.0','payload':self.payload},content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(prepared.status_code,201,prepared.content)
        intent=prepared.json()
        response=self.client.post(self.target,self.payload,content_type='application/json',HTTP_X_CSRFTOKEN=token,
            HTTP_X_WRITE_INTENT=intent['intent_id'],HTTP_X_INTENT_GENERATION=intent['generation'],HTTP_IDEMPOTENCY_KEY=str(self.group.id))
        self.assertEqual(response.status_code,202,response.content)
        task_id=response.json()['task_id']
        poll=self.client.get(f'/api/tasks/{task_id}')
        self.assertEqual(poll.status_code,200,poll.content)
        self.assertEqual(poll.json()['status'],'accepted')
        execute_validation(OutboxEvent.objects.get(task_id=task_id,event_type=EVENT),'synthetic-http-worker')
        saved=self.client.get(f'/api/tasks/{task_id}')
        self.assertEqual(saved.status_code,200,saved.content)
        self.assertEqual(saved.json()['result_ref'],str(self.group.id))


    def test_stale_failure_cannot_overwrite_renewed_same_owner_attempt(self):
        from files.validation import record_failure
        from tasks.service import acquire_lease, create_attempt
        _,task,event=self.submit()
        acquire_lease(task.id,'same-synthetic-owner',timezone.now()+timedelta(seconds=30))
        task.refresh_from_db();old_generation=task.lease_generation
        old=create_attempt(task,old_generation)
        Task.objects.filter(id=task.id).update(lease_until=timezone.now()-timedelta(seconds=1))
        acquire_lease(task.id,'same-synthetic-owner',timezone.now()+timedelta(seconds=30))
        task.refresh_from_db();new=create_attempt(task,task.lease_generation)
        record_failure(event,'same-synthetic-owner','SYNTHETIC_STALE_FAILURE',generation=old_generation)
        record_failure(event,'same-synthetic-owner','SYNTHETIC_UNFENCED_CALLBACK')
        task.refresh_from_db();new.refresh_from_db();old.refresh_from_db();self.group.refresh_from_db()
        self.assertEqual((task.status,new.status,self.group.status),('running','running','validating'))
        self.assertEqual(old.status,'superseded')
        self.assertFalse(ConsumerReceipt.objects.filter(event=event).exists())


    def test_rehashed_task_cannot_redirect_original_receipt_to_other_group(self):
        from configurations.service import compute_checksum
        from files.validation import scope
        _,task,event=self.submit()
        other,_,payload,_=self.make_group()
        current=scope(actor=self.actor,auth_epoch=self.actor.auth_epoch,audience='family',group_id=other.id,payload=payload)
        refs={'context':current.context,'input':payload}
        Task.objects.filter(id=task.id).update(input_refs=refs,input_sha256=compute_checksum(refs))
        with self.assertRaisesRegex(FileRejected,'FILE_VALIDATION_TASK_INVALID'):
            execute_validation(event,'synthetic-tampered-task')
        task.refresh_from_db();self.group.refresh_from_db();other.refresh_from_db()
        self.assertEqual((task.status,self.group.status,other.status),('failed','failed','initiated'))
        self.assertFalse(TaskAttempt.objects.filter(task=task,status='completed').exists())

class FileValidationErrorContractTests(SimpleTestCase):
    def test_policy_conflict_and_malformed_inputs_have_explicit_http_status(self):
        from api.base import handle
        from django.test import RequestFactory
        for reason, expected in [('FILE_VALIDATION_POLICY_REQUIRED', 503),
                ('FILE_VALIDATION_INTENT_EXPIRED', 409), ('FILE_VALIDATION_ORIGINAL_CHANGED', 409),
                ('FILE_VALIDATION_RECEIPT_UNCERTAIN', 409), ('FILE_VALIDATION_INPUT_INVALID', 422)]:
            def rejected(request):
                raise FileRejected(reason)
            with self.subTest(reason=reason):
                request = RequestFactory().post('/synthetic-file-validation')
                request.session = {}
                # This pure test covers exception-to-HTTP mapping; native
                # admission/fence behavior is exercised by restore tests.
                with patch('retention.content_fences.content_reader',return_value=nullcontext()):
                    result = handle(rejected)(request)
                self.assertEqual(result.status_code, expected)
                self.assertEqual(result.data['code'], reason)
