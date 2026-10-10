"""T12 文件上传、多页组、授权原件与来源坐标逻辑验证。

运行：backend/.venv/bin/python backend/manage.py test tests.test_T12 --keepdb
"""
import io
import uuid
from datetime import datetime, timezone

from django.test import TestCase
from PIL import Image

from files.models import FileObject, FilePage, UploadPart
from files.service import FileRejected, complete_file_group, create_file_group, declare_upload_part, read_object_bytes, store_file_object
from files.storage import build_object_key, digest_bytes, parse_range, validate_image
from identity.models import Patient, Principal
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from sources.service import register_revision, register_source


def _now():
    return datetime.now(timezone.utc)


def _png_bytes(width=10, height=20):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


class FileServiceTest(TestCase):
    def setUp(self):
        now = _now()
        self.principal = Principal.objects.create(account_status="active", created_at=now, updated_at=now)
        self.patient = Patient.objects.create(display_id="P-T12", status="active", created_at=now)
        self.retention = RetentionPolicy.objects.create(policy_key="file", version=1, data_class="file", rules={}, schema_version="r-1", status="active")
        source = register_source(source_key="file-src", owner="o", location="l", purpose="p", sensitivity="s", update_frequency="d")
        revision = register_revision(source=source, version="v1", checksum="c" * 64, license="l", allowed_use=["family_observation"])
        self.use_auth = DataUseAuthorization.objects.create(patient=self.patient, source_revision=revision, purpose="family_observation", environment="test", evidence_ref="e", approved_by=self.principal, approved_at=now)
        self.group = create_file_group(patient=self.patient, owner=self.principal, source_revision=revision, use_authorization_id=self.use_auth.id)

    def _store(self, data, ordinal=1):
        checksum = digest_bytes(data)
        part = declare_upload_part(file_group=self.group, client_upload_id=uuid.uuid4(), ordinal=ordinal, declared_checksum=checksum, declared_size_bytes=len(data), declared_media_type="image/png")
        file_object = store_file_object(file_group=self.group, upload_part=part, data=data, purpose="original", retention_policy=self.retention, media_type="image/png")
        return file_object

    def test_declare_part_fixed_object_key(self):
        data = _png_bytes()
        client_id = uuid.uuid4()
        part = declare_upload_part(file_group=self.group, client_upload_id=client_id, ordinal=1, declared_checksum=digest_bytes(data), declared_size_bytes=len(data), declared_media_type="image/png")
        self.assertEqual(part.object_key, build_object_key(self.patient.id, client_id, "original"))

    def test_store_object_creates_page(self):
        data = _png_bytes(10, 20)
        file_object = self._store(data)
        self.assertEqual(file_object.status, "available")
        self.assertEqual(file_object.checksum, digest_bytes(data))
        page = FilePage.objects.get(file_id=file_object.id)
        self.assertEqual(page.width, 10)
        self.assertEqual(page.height, 20)

    def test_checksum_mismatch_rejected(self):
        data = _png_bytes()
        part = declare_upload_part(file_group=self.group, client_upload_id=uuid.uuid4(), ordinal=1, declared_checksum="a" * 64, declared_size_bytes=len(data), declared_media_type="image/png")
        with self.assertRaises(FileRejected):
            store_file_object(file_group=self.group, upload_part=part, data=data, purpose="original", retention_policy=self.retention, media_type="image/png")

    def test_complete_group_requires_all_parts(self):
        with self.assertRaises(FileRejected):
            complete_file_group(file_group=self.group)

    def test_complete_group_after_store(self):
        self._store(_png_bytes())
        complete_file_group(file_group=self.group)
        self.group.refresh_from_db()
        self.assertEqual(self.group.status, "available")

    def test_read_object_round_trip(self):
        data = _png_bytes()
        file_object = self._store(data)
        self.assertEqual(read_object_bytes(file_object.id), data)


class StorageHelperTest(TestCase):
    def test_digest_sha256(self):
        self.assertEqual(len(digest_bytes(b"hello")), 64)

    def test_parse_range(self):
        self.assertIsNone(parse_range(None, 100))
        self.assertEqual(parse_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range("bytes=95-", 100), (95, 99))

    def test_parse_range_unsatisfiable(self):
        from files.storage import RangeNotSatisfiable

        with self.assertRaises(RangeNotSatisfiable):
            parse_range("bytes=200-", 100)

    def test_validate_image(self):
        info = validate_image(_png_bytes(15, 25))
        self.assertEqual(info["format"], "PNG")
        self.assertEqual(info["width"], 15)
        self.assertEqual(info["height"], 25)
