import asyncio
import base64
import os

import httpx
import pytest

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.db.session import session_factory
from short_drama.main import create_app

pytestmark = pytest.mark.integration


def test_saved_key_discovery_is_read_only_and_cannot_redirect_key(
    mysql_engine, db_session, upstream
):
    base, calls, _ = upstream

    async def run():
        app = create_app(
            Settings(
                _env_file=None,
                encryption_key=base64.b64encode(os.urandom(32)).decode(),
                model_discovery_allowed_hosts=["127.0.0.1"],
            )
        )
        factory = session_factory(mysql_engine)

        def session():
            with factory() as value:
                yield value

        app.dependency_overrides[get_session] = session
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                root = "/api/v1/ai-model-configs"
                item = (
                    await client.post(
                        root,
                        json={
                            "service_type": "text",
                            "name": "discovery",
                            "provider": "test",
                            "model_key": "manual",
                            "base_url": base + "/v1",
                            "apikey": "stored-test-secret",
                        },
                    )
                ).json()
                payload = {"config_id": item["id"], "base_url": base + "/v1/"}
                response = await client.post(root + "/discover-models", json=payload)
                assert response.status_code == 200 and response.json()["items"]
                assert calls[-1][1] == "Bearer stored-test-secret"
                assert "stored-test-secret" not in response.text
                call_count = len(calls)
                moved = {**payload, "base_url": base + "/elsewhere"}
                response = await client.post(root + "/discover-models", json=moved)
                assert response.status_code == 400
                assert response.json()["error"]["code"] == "model_discovery_key_address_changed"
                assert len(calls) == call_count
                for override, expected in [
                    ("new-test-secret", "Bearer new-test-secret"),
                    (None, None),
                ]:
                    response = await client.post(
                        root + "/discover-models", json={**moved, "apikey": override}
                    )
                    assert response.status_code == 200
                    assert calls[-1][1] == expected
                assert (await client.get(root + "/" + item["id"])).json() == item
                await client.patch(
                    root + "/" + item["id"],
                    json={"row_version": item["row_version"], "apikey": None},
                )
                response = await client.post(root + "/discover-models", json=moved)
                assert response.status_code == 200, (
                    "Moving a keyless config must not require a new key"
                )
                assert calls[-1][1] is None

    asyncio.run(run())
