"""Writing consistency on the disposable MySQL fixture, without provider calls."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import ProjectScript
from short_drama.service.creation.writing.script.documents import ProjectWritingService
from short_drama.service.projects.management.project import ProjectService
from tests.support.integration.identity import actor as fixture_actor

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


def test_plain_script_migration_preserves_text_and_revisions(migration_mysql_engine):
    with Session(
        migration_mysql_engine, expire_on_commit=False, info={"actor": fixture_actor()}
    ) as session:
        project_id = seed(session)
        writing = ProjectWritingService(session)
        first = writing.save_script(
            project_id, {"script_id": None, "content": "  原稿\n\n", "content_version": "1"}
        )
        final = writing.save_script(
            project_id,
            {
                "script_id": first["script"]["id"],
                "content": "新稿\n",
                "content_version": "2",
                "change_source": "import",
            },
        )
        revision_id = writing.script_revisions(project_id)["items"][0]["id"]
        before = writing.script_revisions(project_id, revision_id=revision_id)

    path = (
        Path(__file__).resolve().parents[5]
        / "docs/database/migrations/2026-10-28-plain-script/001-plain-script.sql"
    )
    with migration_mysql_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE project_scripts ADD COLUMN editor_document JSON NULL, "
            "ADD CONSTRAINT ck_scripts_editor_document CHECK "
            "(editor_document IS NULL OR JSON_TYPE(editor_document) = 'OBJECT')"
        )
        connection.exec_driver_sql(
            "ALTER TABLE project_script_revisions ADD COLUMN before_document JSON NULL, "
            "ADD COLUMN after_document JSON NULL"
        )
        connection.execute(
            text("UPDATE project_scripts SET editor_document = JSON_OBJECT('ops', JSON_ARRAY())")
        )
        connection.execute(
            text(
                "UPDATE project_script_revisions SET before_document = JSON_OBJECT(), "
                "after_document = JSON_OBJECT()"
            )
        )
        for statement in path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                connection.exec_driver_sql(statement)

        inspector = inspect(connection)
        script_columns = {c["name"] for c in inspector.get_columns("project_scripts")}
        assert "editor_document" not in script_columns
        assert not {"before_document", "after_document"} & {
            c["name"] for c in inspector.get_columns("project_script_revisions")
        }
        assert "ck_scripts_editor_document" not in {
            c["name"] for c in inspector.get_check_constraints("project_scripts")
        }

    with Session(migration_mysql_engine, info={"actor": fixture_actor()}) as session:
        writing = ProjectWritingService(session)
        current = writing.get(project_id)
        assert current["editing_script"] == final["script"]
        assert current["content_version"] == final["content_version"]
        assert writing.script_revisions(project_id, revision_id=before["id"]) == before


def seed(session):
    project = ProjectService(session).create_project(
        {
            "name": "Writing",
        }
    )
    return project.id


def test_story_settings_concurrent_first_save_and_script_version(mysql_engine, db_session):
    project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    empty = service.story_settings(project_id)
    barrier = Barrier(2)

    def save(index):
        with Session(
            mysql_engine, expire_on_commit=False, autoflush=False, info={"actor": fixture_actor()}
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


@pytest.mark.parametrize("kind", ["script"])
def test_concurrent_initial_save_has_one_winner(mysql_engine, db_session, kind):
    project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    assert service.get(project_id) == {
        "project_id": str(project_id),
        "content_version": "1",
        "active_writing_tasks": [],
        "editing_script": None,
        "confirmed_script_id": None,
    }
    with Session(mysql_engine) as session:
        for model in (ProjectScript,):
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.project_id == project_id)
                )
                == 0
            )
    barrier = Barrier(2)

    def save(index):
        with Session(
            mysql_engine, expire_on_commit=False, autoflush=False, info={"actor": fixture_actor()}
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
        model = ProjectScript
        assert (
            session.scalar(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            )
            == 1
        )


def test_save_edit_and_noop_preserve_content_and_fence_delayed_requests(mysql_engine, db_session):
    project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    script = service.save_script(
        project_id,
        {"content_version": "1", "script_id": None, "content": "  Scene one\n"},
    )
    script_id = script["script"]["id"]
    assert script["content_version"] == "2"
    confirmed = service.get(project_id)
    assert confirmed["content_version"] == "2"
    assert confirmed["confirmed_script_id"] == script_id
    assert confirmed["editing_script"]["state"] == "confirmed"
    unchanged = service.save_script(
        project_id,
        {"content_version": "2", "script_id": script_id, "content": "  Scene one\n"},
    )
    assert unchanged == {
        "content_version": "3",
        "script": confirmed["editing_script"],
        "confirmed_script_id": script_id,
    }
    with pytest.raises(Conflict):
        service.save_script(
            project_id,
            {"content_version": "2", "script_id": script_id, "content": "delayed edit"},
        )
    edited = service.save_script(
        project_id,
        {"content_version": "3", "script_id": script_id, "content": "scene changed  "},
    )
    assert edited["content_version"] == "4"
    assert edited["script"]["id"] == script_id
    assert edited["script"]["state"] == "confirmed"
    assert edited["script"]["content"] == "scene changed  "
    assert service.get(project_id)["confirmed_script_id"] == script_id
    # A new DB session observes the committed edit, rather than an identity-map value.
    with Session(mysql_engine) as session:
        persisted = ProjectWritingService(session).get(project_id)
    assert persisted["editing_script"] == edited["script"]


def test_two_writing_admissions_share_a_current_read_after_project_mutex(mysql_engine, db_session):
    from short_drama.service.generations.admission.generation import AIGenerationService
    from tests.support.generation_fixtures import config, settings

    config(db_session, "text")
    project_id = seed(db_session)
    saved = ProjectWritingService(db_session).save_script(
        project_id, {"script_id": None, "content": "原正文", "content_version": "1"}
    )
    script_id = saved["script"]["id"]
    barrier = Barrier(2)

    def admit(index):
        with Session(
            mysql_engine, expire_on_commit=False, info={"actor": fixture_actor()}
        ) as session:
            barrier.wait(timeout=10)
            try:
                receipt, _ = AIGenerationService(session, settings).create(
                    "text",
                    {
                        "instructions": "保留原意，改为悬疑语气",
                        "source": {
                            "scene": "script_rewrite",
                            "project_id": str(project_id),
                            "content_version": "2",
                            "script_id": script_id,
                            "start": 0,
                            "end": 3,
                            "selected_text": "原正文",
                        },
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
    assert state["editing_script"]["content"] == "原正文"


def test_save_racing_admission_cannot_be_lost(mysql_engine, db_session):
    from short_drama.service.generations.admission.generation import AIGenerationService
    from tests.support.generation_fixtures import config, settings

    config(db_session, "text")
    project_id = seed(db_session)
    saved = ProjectWritingService(db_session).save_script(
        project_id, {"script_id": None, "content": "原正文", "content_version": "1"}
    )
    script_id = saved["script"]["id"]
    barrier = Barrier(2)

    def operation(index):
        with Session(
            mysql_engine, expire_on_commit=False, info={"actor": fixture_actor()}
        ) as session:
            barrier.wait(timeout=10)
            try:
                if index:
                    return ProjectWritingService(session).save_script(
                        project_id,
                        {
                            "script_id": script_id,
                            "content": "并发手写正文",
                            "content_version": "2",
                        },
                    )
                return AIGenerationService(session, settings).create(
                    "text",
                    {
                        "instructions": "保留原意，改为悬疑语气",
                        "source": {
                            "scene": "script_rewrite",
                            "project_id": str(project_id),
                            "content_version": "2",
                            "script_id": script_id,
                            "start": 0,
                            "end": 3,
                            "selected_text": "原正文",
                        },
                    },
                    "writing-admission-race",
                )[0]
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(operation, range(2)))
    state = ProjectWritingService(db_session).get(project_id)
    if state["editing_script"]["content"] == "并发手写正文":
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
    project_id = seed(db_session)
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
