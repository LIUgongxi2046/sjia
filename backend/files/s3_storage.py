import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import ssl
from typing import BinaryIO, Literal
from urllib.parse import urlsplit
from uuid import UUID
from xml.etree import ElementTree

import boto3
from botocore.config import Config
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from core.errors import CodedRuntimeError
from files.storage import ObjectConflict


class S3StorageRejected(CodedRuntimeError):
    pass


def canonical_key(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or not path.parts or path.is_absolute() or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts) or "\x00" in value):
        raise S3StorageRejected("S3_OBJECT_KEY_INVALID")
    return value


@dataclass(frozen=True)
class S3StorageConfiguration:
    provider_id: str
    endpoint_url: str
    region: str
    bucket: str
    prefix: str
    owner_id: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket_policy_sha256: str = "absent"
    session_token: str | None = field(default=None, repr=False)
    ca_bundle: Path | None = None
    connect_timeout: int = 5
    read_timeout: int = 120

    def __post_init__(self):
        address = urlsplit(self.endpoint_url)
        if (address.scheme != "https" or not address.hostname or address.username is not None
                or address.password is not None or address.path not in {"", "/"}
                or address.query or address.fragment):
            raise S3StorageRejected("S3_HTTPS_ENDPOINT_REQUIRED")
        if any(not value.strip() for value in (self.provider_id, self.region, self.bucket,
                self.prefix, self.owner_id, self.access_key, self.secret_key)):
            raise S3StorageRejected("S3_CONFIGURATION_REQUIRED")
        canonical_key(self.prefix)
        if self.ca_bundle is not None and (not self.ca_bundle.is_file() or self.ca_bundle.is_symlink()):
            raise S3StorageRejected("S3_CA_BUNDLE_UNAVAILABLE")
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise S3StorageRejected("S3_TIMEOUT_INVALID")
        if self.session_token is not None and not self.session_token.strip():
            raise S3StorageRejected("S3_SESSION_TOKEN_INVALID")
        if self.bucket_policy_sha256 != "absent" and (len(self.bucket_policy_sha256) != 64
                or any(character not in "0123456789abcdef" for character in self.bucket_policy_sha256)):
            raise S3StorageRejected("S3_BUCKET_POLICY_CHECKSUM_INVALID")

    @property
    def binding_sha256(self) -> str:
        identity = {name: getattr(self, name) for name in (
            "provider_id", "endpoint_url", "region", "bucket", "prefix", "owner_id")}
        return hashlib.sha256(json.dumps(identity, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    @property
    def access_configuration_sha256(self) -> str:
        identity = {"binding_sha256": self.binding_sha256,
            "bucket_policy_sha256": self.bucket_policy_sha256,
            "access_key_sha256": hashlib.sha256(self.access_key.encode()).hexdigest(),
            "session_token_sha256": hashlib.sha256(self.session_token.encode()).hexdigest() if self.session_token else None,
            "ca_bundle_sha256": hashlib.sha256(self.ca_bundle.read_bytes()).hexdigest() if self.ca_bundle else "system"}
        return hashlib.sha256(json.dumps(identity, sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class S3ObjectReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["s3-object-reference-1"] = "s3-object-reference-1"
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_key: str
    version_id: str = Field(min_length=1)
    etag: str = Field(min_length=1)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    encryption: Literal["AES256"] = "AES256"

    @field_validator("object_key")
    @classmethod
    def validate_key(cls, value):
        return canonical_key(value)

    @field_validator("version_id")
    @classmethod
    def validate_version(cls, value):
        if value == "null":
            raise ValueError("S3_VERSION_REQUIRED")
        return value


class S3DeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["s3-delete-result-1"] = "s3-delete-result-1"
    operation_id: UUID
    object_reference: S3ObjectReference
    outcome: Literal["absent"] = "absent"
    deleted_now: bool
    provider_delete_request_id: str | None = Field(min_length=1)
    provider_delete_status: Literal[204] | None
    absence_query_request_id: str = Field(min_length=1)
    verified_at: AwareDatetime

    @model_validator(mode="after")
    def validate_response(self):
        if self.deleted_now != (self.provider_delete_request_id is not None
                and self.provider_delete_status == 204):
            raise ValueError("S3_DELETE_RESPONSE_MISMATCH")
        if not self.deleted_now and (self.provider_delete_request_id is not None
                or self.provider_delete_status is not None):
            raise ValueError("S3_DELETE_RESPONSE_MISMATCH")
        return self


class S3VersionedStore:
    def __init__(self, configuration: S3StorageConfiguration):
        self.configuration = configuration
        self.client = boto3.client("s3", endpoint_url=configuration.endpoint_url,
            aws_access_key_id=configuration.access_key, aws_secret_access_key=configuration.secret_key,
            aws_session_token=configuration.session_token,
            region_name=configuration.region,
            verify=str(configuration.ca_bundle) if configuration.ca_bundle is not None else True,
            config=Config(signature_version="s3v4", connect_timeout=configuration.connect_timeout,
                read_timeout=configuration.read_timeout, retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"}))

    def native_key(self, key: str) -> str:
        return self.configuration.prefix + "/" + canonical_key(key)

    def validate_bucket(self):
        versioning = self.client.get_bucket_versioning(Bucket=self.configuration.bucket)
        encryption = self.client.get_bucket_encryption(Bucket=self.configuration.bucket)
        rules = encryption["ServerSideEncryptionConfiguration"]["Rules"]
        if (versioning.get("Status") != "Enabled" or len(rules) != 1
                or rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] != "AES256"):
            raise S3StorageRejected("S3_BUCKET_CONFIGURATION_MISMATCH")
        acl = self.client.get_bucket_acl(Bucket=self.configuration.bucket)
        owner = acl["Owner"]["ID"]
        if owner != self.configuration.owner_id or any(item["Grantee"].get("Type") != "CanonicalUser"
                or item["Grantee"].get("ID") != owner for item in acl["Grants"]):
            raise S3StorageRejected("S3_PRIVATE_BUCKET_REQUIRED")
        url = self.client.generate_presigned_url("get_bucket_policy", Params={
            "Bucket": self.configuration.bucket}, ExpiresIn=60)
        context = ssl.create_default_context(cafile=self.configuration.ca_bundle)
        with httpx.Client(verify=context, trust_env=False,
                timeout=httpx.Timeout(self.configuration.read_timeout,
                    connect=self.configuration.connect_timeout)) as client:
            policy = client.get(url)
        if (policy.status_code == 404 and len(policy.content) <= 16384
                and ElementTree.fromstring(policy.content).findtext("Code") == "NoSuchBucketPolicy"):
            policy_checksum = "absent"
        elif policy.status_code == 200 and len(policy.content) <= 20 * 1024:
            policy_checksum = hashlib.sha256(json.dumps(policy.json(), sort_keys=True,
                separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        else:
            raise S3StorageRejected("S3_BUCKET_POLICY_UNAVAILABLE")
        if policy_checksum != self.configuration.bucket_policy_sha256:
            raise S3StorageRejected("S3_BUCKET_POLICY_MISMATCH")
        return {"versioning_request_id": versioning["ResponseMetadata"]["RequestId"],
            "encryption_request_id": encryption["ResponseMetadata"]["RequestId"],
            "policy_request_id": policy.headers["x-amz-request-id"],
            "policy_sha256": policy_checksum,
            "binding_sha256": self.configuration.binding_sha256,
            "access_configuration_sha256": self.configuration.access_configuration_sha256,
            "acl_request_id": acl["ResponseMetadata"]["RequestId"], "owner_id": owner}

    def _params(self, reference: S3ObjectReference):
        if reference.binding_sha256 != self.configuration.binding_sha256:
            raise S3StorageRejected("S3_BINDING_MISMATCH")
        return {"Bucket": self.configuration.bucket, "Key": self.native_key(reference.object_key),
            "VersionId": reference.version_id, "IfMatch": reference.etag}

    def _reference(self, key: str, response: dict) -> S3ObjectReference:
        if response.get("ServerSideEncryption") != "AES256":
            raise S3StorageRejected("S3_ENCRYPTION_MISMATCH")
        return S3ObjectReference(binding_sha256=self.configuration.binding_sha256,
            object_key=key, version_id=response["VersionId"], etag=response["ETag"],
            checksum=response["Metadata"]["sjia-sha256"], size_bytes=response["ContentLength"])

    def verify_reference(self, reference: S3ObjectReference):
        current = self.client.head_object(**self._params(reference), ChecksumMode="ENABLED")
        if self._reference(reference.object_key, current) != reference:
            raise S3StorageRejected("S3_REFERENCE_MISMATCH")
        return current

    def _key_versions(self, key: str):
        native_key = self.native_key(key)
        response = self.client.list_object_versions(Bucket=self.configuration.bucket, Prefix=native_key, MaxKeys=1000)
        if response.get("IsTruncated"):
            raise S3StorageRejected("S3_VERSION_INVENTORY_LIMIT_EXCEEDED")
        versions = [item for item in response.get("Versions", []) if item["Key"] == native_key]
        markers = [item for item in response.get("DeleteMarkers", []) if item["Key"] == native_key]
        return versions, markers, response["ResponseMetadata"]["RequestId"]

    def put_immutable(self, key: str, stream: BinaryIO, *, checksum: str, size_bytes: int) -> S3ObjectReference:
        canonical_key(key)
        position = stream.tell()
        measured = hashlib.sha256()
        count = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            measured.update(chunk)
            count += len(chunk)
        if measured.hexdigest() != checksum or count != size_bytes:
            raise S3StorageRejected("S3_SOURCE_BYTES_MISMATCH")
        stream.seek(position)
        self.validate_bucket()
        versions, markers, _ = self._key_versions(key)
        if markers or len(versions) > 1 or (versions and versions[0].get("IsLatest") is not True):
            raise S3StorageRejected("S3_IMMUTABLE_KEY_VERSION_CONFLICT")
        if versions:
            head = self.client.head_object(Bucket=self.configuration.bucket, Key=self.native_key(key),
                VersionId=versions[0]["VersionId"], ChecksumMode="ENABLED")
            existing = self._reference(key, head)
            if existing.checksum != checksum or existing.size_bytes != size_bytes:
                raise ObjectConflict(key)
            with open(os.devnull, "wb") as destination:
                self.read_to(existing, destination)
            return existing
        response = self.client.put_object(Bucket=self.configuration.bucket, Key=self.native_key(key),
            Body=stream, ContentLength=size_bytes, IfNoneMatch="*", ServerSideEncryption="AES256",
            ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode(),
            Metadata={"sjia-sha256": checksum})
        if response["ResponseMetadata"]["HTTPStatusCode"] != 200:
            raise S3StorageRejected("S3_WRITE_NOT_VERIFIED")
        reference = S3ObjectReference(binding_sha256=self.configuration.binding_sha256,
            object_key=key, version_id=response["VersionId"], etag=response["ETag"],
            checksum=checksum, size_bytes=size_bytes)
        self.verify_reference(reference)
        return reference

    def read_to(self, reference: S3ObjectReference, destination: BinaryIO):
        response = self.client.get_object(**self._params(reference), ChecksumMode="ENABLED")
        measured = hashlib.sha256()
        count = 0
        with response["Body"] as body:
            if self._reference(reference.object_key, response) != reference:
                raise S3StorageRejected("S3_REFERENCE_MISMATCH")
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                if destination.write(chunk) != len(chunk):
                    raise S3StorageRejected("S3_DESTINATION_WRITE_INCOMPLETE")
                measured.update(chunk)
                count += len(chunk)
        if measured.hexdigest() != reference.checksum or count != reference.size_bytes:
            raise S3StorageRejected("S3_OBJECT_BYTES_MISMATCH")
        return count

    def get_range(self, reference: S3ObjectReference, start: int, end: int) -> bytes:
        if not 0 <= start <= end < reference.size_bytes:
            raise S3StorageRejected("S3_RANGE_INVALID")
        response = self.client.get_object(**self._params(reference), Range=f"bytes={start}-{end}")
        expected = end - start + 1
        with response["Body"] as body:
            if (response["ResponseMetadata"]["HTTPStatusCode"] != 206
                    or response.get("ContentRange") != f"bytes {start}-{end}/{reference.size_bytes}"
                    or response["ContentLength"] != expected or response["VersionId"] != reference.version_id
                    or response["ETag"] != reference.etag or response.get("ServerSideEncryption") != "AES256"
                    or response["Metadata"].get("sjia-sha256") != reference.checksum):
                raise S3StorageRejected("S3_RANGE_RESPONSE_MISMATCH")
            data = body.read(expected + 1)
        if len(data) != expected:
            raise S3StorageRejected("S3_RANGE_BYTES_MISMATCH")
        return data

    def _version_inventory(self, reference: S3ObjectReference):
        self._params(reference)
        all_versions, _, request_id = self._key_versions(reference.object_key)
        versions = [item for item in all_versions if item["VersionId"] == reference.version_id]
        if len(versions) > 1:
            raise S3StorageRejected("S3_VERSION_INVENTORY_CONFLICT")
        return versions, request_id

    def delete_version(self, reference: S3ObjectReference, *, operation_id: UUID) -> S3DeleteResult:
        if not isinstance(operation_id, UUID) or operation_id.int == 0:
            raise S3StorageRejected("S3_DELETE_OPERATION_ID_INVALID")
        self.validate_bucket()
        versions, _ = self._version_inventory(reference)
        response = None
        if versions:
            self.verify_reference(reference)
            response = self.client.delete_object(**self._params(reference))
            if (response["ResponseMetadata"]["HTTPStatusCode"] != 204
                    or response.get("VersionId") != reference.version_id):
                raise S3StorageRejected("S3_DELETE_NOT_VERIFIED")
        remaining, query_id = self._version_inventory(reference)
        if remaining:
            raise S3StorageRejected("S3_DELETE_NOT_VERIFIED")
        return S3DeleteResult(operation_id=operation_id, object_reference=reference,
            deleted_now=response is not None,
            provider_delete_request_id=response["ResponseMetadata"]["RequestId"] if response is not None else None,
            provider_delete_status=response["ResponseMetadata"]["HTTPStatusCode"] if response is not None else None,
            absence_query_request_id=query_id, verified_at=datetime.now(timezone.utc))
