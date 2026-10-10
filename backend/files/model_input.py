from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from django.db import transaction

from archives.models import PurposeCopy
from files.models import FileObject
from files.purpose import checked_bytes, current_review, lock_copy_context, runtime_environment
from files.service import FileRejected
from files.storage import digest_bytes


@dataclass(frozen=True)
class ModelInputPermit:
    copy_id: str
    copy_version: str
    checksum: str
    target: str
    media_type: str


_permit = ContextVar('purpose_model_input_permit', default=None)


@transaction.atomic
def model_copy_ref(*, original_file_id, actor, auth_epoch, target, file_page_id=None):
    copies = list(PurposeCopy.objects.filter(original_file_id=original_file_id, purpose='external_recognition',
        environment=runtime_environment(), status='approved', processing_evidence__target=target))
    if file_page_id is not None:
        copies = [copy for copy in copies if copy.processing_evidence.get('file_page_id') == str(file_page_id)
            or (copy.processing_evidence.get('file_page_id') is None
                and FileObject.objects.get(id=original_file_id).media_type.startswith('image/'))]
    if len(copies) != 1:
        raise FileRejected('PURPOSE_COPY_SELECTION_REQUIRED' if len(copies) > 1 else 'PURPOSE_REVIEW_REQUIRED')
    copy, original, derived, actor, patient, gate, group = lock_copy_context(copies[0].id, actor, auth_epoch, service=False)
    if current_review(copy, original, derived) is None:
        raise FileRejected('PURPOSE_REVIEW_REQUIRED')
    checked_bytes(original)
    checked_bytes(derived)
    return {'copy_id': str(copy.id), 'copy_version': str(copy.version), 'checksum': derived.checksum, 'target': target}


@contextmanager
def model_copy_input(*, original_file_id, actor, auth_epoch, reference):
    with transaction.atomic():
        copy, original, derived, actor, patient, gate, group = lock_copy_context(reference['copy_id'], actor, auth_epoch, service=False)
        if (str(original.id) != str(original_file_id) or str(copy.version) != reference['copy_version']
                or derived.checksum != reference['checksum'] or copy.purpose != 'external_recognition'
                or copy.processing_evidence['target'] != reference['target'] or current_review(copy, original, derived) is None):
            raise FileRejected('PURPOSE_MODEL_INPUT_DENIED')
        checked_bytes(original)
        data = checked_bytes(derived)
        permit = ModelInputPermit(str(copy.id), str(copy.version), derived.checksum, reference['target'], derived.media_type)
    token = _permit.set(permit)
    try:
        yield data, derived.media_type
    finally:
        _permit.reset(token)


def require_model_image(data, media_type, target):
    permit = _permit.get()
    if permit is None or permit.checksum != digest_bytes(data) or permit.media_type != media_type or permit.target != target:
        raise FileRejected('PURPOSE_MODEL_INPUT_DENIED')


def require_model_text(data, media_type, target):
    require_model_image(data, media_type, target)
    if media_type != 'text/plain':
        raise FileRejected('PURPOSE_TEXT_INPUT_INVALID')
    text = data.decode('utf-8', errors='strict')
    if not text.strip() or len(text) > 8000:
        raise FileRejected('PURPOSE_TEXT_CONTENT_INVALID')
