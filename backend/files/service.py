"""文件组、上传部分、原件对象与页：固定对象键、字节摘要、Pillow 校验。"""
from datetime import datetime, timezone

from django.db import transaction
from django.utils.functional import SimpleLazyObject

from files.models import FileGroup, FileObject, FilePage, UploadPart
from files.storage import ObjectConflict, build_object_key, digest_bytes
from files.storage_factory import configured_object_store
from files.errors import FileRejected
from files.source_pages import inspect_source


object_store = SimpleLazyObject(configured_object_store)


@transaction.atomic
def create_file_group(*, patient, owner, source_revision, use_authorization_id, draft_id=None, group_id=None) -> FileGroup:
    values = {'id': group_id} if group_id is not None else {}
    return FileGroup.objects.create(
        **values,
        patient=patient,
        owner=owner,
        draft_id=draft_id,
        version=1,
        status=FileGroup.Status.INITIATED,
        source_revision=source_revision,
        use_authorization_id=use_authorization_id,
        created_at=datetime.now(timezone.utc),
    )


@transaction.atomic
def declare_upload_part(*, file_group, client_upload_id, ordinal, declared_checksum, declared_size_bytes, declared_media_type, intent=None) -> UploadPart:
    return UploadPart.objects.create(
        patient_id=file_group.patient_id,
        file_group_id=file_group.id,
        client_upload_id=client_upload_id,
        ordinal=ordinal,
        declared_checksum=declared_checksum,
        declared_size_bytes=declared_size_bytes,
        declared_media_type=declared_media_type,
        status=UploadPart.Status.DECLARED,
        object_key=build_object_key(file_group.patient_id, client_upload_id, "original"),
        intent=intent,
        created_at=datetime.now(timezone.utc),
    )


@transaction.atomic
def store_file_object(*, file_group, upload_part, data: bytes, purpose, retention_policy, media_type=None, limits=None) -> FileObject:
    # 按统一顺序锁定并重读，调用者可能持有另一请求提交前的旧 UploadPart。
    file_group = FileGroup.objects.select_for_update().get(id=file_group.id)
    upload_part = UploadPart.objects.select_for_update().get(
        id=upload_part.id, file_group_id=file_group.id, patient_id=file_group.patient_id)
    checksum = digest_bytes(data)
    if checksum != upload_part.declared_checksum:
        raise FileRejected("CHECKSUM_MISMATCH")
    if len(data) != upload_part.declared_size_bytes:
        raise FileRejected("SIZE_MISMATCH")
    selected_type = media_type or upload_part.declared_media_type
    object_key = build_object_key(file_group.patient_id, upload_part.client_upload_id, purpose)
    if upload_part.status == UploadPart.Status.STORED:
        saved = FileObject.objects.filter(id=upload_part.file_id, patient_id=file_group.patient_id,
            file_group_id=file_group.id, object_key=object_key, object_version=checksum,
            checksum=checksum, size_bytes=len(data), media_type=selected_type, purpose=purpose,
            retention_policy=retention_policy, status=FileObject.Status.AVAILABLE).first()
        if (saved is None or upload_part.object_version != checksum
                or object_store.get(object_key) != data):
            raise FileRejected('UPLOAD_REQUEST_MISMATCH')
        return saved
    if upload_part.file_id is not None:
        raise FileRejected('UPLOAD_REQUEST_MISMATCH')
    pages = inspect_source(data, selected_type, limits) if purpose == FileObject.Purpose.ORIGINAL else []
    existing = list(FilePage.objects.filter(file_group_id=file_group.id).order_by('ordinal'))
    if limits is not None and len(existing) + len(pages) > limits['max_pages']:
        raise FileRejected('FILE_PAGE_LIMIT_EXCEEDED')
    try:
        object_store.put_immutable(object_key, data)
    except ObjectConflict as exc:
        raise FileRejected('UPLOAD_REQUEST_MISMATCH') from exc
    file_object = FileObject.objects.create(
        patient_id=file_group.patient_id,
        file_group_id=file_group.id,
        object_key=object_key,
        object_version=checksum,
        checksum=checksum,
        size_bytes=len(data),
        media_type=media_type or upload_part.declared_media_type,
        purpose=purpose,
        status=FileObject.Status.AVAILABLE,
        retention_policy=retention_policy,
        created_at=datetime.now(timezone.utc),
    )
    for index, info in enumerate(pages, start=1):
        FilePage.objects.create(
            patient_id=file_group.patient_id,
            file_group_id=file_group.id,
            file_id=file_object.id,
            ordinal=len(existing) + index,
            width=info["width"],
            height=info["height"],
            rotation=info['rotation'],
        )
    upload_part.status = UploadPart.Status.STORED
    upload_part.object_version = checksum
    upload_part.file_id = file_object.id
    upload_part.save(update_fields=["status", "object_version", "file_id"])
    ordered = []
    for part in UploadPart.objects.filter(file_group_id=file_group.id, status='stored').order_by('ordinal'):
        ordered.extend(FilePage.objects.filter(file_group_id=file_group.id, file_id=part.file_id).order_by('ordinal'))
    # 上传期间重新排列全部原页，文件内部页序保持，已完成上传组不再修改页序。
    offset = max((page.ordinal for page in ordered), default=0) + len(ordered) + 1
    for index, page in enumerate(ordered, start=offset):
        page.ordinal = index
        page.save(update_fields=['ordinal'])
    for index, page in enumerate(ordered, start=1):
        page.ordinal = index
        page.save(update_fields=['ordinal'])
    return file_object


@transaction.atomic
def complete_file_group(*, file_group) -> FileGroup:
    file_group = FileGroup.objects.select_for_update().get(id=file_group.id)
    if file_group.status == FileGroup.Status.AVAILABLE:
        return file_group
    if file_group.status not in (FileGroup.Status.INITIATED, FileGroup.Status.UPLOADED):
        raise FileRejected("GROUP_NOT_COMPLETABLE")
    parts = UploadPart.objects.filter(file_group_id=file_group.id)
    if not parts.exists():
        raise FileRejected("GROUP_INCOMPLETE")
    if parts.exclude(status=UploadPart.Status.STORED).exists():
        raise FileRejected("GROUP_INCOMPLETE")
    file_group.status = FileGroup.Status.AVAILABLE
    file_group.save(update_fields=["status"])
    return file_group


def read_object_bytes(file_id) -> bytes:
    from retention.content_fences import assert_content_available
    assert_content_available('files.fileobject', file_id)
    file_object = FileObject.objects.filter(id=file_id).first()
    if file_object is None or file_object.status != FileObject.Status.AVAILABLE:
        raise FileRejected("OBJECT_UNAVAILABLE")
    return object_store.get(file_object.object_key)
