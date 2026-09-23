import asyncio
from datetime import datetime

import httpx

from short_drama.api.dependencies import get_asset_image_service, get_asset_library_service
from short_drama.core.config import Settings
from short_drama.main import create_app
from short_drama.schemas.asset_image_candidate import AssetImageCandidateRead
from short_drama.schemas.asset_library import LibraryAssetRead


def library_item():
    now = datetime(2026, 9, 21, 1, 2, 3)
    return LibraryAssetRead(
        id=2**60,
        link_id=2**60 + 1,
        position=1,
        kind="prop",
        name="umbrella",
        label="",
        description="",
        prompt="red umbrella",
        tags=["rain"],
        scene_time="",
        state="unconfirmed",
        row_version=1,
        media_id=None,
        image=None,
        reference_count=1,
        created_at=now,
        updated_at=now,
    )


def test_asset_routes_require_idempotency_and_expose_real_multipart_upload():
    document = create_app(Settings(_env_file=None)).openapi()
    assert "/api/v1/libraries/global/assets" in document["paths"]
    upload = document["paths"]["/api/v1/assets/{asset_id}/image-candidates/upload"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    parameters = document["paths"]["/api/v1/projects/{project_id}/assets"]["post"]["parameters"]
    key = next(parameter for parameter in parameters if parameter["name"] == "Idempotency-Key")
    assert key["required"] is True


def test_asset_http_status_serialization_headers_and_upload_boundary():
    item = library_item()

    class Libraries:
        replay = False
        unlinked = []

        def create(self, kind, parent_id, project_id, payload, key):
            created = not self.replay
            self.replay = True
            return item, created

        def unlink(self, kind, parent_id, project_id, asset_id, row_version):
            self.unlinked.append((kind, asset_id, row_version))

    class Images:
        def upload(self, asset_id, stream, length, name, content_type):
            assert stream.read() == b"image"
            assert (length, name, content_type) == (5, "one.png", "image/png")
            return (
                AssetImageCandidateRead(
                    id=2**60 + 2,
                    media_id=2**60 + 3,
                    url="https://signed.test/one.png",
                    width=2,
                    height=3,
                    created_at=datetime(2026, 9, 21, 1, 2, 3),
                ),
                True,
            )

    async def run():
        app = create_app(Settings(_env_file=None))
        libraries = Libraries()
        app.dependency_overrides[get_asset_library_service] = lambda: libraries
        app.dependency_overrides[get_asset_image_service] = Images
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            payload = {"kind": "prop", "name": "umbrella", "prompt": "red umbrella"}
            missing = await client.post("/api/v1/libraries/global/assets", json=payload)
            assert missing.status_code == 422
            first = await client.post(
                "/api/v1/libraries/global/assets",
                headers={"Idempotency-Key": "create-1"},
                json=payload,
            )
            assert first.status_code == 201
            assert first.json()["id"] == str(2**60)
            replay = await client.post(
                "/api/v1/libraries/global/assets",
                headers={"Idempotency-Key": "create-1"},
                json=payload,
            )
            assert replay.status_code == 200
            invalid = await client.delete(
                f"/api/v1/libraries/global/assets/{2**60}", headers={"If-Match": "1"}
            )
            assert invalid.status_code == 422
            deleted = await client.delete(
                f"/api/v1/libraries/global/assets/{2**60}", headers={"If-Match": '"1"'}
            )
            assert deleted.status_code == 204
            assert libraries.unlinked == [("global", 2**60, 1)]
            uploaded = await client.post(
                f"/api/v1/assets/{2**60}/image-candidates/upload",
                files={"file": ("one.png", b"image", "image/png")},
            )
            assert uploaded.status_code == 201
            assert uploaded.json()["media_id"] == str(2**60 + 3)

    asyncio.run(run())
