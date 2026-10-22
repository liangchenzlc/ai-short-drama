"""Writing consistency on the disposable MySQL fixture, without provider calls."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import Conflict
from short_drama.domain import ProjectNovel, ProjectScript
from short_drama.service.project_script_service import ProjectScriptService
from short_drama.service.project_service import ProjectService
from short_drama.service.project_writing_service import ProjectWritingService

pytestmark = pytest.mark.integration


def seed(session):
    project = ProjectService(session).create_project(
        {
            "name": "Writing",
        }
    )
    episode = ProjectService(session).get(project.id)
    return project.id, episode.id


@pytest.mark.parametrize("kind", ["novel", "script"])
def test_concurrent_initial_save_has_one_winner(mysql_engine, db_session, kind):
    project_id, project_id = seed(db_session)
    service = ProjectWritingService(db_session)
    assert service.get(project_id) == {
        "project_id": str(project_id),
        "content_version": "1",
        "novel": None,
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
    assert state["novel" if kind == "novel" else "editing_script"] == winner[kind]
    with Session(mysql_engine) as session:
        model = ProjectNovel if kind == "novel" else ProjectScript
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
    assert confirmed["content_version"] == "4"
    assert confirmed["confirmed_script_id"] == script_id
    assert confirmed["editing_script"]["state"] == "confirmed"
    assert service.confirm(project_id, script_id, {"content_version": "4"}) == confirmed
    unchanged = service.save_script(
        project_id,
        {"content_version": "4", "script_id": script_id, "content": "  Scene one\n"},
    )
    assert unchanged == {"content_version": "5", "script": confirmed["editing_script"]}
    with pytest.raises(Conflict):
        service.save_script(
            project_id,
            {"content_version": "4", "script_id": script_id, "content": "delayed edit"},
        )
    assert service.select_script(project_id, {"content_version": "5", "script_id": script_id}) == {
        **confirmed,
        "content_version": "5",
    }
    novel_edit = service.save_novel(
        project_id, {"content_version": "5", "content": "Novel revised"}
    )
    assert novel_edit["content_version"] == "6"
    assert service.get(project_id)["editing_script"] == confirmed["editing_script"]
    assert service.save_novel(project_id, {"content_version": "6", "content": "Novel revised"}) == {
        **novel_edit,
        "content_version": "7",
    }
    edited = service.save_script(
        project_id,
        {"content_version": "7", "script_id": script_id, "content": "scene changed  "},
    )
    assert edited["content_version"] == "8"
    assert edited["script"]["id"] == script_id
    assert edited["script"]["state"] == "unconfirmed"
    assert edited["script"]["content"] == "scene changed  "
    assert service.get(project_id)["confirmed_script_id"] is None
    with pytest.raises(Conflict):
        service.confirm(project_id, script_id, {"content_version": "5"})
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
