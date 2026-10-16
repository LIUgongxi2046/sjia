"""Immutable storage, upload rollback recovery and duplicate-request regression tests."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
import uuid

from django.db import close_old_connections, connection, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from PIL import Image

from files.errors import FileRejected
from files.models import FileGroup, FileObject, FilePage, UploadPart
from files.service import create_file_group, declare_upload_part, store_file_object
from files.storage import LocalFileStore, ObjectConflict, digest_bytes
from identity.models import Patient, Principal
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def png_bytes(color='white'):
    output = io.BytesIO()
    Image.new('RGB', (10, 20), color).save(output, format='PNG')
    return output.getvalue()


class ImmutableStoreTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = LocalFileStore(Path(directory.name))

    def test_identical_retry_is_idempotent(self):
        self.store.put_immutable('original/test', b'complete bytes')
        self.store.put_immutable('original/test', b'complete bytes')
        self.assertEqual(self.store.get('original/test'), b'complete bytes')
        self.assertEqual(list((self.store.root / 'original').iterdir()), [self.store.root / 'original/test'])

    def test_conflict_preserves_existing_bytes(self):
        self.store.put_immutable('original/test', b'original bytes')
        with self.assertRaises(ObjectConflict):
            self.store.put_immutable('original/test', b'different bytes')
        self.assertEqual(self.store.get('original/test'), b'original bytes')

    def test_truncated_existing_object_is_a_conflict(self):
        self.store.put('original/test', b'complete')
        with self.assertRaises(ObjectConflict):
            self.store.put_immutable('original/test', b'complete bytes')
        self.assertEqual(self.store.get('original/test'), b'complete')

    def test_failure_before_publish_leaves_no_partial_final_object(self):
        with patch('files.storage.os.fsync', side_effect=OSError('injected disk failure')):
            with self.assertRaises(OSError):
                self.store.put_immutable('original/test', b'complete bytes')
        self.assertFalse(self.store.exists('original/test'))
        self.assertEqual(list((self.store.root / 'original').iterdir()), [])

    def test_failure_after_publish_retains_retryable_complete_object(self):
        from os import fsync

        calls = 0

        def fail_directory_sync(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected directory sync failure')
            fsync(fd)

        with patch('files.storage.os.fsync', side_effect=fail_directory_sync):
            with self.assertRaises(OSError):
                self.store.put_immutable('original/test', b'complete bytes')
        self.assertEqual(self.store.get('original/test'), b'complete bytes')
        self.store.put_immutable('original/test', b'complete bytes')

    def test_parallel_identical_writers_publish_one_complete_object(self):
        barrier = Barrier(8)
        data = b'complete bytes' * 100_000

        def write(_):
            barrier.wait(timeout=10)
            self.store.put_immutable('original/test', data)
            return self.store.get('original/test')

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(write, range(8))), [data] * 8)
        self.assertEqual(len(list((self.store.root / 'original').iterdir())), 1)

    def test_parallel_different_writers_preserve_single_winner(self):
        barrier = Barrier(2)

        def write(data):
            barrier.wait(timeout=10)
            try:
                self.store.put_immutable('original/test', data)
            except ObjectConflict:
                return None
            return data

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, [b'first', b'second']))
        winners = [value for value in results if value is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(self.store.get('original/test'), winners[0])

    def test_upload_conflict_has_stable_http_409_mapping(self):
        from api.base import handle

        @handle
        def conflict(request):
            raise FileRejected('UPLOAD_REQUEST_MISMATCH')

        # Isolate only the DB-backed lifetime gate in this pure error mapping
        # test; actual gate behavior has separate native restore coverage.
        with patch('retention.content_fences.content_reader',return_value=nullcontext()):
            response = conflict(RequestFactory().get('/synthetic-upload-test'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'UPLOAD_REQUEST_MISMATCH')


class UploadFixture:
    def setUp(self):
        super().setUp()
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = LocalFileStore(Path(directory.name))
        patcher = patch('files.service.object_store', self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        now = timezone.now()
        suffix = uuid.uuid4().hex
        principal = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        patient = Patient.objects.create(display_id=f'P-RECOVERY-{suffix}', status='active', created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key=f'recovery-{suffix}', version=1,
            data_class='file', rules={}, schema_version='r-1', status='active')
        source = register_source(source_key=f'recovery-{suffix}', owner='test', location='test',
            purpose='test', sensitivity='test', update_frequency='test')
        revision = register_revision(source=source, version='v1', checksum='c' * 64,
            license='test', allowed_use=['family_observation'])
        authorization = DataUseAuthorization.objects.create(patient=patient, source_revision=revision,
            purpose='family_observation', environment='test', evidence_ref='synthetic',
            approved_by=principal, approved_at=now)
        self.group = create_file_group(patient=patient, owner=principal, source_revision=revision,
            use_authorization_id=authorization.id)
        self.principal, self.patient = principal, patient
        self.source, self.revision, self.authorization = source, revision, authorization
        self.data = png_bytes()
        self.upload_id = uuid.uuid4()

    def declare(self, data=None):
        data = self.data if data is None else data
        return declare_upload_part(file_group=self.group, client_upload_id=self.upload_id,
            ordinal=1, declared_checksum=digest_bytes(data), declared_size_bytes=len(data),
            declared_media_type='image/png')

    def store_part(self, part, data=None, **kwargs):
        return store_file_object(file_group=self.group, upload_part=part,
            data=self.data if data is None else data, purpose='original',
            retention_policy=self.retention, **kwargs)


class UploadRecoveryTests(UploadFixture, TestCase):
    def test_rollback_after_object_write_can_retry_same_upload_id(self):
        with self.assertRaisesRegex(RuntimeError, 'injected commit failure'):
            with transaction.atomic():
                part = self.declare()
                self.store_part(part)
                raise RuntimeError('injected commit failure')
        self.assertEqual(FileObject.objects.filter(file_group_id=self.group.id).count(), 0)
        self.assertEqual(FilePage.objects.filter(file_group_id=self.group.id).count(), 0)
        self.assertEqual(UploadPart.objects.filter(file_group_id=self.group.id).count(), 0)
        self.assertEqual(self.store.get(part.object_key), self.data)
        saved = self.store_part(self.declare())
        self.assertEqual(FileObject.objects.filter(file_group_id=self.group.id).count(), 1)
        self.assertEqual(FilePage.objects.filter(file_id=saved.id).count(), 1)

    def test_page_insert_failure_can_retry_preexisting_declaration(self):
        part = self.declare()
        with patch('files.service.FilePage.objects.create', side_effect=RuntimeError('injected page failure')):
            with self.assertRaises(RuntimeError):
                self.store_part(part)
        part.refresh_from_db()
        self.assertEqual(part.status, UploadPart.Status.DECLARED)
        self.assertEqual(self.store.get(part.object_key), self.data)
        saved = self.store_part(part)
        self.assertEqual(FilePage.objects.filter(file_id=saved.id).count(), 1)

    def test_stale_part_retry_does_not_duplicate_pages_or_exceed_limit(self):
        part = self.declare()
        first = self.store_part(part)
        second = self.store_part(part, limits={'max_pages': 1, 'max_pixels': 200})
        self.assertEqual(first.id, second.id)
        self.assertEqual(FileObject.objects.filter(file_group_id=self.group.id).count(), 1)
        self.assertEqual(list(FilePage.objects.filter(file_group_id=self.group.id).values_list('ordinal', flat=True)), [1])

    def test_different_bytes_at_orphan_key_conflict_without_deleting_object(self):
        part = self.declare()
        self.store.put_immutable(part.object_key, b'different existing bytes')
        with self.assertRaisesRegex(FileRejected, 'UPLOAD_REQUEST_MISMATCH'):
            self.store_part(part)
        self.assertEqual(self.store.get(part.object_key), b'different existing bytes')
        self.assertEqual(FileObject.objects.filter(file_group_id=self.group.id).count(), 0)
        self.assertEqual(FilePage.objects.filter(file_group_id=self.group.id).count(), 0)

    def test_changed_data_after_rollback_cannot_reuse_upload_id(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                part = self.declare()
                self.store_part(part)
                raise RuntimeError('rollback')
        changed = png_bytes('black')
        with self.assertRaisesRegex(FileRejected, 'UPLOAD_REQUEST_MISMATCH'):
            self.store_part(self.declare(changed), changed)
        self.assertEqual(self.store.get(part.object_key), self.data)
        self.assertEqual(FileObject.objects.filter(file_group_id=self.group.id).count(), 0)


class ConcurrentUploadTests(UploadFixture, TransactionTestCase):
    def tearDown(self):
        # Django's public-schema flush does not discover the quoted sjia tables.
        # Remove only this synthetic fixture, in foreign-key dependency order.
        FilePage.objects.filter(file_group_id=self.group.id).delete()
        UploadPart.objects.filter(file_group_id=self.group.id).delete()
        FileObject.objects.filter(file_group_id=self.group.id).delete()
        FileGroup.objects.filter(id=self.group.id).delete()
        self.authorization.delete()
        self.revision.delete()
        self.source.delete()
        self.retention.delete()
        self.patient.delete()
        self.principal.delete()
        super().tearDown()

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_identical_upload_has_one_object_and_page(self):
        part = self.declare()
        barrier = Barrier(2)

        def upload():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.store_part(part).id
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(upload), pool.submit(upload)]
            identifiers = [future.result(timeout=15) for future in futures]
        self.assertEqual(identifiers[0], identifiers[1])
        self.assertEqual(FileObject.objects.filter(file_group_id=self.group.id).count(), 1)
        self.assertEqual(FilePage.objects.filter(file_group_id=self.group.id).count(), 1)
        self.assertEqual(self.store.get(part.object_key), self.data)
