"""固定角色版本的读取与共享发布；SQLite 不作为真实供应商验收。"""

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Asset
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.asset_version_service import AssetVersionService
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from short_drama.service.canvas_character_image_service import CanvasCharacterImageService
from short_drama.service.canvas_service import CanvasService
from tests.catalog_fixtures import ASSET, CANVAS, KEY, MEDIA, OWNING, SOURCE, actor
from tests.unit.test_canvas_character_images import SETTINGS
from tests.unit.test_canvas_character_publications import commit, document, seed_copy
from tests.unit.test_canvas_character_publications import sqlite_mutex_only as sqlite_mutex_only

pytest_plugins = ("tests.catalog_fixtures",)


def advance(baseline):
    with baseline.begin():
        asset = baseline.get(Asset, ASSET)
        asset.name = "新版角色"
        asset.row_version += 1
        return AssetVersionService(baseline).append_locked(asset).id


def test_pinned_reads_old_work_and_images_after_current_changes(catalog_session):
    session, baseline = catalog_session
    catalog = CanvasCharacterCatalogService(session)
    old = catalog.get(KEY, ASSET).item
    current = advance(baseline)
    pinned = catalog.get(KEY, ASSET, requested_version_id=old.current_version_id).item
    assert pinned.name == old.name and pinned.definition == old.definition
    assert pinned.version_id == old.current_version_id
    assert pinned.current_version_id == current and pinned.version == 1
    images = CanvasCharacterImageService(session, SETTINGS).get(
        KEY, ASSET, old.current_version_id, version_policy="pinned"
    )
    assert images.version_id == old.current_version_id
    assert all(image.media_id == MEDIA for image in images.images)
    with pytest.raises(WorkflowError) as stale:
        CanvasCharacterImageService(session, SETTINGS).get(KEY, ASSET, old.current_version_id)
    assert stale.value.code == "canvas_character_version_changed"


def test_pinned_publication_and_member_resave_keep_the_same_version(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"]["characterVersionPolicy"] = "pinned"
    old_version = source["nodes"][0]["metadata"]["characterVersionId"]
    advance(baseline)
    saved = commit(CanvasService(session), source)
    session.info["actor"] = actor(2)
    member_source = CanvasService(session).read(OWNING, CANVAS)["source_document"]
    assert member_source["nodes"][0]["metadata"]["characterVersionId"] == old_version
    commit(
        CanvasService(session), member_source, expected=saved["row_version"], key="pinned-member"
    )
    assert CanvasService(session).read(OWNING, CANVAS)["source_document"] == member_source


def test_pinned_missing_version_is_not_replaced_by_current(catalog_session):
    session, _ = catalog_session
    with pytest.raises(NotFound):
        CanvasCharacterCatalogService(session).get(KEY, ASSET, requested_version_id=999999)
    with pytest.raises(NotFound):
        CanvasCharacterImageService(session, SETTINGS).get(
            KEY, ASSET, 999999, version_policy="pinned"
        )


@pytest.mark.parametrize("project_id", [OWNING, SOURCE])
def test_pinned_keeps_both_project_permissions(catalog_session, project_id):
    session, baseline = catalog_session
    old = CanvasCharacterCatalogService(session).get(KEY, ASSET).item.current_version_id
    with baseline.begin():
        member = baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        member.status = "removed"
    session.info["actor"] = actor(2)
    with pytest.raises(NotFound):
        CanvasCharacterCatalogService(session).get(KEY, ASSET, requested_version_id=old)


def test_generation_freezes_real_pinned_work_and_rejects_forged_identity(catalog_session):
    from short_drama.domain import ProjectCanvas
    from short_drama.schemas.canvas_task_runtime import CanvasTaskMetadata
    from short_drama.service.canvas_character_generation import freeze_canvas_characters

    session, baseline = catalog_session
    source = document(session, cover=None).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"]["characterVersionPolicy"] = "pinned"
    version_id = source["nodes"][0]["metadata"]["characterVersionId"]
    advance(baseline)
    canvases = CanvasService(session)
    metadata = CanvasTaskMetadata.model_validate(
        {
            "nodeId": "target",
            "clientOperationId": "character-generation-fixture",
            "resolvedCharacterVersions": [{"assetId": str(ASSET), "versionId": version_id}],
        }
    )
    with session.begin():
        canvas = session.get(ProjectCanvas, CANVAS)
        frozen = freeze_canvas_characters(canvases, canvas, source, metadata)
        assert frozen[0]["version_id"] == version_id
        assert frozen[0]["name"] == source["nodes"][0]["metadata"]["characterName"]
        metadata.resolved_character_versions[0].version_id = 999999
        with pytest.raises(WorkflowError) as forged:
            freeze_canvas_characters(canvases, canvas, source, metadata)
        assert forged.value.code == "canvas_character_reference_changed"
