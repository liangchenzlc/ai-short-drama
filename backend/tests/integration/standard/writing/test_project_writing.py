"""Writing consistency on the disposable MySQL fixture, without provider calls."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import ProjectNovel, ProjectScript, ProjectStoryOutline
from short_drama.service.project_script_service import ProjectScriptService
from short_drama.service.project_service import ProjectService
from short_drama.service.project_writing_service import ProjectWritingService

pytestmark = pytest.mark.integration


def test_script_revision_migration_is_reentrant_and_preserves_documents(migration_mysql_engine):
    from short_drama.domain import ProjectScriptRevision

    path = Path(__file__).resolve().parents[4] / "scripts/script_revision_migration.py"
    spec = importlib.util.spec_from_file_location("script_revision_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with migration_mysql_engine.begin() as connection:
        ProjectScriptRevision.__table__.drop(connection)
    module.migrate(migration_mysql_engine)
    module.migrate(migration_mysql_engine)
    with migration_mysql_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ProjectScriptRevision)) == 0


def seed(session):
    project = ProjectService(session).create_project(
        {
            "name": "Writing",
        }
    )
    episode = ProjectService(session).get(project.id)
    return project.id, episode.id


def test_story_settings_concurrent_first_save_and_script_version(mysql_engine, db_session):
    project_id, _ = seed(db_session)
    service = ProjectWritingService(db_session)
    empty = service.story_settings(project_id)
    barrier = Barrier(2)

    def save(index):
        with Session(
            mysql_engine, expire_on_commit=False, autoflush=False, info={"legacy_user_id": 1}
        ) as session:
            barrier.wait(timeout=10)
            try:
                return ProjectWritingService(session).save_story_settings(
                    project_id, {**empty, "logline": f"writer {index}"}
                )
            except WorkflowError as cause:
                assert cause.code == "story_settings_version_conflict"
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, range(2)))
    assert outcomes.count("conflict") == 1
    winner = next(value for value in outcomes if isinstance(value, dict))
    assert winner["row_version"] == "1"
    assert service.story_settings(project_id) == winner
    assert service.get(project_id)["content_version"] == "1"


@pytest.mark.parametrize("kind", ["novel", "outline", "script"])
def test_concurrent_initial_save_has_one_winner(mysql_engine, db_session, kind):
    project_id, project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    assert service.get(project_id) == {
        "project_id": str(project_id),
        "content_version": "1",
        "novel": None,
        "outline": None,
        "active_writing_tasks": [],
        "editing_script": None,
        "confirmed_script_id": None,
    }
    with Session(mysql_engine) as session:
        for model in (ProjectNovel, ProjectScript):
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.project_id == project_id)
                )
                == 0
            )
    barrier = Barrier(2)

    def save(index):
        with Session(
            mysql_engine, expire_on_commit=False, autoflush=False, info={"legacy_user_id": 1}
        ) as session:
            writing = ProjectWritingService(session)
            payload = {"content_version": "1", "content": f"  writer {index}\n"}
            if kind == "script":
                payload["script_id"] = None
            barrier.wait(timeout=10)
            try:
                return getattr(writing, f"save_{kind}")(project_id, payload)
            except Conflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, range(2)))
    assert outcomes.count("conflict") == 1
    winner = next(result for result in outcomes if isinstance(result, dict))
    assert winner["content_version"] == "2"
    state = service.get(project_id)
    assert state["content_version"] == "2"
    assert state[kind if kind != "script" else "editing_script"] == winner[kind]
    with Session(mysql_engine) as session:
        model = {"novel": ProjectNovel, "outline": ProjectStoryOutline, "script": ProjectScript}[
            kind
        ]
        assert (
            session.scalar(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            )
            == 1
        )


def test_save_confirm_edit_and_noop_preserve_content_and_fence_delayed_requests(
    mysql_engine, db_session
):
    project_id, project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    novel = service.save_novel(
        project_id, {"content_version": "1", "content": "  第一行\r\n第二行  "}
    )
    assert novel["content_version"] == "2"
    assert novel["novel"]["content"] == "  第一行\r\n第二行  "
    script = service.save_script(
        project_id,
        {"content_version": "2", "script_id": None, "content": "  Scene one\n"},
    )
    script_id = script["script"]["id"]
    assert script["content_version"] == "3"
    confirmed = service.confirm(project_id, script_id, {"content_version": "3"})
    assert confirmed["content_version"] == "3"
    assert confirmed["confirmed_script_id"] == script_id
    assert confirmed["editing_script"]["state"] == "confirmed"
    assert service.confirm(project_id, script_id, {"content_version": "3"}) == confirmed
    unchanged = service.save_script(
        project_id,
        {"content_version": "3", "script_id": script_id, "content": "  Scene one\n"},
    )
    assert unchanged == {
        "content_version": "4",
        "script": confirmed["editing_script"],
        "confirmed_script_id": script_id,
    }
    with pytest.raises(Conflict):
        service.save_script(
            project_id,
            {"content_version": "3", "script_id": script_id, "content": "delayed edit"},
        )
    assert service.select_script(project_id, {"content_version": "4", "script_id": script_id}) == {
        **confirmed,
        "content_version": "4",
    }
    novel_edit = service.save_novel(
        project_id, {"content_version": "4", "content": "Novel revised"}
    )
    assert novel_edit["content_version"] == "5"
    assert service.get(project_id)["editing_script"] == confirmed["editing_script"]
    assert service.save_novel(project_id, {"content_version": "5", "content": "Novel revised"}) == {
        **novel_edit,
        "content_version": "6",
    }
    edited = service.save_script(
        project_id,
        {"content_version": "6", "script_id": script_id, "content": "scene changed  "},
    )
    assert edited["content_version"] == "7"
    assert edited["script"]["id"] == script_id
    assert edited["script"]["state"] == "confirmed"
    assert edited["script"]["content"] == "scene changed  "
    assert service.get(project_id)["confirmed_script_id"] == script_id
    with pytest.raises(Conflict):
        service.confirm(project_id, script_id, {"content_version": "4"})
    # A new DB session observes the committed edit, rather than an identity-map value.
    with Session(mysql_engine) as session:
        persisted = ProjectWritingService(session).get(project_id)
    assert persisted["editing_script"] == edited["script"]
    assert persisted["novel"]["content"] == "Novel revised"


def test_select_and_confirm_second_script_releases_previous_confirmation(db_session):
    project_id, project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    first = service.save_script(
        project_id,
        {"content_version": "1", "script_id": None, "content": "First"},
    )
    first_id = first["script"]["id"]
    service.confirm(project_id, first_id, {"content_version": "2"})
    second = ProjectScriptService(db_session).create(
        {"project_id": project_id, "position": 2, "content": "Second"}
    )
    state = service.get(project_id)
    selected = service.select_script(
        project_id,
        {"content_version": state["content_version"], "script_id": str(second.id)},
    )
    assert selected["confirmed_script_id"] == first_id
    confirmed = service.confirm(
        project_id, second.id, {"content_version": selected["content_version"]}
    )
    assert confirmed["confirmed_script_id"] == str(second.id)
    assert ProjectScriptService(db_session).get(first_id).state == "unconfirmed"
    assert ProjectScriptService(db_session).get(second.id).state == "confirmed"


def test_two_writing_admissions_share_a_current_read_after_project_mutex(mysql_engine, db_session):
    from short_drama.service.ai_generation_service import AIGenerationService
    from tests.support.generation_fixtures import config, settings

    config(db_session, "text")
    _, project_id = seed(db_session)
    barrier = Barrier(2)

    def admit(index):
        with Session(mysql_engine, expire_on_commit=False, info={"legacy_user_id": 1}) as session:
            barrier.wait(timeout=10)
            try:
                receipt, _ = AIGenerationService(session, settings).create(
                    "text",
                    {
                        "source": {
                            "scene": "script_generate",
                            "project_id": str(project_id),
                            "content_version": "1",
                            "input_kind": "idea",
                            "idea": "雨夜来信",
                        }
                    },
                    f"writing-race-{index}",
                )
                return receipt
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(admit, range(2)))
    assert outcomes.count("writing_target_busy") == 1
    state = ProjectWritingService(db_session).get(project_id)
    assert len(state["active_writing_tasks"]) == 1
    assert state["editing_script"] is None


def test_save_racing_admission_cannot_be_lost(mysql_engine, db_session):
    from short_drama.service.ai_generation_service import AIGenerationService
    from tests.support.generation_fixtures import config, settings

    config(db_session, "text")
    _, project_id = seed(db_session)
    barrier = Barrier(2)

    def operation(index):
        with Session(mysql_engine, expire_on_commit=False, info={"legacy_user_id": 1}) as session:
            barrier.wait(timeout=10)
            try:
                if index:
                    return ProjectWritingService(session).save_script(
                        project_id,
                        {
                            "script_id": None,
                            "content": "并发手写正文",
                            "content_version": "1",
                        },
                    )
                return AIGenerationService(session, settings).create(
                    "text",
                    {
                        "source": {
                            "scene": "script_generate",
                            "project_id": str(project_id),
                            "content_version": "1",
                            "input_kind": "idea",
                            "idea": "雨夜来信",
                        }
                    },
                    "writing-admission-race",
                )[0]
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(operation, range(2)))
    state = ProjectWritingService(db_session).get(project_id)
    if state["editing_script"]:
        assert state["editing_script"]["content"] == "并发手写正文"
        assert "writing_version_conflict" in outcomes
    else:
        assert "writing_target_busy" in outcomes
        assert len(state["active_writing_tasks"]) == 1


@pytest.mark.parametrize("content", ["legacy current script", "   "])
def test_writing_migration_only_changes_legacy_state_once(mysql_engine, db_session, content):
    path = Path(__file__).resolve().parents[4] / "scripts" / "writing_workspace_migration.py"
    spec = importlib.util.spec_from_file_location("writing_workspace_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    saved = service.save_script(
        project_id, {"script_id": None, "content": content, "content_version": "1"}
    )
    with db_session.begin():
        script = db_session.get(ProjectScript, int(saved["script"]["id"]))
        script.state = "unconfirmed" if content.strip() else "confirmed"
    assert module.migrate(mysql_engine) == 1
    db_session.expire_all()
    current = service.get(project_id)
    assert current["editing_script"]["state"] == ("confirmed" if content.strip() else "unconfirmed")
    assert current["content_version"] == "3"
    assert module.migrate(mysql_engine) == 0
    db_session.expire_all()
    assert service.get(project_id) == current
