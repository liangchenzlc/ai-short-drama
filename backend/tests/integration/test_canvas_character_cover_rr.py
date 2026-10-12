"""显式启用的真实 MySQL RR/MinIO 封面竞争；沿正常入口，不调用供应商。"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from threading import Event

import pytest
from sqlalchemy import event, select, text

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import (
    Asset,
    CanvasBinaryResource,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)
from short_drama.schemas.canvas import CanvasDocument
from short_drama.schemas.canvas_resource import CanvasResourceCopyRequest
from short_drama.service.base import utcnow
from short_drama.service.canvas_resource_copy import CanvasResourceCopyService
from short_drama.service.canvas_service import CanvasService
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_character_rr import (
    RRCase,
    account_session,
    commit,
    document,
    graph_state,
    run_actor,
    system_session,
    wait_for_lock,
)
from tests.integration.test_canvas_character_rr import rr_case as rr_case
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="RUN_CANVAS_CHARACTER_RR=1 与 RUN_CANVAS_RESOURCE_MINIO=1 启用真实封面竞争",
    ),
]


@dataclass
class CoverCase:
    rr: RRCase
    settings: Settings
    storage: MinioStorage
    content: bytes


@pytest.fixture
def cover_case(rr_case, resource_app):
    # rr_case 负责正常随机临时库；resource_app 负责真实随机桶及对象清理。
    settings, storage = resource_app[2], resource_app[0].state.storage
    content = png("purple")
    stored = storage.put(
        settings.minio_image_bucket,
        f"rr-cover-original/{rr_case.media}.png",
        BytesIO(content),
        len(content),
        "image/png",
    )
    with system_session(rr_case) as session, session.begin():
        media = session.get(MediaFile, rr_case.media)
        media.storage_locator = stored.storage_locator
        media.original_name = "real-rr-source.png"
        media.byte_size = len(content)
        media.checksum_sha256 = hashlib.sha256(content).hexdigest()
    return CoverCase(rr_case, settings, storage, content)


def copy_before_final_commit(cover: CoverCase, ready, release, pending):
    case = cover.rr
    with account_session(case, user_id=1) as session:

        def flushed(actual, _context):
            for entity in actual.new:
                if isinstance(entity, MediaFile) and entity.project_id == case.owning:
                    pending["media"] = entity.id

        def before_commit(actual):
            if "media" not in pending:
                return
            upload = actual.scalar(
                select(CanvasResourceUpload).where(
                    CanvasResourceUpload.media_id == pending["media"]
                )
            )
            assert upload is not None and upload.mode == "copy" and upload.status == "ready"
            assert upload.user_id == 1 and upload.project_id == case.owning
            source = actual.scalar(
                select(CanvasResourceCopySource).where(
                    CanvasResourceCopySource.upload_id == upload.id
                )
            )
            assert source is not None and source.original_resource_id == case.media
            assert source.released_at is not None and source.source_media_id is None
            pending["connection"] = actual.connection().scalar(text("SELECT CONNECTION_ID()"))
            case.observations.append(
                {
                    "scope": "真实 copy 最后提交：Media/ready/released 已 flush，无结果替换",
                    "media_id": str(pending["media"]),
                    "upload_id": str(upload.id),
                    "source_media_id": str(source.original_resource_id),
                    "holder_connection": pending["connection"],
                }
            )
            ready.set()
            assert release.wait(timeout=20), "真实 copy 最后提交事件门超时"

        event.listen(session, "after_flush", flushed)
        event.listen(session, "before_commit", before_commit)
        try:
            return CanvasResourceCopyService(session, cover.settings, cover.storage).copy(
                CanvasResourceCopyRequest(source_resource_id=str(case.media), canvas_key=case.key),
                "rr-new-cover-real-copy:" + case.key,
            )
        finally:
            event.remove(session, "before_commit", before_commit)
            event.remove(session, "after_flush", flushed)


def assert_complete_copy_and_png(cover: CoverCase, cover_id: int) -> int:
    case, content = cover.rr, cover.content
    with account_session(case, user_id=1) as session:
        media = session.scalar(select(MediaFile).where(MediaFile.id == cover_id))
        assert media is not None and media.project_id == case.owning
        assert media.format_code == "image/png" and media.created_by == 1
        assert media.byte_size == len(content)
        assert media.checksum_sha256 == hashlib.sha256(content).hexdigest()
        assert (media.width, media.height) == (37, 19)
        assert media.created_at is not None and media.updated_at is not None
        assert case.media in CanvasResourceDAO(session).copy_ancestors({cover_id})[cover_id]
        upload = session.scalar(
            select(CanvasResourceUpload).where(CanvasResourceUpload.media_id == cover_id)
        )
        assert upload is not None and upload.status == "ready" and upload.user_id == 1
        source = session.scalar(
            select(CanvasResourceCopySource).where(CanvasResourceCopySource.upload_id == upload.id)
        )
        assert source is not None and source.released_at is not None
        assert source.original_resource_id == case.media and source.source_media_id is None
        source_id = source.id
        locator = media.storage_locator
    location = ObjectLocation.parse(locator, {cover.settings.minio_image_bucket})
    with cover.storage.open(location.bucket, location.object_name) as stream:
        actual = stream.read()
    assert actual == content and hashlib.sha256(actual).hexdigest() == media.checksum_sha256
    return source_id


def test_new_real_copy_cover_survives_source_project_wait(cover_case):
    cover, pending, connections = cover_case, {}, []
    case = cover.rr
    source = document(case).model_dump(mode="json", by_alias=True)
    version_id = source["nodes"][0]["metadata"]["characterVersionId"]
    ready, release = Event(), Event()

    def publish(session):
        return commit(case, session, CanvasDocument.model_validate(source), "7", "new-copy-cover")

    with ThreadPoolExecutor(max_workers=2) as pool:
        copied_future = pool.submit(copy_before_final_commit, cover, ready, release, pending)
        try:
            if not ready.wait(timeout=8):
                if copied_future.done():
                    copied_future.result()
                raise AssertionError("真实 copy 未到达 Media/ready/released 最后提交事件门")
            cover_id = pending["media"]
            cover_url = f"/api/v1/canvas-runtime/resources/{cover_id}/file"
            source["nodes"][0]["metadata"]["characterCoverUrl"] = cover_url
            future = pool.submit(run_actor, case, publish, connections, 1)
            wait_for_lock(case, connections, {("projects", case.source)}, pending["connection"])
            release.set()
            copied = copied_future.result(timeout=15)
            assert int(copied.id) == cover_id
            assert_complete_copy_and_png(cover, cover_id)
            result = future.result(timeout=15)
        finally:
            release.set()
    assert result["row_version"] == "8" and graph_state(case)["canvas"]["row_version"] == 8
    with account_session(case, user_id=1) as session:
        shared = CanvasService(session)._document(
            session.get(ProjectCanvas, case.canvas), private=False
        )
        metadata = shared["nodes"][0]["metadata"]
        assert metadata["characterCoverUrl"] == cover_url
        assert metadata["characterVersionId"] == version_id
        assert session.get(MediaFile, cover_id).published_at is not None
        asset = session.get(Asset, case.asset)
        assert str(asset.current_version_id) == version_id and asset.row_version == 3


@contextmanager
def pending_owning_same_id_binary(cover: CoverCase, cover_id: int):
    case, body = cover.rr, b"trusted-owning-cover-namespace-import"
    stored = cover.storage.put(
        cover.settings.minio_video_bucket,
        f"rr-cover-namespace/{cover_id}.bin",
        BytesIO(body),
        len(body),
        "application/octet-stream",
    )
    with system_session(case) as holder:
        transaction = holder.begin()
        try:
            connection = holder.scalar(text("SELECT CONNECTION_ID()"))
            now = utcnow()
            holder.add(
                CanvasBinaryResource(
                    id=cover_id,
                    project_id=case.owning,
                    scope_user_id=None,
                    resource_kind="file",
                    mime_type="application/octet-stream",
                    storage_locator=stored.storage_locator,
                    original_name="trusted-owning-cover.bin",
                    byte_size=len(body),
                    checksum_sha256=hashlib.sha256(body).hexdigest(),
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


def test_committed_binary_rejects_real_previous_cover_and_rolls_back(cover_case):
    cover, connections = cover_case, []
    case = cover.rr
    with account_session(case, user_id=1) as session:
        copied = CanvasResourceCopyService(session, cover.settings, cover.storage).copy(
            CanvasResourceCopyRequest(source_resource_id=str(case.media), canvas_key=case.key),
            "rr-old-cover-real-copy:" + case.key,
        )
    cover_id = int(copied.id)
    private_source_id = assert_complete_copy_and_png(cover, cover_id)
    source = document(case).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"].update(
        characterCoverUrl=f"/api/v1/canvas-runtime/resources/{cover_id}/file",
        characterVoiceName="作者私人声音",
    )
    with account_session(case, user_id=1) as session:
        commit(case, session, CanvasDocument.model_validate(source), "7", "previous-cover")
    with account_session(case, user_id=2) as member:
        assert member.get(CanvasResourceCopySource, private_source_id) is None
        source = CanvasService(member)._document(
            member.get(ProjectCanvas, case.canvas), private=True
        )
        metadata = source["nodes"][0]["metadata"]
        assert metadata["characterCoverUrl"] == f"/api/v1/canvas-runtime/resources/{cover_id}/file"
        assert "characterPrompt" not in metadata and "characterVoiceName" not in metadata
    before = graph_state(case)

    def publish(session):
        return commit(case, session, CanvasDocument.model_validate(source), "8", "binary-cover")

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pending_owning_same_id_binary(cover, cover_id) as (transaction, blocker):
                future = pool.submit(run_actor, case, publish, connections, 2)
                wait_for_lock(case, connections, {("projects", case.owning)}, blocker)
                transaction.commit()
                with account_session(case, user_id=2) as observer:
                    media = observer.scalar(select(MediaFile).where(MediaFile.id == cover_id))
                    binary = observer.scalar(
                        select(CanvasBinaryResource).where(CanvasBinaryResource.id == cover_id)
                    )
                    assert media is not None and binary is not None
                    assert media.id == binary.id == cover_id
                    assert media.project_id == binary.project_id == case.owning
                    assert media.published_at is not None and binary.published_at is not None
                with pytest.raises(NotFound) as caught:
                    future.result(timeout=15)
                assert caught.value.code == "not_found" and caught.value.status_code == 404
                assert caught.value.message == "角色封面不是本画布可发布的真实图片资源，请先归一化"
    finally:
        assert graph_state(case) == before
