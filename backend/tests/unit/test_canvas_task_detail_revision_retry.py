"""真实 Actor/ORM 的 SQLite 竞争检查点；不是 MySQL、浏览器或供应商验收。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_dao import CanvasDAO
from short_drama.domain import AsyncTask, CanvasResult, CanvasTaskBinding, Project, ProjectCanvas
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.canvas_character_publication_service import (
    CanvasCharacterPublicationService,
)
from short_drama.service.canvas_generation_service import CanvasGenerationService
from tests.catalog_fixtures import CANVAS, OWNING, SOURCE, actor
from tests.unit.test_canvas_character_publications import (
    REVERSE_OWNING,
    document,
    reverse_owning,
    seed_task,
)
from tests.unit.test_canvas_character_publications import sqlite_mutex_only as sqlite_mutex_only
from tests.unit.test_canvas_task_list_revision_retry import lawful_save, plain_document

pytest_plugins = ("tests.catalog_fixtures",)


def observe_detail(session, monkeypatch, compete):
    """包装只设置竞争检查点与被动观测；所有 DAO、授权及投影运行真实实现。"""
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
        if dao.session is session and lock:
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


@pytest.mark.parametrize("has_character", [False, True], ids=["ordinary", "character"])
def test_detail_recovers_one_lawful_save_without_repeating_projection(
    catalog_session, monkeypatch, has_character
):
    session, baseline = catalog_session
    source = (
        document(session, cover=None).model_dump(mode="json", by_alias=True)
        if has_character
        else plain_document()
    )
    if has_character:
        lawful_save(baseline, source, key="task-detail-initial-role")
    seed_task(baseline, owning=OWNING)
    initial_version = 8 if has_character else 7
    changed = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            edited = deepcopy(source)
            edited["title"] = "详情读取期间的合法保存"
            lawful_save(baseline, edited, key="task-detail-one-lawful-race")

    journal = observe_detail(session, monkeypatch, compete)
    result = CanvasGenerationService(session, SimpleNamespace()).detail(100)
    assert result["id"] == "100" and result["status"] == "succeeded"
    assert len(journal["attempts"]) == 2
    assert [attempt["preflight"][0][-1] for attempt in journal["attempts"]] == [
        initial_version,
        initial_version + 1,
    ]
    assert journal["attempts"][0]["transaction"] is not journal["attempts"][1]["transaction"]
    assert journal["events"] == ["begin", "rollback", "begin", "commit"]
    assert journal["owned"] == journal["projected"] == [(2, 100)]
    assert not session.in_transaction()
    with baseline.begin():
        assert baseline.get(ProjectCanvas, CANVAS).row_version == initial_version + 1
        assert baseline.get(AsyncTask, 100).status == "succeeded"
        assert baseline.scalar(select(func.count()).select_from(CanvasResult)) == 1


def test_detail_stops_after_two_lawful_save_competitions(catalog_session, monkeypatch):
    session, baseline = catalog_session
    seed_task(baseline, owning=OWNING)
    raced_attempts = set()

    def compete(journal, canvas_id):
        attempt = len(journal["attempts"])
        if canvas_id == CANVAS and attempt not in raced_attempts:
            raced_attempts.add(attempt)
            lawful_save(baseline, plain_document(), key=f"task-detail-bounded-race-{attempt}")

    journal = observe_detail(session, monkeypatch, compete)
    with pytest.raises(WorkflowError) as caught:
        CanvasGenerationService(session, SimpleNamespace()).detail(100)
    assert caught.value.code == "canvas_binding_changed" and caught.value.status_code == 409
    assert len(journal["attempts"]) == 2
    assert journal["events"] == ["begin", "rollback", "begin", "rollback"]
    assert journal["owned"] == journal["projected"] == []
    assert not session.in_transaction()
    with baseline.begin():
        assert baseline.get(ProjectCanvas, CANVAS).row_version == 9
        assert baseline.scalar(select(func.count()).select_from(CanvasResult)) == 0


@pytest.mark.parametrize("field", ["source_project_id", "project_id"], ids=["source", "owning"])
def test_detail_does_not_retry_changed_trusted_scope(catalog_session, monkeypatch, field):
    session, baseline = catalog_session
    seed_task(baseline, owning=OWNING)
    changed = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            # 系统夹具模拟可信绑定改变；公开接口不允许改绑，不能称合法autosave。
            with baseline.begin():
                row = baseline.get(ProjectCanvas, CANVAS)
                setattr(row, field, None if field == "source_project_id" else SOURCE)
                row.row_version += 1

    journal = observe_detail(session, monkeypatch, compete)
    with pytest.raises(WorkflowError) as caught:
        CanvasGenerationService(session, SimpleNamespace()).detail(100)
    assert caught.value.code == "canvas_binding_changed" and caught.value.status_code == 409
    assert len(journal["attempts"]) == 1
    assert journal["events"] == ["begin", "rollback"]
    assert journal["owned"] == journal["projected"] == []
    assert not session.in_transaction()


@pytest.mark.parametrize("revoked_project", [OWNING, SOURCE], ids=["owning", "source"])
def test_detail_rechecks_permission_after_failed_transaction(
    catalog_session, monkeypatch, revoked_project
):
    session, baseline = catalog_session
    role = document(session, cover=None).model_dump(mode="json", by_alias=True)
    lawful_save(baseline, role, key="task-detail-role-before-revoke")
    seed_task(baseline, owning=OWNING)
    with baseline.begin():
        baseline.get(AsyncTask, 100).initiated_by = 2
        baseline.get(CanvasTaskBinding, 101).initiated_by = 2
    session.info["actor"] = actor(2)
    changed = False
    revoked = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            edited = deepcopy(role)
            edited["title"] = "撤权之前的合法保存"
            lawful_save(baseline, edited, key="task-detail-before-revoke-race")

    journal = observe_detail(session, monkeypatch, compete)

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
        CanvasGenerationService(session, SimpleNamespace()).detail(100)
    assert revoked
    assert journal["events"] == ["begin", "rollback", "begin", "rollback"]
    assert journal["owned"] == journal["projected"] == []
    assert not session.in_transaction()


def test_detail_replans_new_character_with_source_before_owning_lock(catalog_session, monkeypatch):
    session, baseline = catalog_session
    reverse_owning(baseline)
    lawful_save(
        baseline,
        plain_document(owning=REVERSE_OWNING),
        owning=REVERSE_OWNING,
        key="task-detail-initial-ordinary",
    )
    seed_task(baseline)
    role = document(session, cover=None, owning=REVERSE_OWNING)
    changed = False

    def compete(_journal, canvas_id):
        nonlocal changed
        if not changed and canvas_id == CANVAS:
            changed = True
            lawful_save(
                baseline, role, owning=REVERSE_OWNING, key="task-detail-first-character-race"
            )

    journal = observe_detail(session, monkeypatch, compete)
    result = CanvasGenerationService(session, SimpleNamespace()).detail(100)
    assert result["id"] == "100"
    assert len(journal["attempts"]) == 2
    scopes = {SOURCE, REVERSE_OWNING}
    first_locks = [value for value in journal["attempts"][0]["locks"] if value in scopes]
    second_locks = [value for value in journal["attempts"][1]["locks"] if value in scopes]
    assert list(dict.fromkeys(first_locks)) == [REVERSE_OWNING]
    assert list(dict.fromkeys(second_locks)) == [SOURCE, REVERSE_OWNING]
    assert journal["events"] == ["begin", "rollback", "begin", "commit"]
    assert journal["owned"] == journal["projected"] == [(2, 100)]
    assert not session.in_transaction()
