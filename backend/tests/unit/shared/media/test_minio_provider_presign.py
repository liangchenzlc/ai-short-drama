"""供应商引用的离线签名合同；不验证公网或真实供应商。"""

from datetime import timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from short_drama.core.config import Settings
from short_drama.core.exceptions import ConfigurationError
from short_drama.storage.minio import MinioStorage


def configured(**changes):
    values = {
        "auth_enabled": True,
        "minio_endpoint": "localhost:9000",
        "minio_access_key": "offline-access",
        "minio_secret_key": "offline-secret",
        "minio_public_endpoint": "media.test:9443",
        "minio_provider_presign_expiry": 1800,
        "minio_region": "us-east-1",
    }
    return Settings(_env_file=None, **{**values, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"minio_public_endpoint": "https://media.test"},
        {"minio_public_endpoint": "user:secret@media.test"},
        {"minio_public_endpoint": "@media.test"},
        {"minio_public_endpoint": "media.test/path"},
        {"minio_public_endpoint": "media.test?q=secret"},
        {"minio_public_endpoint": "media.test?"},
        {"minio_public_endpoint": "media.test#fragment"},
        {"minio_public_endpoint": "media.test#"},
        {"minio_public_endpoint": "media.test\\path"},
        {"minio_public_endpoint": "media.test:65536"},
        {"minio_public_endpoint": "media.test:"},
        {"minio_public_endpoint": "reference.invalid"},
        {"minio_provider_presign_expiry": 0},
        {"minio_provider_presign_expiry": 604801},
        {"minio_provider_presign_expiry": True},
        {"minio_provider_presign_expiry": 1800.0},
        {"minio_provider_presign_expiry": None},
        {"minio_region": ""},
        {"minio_access_key": None},
        {"minio_secret_key": None},
    ],
)
def test_bad_public_configuration_rejects_without_signing(changes):
    with pytest.raises(ValidationError):
        configured(**changes)


def test_provider_ttl_has_no_default_and_disabled_signing_is_controlled():
    settings = Settings(_env_file=None)
    assert settings.minio_provider_presign_expiry is None
    assert not settings.provider_references_enabled
    storage = MinioStorage(settings, client=Mock())
    with pytest.raises(ConfigurationError):
        storage.provider_presigned_get("image", "reference.png")


def test_explicit_region_static_credentials_sign_without_region_or_http_lookup(monkeypatch):
    import socket

    from minio import Minio

    def forbidden(*args, **kwargs):
        raise AssertionError("offline signing must not perform I/O")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(Minio, "_url_open", forbidden)
    storage = MinioStorage(configured(), client=Mock())
    try:
        url = storage.provider_presigned_get("image", "folder/reference.png")
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc == "media.test:9443"
        assert parts.path == "/image/folder/reference.png"
        query = parse_qs(parts.query)
        assert query["X-Amz-Expires"] == ["1800"]
        assert query["X-Amz-Credential"][0].endswith("/us-east-1/s3/aws4_request")
        assert "X-Amz-Signature" in query
    finally:
        storage.close()


def test_browser_presign_keeps_internal_endpoint_and_five_minute_cap():
    from short_drama.service.media.files.storage import StorageService

    sdk = Mock()
    sdk.presigned_get_object.return_value = "http://localhost:9000/image/a?offline=1"
    settings = configured(minio_presign_expiry=1800)
    storage = MinioStorage(settings, client=sdk)
    service = StorageService(storage, settings)
    assert service.download_url("minio://image/a").startswith("http://localhost:9000/")
    sdk.presigned_get_object.assert_called_once_with("image", "a", expires=timedelta(seconds=300))
