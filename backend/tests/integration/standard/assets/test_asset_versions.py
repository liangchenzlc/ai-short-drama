"""随机临时 MySQL 的真实 FK、并发及范围验证；不调用模型或存储供应商。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from short_drama.core.exceptions import WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.dao.canvas_resource_deletion_dao import CanvasResourceDeletionDAO
from short_drama.db.session import AppSession
from short_drama.domain import Asset, AssetVersion, AssetVersionMediaReference, MediaFile
from short_drama.service.asset_service import AssetService
from short_drama.service.base import utcnow
from short_drama.service.project_service import ProjectService
from short_drama.utils.snowflake import next_id

pytestmark = pytest.mark.integration


def owning_project(session):
    if "asset_test_project_id" not in session.info:
        session.info["asset_test_project_id"] = int(
            ProjectService(session).create({"name": "Asset versions"}).id
        )
    return session.info["asset_test_project_id"]


def image(session):
    project_id = owning_project(session)
    row = MediaFile(
        project_id=project_id,
        id=next_id(),
        format_code="image/png",
        storage_locator=f"minio://image/{next_id()}.png",
        original_name="Version image",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    with session.begin():
        session.add(row)
    return row


def character(session, name="Hero"):
    return AssetService(session).create(
        {"kind": "character", "name": name, "prompt": "portrait"},
        project_id=owning_project(session),
    )


def test_historical_media_is_detected_and_mysql_restricts_physical_delete(db_session, mysql_engine):
    first, second = image(db_session), image(db_session)
    item = character(db_session)
    AssetService(db_session).update(item.id, {"row_version": "1", "media_id": str(first.id)})
    AssetService(db_session).update(item.id, {"row_version": "2", "media_id": str(second.id)})
    with db_session.begin():
        assert CanvasResourceDeletionDAO(db_session).referenced(first, history=False)
        assert not CanvasResourceDeletionDAO(db_session).referenced(first, history=True)
        references = list(
            db_session.scalars(
                select(AssetVersionMediaReference).where(
                    AssetVersionMediaReference.media_id == first.id
                )
            )
        )
        assert len(references) == 1
    with pytest.raises(IntegrityError):
        with mysql_engine.begin() as connection:
            connection.execute(delete(MediaFile.__table__).where(MediaFile.id == first.id))
    with pytest.raises(IntegrityError):
        with mysql_engine.begin() as connection:
            connection.execute(delete(Asset.__table__).where(Asset.id == int(item.id)))


def test_mysql_pointer_cannot_reference_other_asset_version(db_session, mysql_engine):
    first, second = character(db_session), character(db_session, "Other")
    with db_session.begin():
        other_pointer = db_session.get(Asset, int(second.id)).current_version_id
    with pytest.raises(IntegrityError):
        with mysql_engine.begin() as connection:
            connection.execute(
                update(Asset.__table__)
                .where(Asset.id == int(first.id))
                .values(current_version_id=other_pointer)
            )


def test_concurrent_character_patch_has_one_winner_and_one_new_version(db_session, mysql_engine):
    item = character(db_session)
    barrier = Barrier(2)

    def patch(description):
        with AppSession(mysql_engine, expire_on_commit=False, autoflush=False) as session:
            session.info["legacy_user_id"] = 1
            barrier.wait(timeout=10)
            try:
                AssetService(session).update(
                    item.id, {"row_version": "1", "description": description}
                )
                return "succeeded"
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(patch, description) for description in ("first", "second")]
        outcomes = [future.result(timeout=30) for future in futures]
    assert sorted(outcomes) == ["asset_version_conflict", "succeeded"]
    with db_session.begin():
        history = list(
            db_session.scalars(
                select(AssetVersion)
                .where(AssetVersion.asset_id == int(item.id))
                .order_by(AssetVersion.version)
            )
        )
        assert [row.version for row in history] == [1, 2]
        asset = db_session.get(Asset, int(item.id))
        assert asset.current_version_id == history[-1].id
        assert asset.description == history[-1].snapshot_json["asset"]["description"]


def test_system_bulk_and_media_slot_orm_writes_cannot_mutate_history(db_session, mysql_engine):
    reference = image(db_session)
    item = character(db_session)
    AssetService(db_session).update(item.id, {"row_version": "1", "media_id": str(reference.id)})
    with AppSession(mysql_engine) as session:
        with pytest.raises(WorkflowError):
            with session.begin():
                session.execute(delete(AssetVersion))
        with pytest.raises(WorkflowError):
            with session.begin():
                slot = session.scalar(
                    select(AssetVersionMediaReference).where(
                        AssetVersionMediaReference.asset_id == int(item.id)
                    )
                )
                slot.role = "back"
                session.flush()


def test_private_character_history_follows_parent_asset_visibility(db_session, mysql_engine):
    item = character(db_session)
    with AppSession(mysql_engine) as session:
        session.info["actor"] = ActorContext(
            user_id=2,
            username="outsider",
            email="outsider@example.test",
            verified=True,
            session_id=2,
            csrf_hash="test-only",
            request_id="version-isolation",
        )
        assert (
            session.scalar(select(AssetVersion).where(AssetVersion.asset_id == int(item.id)))
            is None
        )
