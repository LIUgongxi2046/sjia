from contextlib import ExitStack
import hashlib
import os
import stat
from tempfile import TemporaryFile

from django.http import FileResponse

from core.errors import CodedRuntimeError
from files.deletion import object_identity, object_path
from files.storage import ObjectNotFound
from files.storage_factory import storage_selection


class FileContentRejected(CodedRuntimeError):
    pass


def verified_file_response(file):
    store = storage_selection.store
    with ExitStack() as resources:
        if storage_selection.kind == 's3':
            stream = resources.enter_context(TemporaryFile(mode='w+b', dir=store.control_store.root))
            if store.read_to(file.object_key, stream) != file.size_bytes:
                raise FileContentRejected('FILE_OBJECT_SIZE_MISMATCH')
        elif storage_selection.kind == 'local':
            path = object_path(store, file.object_key)
            if not path.is_file():
                raise ObjectNotFound(file.object_key)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stream = resources.enter_context(os.fdopen(descriptor, 'rb'))
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != file.size_bytes:
                raise FileContentRejected('FILE_OBJECT_SIZE_MISMATCH')
            if hashlib.file_digest(stream, 'sha256').hexdigest() != file.checksum:
                raise FileContentRejected('FILE_OBJECT_BYTES_MISMATCH')
            if object_identity(os.fstat(stream.fileno())) != object_identity(before) or object_identity(path.stat()) != object_identity(before):
                raise FileContentRejected('FILE_OBJECT_CHANGED_DURING_READ')
        else:
            raise FileContentRejected('FILE_OBJECT_BACKEND_UNAVAILABLE')
        stream.seek(0)
        response = FileResponse(stream, content_type=file.media_type, headers={'Accept-Ranges': 'bytes'})
        response.block_size = 1024 * 1024
        resources.pop_all()
        return response
