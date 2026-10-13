"""真实 MySQL 固定角色版本／双项目权限；不读取对象或调用供应商。"""

import os

import pytest
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Asset, ProjectCanvas
from short_drama.domain.collaboration import ProjectMember
from short_drama.schemas.canvas_task_runtime import CanvasTaskMetadata
from short_drama.service.asset_version_service import AssetVersionService
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from short_drama.service.canvas_character_generation import freeze_canvas_characters
from short_drama.service.canvas_character_image_service import CanvasCharacterImageService
from short_drama.service.canvas_service import CanvasService
from tests.integration.test_canvas_character_rr import account_session, document, system_session
from tests.integration.test_canvas_character_rr import rr_case as rr_case

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_CHARACTER_RR") != "1",
        reason="RUN_CANVAS_CHARACTER_RR=1 显式启用真实 MySQL",
    ),
]


def advance(case):
    with system_session(case) as writer, writer.begin():
        asset = writer.get(Asset, case.asset, with_for_update=True)
        old = asset.current_version_id
        asset.name = "新版角色"
        asset.row_version += 1
        current = AssetVersionService(writer).append_locked(asset)
        return old, current.id


def test_pinned_catalog_images_and_generation_freeze_after_current_advance(rr_case):
    case = rr_case
    source = document(case).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"]["characterVersionPolicy"] = "pinned"
    old, current = advance(case)
    with account_session(case) as reader:
        item = (
            CanvasCharacterCatalogService(reader)
            .get(case.key, case.asset, requested_version_id=old)
            .item
        )
        assert item.version_id == old and item.current_version_id == current
        assert item.name == "原角色"
        images = CanvasCharacterImageService(
            reader, Settings(_env_file=None, minio_image_bucket="canvas-character-rr-test")
        ).get(case.key, case.asset, old, version_policy="pinned")
        assert images.version_id == old and images.images[0].media_id == case.media
        with pytest.raises(WorkflowError) as stale:
            CanvasCharacterImageService(
                reader, Settings(_env_file=None, minio_image_bucket="canvas-character-rr-test")
            ).get(case.key, case.asset, old)
        assert stale.value.code == "canvas_character_version_changed"
        metadata = CanvasTaskMetadata.model_validate(
            {
                "nodeId": "target",
                "clientOperationId": "pinned-real-mysql",
                "resolvedCharacterVersions": [{"assetId": str(case.asset), "versionId": str(old)}],
            }
        )
        with reader.begin():
            canvas = reader.get(ProjectCanvas, case.canvas)
            frozen = freeze_canvas_characters(CanvasService(reader), canvas, source, metadata)
            assert frozen[0]["version_id"] == str(old)
            assert frozen[0]["name"] == "原角色"


@pytest.mark.parametrize("scope", ["owning", "source"])
def test_pinned_cannot_read_after_either_membership_is_revoked(rr_case, scope):
    case = rr_case
    old, _ = advance(case)
    with system_session(case) as writer, writer.begin():
        member = writer.scalar(
            select(ProjectMember)
            .where(ProjectMember.project_id == getattr(case, scope), ProjectMember.user_id == 2)
            .with_for_update()
        )
        member.status = "removed"
    with account_session(case) as reader:
        with pytest.raises(NotFound):
            CanvasCharacterCatalogService(reader).get(
                case.key, case.asset, requested_version_id=old
            )
