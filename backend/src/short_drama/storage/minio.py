"""Synchronous MinIO SDK adapter. One client/pool per application process."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import BinaryIO

import urllib3
from minio import Minio
from minio.error import InvalidResponseError, S3Error, ServerError
from urllib3.response import BaseHTTPResponse

from short_drama.core.config import Settings
from short_drama.core.exceptions import ConfigurationError, NotFound, StorageUnavailable

from .models import ObjectLocation, StoredObject


class MinioStorage:
    def __init__(self, settings: Settings, client: Minio | None = None):
        self.settings = settings
        self._pool = None
        self._client = client
        if client is None and all(
            (settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key)
        ):
            self._pool = urllib3.PoolManager(
                num_pools=2,
                maxsize=10,
                timeout=urllib3.Timeout(
                    connect=settings.minio_connect_timeout, read=settings.minio_read_timeout
                ),
                retries=urllib3.Retry(
                    total=2,
                    connect=2,
                    read=0,
                    status=0,
                    allowed_methods=frozenset({"GET", "HEAD"}),
                    backoff_factor=0.2,
                ),
                cert_reqs="CERT_REQUIRED",
            )
            self._client = Minio(
                settings.minio_endpoint,
                access_key=settings.minio_access_key.get_secret_value(),
                secret_key=settings.minio_secret_key.get_secret_value(),
                secure=settings.minio_secure,
                region=settings.minio_region,
                http_client=self._pool,
            )

    @property
    def client(self) -> Minio:
        if self._client is None:
            raise ConfigurationError("Object storage is not configured")
        return self._client

    @contextmanager
    def _errors(self):
        try:
            yield
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject", "NoSuchVersion"}:
                raise NotFound("Storage object does not exist") from None
            raise StorageUnavailable("Object storage is unavailable") from None
        except (urllib3.exceptions.HTTPError, InvalidResponseError, ServerError, OSError):
            raise StorageUnavailable("Object storage is unavailable") from None

    def check_buckets(self) -> dict[str, str]:
        with self._errors():
            for bucket in (self.settings.minio_image_bucket, self.settings.minio_video_bucket):
                if not self.client.bucket_exists(bucket):
                    raise StorageUnavailable("Required storage bucket is unavailable")
        return {"image": "ok", "video": "ok"}

    def put(
        self, bucket: str, key: str, data: BinaryIO, length: int, content_type: str
    ) -> StoredObject:
        with self._errors():
            result = self.client.put_object(
                bucket, key, data, length=length, content_type=content_type
            )
        return StoredObject(
            bucket=bucket,
            object_name=key,
            storage_locator=ObjectLocation(bucket, key).locator,
            size=length,
            content_type=content_type,
            etag=result.etag,
            version_id=result.version_id,
        )

    def stat(self, bucket: str, key: str) -> StoredObject:
        with self._errors():
            result = self.client.stat_object(bucket, key)
        return StoredObject(
            bucket=bucket,
            object_name=key,
            storage_locator=ObjectLocation(bucket, key).locator,
            size=result.size,
            content_type=result.content_type or "application/octet-stream",
            etag=result.etag,
            version_id=result.version_id,
            last_modified=result.last_modified,
        )

    @contextmanager
    def open(self, bucket: str, key: str) -> Iterator[BaseHTTPResponse]:
        with self._errors():
            response = self.client.get_object(bucket, key)
        try:
            with self._errors():
                yield response
        finally:
            try:
                response.close()
            finally:
                response.release_conn()

    def remove(self, bucket: str, key: str, version_id: str | None = None) -> None:
        with self._errors():
            self.client.remove_object(bucket, key, version_id=version_id)

    def presigned_get(self, bucket: str, key: str, expires_seconds: int) -> str:
        with self._errors():
            return self.client.presigned_get_object(
                bucket, key, expires=timedelta(seconds=expires_seconds)
            )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.clear()
