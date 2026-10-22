from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from short_drama.core.exceptions import BusinessError, Conflict, NotFound
from short_drama.domain import Asset, AssetVersion, AssetVersionMediaReference

pytestmark = pytest.mark.integration


def services(session):
    from short_drama.service.asset_service import AssetService
    from short_drama.service.media_file_service import MediaFileService
    from short_drama.service.project_novel_service import ProjectNovelService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_asset_service import ShotAssetService
    from short_drama.service.shot_script_service import ShotScriptService

    return {
        cls.__name__.removesuffix("Service"): cls(session)
        for cls in (
            ProjectService,
            ProjectNovelService,
            AssetService,
            ShotScriptService,
            ShotAssetService,
            MediaFileService,
        )
    }


def seed(session):
    svc = services(session)
    project = svc["Project"].create_project({"name": "project"})
    session.info["resource_scope"] = (None, project.id)
    media = svc["MediaFile"].create({"format_code": "image/png", "storage_locator": uuid4().hex})
    asset = svc["Asset"].create(
        {"name": "asset", "kind": "scene", "media_id": media.id}, project_id=project.id
    )
    novel = svc["ProjectNovel"].create({"project_id": project.id, "content": "novel"})
    shot = svc["ShotScript"].create({"project_id": project.id, "position": 1, "script": "shot"})
    shot_asset = svc["ShotAsset"].create(
        {"project_id": project.id, "shot_id": shot.id, "asset_id": asset.id}
    )
    return svc, dict(
        Project=project,
        MediaFile=media,
        Asset=asset,
        ProjectNovel=novel,
        ShotScript=shot,
        ShotAsset=shot_asset,
    )


def test_crud_audit_pagination_and_restrict(db_session):
    svc, rows = seed(db_session)
    updates = {
        "Project": {"name": "changed"},
        "MediaFile": {"original_name": "changed"},
        "Asset": {"name": "changed"},
        "ProjectNovel": {"content": "changed"},
        "ShotScript": {"script": "changed"},
        "ShotAsset": {"asset_id": rows["Asset"].id},
    }
    for name, row in rows.items():
        service = svc[name]
        before = service.get(row.id)
        assert before.id == row.id
        assert isinstance(row.model_dump(mode="json")["id"], str)
        page = service.list(limit=1)
        assert len(page.items) == page.total == 1
        assert row.created_at is not None
        assert row.created_by is None
        version = {"row_version": before.row_version} if name == "Asset" else {}
        unchanged = service.update(row.id, version)
        if hasattr(row, "updated_at"):
            assert unchanged.updated_at == before.updated_at
        patch = {**updates[name], **version}
        changed = service.update(row.id, patch)
        for key, value in updates[name].items():
            assert getattr(changed, key) == value
        with pytest.raises(NotFound):
            service.get(2**64 - 1)
    with pytest.raises(Conflict):
        svc["Project"].delete(rows["Project"].id)
    assert svc["Project"].get(rows["Project"].id).name == "changed"
    for name in (
        "ShotAsset",
        "ShotScript",
        "ProjectNovel",
        "Asset",
        "MediaFile",
    ):
        svc[name].delete(rows[name].id)
        assert svc[name].list().total == 0
    # Internal replacement tombstones retain historical references and restrict parent deletion.
    from short_drama.domain import ShotScript

    with db_session.begin():
        archived = db_session.get(ShotScript, rows["ShotScript"].id)
        assert archived is not None
        assert archived.deleted_at is not None
    with pytest.raises(Conflict):
        svc["Project"].delete(rows["Project"].id)
    assert svc["Project"].get(rows["Project"].id).name == "changed"


def test_character_and_historical_media_delete_preserve_immutable_history(db_session):
    svc = services(db_session)
    project = svc["Project"].create_project({"name": "History"})
    db_session.info["resource_scope"] = (None, project.id)
    first, second = (
        svc["MediaFile"].create({"format_code": "image/png", "storage_locator": uuid4().hex})
        for _ in range(2)
    )
    original = svc["Asset"].create(
        {"name": "character", "kind": "character", "media_id": first.id}, project_id=project.id
    )
    current = svc["Asset"].update(
        original.id,
        {"row_version": original.row_version, "name": "changed", "media_id": second.id},
    )
    assert current.media_id == second.id and current.media_id != first.id

    def history():
        with db_session.begin():
            pointer = db_session.scalar(
                select(Asset.current_version_id).where(Asset.id == original.id)
            )
            versions = [
                dict(row)
                for row in db_session.execute(
                    select(AssetVersion.__table__)
                    .where(AssetVersion.asset_id == original.id)
                    .order_by(AssetVersion.version)
                ).mappings()
            ]
            references = [
                dict(row)
                for row in db_session.execute(
                    select(AssetVersionMediaReference.__table__)
                    .where(AssetVersionMediaReference.asset_id == original.id)
                    .order_by(AssetVersionMediaReference.id)
                ).mappings()
            ]
            return pointer, versions, references

    before = history()
    pointer, versions, references = before
    assert pointer == versions[-1]["id"]
    assert [row["version"] for row in versions] == [1, 2]
    assert [row["source_row_version"] for row in versions] == [1, 2]
    assert [row["snapshot_json"]["asset"]["media_id"] for row in versions] == [
        str(first.id),
        str(second.id),
    ]
    assert {(row["version_id"], row["media_id"]) for row in references} == {
        (versions[0]["id"], first.id),
        (versions[1]["id"], second.id),
    }
    for service, identifier in (
        (svc["Asset"], original.id),
        (svc["MediaFile"], first.id),
        (svc["MediaFile"], second.id),
    ):
        with pytest.raises(Conflict):
            service.delete(identifier)
        assert svc["Asset"].get(original.id) == current
        assert svc["MediaFile"].get(first.id) == first
        assert svc["MediaFile"].get(second.id) == second
        assert history() == before


def test_reorder_swaps_without_unique_conflict_and_rolls_back_invalid_ids(db_session):
    svc, rows = seed(db_session)
    second = svc["ShotScript"].create({"project_id": rows["Project"].id, "position": 2})
    ids = [second.id, rows["ShotScript"].id]
    result = svc["ShotScript"].reorder(rows["Project"].id, ids)
    assert [item.id for item in result] == ids
    assert [item.position for item in result] == [1, 2]
    with pytest.raises(BusinessError):
        svc["ShotScript"].reorder(rows["Project"].id, [second.id])
    assert [item.id for item in svc["ShotScript"].list().items] == ids


def test_project_open_does_not_modify_content_audit(db_session):
    svc, rows = seed(db_session)
    opened = svc["Project"].open(rows["Project"].id)
    assert opened.last_opened_at is not None
    assert opened.updated_at == rows["Project"].updated_at


def test_large_ids_serialize_without_precision_loss(db_session):
    large_id = 2**63 + 7
    with db_session.begin():
        db_session.execute(
            text("INSERT INTO projects (id,owner_user_id,name) VALUES (:id,1,'large')"),
            {"id": large_id},
        )
    row = services(db_session)["Project"].get(large_id)
    assert row.model_dump(mode="json")["id"] == str(large_id)


def test_media_and_ownership_validation(db_session):
    svc, rows = seed(db_session)
    video = svc["MediaFile"].create({"format_code": "video/mp4", "storage_locator": uuid4().hex})
    with pytest.raises(BusinessError):
        svc["Asset"].update(
            rows["Asset"].id,
            {
                "media_id": video.id,
                "row_version": rows["Asset"].row_version,
            },
        )
    assert svc["Asset"].get(rows["Asset"].id).media_id == rows["MediaFile"].id
    with pytest.raises(ValidationError):
        svc["Project"].update_project(rows["Project"].id, {"project_id": 77})
    with pytest.raises(ValidationError):
        svc["MediaFile"].update(video.id, {"storage_locator": "replacement"})


def test_dao_flush_does_not_commit_caller_transaction(db_session):
    from short_drama.dao.project_dao import ProjectDAO

    dao = ProjectDAO(db_session)
    with pytest.raises(RuntimeError, match="rollback"):
        with db_session.begin():
            row = dao.create({"name": "temporary"})
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
    svc = services(db_session)
    project = svc["Project"].create_project({"name": "Historical model"})
    assets = svc["Asset"]
    original = assets.create({"kind": "scene", "name": "first"}, project_id=project.id)
    original = assets.update(
        original.id, {"model_id": model_id, "row_version": original.row_version}
    )
    with db_session.begin():
        db_session.execute(
            text("UPDATE ai_model_configs SET enabled=0 WHERE id=:id"), {"id": model_id}
        )
    renamed = assets.update(original.id, {"name": "renamed", "row_version": original.row_version})
    assert renamed.name == "renamed"
    assert renamed.model_id == model_id
    another = assets.create({"kind": "scene", "name": "new"}, project_id=project.id)
    with pytest.raises(BusinessError):
        assets.update(another.id, {"model_id": model_id, "row_version": another.row_version})
    assert assets.get(another.id).model_id is None
    assert assets.list().total == 2
