"""可信来源只读行为；SQLite与SQL编译不证明MySQL并发或真实供应商。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_source_project_dao import CanvasSourceProjectDAO
from short_drama.domain import Episode, EpisodeNovel, Project, ProjectCanvas
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.base import utcnow
from short_drama.service.canvas_source_project_service import CanvasSourceProjectService
from tests.catalog_fixtures import CANVAS, KEY, OWNING, SOURCE, actor

pytest_plugins = ("tests.catalog_fixtures",)

EPISODE = 9600000000000001
RAW = "  <hero> &amp; <不是HTML>\r\n\n原文尾部  "
EXPECTED = (7, OWNING, SOURCE)


@pytest.fixture
def source_session(catalog_session):
    session, baseline = catalog_session
    now = utcnow()
    with baseline.begin():
        project = baseline.get(Project, SOURCE)
        project.name, project.synopsis, project.style = "真实来源名", "真实梗概", "低饱和自由文本"
        project.row_version = 9
        for offset, position, project_id in [
            (0, 3, SOURCE),
            (1, 1, SOURCE),
            (2, 2, SOURCE),
            (3, 1, OWNING),
        ]:
            baseline.add(
                Episode(
                    id=EPISODE + offset,
                    project_id=project_id,
                    position=position,
                    title=f"分集 {offset}",
                    synopsis="不作为正文",
                    aspect="16:9",
                    style="",
                    row_version=1,
                    content_version=5,
                    storyboard_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        for offset, content in [(0, RAW), (1, "")]:
            baseline.add(
                EpisodeNovel(id=EPISODE + 20 + offset, episode_id=EPISODE + offset, content=content)
            )
    return session, baseline


def operations(session):
    service = CanvasSourceProjectService(session)
    return (
        lambda: service.overview(KEY, *EXPECTED),
        lambda: service.episode(KEY, EPISODE, *EXPECTED, 5),
    )


def test_overview_uses_real_fields_order_and_explicit_unavailable_features(source_session):
    session, _ = source_session
    read = operations(session)[0]().model_dump(mode="json")
    assert set(read) == {
        "canvas_key",
        "canvas_row_version",
        "owning_project_id",
        "source_project_id",
        "project",
        "episodes",
        "capabilities",
    }
    assert read["canvas_key"] == KEY and read["canvas_row_version"] == "7"
    assert read["owning_project_id"] == str(OWNING) and read["source_project_id"] == str(SOURCE)
    assert read["project"] == {
        "id": str(SOURCE),
        "row_version": "9",
        "name": "真实来源名",
        "synopsis": "真实梗概",
        "aspect": "16:9",
        "style_text": "低饱和自由文本",
    }
    assert [row["id"] for row in read["episodes"]] == [
        str(EPISODE + 1),
        str(EPISODE + 2),
        str(EPISODE),
    ]
    assert [row["position"] for row in read["episodes"]] == [1, 2, 3]
    assert all(
        row["kind"] == "episode" and row["content_version"] == "5" for row in read["episodes"]
    )
    assert read["capabilities"] == {
        "source_style_snapshot": "unavailable",
        "source_asset_folders": "unavailable",
        "source_assets": "not_included",
    }


@pytest.mark.parametrize(
    "offset,state,text", [(0, "present", RAW), (1, "present", ""), (2, "missing", None)]
)
def test_novel_original_plain_text_empty_and_missing_are_distinct(
    source_session, offset, state, text
):
    session, _ = source_session
    read = (
        CanvasSourceProjectService(session)
        .episode(KEY, EPISODE + offset, *EXPECTED, 5)
        .model_dump(mode="json")
    )
    assert read["novel"] == {"state": state, "content_format": "plain_text", "plain_text": text}
    assert read["episode"]["id"] == str(EPISODE + offset)
    assert set(read) == {
        "canvas_key",
        "canvas_row_version",
        "owning_project_id",
        "source_project_id",
        "episode",
        "novel",
    }


def test_member_with_both_permissions_can_read(source_session):
    session, _ = source_session
    session.info["actor"] = actor(2)
    assert operations(session)[0]().project.name == "真实来源名"
    assert operations(session)[1]().novel.plain_text == RAW


@pytest.mark.parametrize("project_id", [OWNING, SOURCE])
def test_losing_either_project_permission_denies_both_reads(source_session, project_id):
    session, baseline = source_session
    session.info["actor"] = actor(2)
    assert operations(session)[0]().episodes
    with baseline.begin():
        baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).status = "removed"
    for operation in operations(session):
        with pytest.raises(NotFound):
            operation()
    assert not session.in_transaction()


@pytest.mark.parametrize("target", ["canvas", "owning", "source"])
def test_archive_denies_both_reads(source_session, target):
    session, baseline = source_session
    with baseline.begin():
        row = (
            baseline.get(ProjectCanvas, CANVAS)
            if target == "canvas"
            else baseline.get(Project, OWNING if target == "owning" else SOURCE)
        )
        row.archived_at = utcnow()
    for operation in operations(session):
        with pytest.raises(NotFound):
            operation()


@pytest.mark.parametrize("damage", ["no_source", "self", "owning_mode", "source_mode"])
def test_invalid_binding_is_explicit_without_json_fallback(source_session, damage):
    session, baseline = source_session
    with baseline.begin():
        if damage in {"no_source", "self"}:
            baseline.get(ProjectCanvas, CANVAS).source_project_id = (
                None if damage == "no_source" else OWNING
            )
        else:
            baseline.get(Project, OWNING if damage == "owning_mode" else SOURCE).workspace_mode = (
                "standard" if damage == "owning_mode" else "infinite_canvas"
            )
    for operation in operations(session):
        with pytest.raises(WorkflowError) as rejected:
            operation()
        assert rejected.value.code == (
            "canvas_source_project_required" if damage == "no_source" else "canvas_binding_invalid"
        )


@pytest.mark.parametrize(
    "expected", [(6, OWNING, SOURCE), (7, OWNING + 1, SOURCE), (7, OWNING, SOURCE + 1)]
)
def test_expected_context_is_comparison_not_source_selection(source_session, expected):
    session, _ = source_session
    with pytest.raises(WorkflowError) as rejected:
        CanvasSourceProjectService(session).overview(KEY, *expected)
    assert rejected.value.code in {"canvas_revision_conflict", "canvas_source_context_conflict"}


def test_wrong_source_episode_and_stale_content_version_are_denied(source_session):
    session, _ = source_session
    service = CanvasSourceProjectService(session)
    with pytest.raises(NotFound):
        service.episode(KEY, EPISODE + 3, *EXPECTED, 5)
    with pytest.raises(WorkflowError) as rejected:
        service.episode(KEY, EPISODE, *EXPECTED, 4)
    assert rejected.value.code == "canvas_source_content_conflict"


@pytest.mark.parametrize("change", ["binding", "version"])
def test_locked_canvas_recheck_rejects_identity_and_version_change(
    source_session, monkeypatch, change
):
    session, _ = source_session
    service = CanvasSourceProjectService(session)
    original = service.canvas_dao.source_canvas

    def changed(key, *, lock=False):
        value = original(key, lock=lock)
        if not lock:
            return value
        return SimpleNamespace(
            id=value.id,
            project_id=value.project_id,
            source_project_id=None if change == "binding" else value.source_project_id,
            row_version=8 if change == "version" else value.row_version,
        )

    monkeypatch.setattr(service.canvas_dao, "source_canvas", changed)
    with pytest.raises(WorkflowError) as rejected:
        service.overview(KEY, *EXPECTED)
    assert rejected.value.code == (
        "canvas_binding_changed" if change == "binding" else "canvas_revision_conflict"
    )


def test_anonymous_rejected_before_any_source_query(source_session, monkeypatch):
    session, _ = source_session
    session.info.pop("actor")
    service = CanvasSourceProjectService(session)
    monkeypatch.setattr(
        service.canvas_dao, "source_canvas", lambda *_args, **_kwargs: pytest.fail("匿名不应查询")
    )
    with pytest.raises(WorkflowError) as rejected:
        service.overview(KEY, *EXPECTED)
    assert rejected.value.code == "authentication_required"


def test_reads_emit_only_select_and_never_query_private_creation_tables(source_session):
    session, _ = source_session
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for operation in operations(session):
            operation()
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        all_sql = " ".join(statements).lower()
        assert not any(
            name in all_sql
            for name in (
                "canvas_library",
                "ai_model_configs",
                "async_tasks",
                "canvas_tool_runs",
                "agent_conversations",
            )
        )
        assert (
            not session.new
            and not session.dirty
            and not session.deleted
            and not session.in_transaction()
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_dao_reads_compile_to_shared_current_locks_with_scope_and_refresh():
    class RecordingSession:
        def __init__(self):
            self.statements = []

        def scalar(self, statement):
            self.statements.append(statement)

        def scalars(self, statement):
            self.statements.append(statement)
            return []

    session = RecordingSession()
    dao = CanvasSourceProjectDAO(session)
    dao.episodes(SOURCE)
    dao.episode(SOURCE, EPISODE)
    dao.novel(SOURCE, EPISODE)
    assert len(session.statements) == 3
    for statement in session.statements:
        sql = str(statement.compile(dialect=mysql.dialect()))
        assert "LOCK IN SHARE MODE" in sql and "episodes.project_id" in sql
        assert statement.get_execution_options()["populate_existing"] is True
