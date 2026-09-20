from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from short_drama.core.exceptions import BusinessError, Conflict, NotFound

pytestmark = pytest.mark.integration


def services(session):
    from short_drama.service.asset_service import AssetService
    from short_drama.service.episode_asset_service import EpisodeAssetService
    from short_drama.service.episode_novel_service import EpisodeNovelService
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.global_asset_service import GlobalAssetService
    from short_drama.service.media_file_service import MediaFileService
    from short_drama.service.project_asset_service import ProjectAssetService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_asset_service import ShotAssetService
    from short_drama.service.shot_script_service import ShotScriptService

    return {
        cls.__name__.removesuffix("Service"): cls(session)
        for cls in (
            ProjectService,
            EpisodeService,
            EpisodeNovelService,
            AssetService,
            GlobalAssetService,
            ProjectAssetService,
            EpisodeAssetService,
            ShotScriptService,
            ShotAssetService,
            MediaFileService,
        )
    }


def seed(session):
    svc = services(session)
    project = svc["Project"].create({"name": "project", "aspect": "16:9"})
    episode = svc["Episode"].create(
        {"project_id": project.id, "position": 1, "title": "episode", "aspect": "16:9"}
    )
    media = svc["MediaFile"].create({"format_code": "image/png", "storage_locator": uuid4().hex})
    asset = svc["Asset"].create({"name": "asset", "kind": "character", "media_id": media.id})
    novel = svc["EpisodeNovel"].create({"episode_id": episode.id, "content": "novel"})
    shot = svc["ShotScript"].create({"episode_id": episode.id, "position": 1, "script": "shot"})
    global_asset = svc["GlobalAsset"].create({"asset_id": asset.id, "position": 1})
    project_asset = svc["ProjectAsset"].create(
        {"project_id": project.id, "asset_id": asset.id, "position": 1}
    )
    episode_asset = svc["EpisodeAsset"].create(
        {"episode_id": episode.id, "asset_id": asset.id, "position": 1}
    )
    shot_asset = svc["ShotAsset"].create(
        {"episode_id": episode.id, "shot_id": shot.id, "asset_id": asset.id}
    )
    return svc, dict(
        Project=project,
        Episode=episode,
        MediaFile=media,
        Asset=asset,
        EpisodeNovel=novel,
        ShotScript=shot,
        GlobalAsset=global_asset,
        ProjectAsset=project_asset,
        EpisodeAsset=episode_asset,
        ShotAsset=shot_asset,
    )


def test_crud_audit_pagination_and_restrict(db_session):
    svc, rows = seed(db_session)
    updates = {
        "Project": {"name": "changed"},
        "Episode": {"title": "changed"},
        "MediaFile": {"original_name": "changed"},
        "Asset": {"name": "changed"},
        "EpisodeNovel": {"content": "changed"},
        "ShotScript": {"script": "changed"},
        "GlobalAsset": {"position": 2},
        "ProjectAsset": {"position": 2},
        "EpisodeAsset": {"position": 2},
        "ShotAsset": {"asset_id": rows["Asset"].id},
    }
    for name, row in rows.items():
        service = svc[name]
        assert service.get(row.id).id == row.id
        assert isinstance(row.model_dump(mode="json")["id"], str)
        page = service.list(limit=1)
        assert len(page.items) == page.total == 1
        assert row.created_at is not None
        assert row.created_by is None
        unchanged = service.update(row.id, {})
        if hasattr(row, "updated_at"):
            assert unchanged.updated_at == row.updated_at
        changed = service.update(row.id, updates[name])
        for key, value in updates[name].items():
            assert getattr(changed, key) == value
        with pytest.raises(NotFound):
            service.get(2**64 - 1)
    with pytest.raises(Conflict):
        svc["Project"].delete(rows["Project"].id)
    assert svc["Project"].get(rows["Project"].id).name == "changed"
    for name in (
        "ShotAsset",
        "EpisodeAsset",
        "ProjectAsset",
        "GlobalAsset",
        "ShotScript",
        "EpisodeNovel",
        "Asset",
        "MediaFile",
        "Episode",
        "Project",
    ):
        svc[name].delete(rows[name].id)
        assert svc[name].list().total == 0


def test_reorder_swaps_without_unique_conflict_and_rolls_back_invalid_ids(db_session):
    svc, rows = seed(db_session)
    second = svc["Episode"].create(
        {"project_id": rows["Project"].id, "position": 2, "title": "second", "aspect": "16:9"}
    )
    ids = [second.id, rows["Episode"].id]
    result = svc["Episode"].reorder(rows["Project"].id, ids)
    assert [item.id for item in result] == ids
    assert [item.position for item in result] == [1, 2]
    with pytest.raises(BusinessError):
        svc["Episode"].reorder(rows["Project"].id, [second.id])
    assert [item.id for item in svc["Episode"].list().items] == ids


def test_project_open_does_not_modify_content_audit(db_session):
    svc, rows = seed(db_session)
    opened = svc["Project"].open(rows["Project"].id)
    assert opened.last_opened_at is not None
    assert opened.updated_at == rows["Project"].updated_at


def test_large_ids_serialize_without_precision_loss(db_session):
    large_id = 2**63 + 7
    with db_session.begin():
        db_session.execute(
            text("INSERT INTO projects (id,name,aspect) VALUES (:id,'large','16:9')"),
            {"id": large_id},
        )
    row = services(db_session)["Project"].get(large_id)
    assert row.model_dump(mode="json")["id"] == str(large_id)


def test_media_and_ownership_validation(db_session):
    svc, rows = seed(db_session)
    video = svc["MediaFile"].create({"format_code": "video/mp4", "storage_locator": uuid4().hex})
    with pytest.raises(BusinessError):
        svc["Asset"].update(rows["Asset"].id, {"media_id": video.id})
    assert svc["Asset"].get(rows["Asset"].id).media_id == rows["MediaFile"].id
    with pytest.raises(ValidationError):
        svc["Episode"].update(rows["Episode"].id, {"project_id": 77})
    with pytest.raises(ValidationError):
        svc["MediaFile"].update(video.id, {"storage_locator": "replacement"})


def test_dao_flush_does_not_commit_caller_transaction(db_session):
    from short_drama.dao.project_dao import ProjectDAO

    dao = ProjectDAO(db_session)
    with pytest.raises(RuntimeError, match="rollback"):
        with db_session.begin():
            row = dao.create({"name": "temporary", "aspect": "16:9"})
            assert row.id > 2**53
            raise RuntimeError("rollback")
    assert services(db_session)["Project"].list().total == 0


def test_disabled_historical_model_allows_unrelated_asset_edits(db_session):
    from short_drama.dao.ai_model_config_dao import AIModelConfigDAO

    with db_session.begin():
        model = AIModelConfigDAO(db_session).create(
            {
                "service_type": "image",
                "name": "historical",
                "model_key": "image-1",
                "provider": "test",
                "enabled": 1,
            }
        )
        model_id = model.id
    assets = services(db_session)["Asset"]
    original = assets.create({"kind": "scene", "name": "first", "model_id": model_id})
    with db_session.begin():
        db_session.execute(
            text("UPDATE ai_model_configs SET enabled=0 WHERE id=:id"), {"id": model_id}
        )
    assert assets.update(original.id, {"name": "renamed"}).name == "renamed"
    with pytest.raises(BusinessError):
        assets.create({"kind": "scene", "name": "new", "model_id": model_id})
    assert assets.list().total == 1
