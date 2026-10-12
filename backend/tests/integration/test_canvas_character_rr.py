"""显式启用的真实 MySQL RR 回归；媒体仅为数据库夹具，不做对象 I/O。"""

import hashlib
import json
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import Engine

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.session import AppSession
from short_drama.domain import (
    Asset,
    AssetVersion,
    AssetVersionMediaReference,
    CanvasBinaryResource,
    MediaFile,
    Project,
    ProjectAsset,
    ProjectCanvas,
)
from short_drama.domain.base import Base
from short_drama.domain.collaboration import ProjectMember, User
from short_drama.schemas.canvas import CanvasDocument
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.asset_version_service import AssetVersionService, canonical_current
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from short_drama.service.canvas_character_image_service import CanvasCharacterImageService
from short_drama.service.canvas_service import CanvasService
from short_drama.service.episode_service import EpisodeService
from short_drama.utils.snowflake import next_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1",
        reason="RUN_CANVAS_CHARACTER_RR=1 显式启用真实 MySQL RR 回归",
    ),
]

WAIT_SQL = text(
    "SELECT r.OBJECT_NAME,r.LOCK_DATA,r.LOCK_MODE,r.LOCK_STATUS,"
    "rt.PROCESSLIST_ID AS requesting_connection,bt.PROCESSLIST_ID AS blocking_connection "
    "FROM performance_schema.data_lock_waits w "
    "JOIN performance_schema.data_locks r ON r.ENGINE_LOCK_ID=w.REQUESTING_ENGINE_LOCK_ID "
    "JOIN performance_schema.data_locks b ON b.ENGINE_LOCK_ID=w.BLOCKING_ENGINE_LOCK_ID "
    "JOIN performance_schema.threads rt ON rt.THREAD_ID=r.THREAD_ID "
    "JOIN performance_schema.threads bt ON bt.THREAD_ID=b.THREAD_ID "
    "WHERE r.OBJECT_SCHEMA=:database AND rt.PROCESSLIST_ID=:connection"
)


@dataclass
class RRCase:
    engine: Engine
    source: int
    owning: int
    canvas: int
    asset: int
    media: int
    key: str
    observations: list[dict[str, Any]] = field(default_factory=list)


def actor(user_id: int) -> ActorContext:
    return ActorContext(
        user_id, f"rr{user_id}", f"rr{user_id}@example.test", True, user_id, "csrf", "rr-test"
    )


def system_session(case: RRCase) -> AppSession:
    # 仅局部夹具、holder 和被动状态检查使用无 Actor 的可信系统 Session。
    return AppSession(case.engine, expire_on_commit=False, autoflush=False)


def media_row(case: RRCase, identifier: int) -> MediaFile:
    now = utcnow()
    return MediaFile(
        id=identifier,
        project_id=case.source,
        format_code="image/png",
        storage_locator=f"minio://canvas-character-rr-test/{identifier}.png",
        original_name="db-fixture.png",
        byte_size=1,
        width=37,
        height=19,
        created_by=1,
        published_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def rr_case(db_session, mysql_engine, request):
    # db_session 沿现有 fixture 建 User 1，并在 finally 清理所有表；不另建数据库。
    assert db_session.get_bind() is mysql_engine
    assert mysql_engine.dialect.name == "mysql" and mysql_engine.url.database.endswith("_test")
    case = RRCase(mysql_engine, next_id(), next_id(), next_id(), next_id(), next_id(), uuid4().hex)
    try:
        with mysql_engine.connect() as observer:
            assert observer.scalar(text("SELECT @@performance_schema")) == 1
            observer.execute(
                WAIT_SQL, {"database": mysql_engine.url.database, "connection": -1}
            ).all()
    except Exception as error:
        raise AssertionError("角色 RR 回归需要 performance_schema 锁表可读权限") from error
    now = utcnow()
    with system_session(case) as session, session.begin():
        session.add(
            User(
                id=2,
                username="rr_member",
                display_name="RR 成员",
                email="rr2@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=now,
                created_at=now,
            )
        )
        session.flush()
        for identifier, mode in ((case.source, "standard"), (case.owning, "infinite_canvas")):
            session.add(
                Project(
                    id=identifier,
                    owner_user_id=1,
                    name="角色 RR",
                    synopsis="",
                    style="",
                    aspect="16:9",
                    workspace_mode=mode,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
            session.add(
                ProjectMember(
                    id=next_id(), project_id=identifier, user_id=2, status="active", joined_at=now
                )
            )
        session.add(
            ProjectCanvas(
                id=case.canvas,
                project_id=case.owning,
                source_project_id=case.source,
                source_key=case.key,
                title="角色 RR",
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
        session.add(media_row(case, case.media))
        session.flush()
        asset = Asset(
            id=case.asset,
            project_id=case.source,
            kind="character",
            name="原角色",
            label="主角",
            description="原定义",
            prompt="portrait",
            tags=[],
            scene_time="",
            media_id=case.media,
            reference_media_ids=[str(case.media)],
            row_version=3,
            state="confirmed",
            created_by=1,
            updated_by=1,
            created_at=now,
            updated_at=now,
        )
        session.add(asset)
        session.flush()
        AssetVersionService(session).append_locked(asset)
        session.add(
            ProjectAsset(id=next_id(), project_id=case.source, asset_id=case.asset, position=1)
        )
    try:
        yield case
    finally:
        request.node.user_properties.append(
            ("rr_observations", json.dumps(case.observations, ensure_ascii=False))
        )
        db_session.rollback()


@contextmanager
def account_session(case: RRCase, connections: list[int] | None = None, user_id: int = 2):
    with system_session(case) as session:
        session.info["actor"] = actor(user_id)

        def begun(_session, _transaction, connection):
            identifier = connection.scalar(text("SELECT CONNECTION_ID()"))
            isolation = connection.scalar(text("SELECT @@session.transaction_isolation"))
            assert isolation == "REPEATABLE-READ", "业务 reader 必须保留默认 RR 隔离"
            case.observations.append({"connection": identifier, "isolation": isolation})
            if connections is not None and not connections:
                connections.append(identifier)

        event.listen(session, "after_begin", begun)
        try:
            yield session
        finally:
            event.remove(session, "after_begin", begun)


def run_actor(case: RRCase, callback, connections: list[int], user_id: int = 2):
    with account_session(case, connections, user_id) as session:
        return callback(session)


def document(case: RRCase) -> CanvasDocument:
    with account_session(case) as session:
        item = CanvasCharacterCatalogService(session).get(case.key, case.asset).item
    return CanvasDocument.model_validate(
        {
            "id": case.key,
            "workspaceProjectId": str(case.owning),
            "projectId": str(case.source),
            "title": "RR 旧卡",
            "nodes": [
                {
                    "id": "character",
                    "type": "text",
                    "title": item.name,
                    "position": {"x": 0, "y": 0},
                    "width": 320,
                    "height": 260,
                    "metadata": {
                        "workflowKind": "character",
                        "characterAssetId": str(case.asset),
                        "characterVersionId": str(item.current_version_id),
                        "characterVersionPolicy": "current",
                        "characterName": item.name,
                        "characterAliases": [],
                        "characterDefinition": item.definition,
                        "characterCoverUrl": "",
                        "characterPrompt": "成员私人草稿",
                    },
                }
            ],
            "connections": [],
        }
    )


def commit(case: RRCase, session: AppSession, old: CanvasDocument, revision: str, key: str):
    return CanvasService(session).commit(
        case.owning,
        case.canvas,
        {"expected_row_version": revision, "source_document": old},
        key + ":" + case.key,
    )


def saved_old_card(case: RRCase):
    old = document(case)
    with account_session(case) as session:
        result = commit(case, session, old, "7", "initial")
    return old, result["row_version"]


def graph_state(case: RRCase) -> dict[str, Any]:
    # 全量行比较包含 revision、节点内容及所有有 canvas_id 的共享/私人表与写回执。
    with system_session(case) as session:
        canvas = dict(
            session.execute(select(ProjectCanvas.__table__).where(ProjectCanvas.id == case.canvas))
            .mappings()
            .one()
        )
        children = {
            table.name: [
                dict(row)
                for row in session.execute(
                    select(table)
                    .where(table.c.canvas_id == case.canvas)
                    .order_by(*table.primary_key.columns)
                ).mappings()
            ]
            for table in sorted(Base.metadata.tables.values(), key=lambda row: row.name)
            if "canvas_id" in table.c
        }
    return {"canvas": canvas, "rows": children, "counts": {k: len(v) for k, v in children.items()}}


@contextmanager
def pending_version(case: RRCase, *, new_media: bool):
    with system_session(case) as holder:
        transaction = holder.begin()
        try:
            connection = holder.scalar(text("SELECT CONNECTION_ID()"))
            asset = holder.scalar(select(Asset).where(Asset.id == case.asset).with_for_update())
            identifier = next_id() if new_media else case.media
            if new_media:
                holder.add(media_row(case, identifier))
                holder.flush()
                asset.media_id = identifier
                asset.reference_media_ids = [str(identifier)]
            asset.name = "新角色"
            asset.description = "新定义"
            asset.row_version += 1
            snapshot = canonical_current(asset)
            if new_media:
                snapshot["definition_origin"] = "structured"
                snapshot["definition"] = {"description": "完整新定义", "aliases": ["新角色"]}
                snapshot["representations"] = [{"role": "front", "media_id": str(identifier)}]
            version = AssetVersionService(holder).append_locked(asset, snapshot=snapshot)
            holder.flush()
            yield transaction, connection, identifier, version.id, snapshot["definition"]
        finally:
            if transaction.is_active:
                transaction.rollback()


def wait_for_lock(
    case: RRCase, connections: list[int], targets: set[tuple[str, int]], blocker: int
):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if connections:
            try:
                with case.engine.connect() as observer:
                    rows = [
                        dict(row)
                        for row in observer.execute(
                            WAIT_SQL,
                            {"database": case.engine.url.database, "connection": connections[0]},
                        ).mappings()
                    ]
            except Exception as error:
                raise AssertionError(
                    "读取角色 RR 实际锁等待失败，需要 performance_schema 权限"
                ) from error
            matched = [
                row
                for row in rows
                if (row["OBJECT_NAME"], str(row["LOCK_DATA"]))
                in {(table, str(identifier)) for table, identifier in targets}
            ]
            if matched:
                assert all(row["LOCK_STATUS"] == "WAITING" for row in matched)
                assert all(row["requesting_connection"] == connections[0] for row in matched)
                assert all(row["blocking_connection"] == blocker for row in matched)
                case.observations.append({"targets": sorted(targets), "waits": matched})
                return
        time.sleep(0.05)
    raise AssertionError(f"未观测到角色 RR 的真实锁等待：{sorted(targets)}，holder={blocker}")


def observed_result(case, future: Future, connections, targets, blocker, release):
    # 释放锁后取回 reader；关系变更调用者统一收取 holder，保留所有失败。
    failures = []
    try:
        wait_for_lock(case, connections, targets, blocker)
    except Exception as error:
        failures.append(error)
    try:
        release()
    except Exception as error:
        failures.append(error)
    result = None
    try:
        result = future.result(timeout=15)
    except Exception as error:
        failures.append(error)
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise ExceptionGroup("角色 RR 锁观测/业务线程失败", failures)
    return result


@pytest.mark.parametrize("new_media", [False, True], ids=["current-version", "new-media"])
def test_stale_card_returns_exact_409_and_preserves_complete_graph(rr_case, new_media):
    case = rr_case
    old, revision = saved_old_card(case)
    before = graph_state(case)
    connections = []
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            pending_version(case, new_media=new_media) as (
                transaction,
                blocker,
                _media,
                _version,
                _definition,
            ),
        ):
            future = pool.submit(
                run_actor,
                case,
                lambda session: commit(case, session, old, revision, "stale"),
                connections,
            )
            targets = {("assets", case.asset)}
            if new_media:
                targets.add(("projects", case.source))
            with pytest.raises(WorkflowError) as caught:
                observed_result(case, future, connections, targets, blocker, transaction.commit)
            assert caught.value.code == "canvas_character_version_changed"
            assert caught.value.status_code == 409
    finally:
        assert graph_state(case) == before


def test_new_media_catalog_reads_committed_definition_and_representation(rr_case):
    case = rr_case
    connections = []
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        pending_version(case, new_media=True) as (
            transaction,
            blocker,
            media,
            version,
            definition,
        ),
    ):
        future = pool.submit(
            run_actor,
            case,
            lambda session: CanvasCharacterCatalogService(session).get(case.key, case.asset),
            connections,
        )
        item = observed_result(
            case,
            future,
            connections,
            {("projects", case.source), ("assets", case.asset)},
            blocker,
            transaction.commit,
        ).model_dump(mode="json")["item"]
        assert item["current_version_id"] == str(version)
        assert item["definition"] == definition
        assert [(slot["role"], slot["media_id"]) for slot in item["representations"]] == [
            ("front", str(media))
        ]


@pytest.mark.parametrize("entry", ["get", "list"])
@pytest.mark.parametrize("change", ["unlink", "relink"])
def test_catalog_reads_legal_relation_committed_during_project_wait(rr_case, entry, change):
    case = rr_case
    if change == "relink":
        with account_session(case, user_id=1) as session:
            episode = EpisodeService(session).create_for_project(case.source, {"title": "合法重连"})
            library = AssetLibraryService(session)
            library.link("episode", episode.id, case.source, case.asset)
            with session.begin():
                assert library._may_link("project", session.get(Project, case.source), case.asset)
            library.unlink("project", case.source, None, case.asset, "3")
    locked, release = Event(), Event()
    holder_connections, reader_connections = [], []

    def mutate(session):
        library = AssetLibraryService(session)
        original_lock = library._lock_scope

        def pause_after_actual_lock(*args, **kwargs):
            parent = original_lock(*args, **kwargs)
            locked.set()
            assert release.wait(timeout=15), "合法关联变更未获释放"
            return parent

        library._lock_scope = pause_after_actual_lock
        try:
            if change == "unlink":
                return library.unlink("project", case.source, None, case.asset, "3")
            return library.link("project", case.source, None, case.asset)
        finally:
            library._lock_scope = original_lock

    def read(session):
        catalog = CanvasCharacterCatalogService(session)
        return catalog.get(case.key, case.asset) if entry == "get" else catalog.list(case.key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(run_actor, case, mutate, holder_connections, 1)
        failures = []
        try:
            assert locked.wait(timeout=10), "未获取合法关联变更的真实来源 Project 锁"
            future = pool.submit(run_actor, case, read, reader_connections)
            if change == "unlink" and entry == "get":
                with pytest.raises(NotFound) as caught:
                    observed_result(
                        case,
                        future,
                        reader_connections,
                        {("projects", case.source)},
                        holder_connections[0],
                        release.set,
                    )
                assert caught.value.status_code == 404
            else:
                result = observed_result(
                    case,
                    future,
                    reader_connections,
                    {("projects", case.source)},
                    holder_connections[0],
                    release.set,
                )
                result = result.model_dump(mode="json")
                if entry == "get":
                    assert result["item"]["asset_id"] == str(case.asset)
                else:
                    assert [item["asset_id"] for item in result["items"]] == (
                        [str(case.asset)] if change == "relink" else []
                    )
            with system_session(case) as session:
                relation = session.scalar(
                    select(ProjectAsset).where(
                        ProjectAsset.project_id == case.source, ProjectAsset.asset_id == case.asset
                    )
                )
                assert (relation is not None) == (change == "relink")
        except Exception as error:
            failures.append(error)
        finally:
            release.set()
            try:
                holder.result(timeout=15)
            except Exception as error:
                failures.append(error)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup("角色 RR 关联观察/reader/holder 失败", failures)


def image_settings() -> Settings:
    # Images GET 仅解析数据库 locator；不读取 dotenv、不连接对象存储。
    return Settings(
        _env_file=None,
        auth_enabled=False,
        agent_enabled=False,
        minio_image_bucket="canvas-character-rr-test",
        minio_video_bucket="canvas-character-rr-binary",
        minio_audio_bucket="canvas-character-rr-audio",
    )


@contextmanager
def pending_new_media_only(case: RRCase):
    with system_session(case) as holder:
        transaction = holder.begin()
        try:
            connection = holder.scalar(text("SELECT CONNECTION_ID()"))
            identifier = next_id()
            holder.add(media_row(case, identifier))
            holder.flush()
            yield transaction, connection, identifier
        finally:
            if transaction.is_active:
                transaction.rollback()


@contextmanager
def pending_same_id_binary(case: RRCase):
    # 可信迁移数据库夹具：保留真实两命名空间同 ID，不经普通上传冲突检查。
    with system_session(case) as holder:
        transaction = holder.begin()
        try:
            connection = holder.scalar(text("SELECT CONNECTION_ID()"))
            now = utcnow()
            holder.add(
                CanvasBinaryResource(
                    id=case.media,
                    project_id=case.source,
                    resource_kind="file",
                    mime_type="application/octet-stream",
                    storage_locator=f"minio://canvas-character-rr-binary/{case.media}.bin",
                    original_name="db-fixture.bin",
                    byte_size=1,
                    checksum_sha256=hashlib.sha256(b"\x00").hexdigest(),
                    created_by=1,
                    updated_by=1,
                    published_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            holder.flush()
            yield transaction, connection
        finally:
            if transaction.is_active:
                transaction.rollback()


def test_append_after_committed_new_media_preserves_real_before_flush_parent(rr_case):
    case = rr_case
    before = graph_state(case)
    connections = []
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            pending_new_media_only(case) as (transaction, blocker, media),
        ):

            def append(session):
                with session.begin():
                    catalog = CanvasCharacterCatalogService(session)
                    catalog._bound_canvas(case.key)
                    asset = catalog.catalog.locked_asset(case.asset)
                    asset.name = "RR appended after committed new media"
                    asset.media_id = media
                    asset.reference_media_ids = [str(media)]
                    asset.row_version += 1
                    asset.updated_at = utcnow()
                    return AssetVersionService(session).append_locked(asset).id

            future = pool.submit(run_actor, case, append, connections)
            version_id = observed_result(
                case,
                future,
                connections,
                {("projects", case.source)},
                blocker,
                transaction.commit,
            )
            with system_session(case) as session:
                asset = session.get(Asset, case.asset)
                history = list(
                    session.scalars(
                        select(AssetVersion)
                        .where(AssetVersion.asset_id == asset.id)
                        .order_by(AssetVersion.version)
                    )
                )
                slots = list(
                    session.scalars(
                        select(AssetVersionMediaReference).where(
                            AssetVersionMediaReference.asset_id == asset.id,
                            AssetVersionMediaReference.version_id == version_id,
                        )
                    )
                )
                assert [version.version for version in history] == [1, 2]
                assert asset.current_version_id == version_id
                assert asset.media_id == media and asset.row_version == 4
                assert asset.name == "RR appended after committed new media"
                assert any(slot.media_id == media for slot in slots)
    finally:
        assert graph_state(case) == before


def test_dirty_media_name_survives_current_reads_and_real_append(rr_case):
    case = rr_case
    selected_name = "rr-author-selected-original.png"
    history = []
    with account_session(case, user_id=1) as session, session.begin():
        catalog = CanvasCharacterCatalogService(session)
        catalog._bound_canvas(case.key)
        asset = catalog.catalog.locked_asset(case.asset)
        media = session.scalar(select(MediaFile).where(MediaFile.id == case.media))
        media.original_name = selected_name
        media.updated_by = 1
        media.updated_at = utcnow()
        assert inspect(media).attrs.original_name.history.added == [selected_name]
        assert catalog.catalog.media(media.id).original_name == selected_name
        assert inspect(media).attrs.original_name.history.added == [selected_name]
        versions = AssetVersionService(session)
        versions._validate_media(asset, canonical_current(asset))
        assert inspect(media).attrs.original_name.history.added == [selected_name]

        def before_flush(_session, _context, _instances):
            changed = inspect(media).attrs.original_name.history
            history.append(
                {
                    "original_name": media.original_name,
                    "added": list(changed.added),
                    "deleted": list(changed.deleted),
                }
            )

        event.listen(session, "before_flush", before_flush)
        try:
            asset.name = "RR changed asset with dirty media"
            asset.row_version += 1
            asset.updated_at = utcnow()
            version_id = versions.append_locked(asset).id
            assert any(point["added"] == [selected_name] for point in history)
            assert all(point["original_name"] == selected_name for point in history)
        finally:
            event.remove(session, "before_flush", before_flush)
            case.observations.append({"dirty_media_history": history})
    with system_session(case) as session:
        asset = session.get(Asset, case.asset)
        media = session.get(MediaFile, case.media)
        version = session.get(AssetVersion, version_id)
        slots = list(
            session.scalars(
                select(AssetVersionMediaReference).where(
                    AssetVersionMediaReference.asset_id == asset.id,
                    AssetVersionMediaReference.version_id == version_id,
                )
            )
        )
        assert media.original_name == selected_name
        assert asset.name == "RR changed asset with dirty media" and asset.row_version == 4
        assert asset.current_version_id == version_id and version.version == 2
        assert slots and all(slot.media_id == media.id for slot in slots)


def test_images_reads_committed_new_media_database_resource(rr_case):
    case = rr_case
    connections = []
    before = graph_state(case)
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            pending_version(case, new_media=True) as (
                transaction,
                blocker,
                media,
                version,
                _definition,
            ),
        ):
            future = pool.submit(
                run_actor,
                case,
                lambda session: CanvasCharacterImageService(session, image_settings()).get(
                    case.key, case.asset, version
                ),
                connections,
            )
            result = observed_result(
                case,
                future,
                connections,
                {("projects", case.source), ("assets", case.asset)},
                blocker,
                transaction.commit,
            ).model_dump(mode="json")
            assert result["version_id"] == str(version)
            assert len(result["images"]) == 1
            image = result["images"][0]
            assert image["role"] == "front" and image["media_id"] == str(media)
            assert image["resource_key"] == f"resource:{media}"
            assert image["file_path"] == f"/api/v1/canvas-runtime/resources/{media}/file"
            assert image["requires_canvas_copy"] is True
            assert image["resource"]["id"] == str(media)
    finally:
        assert graph_state(case) == before


def test_images_committed_same_id_binary_returns_exact_409_and_preserves_graph(rr_case):
    case = rr_case
    with account_session(case) as session:
        version = (
            CanvasCharacterCatalogService(session).get(case.key, case.asset).item.current_version_id
        )
    before = graph_state(case)
    connections = []
    try:
        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            pending_same_id_binary(case) as (transaction, blocker),
        ):
            future = pool.submit(
                run_actor,
                case,
                lambda session: CanvasCharacterImageService(session, image_settings()).get(
                    case.key, case.asset, version
                ),
                connections,
            )
            with pytest.raises(WorkflowError) as caught:
                observed_result(
                    case,
                    future,
                    connections,
                    {("projects", case.source)},
                    blocker,
                    transaction.commit,
                )
            assert caught.value.code == "canvas_character_resource_ambiguous"
            assert caught.value.status_code == 409
            with account_session(case) as observer:
                media = observer.scalar(select(MediaFile).where(MediaFile.id == case.media))
                binary = observer.scalar(
                    select(CanvasBinaryResource).where(CanvasBinaryResource.id == case.media)
                )
                assert media is not None and binary is not None
                assert media.id == binary.id == case.media
                assert media.project_id == binary.project_id == case.source
    finally:
        assert graph_state(case) == before
