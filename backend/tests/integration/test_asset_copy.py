from uuid import uuid4

import pytest
from sqlalchemy import text

from short_drama.core.exceptions import BusinessError
from short_drama.dao.ai_model_config_dao import AIModelConfigDAO
from short_drama.service.asset_service import AssetService
from short_drama.service.episode_asset_service import EpisodeAssetService
from short_drama.service.episode_service import EpisodeService
from short_drama.service.global_asset_service import GlobalAssetService
from short_drama.service.media_file_service import MediaFileService
from short_drama.service.project_asset_service import ProjectAssetService
from short_drama.service.project_service import ProjectService
from short_drama.service.shot_asset_service import ShotAssetService
from short_drama.service.shot_script_service import ShotScriptService

pytestmark = pytest.mark.integration


@pytest.fixture
def shared_asset(db_session):
    project = ProjectService(db_session).create({"name": "copy project", "aspect": "16:9"})
    episode = EpisodeService(db_session).create(
        {"project_id": project.id, "title": "copy episode", "aspect": "16:9", "position": 1}
    )
    shot = ShotScriptService(db_session).create({"episode_id": episode.id, "position": 1})
    media = MediaFileService(db_session).create(
        {"format_code": "image/png", "storage_locator": uuid4().hex}
    )
    assets = AssetService(db_session)
    original = assets.create(
        {"name": "original", "kind": "character", "media_id": media.id, "prompt": "shared prompt"}
    )
    services = {
        "global": GlobalAssetService(db_session),
        "project": ProjectAssetService(db_session),
        "episode": EpisodeAssetService(db_session),
    }
    links = {
        "global": services["global"].create({"asset_id": original.id, "position": 1}),
        "project": services["project"].create(
            {"project_id": project.id, "asset_id": original.id, "position": 1}
        ),
        "episode": services["episode"].create(
            {"episode_id": episode.id, "asset_id": original.id, "position": 1}
        ),
    }
    shot_link = ShotAssetService(db_session).create(
        {"shot_id": shot.id, "episode_id": episode.id, "asset_id": original.id}
    )
    return assets, original, services, links, shot_link


@pytest.mark.parametrize("library", ["global", "project", "episode"])
def test_copy_retargets_only_selected_library(db_session, shared_asset, library):
    assets, original, services, links, shot_link = shared_asset
    copied = assets.copy_for_library(library, links[library].id, {"name": "independent"})
    assert copied.id != original.id
    assert copied.name == "independent"
    assert copied.media_id == original.media_id
    assert copied.prompt == original.prompt
    assert copied.created_at is not None
    assert copied.created_by is None
    assert copied.updated_by is None
    assert assets.get(original.id) == original
    assert assets.list().total == 2
    for name, service in services.items():
        association = service.get(links[name].id)
        assert association.asset_id == (copied.id if name == library else original.id)
        assert association.position == links[name].position
        assert association.created_at == links[name].created_at
    assert ShotAssetService(db_session).get(shot_link.id).asset_id == original.id


def test_copy_invalid_media_leaves_asset_and_link_unchanged(db_session, shared_asset):
    assets, original, services, links, _ = shared_asset
    video = MediaFileService(db_session).create(
        {"format_code": "video/mp4", "storage_locator": uuid4().hex}
    )
    with pytest.raises(BusinessError):
        assets.copy_for_library("project", links["project"].id, {"media_id": video.id})
    assert assets.list().total == 1
    assert assets.get(original.id) == original
    assert services["project"].get(links["project"].id).asset_id == original.id


def test_copy_preserves_historical_disabled_model_but_rejects_new_selection(
    db_session, shared_asset
):
    assets, original, services, links, _ = shared_asset
    with db_session.begin():
        dao = AIModelConfigDAO(db_session)
        model = dao.create(
            {"service_type": "image", "name": "old", "model_key": "image-1", "provider": "test"}
        )
        another = dao.create(
            {"service_type": "image", "name": "other", "model_key": "image-2", "provider": "test"}
        )
        old_id, other_id = model.id, another.id
    assets.update(original.id, {"model_id": old_id})
    with db_session.begin():
        db_session.execute(text("UPDATE ai_model_configs SET enabled=0"))
    copied = assets.copy_for_library("episode", links["episode"].id, {"name": "historical copy"})
    assert copied.model_id == old_id
    with pytest.raises(BusinessError):
        assets.copy_for_library("project", links["project"].id, {"model_id": other_id})
    assert assets.list().total == 2
    assert services["project"].get(links["project"].id).asset_id == original.id
