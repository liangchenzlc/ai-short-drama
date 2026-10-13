"""真实 MySQL 检查角色媒体旧 identity；不调用供应商或读取对象字节。"""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, inspect, select, text

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import MediaFile
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from tests.integration.test_canvas_character_rr import (
    account_session,
    graph_state,
    run_actor,
    system_session,
    wait_for_lock,
)
from tests.integration.test_canvas_character_rr import rr_case as rr_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1",
        reason="RUN_CANVAS_CHARACTER_RR=1 显式启用真实 MySQL",
    ),
]


@pytest.mark.parametrize("entry", ["get", "list"])
def test_catalog_reuses_session_but_reads_current_media_metadata(rr_case, entry):
    case = rr_case
    before = graph_state(case)
    with account_session(case) as reader:
        with reader.begin():
            held = reader.scalar(select(MediaFile).where(MediaFile.id == case.media))
            assert (held.original_name, held.width, held.height) == ("db-fixture.png", 37, 19)
        with system_session(case) as writer, writer.begin():
            current = writer.scalar(
                select(MediaFile).where(MediaFile.id == case.media).with_for_update()
            )
            current.original_name = "committed-current.png"
            current.width = 74
            current.height = 38
            current.updated_at = utcnow()
        service = CanvasCharacterCatalogService(reader)
        item = (
            service.get(case.key, case.asset).item
            if entry == "get"
            else service.list(case.key).items[0]
        )
        media = item.representations[0]
        assert (media.original_name, media.width, media.height) == ("committed-current.png", 74, 38)
        assert (held.original_name, held.width, held.height) == ("committed-current.png", 74, 38)
    assert graph_state(case) == before


def test_cached_image_does_not_hide_committed_incompatible_media_type(rr_case):
    case = rr_case
    before = graph_state(case)
    with account_session(case) as reader:
        with reader.begin():
            held = reader.scalar(select(MediaFile).where(MediaFile.id == case.media))
            assert held.format_code == "image/png"
        with system_session(case) as writer, writer.begin():
            current = writer.scalar(
                select(MediaFile).where(MediaFile.id == case.media).with_for_update()
            )
            current.format_code = "audio/wav"
            current.updated_at = utcnow()
        with pytest.raises(WorkflowError) as failure:
            CanvasCharacterCatalogService(reader).get(case.key, case.asset)
        assert failure.value.code == "invalid_asset_version_media"
        assert failure.value.status_code == 422
    assert graph_state(case) == before


def test_dirty_media_name_keeps_history_while_other_columns_refresh(rr_case):
    case = rr_case
    selected_name = "author-unflushed.png"
    before = graph_state(case)
    with account_session(case, user_id=1) as reader:
        with reader.begin():
            held = reader.scalar(select(MediaFile).where(MediaFile.id == case.media))
            assert held.width == 37
        with system_session(case) as writer, writer.begin():
            current = writer.scalar(
                select(MediaFile).where(MediaFile.id == case.media).with_for_update()
            )
            current.width = 74
            current.height = 38
            current.updated_at = utcnow()
        with reader.begin():
            service = CanvasCharacterCatalogService(reader)
            service._bound_canvas(case.key)
            asset = service.catalog.locked_asset(case.asset)
            held.original_name = selected_name
            assert inspect(held).attrs.original_name.history.added == [selected_name]
            actual = service.catalog.media(case.media)
            assert actual is held
            assert (actual.width, actual.height) == (74, 38)
            assert actual.original_name == selected_name
            assert inspect(held).attrs.original_name.history.added == [selected_name]
            item = service._item(service.catalog.relation(case.source, case.asset), asset)
            assert item.representations[0].original_name == selected_name
            assert item.representations[0].width == 74
            assert inspect(held).attrs.original_name.history.added == [selected_name]
        with system_session(case) as observer:
            current = observer.get(MediaFile, case.media)
            assert (current.original_name, current.width, current.height) == (selected_name, 74, 38)
    assert graph_state(case) == before


def test_public_catalog_get_upgrades_existing_shared_media_lock(rr_case):
    case = rr_case
    before = graph_state(case)
    connections = []
    held_shared = []

    def read_current(session):
        inserted = False

        def before_query(state):
            nonlocal inserted
            if inserted or not state.is_select:
                return
            if not any(mapper.class_ is MediaFile for mapper in state.all_mappers):
                return
            sql = str(state.statement.compile(dialect=case.engine.dialect)).upper()
            if not sql.endswith("FOR UPDATE"):
                return
            inserted = True
            held_shared.append(
                session.scalar(
                    select(MediaFile).where(MediaFile.id == case.media).with_for_update(read=True)
                )
            )

        event.listen(session, "do_orm_execute", before_query)
        try:
            return CanvasCharacterCatalogService(session).get(case.key, case.asset)
        finally:
            event.remove(session, "do_orm_execute", before_query)

    with system_session(case) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = holder.begin()
        blocker = holder.scalar(text("SELECT CONNECTION_ID()"))
        holder.scalar(
            select(MediaFile).where(MediaFile.id == case.media).with_for_update(read=True)
        )
        future = pool.submit(run_actor, case, read_current, connections)
        try:
            wait_for_lock(case, connections, {("media_files", case.media)}, blocker)
            with case.engine.connect() as observer:
                locks = (
                    observer.execute(
                        text(
                            "SELECT l.LOCK_MODE,l.LOCK_STATUS FROM performance_schema.data_locks l "
                            "JOIN performance_schema.threads t ON t.THREAD_ID=l.THREAD_ID "
                            "WHERE l.OBJECT_SCHEMA=:database AND l.OBJECT_NAME='media_files' "
                            "AND t.PROCESSLIST_ID=:connection AND l.LOCK_DATA=:identifier"
                        ),
                        {
                            "database": case.engine.url.database,
                            "connection": connections[0],
                            "identifier": str(case.media),
                        },
                    )
                    .mappings()
                    .all()
                )
            assert any(
                row["LOCK_MODE"].startswith("S") and row["LOCK_STATUS"] == "GRANTED"
                for row in locks
            )
            assert any(
                row["LOCK_MODE"].startswith("X") and row["LOCK_STATUS"] == "WAITING"
                for row in locks
            )
            case.observations.append(
                {"explicit_shared_media_before_full_get": [dict(row) for row in locks]}
            )
        finally:
            transaction.rollback()
        result = future.result(timeout=15)
    assert held_shared and held_shared[0].id == case.media
    assert result.item.representations[0].media_id == case.media
    assert result.model_dump(mode="json")["item"]["representations"][0]["media_id"] == str(
        case.media
    )
    assert result.item.representations[0].format_code == "image/png"
    assert graph_state(case) == before
