import pytest
from sqlalchemy import func, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import ProjectNovel, ProjectScript
from short_drama.service.project_service import ProjectService
from short_drama.service.project_writing_service import ProjectWritingService
from tests.support.generation_fixtures import generation_session


def test_import_retains_durable_before_after_and_restore_fences_later_edits():
    with generation_session() as session:
        project_id, service = setup(session)
        original = service.save_script(
            project_id,
            {"content_version": "1", "script_id": None, "content": "整篇导入前的完整原稿"},
        )
        imported = service.save_script(
            project_id,
            {
                "content_version": original["content_version"],
                "script_id": original["script"]["id"],
                "content": "新导入正文",
                "change_source": "import",
            },
        )
        history = service.script_revisions(project_id)["items"]
        assert len(history) == 1 and history[0]["source"] == "import"
        assert history[0]["created_at"].endswith("Z")
        # A fresh service reads persistent snapshots, independently of editor undo.
        reloaded = ProjectWritingService(session)
        detail = reloaded.script_revisions(project_id, revision_id=history[0]["id"])
        assert (detail["before_content"], detail["after_content"]) == (
            "整篇导入前的完整原稿",
            "新导入正文",
        )
        with pytest.raises(Conflict):
            reloaded.restore_script_revision(
                project_id, history[0]["id"], {"content_version": original["content_version"]}
            )
        restored = reloaded.restore_script_revision(
            project_id, history[0]["id"], {"content_version": imported["content_version"]}
        )
        assert restored["editing_script"]["content"] == "整篇导入前的完整原稿"
        assert [item["source"] for item in reloaded.script_revisions(project_id)["items"]] == [
            "restore",
            "import",
        ]


def setup(session):
    project = ProjectService(session).create_project(
        {
            "name": "writing",
        }
    )
    episode = ProjectService(session).get(project.id)
    return episode.id, ProjectWritingService(session)


def test_story_settings_are_lazy_versioned_and_independent_from_script():
    from short_drama.domain import ProjectStorySettings

    with generation_session() as session:
        project_id, service = setup(session)
        empty = service.story_settings(project_id)
        assert empty == {
            "row_version": "0",
            "logline": "",
            "synopsis": "",
            "genre": "",
            "character_bios": "",
        }
        with session.begin():
            assert session.scalar(select(func.count()).select_from(ProjectStorySettings)) == 0
        first = service.save_story_settings(
            project_id, {**empty, "logline": "卖点😀", "synopsis": "  保留空白\n"}
        )
        assert first["row_version"] == "1"
        assert first["synopsis"] == "  保留空白\n"
        with pytest.raises(WorkflowError) as conflict:
            service.save_story_settings(project_id, {**empty, "genre": "过期"})
        assert conflict.value.code == "story_settings_version_conflict"
        assert service.story_settings(project_id) == first
        second = service.save_story_settings(project_id, {**first, "character_bios": "人物小传"})
        assert second["row_version"] == "2"
        assert service.get(project_id)["content_version"] == "1"
        script = service.save_script(
            project_id, {"content_version": "1", "script_id": None, "content": "正文"}
        )
        assert script["content_version"] == "2"
        assert service.story_settings(project_id) == second


def test_story_settings_contract_rejects_unknown_fields_and_oversized_values():
    from pydantic import ValidationError

    from short_drama.schemas.project_writing import StorySettingsRead, StorySettingsSave

    empty = StorySettingsRead().model_dump(mode="json")
    for patch in (
        {"row_version": "-1"},
        {"logline": "字" * 201},
        {"genre": "字" * 101},
        {"synopsis": "字" * 20001},
        {"character_bios": "字" * 20001},
        {"content_version": "1"},
    ):
        with pytest.raises(ValidationError):
            StorySettingsSave.model_validate({**empty, **patch})


def test_empty_read_has_no_side_effects_and_novel_preserves_whitespace():
    with generation_session() as session:
        e, service = setup(session)
        result = service.get(e)
        assert result == {
            "project_id": str(e),
            "content_version": "1",
            "novel": None,
            "outline": None,
            "active_writing_tasks": [],
            "editing_script": None,
            "confirmed_script_id": None,
        }
        with session.begin():
            for model in (ProjectNovel, ProjectScript):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        saved = service.save_novel(e, {"content": "  text\n\n", "content_version": "1"})
        assert saved["content_version"] == "2"
        assert saved["novel"]["content"] == "  text\n\n"
        assert saved["novel"]["updated_at"].endswith("Z")
        assert service.get(e)["novel"] == saved["novel"]
        same = service.save_novel(e, {"content": "  text\n\n", "content_version": "2"})
        assert same == {**saved, "content_version": "3"}
        with pytest.raises(Conflict):
            service.save_novel(e, {"content": "stale", "content_version": "1"})
        assert service.get(e)["novel"]["content"] == "  text\n\n"


@pytest.mark.parametrize("field", ["novel", "script"])
def test_unchanged_save_fences_a_delayed_write_without_changing_content_or_confirmation(field):
    with generation_session() as session:
        episode, service = setup(session)
        if field == "novel":
            service.save_novel(episode, {"content": "base", "content_version": "1"})
        else:
            saved = service.save_script(
                episode,
                {"script_id": None, "content": "base", "content_version": "1"},
            )
            service.confirm(episode, saved["script"]["id"], {"content_version": "2"})
        before = service.get(episode)
        payload = {"content": "base", "content_version": before["content_version"]}
        if field == "script":
            payload["script_id"] = before["editing_script"]["id"]
        save = getattr(service, f"save_{field}")
        result = save(episode, payload)
        assert result["content_version"] == str(int(before["content_version"]) + 1)
        with pytest.raises(Conflict):
            save(episode, {**payload, "content": "delayed obsolete edit"})
        current = service.get(episode)
        assert current["novel"] == before["novel"]
        assert current["editing_script"] == before["editing_script"]
        assert current["confirmed_script_id"] == before["confirmed_script_id"]


def test_script_save_automatically_confirms_and_empty_save_clears_availability():
    with generation_session() as session:
        e, service = setup(session)
        saved = service.save_script(
            e, {"script_id": None, "content": "script", "content_version": "1"}
        )
        identifier = saved["script"]["id"]
        assert saved["content_version"] == "2"
        assert saved["script"]["state"] == "confirmed"
        assert saved["confirmed_script_id"] == identifier
        with pytest.raises(Conflict):
            service.save_script(
                e, {"script_id": None, "content": "duplicate", "content_version": "2"}
            )
        confirmed = service.confirm(e, identifier, {"content_version": "2"})
        assert confirmed["content_version"] == "2"
        edited = service.save_script(
            e, {"script_id": identifier, "content": "revised", "content_version": "2"}
        )
        assert edited["script"]["state"] == "confirmed"
        assert service.get(e)["confirmed_script_id"] == identifier
        cleared = service.save_script(
            e, {"script_id": identifier, "content": " \n", "content_version": "3"}
        )
        assert cleared["content_version"] == "4"
        assert cleared["confirmed_script_id"] is None
        with pytest.raises(Exception) as exc:
            service.confirm(e, identifier, {"content_version": "4"})
        assert exc.value.status_code == 422
        assert service.get(e)["content_version"] == "4"


def test_selection_preserves_candidates_and_confirmation_is_explicit():
    from short_drama.service.project_script_service import ProjectScriptService

    with generation_session() as session:
        e, service = setup(session)
        first = service.save_script(
            e, {"script_id": None, "content": "one", "content_version": "1"}
        )
        first_id = first["script"]["id"]
        service.confirm(e, first_id, {"content_version": "2"})
        second = ProjectScriptService(session).create(
            {"project_id": e, "position": 2, "content": "two"}
        )
        previous = service.get(e)
        assert int(previous["content_version"]) == 3
        selected = service.select_script(
            e, {"script_id": str(second.id), "content_version": previous["content_version"]}
        )
        assert selected["editing_script"]["content"] == "two"
        assert selected["confirmed_script_id"] == first_id
        with pytest.raises(Conflict):
            service.confirm(e, first_id, {"content_version": selected["content_version"]})
        result = service.confirm(e, second.id, {"content_version": selected["content_version"]})
        assert result["confirmed_script_id"] == str(second.id)
        assert ProjectScriptService(session).get(first_id).state == "unconfirmed"


def test_agent_script_selection_requires_adoption_and_preserves_editor():
    from short_drama.domain import AgentArtifact
    from short_drama.service.base import utcnow
    from short_drama.service.project_script_service import ProjectScriptService

    with generation_session() as session:
        e, service = setup(session)
        script = ProjectScriptService(session).create(
            {"project_id": e, "position": 1, "content": "Private agent draft"}
        )
        with session.begin():
            session.add(
                AgentArtifact(
                    id=101,
                    project_id=e,
                    tool_call_id=102,
                    result_index=1,
                    kind="script_candidate",
                    script_id=script.id,
                    status="ready",
                    created_by=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        before = service.get(e)
        assert service.candidates(e)["items"] == []
        with pytest.raises(WorkflowError) as blocked:
            service.select_script(
                e, {"script_id": str(script.id), "content_version": before["content_version"]}
            )
        assert blocked.value.code == "agent_artifact_adoption_required"
        assert service.get(e) == before
        with session.begin():
            artifact = session.get(AgentArtifact, 101)
            artifact.status, artifact.applied_by = "applied", 1
            artifact.applied_at, artifact.apply_receipt = utcnow(), {"action": "select_script"}
        assert service.candidates(e)["items"][0]["id"] == str(script.id)
        assert service.select_script(
            e, {"script_id": str(script.id), "content_version": before["content_version"]}
        )["editing_script"]["id"] == str(script.id)


def test_prompt_only_schema_without_agent_table_keeps_script_workflow():
    from short_drama.domain import AgentArtifact

    with generation_session() as session:
        e, _ = setup(session)
        AgentArtifact.__table__.drop(session.get_bind())
        service = ProjectWritingService(session)
        saved = service.save_script(
            e, {"script_id": None, "content": "Regular draft", "content_version": "1"}
        )
        assert service.candidates(e)["items"][0]["id"] == saved["script"]["id"]
        assert (
            service.select_script(
                e,
                {"script_id": saved["script"]["id"], "content_version": saved["content_version"]},
            )["editing_script"]["content"]
            == "Regular draft"
        )


def test_format_only_script_save_keeps_legacy_confirmation_and_increments_version():
    with generation_session() as session:
        e, service = setup(session)
        saved = service.save_script(
            e, {"script_id": None, "content": "正文", "content_version": "1"}
        )
        sid = saved["script"]["id"]
        service.confirm(e, sid, {"content_version": "2"})
        formatted = service.save_script(
            e,
            {
                "script_id": sid,
                "content": "正文",
                "content_version": "2",
                "editor_document": {
                    "ops": [{"insert": "正文", "attributes": {"bold": True}}, {"insert": "\n"}]
                },
            },
        )
        assert formatted["content_version"] == "3"
        assert formatted["script"]["state"] == "confirmed"
        assert service.get(e)["confirmed_script_id"] == sid
        with pytest.raises(Conflict):
            service.save_script(
                e, {"script_id": sid, "content": "旧窗口写入", "content_version": "2"}
            )


@pytest.mark.parametrize("operation", ["save", "confirm"])
def test_legacy_editing_pointer_cannot_publish_an_unadopted_agent_script(operation):
    from short_drama.domain import AgentArtifact, Project
    from short_drama.service.base import utcnow
    from short_drama.service.project_script_service import ProjectScriptService

    with generation_session() as session:
        e, service = setup(session)
        script = ProjectScriptService(session).create(
            {"project_id": e, "position": 1, "content": "Unadopted original"}
        )
        with session.begin():
            session.get(Project, e).editing_script_id = script.id
            session.add(
                AgentArtifact(
                    id=101,
                    project_id=e,
                    tool_call_id=102,
                    result_index=1,
                    kind="script_candidate",
                    script_id=script.id,
                    status="ready",
                    created_by=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        before = service.get(e)
        with pytest.raises(WorkflowError) as blocked:
            if operation == "save":
                service.save_script(
                    e,
                    {
                        "script_id": str(script.id),
                        "content": "Changed",
                        "content_version": before["content_version"],
                    },
                )
            else:
                service.confirm(e, str(script.id), {"content_version": before["content_version"]})
        assert blocked.value.code == "agent_artifact_adoption_required"
        assert service.get(e) == before


def test_scopes_and_input_validation():
    from pydantic import ValidationError

    with generation_session() as session:
        e, service = setup(session)
        other_e, _ = setup(session)
        with pytest.raises(NotFound):
            service.get(123)
        remote = service.save_script(
            other_e, {"script_id": None, "content": "other", "content_version": "1"}
        )
        with pytest.raises(NotFound):
            service.select_script(e, {"script_id": remote["script"]["id"], "content_version": "1"})
        for payload in [
            {"content": None, "content_version": "1"},
            {"content": "x" * (1024**2 + 1), "content_version": "1"},
            {"content": "汉" * 349526, "content_version": "1"},
            {"content": "x", "content_version": "0"},
            {"content": "x", "content_version": "1", "project_id": str(other_e)},
        ]:
            with pytest.raises(ValidationError):
                service.save_novel(e, payload)
        assert service.get(e)["content_version"] == "1"


def test_story_settings_respect_project_ownership_and_archive_scope():
    from short_drama.core.identity import ActorContext

    owner = ActorContext(1, "owner", "owner@example.test", True, 1, "", "settings-test")
    with generation_session() as session:
        session.info["actor"] = owner
        project_id, service = setup(session)
        other_id, _ = setup(session)
        empty = service.story_settings(project_id)
        saved = service.save_story_settings(project_id, {**empty, "logline": "owner's story"})
        assert service.story_settings(other_id) == empty
        session.info["actor"] = ActorContext(
            2, "other", "other@example.test", True, 2, "", "settings-test"
        )
        with pytest.raises(NotFound):
            service.story_settings(project_id)
        with pytest.raises(NotFound):
            service.save_story_settings(project_id, {**saved, "logline": "unauthorized"})
        session.info["actor"] = owner
        assert service.story_settings(project_id) == saved
        ProjectService(session).delete_project(project_id, "1")
        with pytest.raises(NotFound):
            service.story_settings(project_id)
        with pytest.raises(NotFound):
            service.save_story_settings(project_id, saved)


def test_http_story_settings_serialization_validation_and_conflict():
    import asyncio

    import httpx

    from short_drama.api.dependencies import get_project_writing_service
    from short_drama.core.config import Settings
    from short_drama.main import create_app
    from short_drama.schemas.project_writing import StorySettingsRead

    calls = []

    class Service:
        def story_settings(self, project_id):
            assert project_id == 2
            return StorySettingsRead().model_dump(mode="json")

        def save_story_settings(self, project_id, payload):
            calls.append((project_id, payload.model_dump()))
            if payload.row_version != 0:
                raise WorkflowError("story_settings_version_conflict", "版本已变化", 409)
            return {**payload.model_dump(), "row_version": 1}

    async def run():
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_project_writing_service] = Service
        url = "/api/v1/projects/2/story-settings"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(url)
            assert response.status_code == 200
            empty = response.json()
            assert empty["row_version"] == "0"
            saved = await client.put(url, json={**empty, "synopsis": "  梗概\n"})
            assert saved.status_code == 200
            assert saved.json()["row_version"] == "1"
            assert saved.json()["synopsis"] == "  梗概\n"
            conflict = await client.put(url, json=saved.json())
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "story_settings_version_conflict"
            for invalid in [{**empty, "content_version": "1"}, {**empty, "logline": "x" * 201}]:
                assert (await client.put(url, json=invalid)).status_code == 422
        assert len(calls) == 2 and calls[0][0] == 2

    asyncio.run(run())


def test_http_writing_contract_and_closed_inputs():
    import asyncio

    import httpx

    from short_drama.api.dependencies import get_project_writing_service
    from short_drama.core.config import Settings
    from short_drama.main import create_app

    document = {"id": "100", "content": "text", "updated_at": "2026-09-21T00:00:00Z"}
    script = {**document, "state": "unconfirmed", "editor_document": None}
    view = {
        "project_id": "2",
        "content_version": "1",
        "novel": document,
        "outline": None,
        "active_writing_tasks": [],
        "editing_script": script,
        "confirmed_script_id": None,
    }
    calls = []

    class Service:
        def get(self, project_id):
            calls.append(project_id)
            return view

        def save_novel(self, project_id, payload):
            calls.append(payload)
            return {"content_version": "2", "novel": document}

        def save_script(self, project_id, payload):
            calls.append(payload)
            return {"content_version": "2", "script": script, "confirmed_script_id": None}

        def select_script(self, project_id, payload):
            calls.append(payload)
            return view

        def confirm(self, project_id, script_id, payload):
            raise Conflict("Stale version")

    async def run():
        app = create_app(Settings(_env_file=None))
        app.dependency_overrides[get_project_writing_service] = Service
        root = "/api/v1/projects/2"
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
            assert (await client.get("/api/v1/projects/demo/writing")).status_code == 422
            assert len(calls) == 4

    asyncio.run(run())
