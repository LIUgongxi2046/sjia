import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from configurations.models import ConfigVersion
from files.models import FileGroup, FileObject
from files.service import create_file_group, declare_upload_part, store_file_object, complete_file_group
from files.storage import digest_bytes
from identity.models import Patient, Principal
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import Task, OutboxEvent, ConsumerReceipt
from recognition.jobs import execute_recognition
from recognition.service import RecognitionRejected


class RecognitionAdmissionTest(TestCase):
    def setUp(self):
        call_command('seed_demo', verbosity=0)
        self.actor = Principal.objects.first()
        self.patient = Patient.objects.get(display_id='demo-child')
        authorization = DataUseAuthorization.objects.get(patient=self.patient)
        self.group = create_file_group(patient=self.patient, owner=self.actor,
            source_revision=authorization.source_revision, use_authorization_id=authorization.id)
        image = (Path(settings.BASE_DIR).parent / 'docs/testing/evidence/QA02/simulated-report.png').read_bytes()
        part = declare_upload_part(file_group=self.group, client_upload_id=uuid.uuid4(), ordinal=1,
            declared_checksum=digest_bytes(image), declared_size_bytes=len(image), declared_media_type='image/png')
        self.file = store_file_object(file_group=self.group, upload_part=part, data=image,
            purpose=FileObject.Purpose.ORIGINAL, retention_policy=RetentionPolicy.objects.first())
        complete_file_group(file_group=self.group)
        ConfigVersion.objects.create(config_key='qa03-report', kind='content', version=1,
            schema_version='field-config-1', payload={'kind': 'report', 'fields': [{'field_code': 'report_item', 'writable': True}]},
            checksum=digest_bytes(b'qa03-report'), status='enabled', created_by=self.actor, created_at=timezone.now())
        session = self.client.session
        session['_sjia_principal_id'] = str(self.actor.id)
        session['_sjia_auth_epoch'] = self.actor.auth_epoch
        session['_sjia_audience'] = 'family'
        session.save()

    def create(self, **extra):
        return self.client.post('/api/recognition-jobs', {
            'file_group_id': str(self.group.id), 'file_group_version': str(self.group.version), **extra,
        }, content_type='application/json')

    def test_registers_pending_task_and_durable_event(self):
        response = self.create()
        self.assertEqual(response.status_code, 202, response.content)
        task = Task.objects.get(id=response.json()['task_id'])
        self.assertEqual(task.status, 'accepted')
        self.assertNotEqual(task.input_sha256, 'a' * 64)
        self.assertEqual(task.input_refs['files'][0]['checksum'], self.file.checksum)
        self.assertTrue(OutboxEvent.objects.filter(task=task, event_type='recognition.requested').exists())

    def test_same_group_is_idempotent(self):
        first, second = self.create(), self.create()
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(second.json()['task_id'], first.json()['task_id'])

    def test_superseded_group_is_rejected(self):
        self.group.status = FileGroup.Status.SUPERSEDED
        self.group.save(update_fields=['status'])
        self.assertEqual(self.create().status_code, 409)

    def test_incorrect_version_is_rejected(self):
        self.assertEqual(self.create(file_group_version='2').status_code, 409)

    def test_provider_is_server_controlled(self):
        self.assertEqual(self.create(provider='unapproved').status_code, 422)

    def test_quarantined_file_is_rejected(self):
        self.file.status = FileObject.Status.QUARANTINED
        self.file.save(update_fields=['status'])
        self.assertEqual(self.create().status_code, 409)

    def test_cancelled_event_is_consumed_without_provider(self):
        response = self.create()
        task = Task.objects.get(id=response.json()['task_id'])
        task.status = Task.Status.CANCELLED
        task.save(update_fields=['status'])
        event = OutboxEvent.objects.get(task=task)
        execute_recognition(event, 'qa03-cancel-check')
        execute_recognition(event, 'qa03-duplicate-check')
        self.assertEqual(ConsumerReceipt.objects.filter(event=event).count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)

    def test_expired_task_is_rejected_before_provider(self):
        response = self.create()
        task = Task.objects.get(id=response.json()['task_id'])
        task.deadline_at = timezone.now() - timedelta(seconds=1)
        task.save(update_fields=['deadline_at'])
        with self.assertRaises(RecognitionRejected) as raised:
            execute_recognition(OutboxEvent.objects.get(task=task), 'qa03-expired-check')
        self.assertEqual(raised.exception.reason_code, 'TASK_DEADLINE_EXCEEDED')

    def test_cancelled_retry_creates_one_new_task(self):
        original = self.create().json()['task_id']
        self.client.post(f'/api/tasks/{original}/cancel', {}, content_type='application/json')
        first = self.client.post(f'/api/tasks/{original}/retry', {}, content_type='application/json')
        self.assertEqual(first.status_code, 200, first.content)
        second = self.client.post(f'/api/tasks/{original}/retry', {}, content_type='application/json')
        self.assertEqual(second.json()['task_id'], first.json()['task_id'])
        self.assertNotEqual(first.json()['task_id'], original)
        self.assertEqual(Task.objects.get(id=original).status, Task.Status.CANCELLED)

    def test_pending_task_cannot_be_retried(self):
        task_id = self.create().json()['task_id']
        response = self.client.post(f'/api/tasks/{task_id}/retry', {}, content_type='application/json')
        self.assertEqual(response.json()['code'], 'TASK_NOT_RETRYABLE')
        self.assertEqual(Task.objects.filter(task_type='ocr').count(), 1)

    def test_other_actor_cannot_retry_task(self):
        from identity.service import register_account

        task_id = self.create().json()['task_id']
        other = register_account(username='qa03-other', password='QA03-other-2026')
        session = self.client.session
        session['_sjia_principal_id'] = str(other.id)
        session['_sjia_auth_epoch'] = other.auth_epoch
        session.save()
        response = self.client.post(f'/api/tasks/{task_id}/retry', {}, content_type='application/json')
        self.assertEqual(response.status_code, 404)

    def test_upload_rejects_completed_group(self):
        response = self.client.put(f'/api/uploads/{self.group.id}/files/{uuid.uuid4()}?size=1', b'x', content_type='application/octet-stream')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'FILE_GROUP_NOT_EDITABLE')

    def test_upload_rejects_invalid_size_and_ordinal(self):
        authorization = DataUseAuthorization.objects.get(patient=self.patient)
        group = create_file_group(patient=self.patient, owner=self.actor, source_revision=authorization.source_revision,
            use_authorization_id=authorization.id)
        for query, code in [('size=abc', 'FILE_SIZE_INVALID'), ('size=-1', 'FILE_SIZE_INVALID'),
                            ('size=1&ordinal=0', 'FILE_ORDINAL_INVALID'), ('size=1&ordinal=11', 'FILE_ORDINAL_INVALID'),
                            ('size=1&ordinal=nan', 'FILE_ORDINAL_INVALID')]:
            with self.subTest(query=query):
                response = self.client.put(f'/api/uploads/{group.id}/files/{uuid.uuid4()}?{query}', b'x', content_type='application/octet-stream')
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()['code'], code)
        self.assertFalse(FileObject.objects.filter(file_group_id=group.id).exists())
