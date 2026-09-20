from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from urllib3.exceptions import MaxRetryError


def settings(**overrides):
    from short_drama.core.config import Settings

    return Settings(
        _env_file=None,
        minio_endpoint="localhost:9000",
        minio_access_key="test-access",
        minio_secret_key="test-secret",
        **overrides,
    )


def service(sdk=None):
    from short_drama.service.storage_service import StorageService
    from short_drama.storage.minio import MinioStorage

    sdk = sdk or Mock()
    sdk.put_object.return_value = SimpleNamespace(etag="etag", version_id=None)
    return StorageService(MinioStorage(settings(), client=sdk), settings()), sdk


@pytest.mark.parametrize(
    "mime,bucket,suffix",
    [
        ("image/png", "image", ".png"),
        ("video/mp4", "video", ".mp4"),
    ],
)
def test_upload_routes_media_and_returns_stable_snowflake_locator(mime, bucket, suffix):
    store, sdk = service()
    stream = BytesIO(b"test")
    result = store.upload(stream, length=4, content_type=mime)
    assert result.bucket == bucket
    assert result.size == 4
    assert result.content_type == mime
    assert result.object_name.endswith(suffix)
    assert int(result.object_name.rsplit("/", 1)[-1].removesuffix(suffix)) > 2**53
    assert result.storage_locator == f"minio://{bucket}/{result.object_name}"
    assert "?" not in result.storage_locator
    sdk.put_object.assert_called_once_with(
        bucket,
        result.object_name,
        stream,
        length=4,
        content_type=mime,
    )


@pytest.mark.parametrize(
    "mime,length",
    [
        ("text/html", 4),
        ("image/", 4),
        ("demo:image", 4),
        ("video/mp4", -1),
        ("image/png", True),
        ("image/png", 1.5),
    ],
)
def test_upload_rejects_invalid_media_before_network(mime, length):
    from short_drama.core.exceptions import BusinessError

    store, sdk = service()
    with pytest.raises(BusinessError):
        store.upload(BytesIO(b"test"), length=length, content_type=mime)
    sdk.put_object.assert_not_called()


@pytest.mark.parametrize(
    "locator",
    [
        "https://example.com/image/a.png",
        "minio://other/a.png",
        "minio://image/",
        "minio://image/../a",
        "minio://image/a/./b",
        "minio://image/a?token=x",
        "minio://image/a#fragment",
        "minio://image/a\\b",
        "minio://image/%2e%2e/a",
        "minio://image/a%2Fb",
        "minio://image/a%00b",
        "minio://image/%zz",
        "minio://user@image/a",
        "minio://image:9000/a",
        "minio://image/" + "x" * 700,
    ],
)
def test_locator_rejects_unsafe_or_ambiguous_keys(locator):
    from short_drama.core.exceptions import BusinessError
    from short_drama.storage.models import ObjectLocation

    with pytest.raises(BusinessError):
        ObjectLocation.parse(locator, allowed_buckets={"image", "video"})


def test_locator_preserves_encoded_spaces_and_unicode():
    from short_drama.storage.models import ObjectLocation

    location = ObjectLocation("image", "folder/图片 one.png")
    restored = ObjectLocation.parse(location.locator, allowed_buckets={"image", "video"})
    assert restored == location
    assert "%20" in location.locator


def test_stream_is_closed_and_connection_released_on_consumer_error():
    store, sdk = service()
    response = Mock()
    sdk.get_object.return_value = response
    with pytest.raises(RuntimeError, match="consumer"):
        with store.open("minio://video/test.mp4") as opened:
            assert opened is response
            raise RuntimeError("consumer")
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_network_errors_are_sanitized():
    from short_drama.core.exceptions import StorageUnavailable

    store, sdk = service()
    sdk.stat_object.side_effect = MaxRetryError(None, "http://secret-access:secret@server")
    with pytest.raises(StorageUnavailable) as error:
        store.stat("minio://image/test.png")
    assert "secret" not in str(error.value)
    assert error.value.status_code == 503


def test_missing_object_maps_to_not_found():
    from minio.error import S3Error

    from short_drama.core.exceptions import NotFound

    store, sdk = service()
    sdk.stat_object.side_effect = S3Error(
        response=None,
        code="NoSuchKey",
        message="missing",
        resource="/image/a",
        request_id="req",
        host_id="host",
    )
    with pytest.raises(NotFound):
        store.stat("minio://image/a")


@pytest.mark.parametrize("expiry", [0, -1, 604801, True, 1.5])
def test_signed_url_expiry_rejected_before_network(expiry):
    from short_drama.core.exceptions import BusinessError

    store, sdk = service()
    with pytest.raises(BusinessError):
        store.download_url("minio://image/a", expires_seconds=expiry)
    sdk.presigned_get_object.assert_not_called()


def test_signed_url_uses_configured_default_expiry():
    from datetime import timedelta

    store, sdk = service()
    sdk.presigned_get_object.return_value = "http://storage/image/a?signature=test"
    assert store.download_url("minio://image/a").endswith("signature=test")
    sdk.presigned_get_object.assert_called_once_with(
        "image",
        "a",
        expires=timedelta(seconds=900),
    )


def test_missing_bucket_is_a_service_failure_without_creation():
    from short_drama.core.exceptions import StorageUnavailable

    store, sdk = service()
    sdk.bucket_exists.side_effect = [True, False]
    with pytest.raises(StorageUnavailable):
        store.check_storage()
    sdk.make_bucket.assert_not_called()


def test_missing_credentials_fail_only_when_storage_is_used():
    from short_drama.core.config import Settings
    from short_drama.core.exceptions import ConfigurationError
    from short_drama.storage.minio import MinioStorage

    adapter = MinioStorage(Settings(_env_file=None))
    try:
        with pytest.raises(ConfigurationError):
            adapter.check_buckets()
    finally:
        adapter.close()


@pytest.mark.parametrize("bucket", ["a..b", "127.0.0.1", "a.-b", "a-.b"])
def test_invalid_s3_bucket_configuration_fails_early(bucket):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        settings(minio_image_bucket=bucket)


def test_blank_object_name_is_rejected_before_sdk():
    from short_drama.core.exceptions import BusinessError

    store, sdk = service()
    with pytest.raises(BusinessError):
        store.stat("minio://image/%20")
    sdk.stat_object.assert_not_called()
