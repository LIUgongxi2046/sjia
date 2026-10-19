from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal

from django.conf import settings
from django.utils.functional import SimpleLazyObject
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from core.errors import CodedRuntimeError
from files.storage import LocalFileStore


class StorageConfigurationRejected(CodedRuntimeError):
    pass


class S3PrivateConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal["s3-private-storage-1"]
    provider_id: str = Field(min_length=1)
    endpoint_url: str = Field(min_length=1)
    region: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    prefix: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    access_key: SecretStr = Field(min_length=1, repr=False)
    secret_key: SecretStr = Field(min_length=1, repr=False)
    session_token: SecretStr | None = Field(default=None, repr=False)
    ca_bundle: Path | None = None
    control_directory: Path
    bucket_policy_sha256: str = "absent"
    connect_timeout: int = Field(default=5, gt=0)
    read_timeout: int = Field(default=120, gt=0)

    @model_validator(mode="after")
    def absolute_paths(self):
        if not self.control_directory.is_absolute() or self.ca_bundle is not None and not self.ca_bundle.is_absolute():
            raise ValueError("S3_CONFIGURATION_ABSOLUTE_PATH_REQUIRED")
        return self


def _checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _file_identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def read_private_configuration(path):
    if not path.is_absolute() or path.is_symlink():
        raise StorageConfigurationRejected("STORAGE_CONFIGURATION_PATH_INVALID")
    current = path.lstat()
    if (not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid()
            or current.st_mode & 0o077 or current.st_size > 65536):
        raise StorageConfigurationRejected("STORAGE_CONFIGURATION_PERMISSIONS_INVALID")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or before.st_size > 65536):
            raise StorageConfigurationRejected("STORAGE_CONFIGURATION_PERMISSIONS_INVALID")
        data = stream.read(65537)
        after = os.fstat(stream.fileno())
    if (len(data) > 65536 or _file_identity(before) != _file_identity(after)
            or _file_identity(path.stat()) != _file_identity(after)):
        raise StorageConfigurationRejected("STORAGE_CONFIGURATION_FILE_CHANGED")
    return data


def directory_identity(path):
    if not path.is_dir() or path.is_symlink() or not os.access(path, os.R_OK | os.X_OK):
        raise StorageConfigurationRejected("STORAGE_DIRECTORY_UNAVAILABLE")
    current = path.stat()
    return {"path": str(path.resolve(strict=True)), "device": current.st_dev, "inode": current.st_ino,
        "owner": current.st_uid, "mode": stat.S_IMODE(current.st_mode)}


@dataclass(frozen=True)
class StorageSelection:
    kind: Literal["local", "s3"]
    store: object
    configuration_file: Path | None = None
    configuration_sha256: str | None = None

    def declaration(self):
        if self.kind == "local":
            return {"kind": "local", "root_identity_sha256": _checksum(directory_identity(self.store.root))}
        data = read_private_configuration(self.configuration_file)
        if hashlib.sha256(data).hexdigest() != self.configuration_sha256:
            raise StorageConfigurationRejected("STORAGE_CONFIGURATION_RESTART_REQUIRED")
        identity = directory_identity(self.store.control_store.root)
        if ((identity["device"], identity["inode"]) != self.store.control_identity
                or identity["owner"] != os.getuid() or identity["mode"] & 0o077
                or not os.access(self.store.control_store.root, os.W_OK)):
            raise StorageConfigurationRejected("S3_CONTROL_DIRECTORY_UNAVAILABLE")
        return {"kind": "s3", "binding_sha256": self.store.driver.configuration.binding_sha256,
            "access_configuration_sha256": self.store.driver.configuration.access_configuration_sha256,
            "control_directory_sha256": _checksum(identity), "configuration_file_sha256": self.configuration_sha256}

    def writable(self):
        if self.kind == "s3":
            return os.access(self.store.control_store.root, os.R_OK | os.W_OK | os.X_OK)
        root = self.store.root
        return root.is_dir() and not root.is_symlink() and os.access(root, os.R_OK | os.W_OK | os.X_OK)


def build_storage_selection():
    backend = settings.FILE_OBJECT_BACKEND
    configuration_file = settings.S3_STORAGE_CONFIGURATION_FILE
    if backend == "local":
        if configuration_file:
            raise StorageConfigurationRejected("STORAGE_CONFIGURATION_SELECTION_CONFLICT")
        return StorageSelection(kind="local", store=LocalFileStore())
    if backend != "s3":
        raise StorageConfigurationRejected("STORAGE_BACKEND_UNREGISTERED")
    if not configuration_file:
        raise StorageConfigurationRejected("S3_STORAGE_CONFIGURATION_REQUIRED")
    path = Path(configuration_file)
    data = read_private_configuration(path)
    parsed = S3PrivateConfiguration.model_validate_json(data)
    from files.s3_registered import S3RegisteredStore
    from files.s3_storage import S3StorageConfiguration, S3VersionedStore
    configuration = S3StorageConfiguration(provider_id=parsed.provider_id, endpoint_url=parsed.endpoint_url,
        region=parsed.region, bucket=parsed.bucket, prefix=parsed.prefix, owner_id=parsed.owner_id,
        access_key=parsed.access_key.get_secret_value(), secret_key=parsed.secret_key.get_secret_value(),
        session_token=parsed.session_token.get_secret_value() if parsed.session_token is not None else None,
        ca_bundle=parsed.ca_bundle, bucket_policy_sha256=parsed.bucket_policy_sha256,
        connect_timeout=parsed.connect_timeout, read_timeout=parsed.read_timeout)
    store = S3RegisteredStore(S3VersionedStore(configuration), control_directory=parsed.control_directory)
    return StorageSelection(kind="s3", store=store, configuration_file=path,
        configuration_sha256=hashlib.sha256(data).hexdigest())


storage_selection = SimpleLazyObject(build_storage_selection)


def configured_object_store():
    return storage_selection.store


def inspect_storage_configuration(expected):
    if expected is None:
        if storage_selection.kind != "local":
            raise StorageConfigurationRejected("RUNTIME_S3_STORAGE_DECLARATION_REQUIRED")
        return None
    actual = storage_selection.declaration()
    if actual != expected:
        raise StorageConfigurationRejected("RUNTIME_STORAGE_CONFIGURATION_MISMATCH")
    if storage_selection.kind == "s3":
        storage_selection.store.driver.validate_bucket()
    return actual
