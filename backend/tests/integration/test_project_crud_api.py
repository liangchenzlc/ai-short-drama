import asyncio

import httpx
import pytest

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.db.session import session_factory
from short_drama.main import create_app
from short_drama.service.episode_novel_service import EpisodeNovelService
from short_drama.service.episode_service import EpisodeService

pytestmark = pytest.mark.integration


def test_project_episode_crud_pagination_ownership_and_references(mysql_engine, db_session):
    async def run():
        app = create_app(Settings(_env_file=None))
        factory = session_factory(mysql_engine)

        def test_session():
            with factory() as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            root = "/api/v1/projects"
            first = await client.post(root, json={"name": "story%one", "aspect": "16:9"})
            second = await client.post(root, json={"name": "other", "aspect": "9:16"})
            assert first.status_code == second.status_code == 201
            p1, p2 = first.json()["id"], second.json()["id"]
            listing = (await client.get(root, params={"q": "%", "limit": 1})).json()
            assert listing["total"] == 1
            assert listing["items"][0]["id"] == p1
            assert listing["items"][0]["episode_count"] == 0
            assert (await client.get(root, params={"offset": 1, "limit": 1})).json()["total"] == 2
            original = (await client.get(f"{root}/{p1}")).json()
            opened = await client.post(f"{root}/{p1}/open")
            assert opened.status_code == 200
            assert opened.json()["updated_at"] == original["updated_at"]
            assert (await client.get(root)).json()["items"][0]["id"] == p1
            changed = await client.patch(f"{root}/{p1}", json={"name": " renamed ", "style": "ink"})
            assert changed.status_code == 200 and changed.json()["name"] == "renamed"
            assert changed.json()["created_at"] == original["created_at"]
            eps = f"{root}/{p1}/episodes"
            first_episode = await client.post(eps, json={"title": "first"})
            second_episode = await client.post(eps, json={"title": "second"})
            e1, e2 = first_episode.json()["id"], second_episode.json()["id"]
            assert first_episode.json()["style"] == "ink"
            assert (await client.get(root, params={"q": "renamed"})).json()["items"][0][
                "episode_count"
            ] == 2
            await client.patch(f"{root}/{p1}", json={"style": "watercolor"})
            detail = (await client.get(f"{eps}/{e1}")).json()
            assert detail["style"] == "ink" and detail["episode_number"] == 1
            result = await client.patch(
                f"{eps}/{e1}", json={"title": " edited ", "aspect": "9:16", "style": ""}
            )
            assert result.status_code == 200
            assert (result.json()["title"], result.json()["style"], result.json()["aspect"]) == (
                "edited",
                "",
                "9:16",
            )
            page = (await client.get(eps, params={"offset": 1, "limit": 1})).json()
            assert page["total"] == 2 and page["items"][0]["id"] == e2
            for method, body in (("GET", None), ("PATCH", {"title": "hijack"}), ("DELETE", None)):
                response = await client.request(method, f"{root}/{p2}/episodes/{e1}", json=body)
                assert response.status_code == 404
            for path, body in (
                (f"{root}/{p1}", {"target_ms": 60000}),
                (f"{root}/{p1}", {"last_opened_at": None}),
                (f"{root}/{p1}", {"name": None}),
                (f"{eps}/{e1}", {"project_id": p2}),
                (f"{eps}/{e1}", {"position": 3}),
                (f"{eps}/{e1}", {"style": None}),
            ):
                assert (await client.patch(path, json=body)).status_code == 422
            assert (await client.delete(f"{root}/{p1}")).status_code == 409
            novel = EpisodeNovelService(db_session).create({"episode_id": e1, "content": "keep"})
            assert (await client.delete(f"{eps}/{e1}")).status_code == 409
            assert (await client.get(f"{eps}/{e1}")).status_code == 200
            EpisodeNovelService(db_session).delete(novel.id)
            assert (await client.delete(f"{eps}/{e1}")).status_code == 204
            # Position 2 now displays as the first episode; IDs remain stable.
            assert (await client.get(f"{eps}/{e2}")).json()["episode_number"] == 1
            assert (await client.get(f"{eps}/{e1}")).status_code == 404
            EpisodeService(db_session).update(e2, {"position": 10})
            assert (await client.get(f"{eps}/{e2}")).json()["episode_number"] == 1
            assert (await client.delete(f"{eps}/{e2}")).status_code == 204
            assert (await client.get(eps)).json()["total"] == 0
            assert (await client.delete(f"{root}/{p1}")).status_code == 204
            assert (await client.get(f"{root}/{p1}")).status_code == 404
            assert (await client.get(eps)).status_code == 404
            assert (await client.delete(f"{root}/{p2}")).status_code == 204
            assert (await client.get(root)).json()["total"] == 0

    asyncio.run(run())
