import asyncio

import httpx

from short_drama.api.dependencies import get_session
from short_drama.api.v1 import generation_references
from short_drama.core.config import Settings
from short_drama.main import create_app


def test_reference_routes_validate_owner_and_version_and_accept_multipart(monkeypatch):
    calls = []

    class References:
        def upload(self, owner, stream, size, name, content_type):
            assert stream.read() == b"image"
            assert (size, name, content_type) == (5, "reference.png", "image/png")
            return {"row_version": "8", "items": [{"media_id": str(2**60)}]}, True

        def remove_reference(self, owner, media_id):
            return {"row_version": "9", "items": []}

    def service(request, session, kind, version=None):
        calls.append((kind, version))
        return References()

    monkeypatch.setattr(generation_references, "service", service)

    async def run():
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_session] = lambda: None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            url = "/api/v1/generation-references/asset/101"
            file = {"file": ("reference.png", b"image", "image/png")}
            assert (await client.post(url, files=file)).status_code == 422
            assert (
                await client.post(url, files=file, headers={"If-Match": "7"})
            ).status_code == 422
            assert (
                await client.post(
                    url.replace("/asset/", "/unknown/"), files=file, headers={"If-Match": '"7"'}
                )
            ).status_code == 422
            result = await client.post(url, files=file, headers={"If-Match": '"7"'})
            assert result.status_code == 200
            assert result.json()["items"][0]["media_id"] == str(2**60)
            removed = await client.delete(f"{url}/{2**60}", headers={"If-Match": '"8"'})
            assert removed.status_code == 200 and removed.json()["items"] == []
            assert calls == [("asset", 7), ("asset", 8)]

    asyncio.run(run())
