import hashlib
import io
import os
from pathlib import Path

from django.conf import settings
from PIL import Image
from werkzeug.http import parse_range_header


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_object_key(patient_id, file_id, purpose) -> str:
    return f"patients/{patient_id}/{purpose}/{file_id}"


def validate_image(data: bytes) -> dict:
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        return {"format": image.format, "width": image.width, "height": image.height}


def parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    """解析 Range 头，返回 (start, end) 含端点；无 Range 返回 None；无效返回 416 语义。"""
    if range_header is None:
        return None
    parsed = parse_range_header(range_header)
    if parsed is None or parsed.units != 'bytes' or len(parsed.ranges) != 1 or size <= 0:
        raise RangeNotSatisfiable(size)
    begin, stop = parsed.ranges[0]
    # 后缀长度超过文件长度时，HTTP 语义要求返回完整文件。
    if stop is None and begin < -size:
        return 0, size - 1
    bounds = parsed.range_for_length(size)
    if bounds is None:
        raise RangeNotSatisfiable(size)
    return bounds[0], bounds[1] - 1


class RangeNotSatisfiable(Exception):
    def __init__(self, size=None):
        super().__init__('Range 无法满足')
        self.size = size


class ObjectNotFound(Exception):
    pass


class LocalFileStore:
    """本地文件对象存储，root 位于项目工作目录。"""

    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else settings.FILE_OBJECT_ROOT

    def put(self, object_key: str, data: bytes) -> None:
        path = self.root / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_immutable(self, object_key: str, data: bytes) -> None:
        path = self.root / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def get(self, object_key: str) -> bytes:
        path = self.root / object_key
        if not path.is_file():
            raise ObjectNotFound(object_key)
        return path.read_bytes()

    def exists(self, object_key: str) -> bool:
        path = (self.root / object_key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError('对象路径超出保存目录')
        return path.exists()

    def get_range(self, object_key: str, start: int, end: int) -> bytes:
        path = self.root / object_key
        if not path.is_file():
            raise ObjectNotFound(object_key)
        with path.open("rb") as stream:
            stream.seek(start)
            return stream.read(end - start + 1)
