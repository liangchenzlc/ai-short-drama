import asyncio

import httpx


def request_minio(settings, storage_service=None):
    from short_drama.api.dependencies import get_storage_service
    from short_drama.main import create_app

    async def run():
        app = create_app(settings)
        if storage_service is not None:
            app.dependency_overrides[get_storage_service] = lambda: storage_service
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                return await client.get("/api/v1/test/minio")

    return asyncio.run(run())


def test_unconfigured_storage_returns_sanitized_503():
    from short_drama.core.config import Settings

    response = request_minio(Settings(_env_file=None))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "configuration_error"
    assert "secret" not in response.text.lower()


def test_minio_route_checks_both_buckets():
    from short_drama.core.config import Settings

    class Storage:
        def check_storage(self):
            return {"image": "ok", "video": "ok"}

    response = request_minio(Settings(_env_file=None), Storage())
    assert response.status_code == 200
    assert response.json() == {"message": "ok", "buckets": {"image": "ok", "video": "ok"}}
