import asyncio
import base64
import os

import httpx
import pytest
from sqlalchemy import select

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.db.session import session_factory
from short_drama.domain import AIModelConfig
from short_drama.main import create_app

pytestmark = pytest.mark.integration


def test_http_ai_crud_persists_all_types_and_hides_key(mysql_engine, db_session):
    key = base64.b64encode(os.urandom(32)).decode()
    created_ids = []

    async def run():
        app = create_app(Settings(_env_file=None, encryption_key=key))
        factory = session_factory(mysql_engine)

        def test_session():
            with factory() as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                root = "/api/v1/ai-model-configs"
                for kind in ("text", "image", "video"):
                    response = await client.post(
                        root,
                        json={
                            "service_type": kind,
                            "name": kind + " config",
                            "provider": "test",
                            "model_key": "model-" + kind,
                            "apikey": "test-only-key",
                        },
                    )
                    assert response.status_code == 201, response.text
                    value = response.json()
                    created_ids.append(int(value["id"]))
                    assert isinstance(value["id"], str)
                    assert value["row_version"] == "1"
                    assert value["has_api_key"] is True
                    assert "apikey" not in value and "test-only-key" not in response.text
                    listing = await client.get(root, params={"service_type": kind, "limit": 1})
                    assert listing.status_code == 200
                    assert listing.json()["total"] == 1
                    assert listing.json()["items"][0]["id"] == value["id"]
                item_id = str(created_ids[0])
                detail = await client.get(f"{root}/{item_id}")
                assert detail.json()["name"] == "text config"
                updated = await client.patch(
                    f"{root}/{item_id}",
                    json={
                        "name": "updated",
                        "model_key": "changed",
                        "row_version": "1",
                    },
                )
                assert updated.status_code == 200
                assert updated.json()["row_version"] == "2"
                assert updated.json()["has_api_key"] is True
                stale = await client.patch(
                    f"{root}/{item_id}",
                    json={
                        "name": "stale",
                        "row_version": "1",
                    },
                )
                assert stale.status_code == 409
                assert (await client.get(f"{root}/{item_id}")).json()["name"] == "updated"
                default = await client.put(f"{root}/{item_id}/default", json={"row_version": "2"})
                assert default.status_code == 200 and default.json()["is_default"] == 1
                cleared = await client.patch(
                    f"{root}/{item_id}",
                    json={
                        "apikey": None,
                        "row_version": default.json()["row_version"],
                    },
                )
                assert cleared.status_code == 200 and cleared.json()["has_api_key"] is False
                for item in (await client.get(root)).json()["items"]:
                    response = await client.delete(
                        f"{root}/{item['id']}",
                        params={
                            "row_version": item["row_version"],
                        },
                    )
                    assert response.status_code == 204 and not response.content
                    assert (await client.get(f"{root}/{item['id']}")).status_code == 404
                assert (await client.get(root)).json()["total"] == 0

    asyncio.run(run())
    with db_session.begin():
        persisted = list(db_session.scalars(select(AIModelConfig).order_by(AIModelConfig.id)))
        assert len(persisted) == 3 and all(row.is_deleted for row in persisted)
        assert persisted[0].apikey is None
        assert KeyCipher(key).decrypt(persisted[1].apikey) == "test-only-key"


def test_http_default_switch_keeps_categories_independent(mysql_engine, db_session):
    async def run():
        app = create_app(Settings(_env_file=None))
        factory = session_factory(mysql_engine)

        def test_session():
            with factory() as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                root = "/api/v1/ai-model-configs"
                values = []
                for kind in ("text", "image", "text"):
                    item = (
                        await client.post(
                            root,
                            json={
                                "name": kind,
                                "service_type": kind,
                                "model_key": "m",
                                "provider": "p",
                            },
                        )
                    ).json()
                    values.append(item)
                    response = await client.put(
                        f"{root}/{item['id']}/default",
                        json={
                            "row_version": item["row_version"],
                        },
                    )
                    assert response.status_code == 200
                items = (await client.get(root)).json()["items"]
                defaults = [value["id"] for value in items if value["is_default"]]
                assert set(defaults) == {values[1]["id"], values[2]["id"]}
                response = await client.delete(
                    f"{root}/{values[0]['id']}", params={"row_version": "1"}
                )
                assert response.status_code == 409

    asyncio.run(run())
