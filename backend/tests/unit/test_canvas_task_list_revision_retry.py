"""真实 Actor/ORM 的 SQLite 确定性竞争；不代表 MySQL 并发、存储或模型验收。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.db.session import session_factory
from short_drama.domain import CanvasResult, Project, ProjectAsset, ProjectCanvas
from short_drama.domain.canvas import CanvasNode
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_publication_service import (
    CanvasCharacterPublicationService,
)
from short_drama.service.canvas_document import character_node
from short_drama.service.canvas_generation_service import CanvasGenerationService
from short_drama.service.canvas_service import CanvasService
from tests.catalog_fixtures import ASSET, CANVAS, KEY, OWNING, SOURCE, actor
from tests.unit.test_canvas_character_publications import (
    REVERSE_OWNING,
    document,
    reverse_owning,
    seed_task,
)
from tests.unit.test_canvas_character_publications import sqlite_mutex_only as sqlite_mutex_only

pytest_plugins = ("tests.catalog_fixtures",)


def plain_document(*, key=KEY, owning=OWNING, source=SOURCE):
    return {
        "id": key,
        "workspaceProjectId": str(owning),
        "projectId": str(source),
        "title": "合法自动保存",
        "nodes": [
            {
                "id": "character-one",
                "type": "text",
                "title": "普通文字",
                "position": {"x": 0, "y": 0},
                "width": 320,
                "height": 220,
                "metadata": {"content": "普通作品"},
            }
        ],
        "connections": [],
    }


def lawful_save(baseline, source, *, owning=OWNING, canvas_id=CANVAS, key):
    with session_factory(baseline.get_bind())() as writer:
        writer.info["actor"] = actor()
        with writer.begin():
            expected = str(writer.get(ProjectCanvas, canvas_id).row_version)
        return CanvasService(writer).commit(
            owning,
            canvas_id,
            {"expected_row_version": expected, "source_document": source},
            key,
        )


def observe_list(session, monkeypatch, compete):
    """只设置竞争检查点；实际锁校验、任务投影和提交均运行原实现。"""
    journal = {"attempts": [], "events": [], "owned": [], "projected": []}
    original_batch = CanvasCharacterPublicationService.lock_existing_canvases
    original_canvas = CanvasDAO.canvas
    original_owned = CanvasGenerationService._owned
    original_project = CanvasGenerationService._project

    def batch(service, originals):
        if service.session is session:
            journal["attempts"].append(
                {
                    "transaction": session.get_transaction(),
                    "preflight": [
                        (row.id, row.project_id, row.source_project_id, row.row_version)
                        for row in originals
                    ],
                    "locks": [],
                }
            )
        return original_batch(service, originals)

    def canvas(dao, project_id, canvas_id, *, lock=False, refresh=False):
        if dao.session is session and lock and journal["attempts"]:
            compete(journal, canvas_id)
        return original_canvas(dao, project_id, canvas_id, lock=lock, refresh=refresh)

    def owned(service, identifier):
        if service.session is session:
            journal["owned"].append((len(journal["attempts"]), identifier))
        return original_owned(service, identifier)

    def projected(service, canvas, task, binding):
        if service.session is session:
            journal["projected"].append((len(journal["attempts"]), task.id))
        return original_project(service, canvas, task, binding)

    def inspect_lock(state):
        statement = state.statement
        if not journal["attempts"] or getattr(statement, "_for_update_arg", None) is None:
            return
        if any(
            row.get("entity") is Project for row in getattr(statement, "column_descriptions", [])
        ):
            journal["attempts"][-1]["locks"].extend(statement.compile().params.values())

    monkeypatch.setattr(CanvasCharacterPublicationService, "lock_existing_canvases", batch)
    monkeypatch.setattr(CanvasDAO, "canvas", canvas)
    monkeypatch.setattr(CanvasGenerationService, "_owned", owned)
    monkeypatch.setattr(CanvasGenerationService, "_project", projected)
    event.listen(session, "do_orm_execute", inspect_lock)
    event.listen(session, "after_begin", lambda *_: journal["events"].append("begin"))
    event.listen(session, "after_rollback", lambda *_: journal["events"].append("rollback"))
    event.listen(session, "after_commit", lambda *_: journal["events"].append("commit"))
    return journal


@pytest.mark.parametrize("has_task", [False, True], ids=["empty", "materialized"])
def test_list_rebuilds_preflight_after_one_lawful_save(catalog_session, monkeypatch, has_task):
    session, baseline = catalog_session
    source = plain_document()
    if has_task:
        seed_task(baseline, owning=OWNING)
    changed = False

    def compete(journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            lawful_save(baseline, source, key="task-list-lawful-race")

    journal = observe_list(session, monkeypatch, compete)
    result = CanvasGenerationService(session, SimpleNamespace()).list(project_id=KEY)
    assert [row["id"] for row in result] == (["100"] if has_task else [])
    assert len(journal["attempts"]) == 2
    assert journal["attempts"][0]["preflight"][0][-1] == 7
    assert journal["attempts"][1]["preflight"][0][-1] == 8
    assert journal["attempts"][0]["transaction"] is not journal["attempts"][1]["transaction"]
    assert journal["events"] == ["begin", "rollback", "begin", "commit"]
    assert journal["owned"] == ([(2, 100)] if has_task else [])
    assert journal["projected"] == ([(2, 100)] if has_task else [])
    assert not session.in_transaction()
    with baseline.begin():
        assert baseline.get(ProjectCanvas, CANVAS).row_version == 8
        assert baseline.scalar(select(func.count()).select_from(CanvasResult)) == int(has_task)


def test_list_stops_after_two_lawful_save_competitions(catalog_session, monkeypatch):
    session, baseline = catalog_session
    source = plain_document()
    changed_attempts = set()

    def compete(journal, canvas_id):
        number = len(journal["attempts"])
        if canvas_id == CANVAS and number not in changed_attempts:
            changed_attempts.add(number)
            changed = deepcopy(source)
            changed["title"] = f"合法保存{number}"
            lawful_save(baseline, changed, key=f"task-list-continuous-{number}")

    journal = observe_list(session, monkeypatch, compete)
    with pytest.raises(WorkflowError) as caught:
        CanvasGenerationService(session, SimpleNamespace()).list(project_id=KEY)
    assert caught.value.code == "canvas_binding_changed" and caught.value.status_code == 409
    assert len(journal["attempts"]) == 2
    assert journal["events"] == ["begin", "rollback", "begin", "rollback"]
    assert journal["owned"] == journal["projected"] == []
    assert not session.in_transaction()
    with baseline.begin():
        assert baseline.get(ProjectCanvas, CANVAS).row_version == 9
        assert baseline.scalar(select(func.count()).select_from(CanvasResult)) == 0


def test_list_does_not_retry_changed_trusted_source(catalog_session, monkeypatch):
    session, baseline = catalog_session
    changed = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            # 系统夹具模拟来源改变；不存在可改绑的 HTTP 入口，不能伪造合法来源写入。
            with baseline.begin():
                row = baseline.get(ProjectCanvas, CANVAS)
                row.source_project_id = None
                row.row_version += 1

    journal = observe_list(session, monkeypatch, compete)
    with pytest.raises(WorkflowError) as caught:
        CanvasGenerationService(session, SimpleNamespace()).list(project_id=KEY)
    assert caught.value.code == "canvas_binding_changed"
    assert len(journal["attempts"]) == 1
    assert journal["events"] == ["begin", "rollback"]
    assert journal["owned"] == journal["projected"] == []
    assert not session.in_transaction()


@pytest.mark.parametrize("revoked_project", [OWNING, SOURCE], ids=["owning", "source"])
def test_list_rechecks_permission_after_failed_transaction(
    catalog_session, monkeypatch, revoked_project
):
    session, baseline = catalog_session
    role = document(session, cover=None).model_dump(mode="json", by_alias=True)
    lawful_save(baseline, role, key="task-list-role-before-revoke")
    session.info["actor"] = actor(2)
    changed = False
    revoked = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            edited = deepcopy(role)
            edited["title"] = "撤权之前的合法保存"
            lawful_save(baseline, edited, key="task-list-before-revoke-race")

    journal = observe_list(session, monkeypatch, compete)

    def revoke_after_rollback(_session, transaction):
        nonlocal revoked
        if (
            not revoked
            and transaction.parent is None
            and journal["events"] == ["begin", "rollback"]
        ):
            assert not session.in_transaction()
            revoked = True
            with baseline.begin():
                row = baseline.scalar(
                    select(ProjectMember).where(ProjectMember.project_id == revoked_project)
                )
                row.status = "removed"

    event.listen(session, "after_transaction_end", revoke_after_rollback)
    with pytest.raises(NotFound):
        CanvasGenerationService(session, SimpleNamespace()).list(project_id=KEY)
    assert revoked
    assert journal["events"] == ["begin", "rollback", "begin", "rollback"]
    assert journal["owned"] == journal["projected"] == []
    assert not session.in_transaction()


def test_list_replans_new_character_and_all_project_locks_before_materialization(
    catalog_session, monkeypatch
):
    session, baseline = catalog_session
    reverse_owning(baseline)
    first_role = document(session, cover=None, owning=REVERSE_OWNING)
    lawful_save(baseline, first_role, owning=REVERSE_OWNING, key="task-list-first-role")
    seed_task(baseline)
    low_source, second_owning, second_canvas = SOURCE - 100, REVERSE_OWNING + 100, CANVAS + 100
    second_key = "task-list-second-canvas"
    now = utcnow()
    with baseline.begin():
        for identifier, mode in [(low_source, "standard"), (second_owning, "infinite_canvas")]:
            baseline.add(
                Project(
                    id=identifier,
                    owner_user_id=1,
                    name=mode,
                    aspect="16:9",
                    synopsis="",
                    style="",
                    workspace_mode=mode,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        baseline.flush()
        baseline.add(
            ProjectCanvas(
                id=second_canvas,
                project_id=second_owning,
                source_project_id=low_source,
                source_key=second_key,
                title="第二张画布",
                position=1,
                schema_version=1,
                row_version=7,
                properties_json={},
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(
            ProjectAsset(id=ASSET + 100, project_id=low_source, asset_id=ASSET, position=1)
        )
    lawful_save(
        baseline,
        plain_document(key=second_key, owning=second_owning, source=low_source),
        owning=second_owning,
        canvas_id=second_canvas,
        key="task-list-second-plain",
    )
    with baseline.begin():
        assert all(
            not character_node(row.content_json)
            for row in baseline.scalars(
                select(CanvasNode).where(CanvasNode.canvas_id == second_canvas)
            )
        )
    second_role = document(
        session, cover=None, canvas_key=second_key, owning=second_owning, source_id=low_source
    )
    seed_task(baseline, task_id=200, canvas_id=second_canvas, owning=second_owning)
    changed = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            lawful_save(
                baseline,
                second_role,
                owning=second_owning,
                canvas_id=second_canvas,
                key="task-list-second-new-role",
            )

    journal = observe_list(session, monkeypatch, compete)
    result = CanvasGenerationService(session, SimpleNamespace()).list()
    assert {row["id"] for row in result} == {"100", "200"}
    assert len(journal["attempts"]) == 2
    scopes = {low_source, SOURCE, REVERSE_OWNING, second_owning}
    first_locks = [value for value in journal["attempts"][0]["locks"] if value in scopes]
    second_locks = [value for value in journal["attempts"][1]["locks"] if value in scopes]
    assert list(dict.fromkeys(first_locks)) == [SOURCE, REVERSE_OWNING, second_owning]
    assert list(dict.fromkeys(second_locks)) == [low_source, SOURCE, REVERSE_OWNING, second_owning]
    assert journal["events"] == ["begin", "rollback", "begin", "commit"]
    assert journal["owned"] == [(2, 100), (2, 200)]
    assert journal["projected"] == [(2, 100), (2, 200)]
    with baseline.begin():
        assert baseline.scalar(select(func.count()).select_from(CanvasResult)) == 2
