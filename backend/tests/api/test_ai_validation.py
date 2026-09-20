import asyncio

import httpx

from short_drama.core.config import Settings
from short_drama.main import create_app


def send(method, path, **kwargs):
    async def run():
        app = create_app(Settings(_env_file=None))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_create_validation_never_echoes_sensitive_input():
    response = send(
        "POST",
        "/api/v1/ai-model-configs",
        json={
            "service_type": "invalid",
            "name": "",
            "model_key": "m",
            "provider": "p",
            "apikey": {"accidental_secret": "sensitive-example-credential"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "sensitive-example-credential" not in response.text
    assert "input" not in response.text
    fields = {item["field"] for item in response.json()["error"]["fields"]}
    assert {"service_type", "name", "apikey"} <= fields


def test_invalid_identifier_and_missing_version_are_422():
    for method, path, body in [
        ("GET", "/api/v1/ai-model-configs/18446744073709551616", None),
        ("GET", "/api/v1/ai-model-configs/1.5", None),
        ("DELETE", "/api/v1/ai-model-configs/1", None),
        ("PATCH", "/api/v1/ai-model-configs/1", {"name": "changed"}),
        ("PUT", "/api/v1/ai-model-configs/1/default", {}),
        ("GET", "/api/v1/ai-model-configs?limit=101", None),
    ]:
        assert send(method, path, json=body).status_code == 422


def test_ai_openapi_exposes_complete_crud_and_default_operation():
    schema = send("GET", "/openapi.json").json()
    paths = schema["paths"]
    assert {"get", "post"} <= paths["/api/v1/ai-model-configs"].keys()
    assert {"get", "patch", "delete"} <= paths["/api/v1/ai-model-configs/{config_id}"].keys()
    assert "put" in paths["/api/v1/ai-model-configs/{config_id}/default"]
