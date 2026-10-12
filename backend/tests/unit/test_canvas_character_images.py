"""真实 ORM 资源与表示槽位的隔离合同；不下载对象或调用模型。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.domain import (
    Asset,
    AssetVersionMediaReference,
    CanvasBinaryResource,
    MediaFile,
)
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_catalog_service import (
    CanvasCharacterCatalogService,
)
from short_drama.service.canvas_character_image_service import (
    CanvasCharacterImageService,
)
from tests.catalog_fixtures import ASSET, KEY, MEDIA, OWNING, SOURCE, actor, seed_character

pytest_plugins = ("tests.catalog_fixtures",)
SETTINGS = SimpleNamespace(
    minio_image_bucket="fixture-only",
    minio_video_bucket="fixture-video",
    minio_audio_bucket="fixture-audio",
)


def version(session):
    return CanvasCharacterCatalogService(session).get(KEY, ASSET).item.current_version_id


def resolve(session, expected=None):
    return CanvasCharacterImageService(session, SETTINGS).get(
        KEY, ASSET, version(session) if expected is None else expected
    )


def test_native_resource_and_each_real_slot_are_preserved(catalog_session):
    session, _ = catalog_session
    expected = version(session)
    value = resolve(session, expected).model_dump(mode="json", by_alias=True)
    assert value["asset_id"] == str(ASSET) and value["version_id"] == str(expected)
    assert value["canvas_key"] == KEY and value["source_project_id"] == str(SOURCE)
    assert [(r["role"], r["ordinal"]) for r in value["images"]] == [
        ("side", 0),
        ("front", 1),
    ]
    assert value["images"][0]["reference_id"] != value["images"][1]["reference_id"]
    for image in value["images"]:
        assert image["media_id"] == image["resource"]["id"] == str(MEDIA)
        assert image["resource_key"] == f"resource:{MEDIA}"
        assert image["file_path"] == f"/api/v1/canvas-runtime/resources/{MEDIA}/file"
        assert image["requires_canvas_copy"] is True
        assert image["resource"]["mimeType"] == "image/png"
        assert image["resource"]["size"] == 12
        assert (image["resource"]["width"], image["resource"]["height"]) == (2, 3)
    assert "minio://" not in str(value) and "storage_locator" not in str(value)
    assert "definition" not in value and not session.in_transaction()


def test_repeated_get_is_stable_and_select_only(catalog_session):
    session, _ = catalog_session
    expected = version(session)
    sql = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        sql.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        first = resolve(session, expected)
        assert resolve(session, expected) == first
        assert sql and all(s.lstrip().upper().startswith("SELECT") for s in sql)
        assert not session.new and not session.dirty and not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_stale_version_is_explicit_and_does_not_silently_refresh(catalog_session):
    session, _ = catalog_session
    with pytest.raises(WorkflowError) as error:
        resolve(session, version(session) + 1)
    assert error.value.code == "canvas_character_version_changed"


def test_missing_actor_rejected_without_transaction(catalog_session):
    session, _ = catalog_session
    expected = version(session)
    session.info.pop("actor")
    with pytest.raises(WorkflowError) as error:
        resolve(session, expected)
    assert error.value.code == "authentication_required" and not session.in_transaction()


@pytest.mark.parametrize("project_id", [OWNING, SOURCE])
def test_revoking_either_scope_blocks_repeat_read(catalog_session, project_id):
    session, baseline = catalog_session
    session.info["actor"] = actor(2)
    expected = version(session)
    assert resolve(session, expected).images
    with baseline.begin():
        baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).status = "removed"
    with pytest.raises(NotFound):
        resolve(session, expected)


def test_adopted_version_does_not_invent_or_require_current_author(catalog_session):
    session, baseline = catalog_session
    expected = version(session)
    from short_drama.domain import AssetVersion

    with baseline.get_bind().begin() as connection:
        connection.execute(AssetVersion.__table__.update().values(created_by=None))
    session.info["actor"] = actor(2)
    assert resolve(session, expected).images[0].resource.user_id == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("created_by", None),
        ("created_at", None),
        ("updated_at", None),
        ("byte_size", None),
        ("format_code", "demo:image"),
        ("storage_locator", "minio://other/image.png"),
        ("storage_locator", "minio://fixture-only/../image.png"),
        ("storage_locator", "minio://fixture-only/image.png?signature=fixture"),
    ],
)
def test_invalid_native_resource_metadata_is_explicit(catalog_session, field, value):
    session, baseline = catalog_session
    expected = version(session)
    with baseline.get_bind().begin() as connection:
        connection.execute(
            MediaFile.__table__.update().where(MediaFile.id == MEDIA).values(**{field: value})
        )
    with pytest.raises(BusinessError):
        resolve(session, expected)
    assert not session.new and not session.dirty and not session.deleted


@pytest.mark.parametrize("damage", ["private", "scope", "missing", "slot", "drift"])
def test_real_media_slot_and_current_snapshot_must_still_match(catalog_session, damage):
    session, baseline = catalog_session
    expected = version(session)
    with baseline.get_bind().begin() as connection:
        if damage == "slot":
            connection.execute(AssetVersionMediaReference.__table__.delete())
        elif damage == "drift":
            connection.execute(
                Asset.__table__.update().where(Asset.id == ASSET).values(description="drift")
            )
        elif damage == "missing":
            connection.execute(MediaFile.__table__.delete().where(MediaFile.id == MEDIA))
        else:
            change = {"published_at": None} if damage == "private" else {"project_id": OWNING}
            connection.execute(
                MediaFile.__table__.update().where(MediaFile.id == MEDIA).values(**change)
            )
    session.info["actor"] = actor(2)
    with pytest.raises(BusinessError):
        resolve(session, expected)


def test_ambiguous_resource_namespace_is_not_chosen_by_first_match(catalog_session):
    session, baseline = catalog_session
    expected = version(session)
    now = utcnow()
    with baseline.begin():
        baseline.add(
            CanvasBinaryResource(
                id=MEDIA,
                project_id=SOURCE,
                resource_kind="file",
                mime_type="application/pdf",
                storage_locator="minio://fixture-only/file.pdf",
                original_name="file.pdf",
                byte_size=12,
                checksum_sha256="a" * 64,
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
                published_at=now,
            )
        )
    with pytest.raises(WorkflowError) as error:
        resolve(session, expected)
    assert error.value.code == "canvas_character_resource_ambiguous"


def test_empty_representations_is_valid_and_unlinked_asset_is_denied(catalog_session):
    session, baseline = catalog_session
    seed_character(baseline, identifier=ASSET + 100, position=1)
    item = CanvasCharacterCatalogService(session).get(KEY, ASSET + 100).item
    assert (
        CanvasCharacterImageService(session, SETTINGS)
        .get(KEY, ASSET + 100, item.current_version_id)
        .images
        == []
    )
    seed_character(baseline, identifier=ASSET + 200, position=1, linked=False)
    with pytest.raises(NotFound):
        CanvasCharacterImageService(session, SETTINGS).get(KEY, ASSET + 200, 1)
