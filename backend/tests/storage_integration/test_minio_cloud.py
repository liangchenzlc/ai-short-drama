"""Opt-in cloud test. Only objects created by this run may be removed."""

import base64
import os
from io import BytesIO

import httpx
import pytest

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage

pytestmark = [
    pytest.mark.storage_integration,
    pytest.mark.skipif(
        os.environ.get("RUN_MINIO_INTEGRATION") != "1",
        reason="Set RUN_MINIO_INTEGRATION=1 to test the configured cloud MinIO",
    ),
]


@pytest.mark.parametrize(
    "mime,kind,content",
    [
        (
            "image/png",
            "image",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1kAAAAASUVORK5CYII="
            ),
        ),
        # A minimal ISO-BMFF ftyp box exercises storage transport; this is not a playable video.
        ("video/mp4", "video", b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"),
    ],
)
def test_cloud_upload_stat_stream_presign_and_cleanup(mime, kind, content):
    settings = Settings()
    adapter = MinioStorage(settings)
    service = StorageService(adapter, settings)
    uploaded = None
    try:
        assert service.check_storage() == {"image": "ok", "video": "ok"}
        uploaded = service.upload(BytesIO(content), length=len(content), content_type=mime)
        assert uploaded.bucket == getattr(settings, f"minio_{kind}_bucket")
        metadata = service.stat(uploaded.storage_locator)
        assert metadata.size == len(content)
        assert metadata.content_type == mime
        with service.open(uploaded.storage_locator) as response:
            assert b"".join(response.stream(8)) == content
        # Never print a presigned URL: it grants temporary access to this object.
        url = service.download_url(uploaded.storage_locator, expires_seconds=60)
        response = httpx.get(url, timeout=10, trust_env=False)
        assert response.status_code == 200
        assert response.content == content
    finally:
        try:
            if uploaded is not None:
                service.delete(uploaded.storage_locator, version_id=uploaded.version_id)
                with pytest.raises(NotFound):
                    service.stat(uploaded.storage_locator)
        finally:
            adapter.close()
