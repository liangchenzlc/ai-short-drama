import pytest
from generation_fixtures import generation_session
from sqlalchemy import func, select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.domain import EpisodeNovel, EpisodeScript
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.project_service import ProjectService


def setup(session):
    project = ProjectService(session).create_project({"name": "writing", "aspect": "16:9"})
    episode = EpisodeService(session).create_for_project(project.id, {"title": "first"})
    return project.id, episode.id, EpisodeWritingService(session)


def test_empty_read_has_no_side_effects_and_novel_preserves_whitespace():
    with generation_session() as session:
        p, e, service = setup(session)
        result = service.get(p, e)
        assert result == {
            "episode_id": str(e),
            "content_version": "1",
            "novel": None,
            "editing_script": None,
            "confirmed_script_id": None,
        }
        with session.begin():
            for model in (EpisodeNovel, EpisodeScript):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        saved = service.save_novel(p, e, {"content": "  text\n\n", "content_version": "1"})
        assert saved["content_version"] == "2"
        assert saved["novel"]["content"] == "  text\n\n"
        assert saved["novel"]["updated_at"].endswith("Z")
        assert service.get(p, e)["novel"] == saved["novel"]
        same = service.save_novel(p, e, {"content": "  text\n\n", "content_version": "2"})
        assert same == saved
        with pytest.raises(Conflict):
            service.save_novel(p, e, {"content": "stale", "content_version": "1"})
        assert service.get(p, e)["novel"]["content"] == "  text\n\n"


def test_script_create_confirm_edit_and_empty_confirmation():
    with generation_session() as session:
        p, e, service = setup(session)
        saved = service.save_script(
            p,
            e,
            {
                "script_id": None,
                "content": "script",
                "content_version": "1",
            },
        )
        identifier = saved["script"]["id"]
        assert saved["content_version"] == "2"
        assert saved["script"]["state"] == "unconfirmed"
        with pytest.raises(Conflict):
            service.save_script(
                p, e, {"script_id": None, "content": "duplicate", "content_version": "2"}
            )
        confirmed = service.confirm(p, e, identifier, {"content_version": "2"})
        assert confirmed["confirmed_script_id"] == identifier
        assert confirmed["content_version"] == "3"
        assert service.confirm(p, e, identifier, {"content_version": "3"}) == confirmed
        edited = service.save_script(
            p,
            e,
            {
                "script_id": identifier,
                "content": "revised",
                "content_version": "3",
            },
        )
        assert edited["script"]["state"] == "unconfirmed"
        assert service.get(p, e)["confirmed_script_id"] is None
        cleared = service.save_script(
            p,
            e,
            {
                "script_id": identifier,
                "content": " \n",
                "content_version": "4",
            },
        )
        assert cleared["content_version"] == "5"
        with pytest.raises(Exception) as exc:
            service.confirm(p, e, identifier, {"content_version": "5"})
        assert exc.value.status_code == 422
        assert service.get(p, e)["content_version"] == "5"


def test_selection_preserves_candidates_and_confirmation_is_explicit():
    from short_drama.service.episode_script_service import EpisodeScriptService

    with generation_session() as session:
        p, e, service = setup(session)
        first = service.save_script(
            p, e, {"script_id": None, "content": "one", "content_version": "1"}
        )
        first_id = first["script"]["id"]
        service.confirm(p, e, first_id, {"content_version": "2"})
        second = EpisodeScriptService(session).create(
            {"episode_id": e, "position": 2, "content": "two"}
        )
        previous = service.get(p, e)
        assert int(previous["content_version"]) > 3
        selected = service.select_script(
            p, e, {"script_id": str(second.id), "content_version": previous["content_version"]}
        )
        assert selected["editing_script"]["content"] == "two"
        assert selected["confirmed_script_id"] == first_id
        with pytest.raises(Conflict):
            service.confirm(p, e, first_id, {"content_version": selected["content_version"]})
        result = service.confirm(p, e, second.id, {"content_version": selected["content_version"]})
        assert result["confirmed_script_id"] == str(second.id)
        assert EpisodeScriptService(session).get(first_id).state == "unconfirmed"


def test_scopes_and_input_validation():
    from pydantic import ValidationError

    with generation_session() as session:
        p, e, service = setup(session)
        other_p, other_e, _ = setup(session)
        with pytest.raises(NotFound):
            service.get(other_p, e)
        remote = service.save_script(
            other_p, other_e, {"script_id": None, "content": "other", "content_version": "1"}
        )
        with pytest.raises(NotFound):
            service.select_script(
                p, e, {"script_id": remote["script"]["id"], "content_version": "1"}
            )
        for payload in [
            {"content": None, "content_version": "1"},
            {"content": "x" * (1024**2 + 1), "content_version": "1"},
            {"content": "汉" * 349526, "content_version": "1"},
            {"content": "x", "content_version": "0"},
            {"content": "x", "content_version": "1", "episode_id": str(other_e)},
        ]:
            with pytest.raises(ValidationError):
                service.save_novel(p, e, payload)
        assert service.get(p, e)["content_version"] == "1"


def test_http_writing_contract_and_closed_inputs():
    import asyncio

    import httpx

    from short_drama.api.dependencies import get_episode_writing_service
    from short_drama.core.config import Settings
    from short_drama.main import create_app

    document = {"id": "100", "content": "text", "updated_at": "2026-09-21T00:00:00Z"}
    script = {**document, "state": "unconfirmed"}
    view = {
        "episode_id": "2",
        "content_version": "1",
        "novel": document,
        "editing_script": script,
        "confirmed_script_id": None,
    }
    calls = []

    class Service:
        def get(self, project_id, episode_id):
            calls.append((project_id, episode_id))
            return view

        def save_novel(self, project_id, episode_id, payload):
            calls.append(payload)
            return {"content_version": "2", "novel": document}

        def save_script(self, project_id, episode_id, payload):
            calls.append(payload)
            return {"content_version": "2", "script": script}

        def select_script(self, project_id, episode_id, payload):
            calls.append(payload)
            return view

        def confirm(self, project_id, episode_id, script_id, payload):
            raise Conflict("Stale version")

    async def run():
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_episode_writing_service] = Service
        root = "/api/v1/projects/1/episodes/2"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(root + "/writing")
            assert response.status_code == 200
            assert response.json() == view
            for suffix, data in [
                ("novel", {"content": "x", "content_version": "1"}),
                ("script", {"script_id": None, "content": "x", "content_version": "1"}),
                ("editing-script", {"script_id": "100", "content_version": "1"}),
            ]:
                response = await client.put(root + "/" + suffix, json=data)
                assert response.status_code == 200, response.text
                assert isinstance(response.json()["content_version"], str)
            response = await client.post(
                root + "/scripts/100/confirm", json={"content_version": "1"}
            )
            assert response.status_code == 409
            for data in [
                {"content": "x"},
                {"content": None, "content_version": "1"},
                {"content": "x", "content_version": "1", "state": "confirmed"},
            ]:
                response = await client.put(root + "/novel", json=data)
                assert response.status_code == 422
            assert (await client.get("/api/v1/projects/1/episodes/demo/writing")).status_code == 422
            assert len(calls) == 4

    asyncio.run(run())
