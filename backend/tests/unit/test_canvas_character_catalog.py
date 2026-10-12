"""只读角色目录行为；不代表 MySQL、MinIO、供应商或 UI 验收。"""

import pytest
from sqlalchemy import event, select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import Asset, AssetVersion, MediaFile, Project, ProjectAsset, ProjectCanvas
from short_drama.domain.collaboration import ProjectMember
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from tests.catalog_fixtures import (
    ASSET,
    CANVAS,
    KEY,
    MEDIA,
    OWNING,
    SOURCE,
    actor,
    seed_character,
)

pytest_plugins = ("tests.catalog_fixtures",)


def test_actual_identity_structured_definition_slots_and_string_versions(catalog_session):
    session, _ = catalog_session
    read = CanvasCharacterCatalogService(session).list(KEY).model_dump(mode="json")
    assert read["canvas_key"] == KEY and read["canvas_row_version"] == "7"
    assert read["source_project_id"] == str(SOURCE)
    item = read["items"][0]
    assert item["asset_id"] == str(ASSET) and item["asset_row_version"] == "3"
    assert item["version"] == "1" and item["current_version_id"].isdigit()
    assert item["definition"] == {"description": "full", "extensions": {"nested": [{}, 4]}}
    assert item["definition_origin"] == "structured"
    assert [r["role"] for r in item["representations"]] == ["side", "front"]
    assert [r["ordinal"] for r in item["representations"]] == [0, 1]
    assert all(r["media_id"] == str(MEDIA) for r in item["representations"])
    assert all(r["mime_type"] == "image/png" for r in item["representations"])
    assert item["representations"][0]["reference_id"] != item["representations"][1]["reference_id"]
    assert "storage_locator" not in str(read) and "resourceId" not in str(read)
    assert "source_audit" not in str(read) and "model_id" not in str(read)
    assert not session.in_transaction()


def test_list_and_detail_emit_no_dml_or_session_mutations(catalog_session):
    session, _ = catalog_session
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        service = CanvasCharacterCatalogService(session)
        listing = service.list(KEY)
        detail = service.get(KEY, ASSET)
        assert detail.item == listing.items[0]
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert not session.new and not session.dirty and not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_members_read_published_roles_in_both_scopes(catalog_session):
    session, _ = catalog_session
    session.info["actor"] = actor(2)
    assert CanvasCharacterCatalogService(session).list(KEY).items[0].asset_id == ASSET


@pytest.mark.parametrize("project_id", [OWNING, SOURCE])
def test_losing_either_scope_denies_list_and_detail(catalog_session, project_id):
    session, baseline = catalog_session
    session.info["actor"] = actor(2)
    with baseline.begin():
        member = baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        member.status = "removed"
    for operation in [
        lambda: CanvasCharacterCatalogService(session).list(KEY),
        lambda: CanvasCharacterCatalogService(session).get(KEY, ASSET),
    ]:
        with pytest.raises(NotFound):
            operation()
    assert not session.in_transaction()


@pytest.mark.parametrize("target", ["canvas", "owning", "source"])
def test_archived_scope_is_not_readable(catalog_session, target):
    session, baseline = catalog_session
    with baseline.begin():
        row = (
            baseline.get(ProjectCanvas, CANVAS)
            if target == "canvas"
            else baseline.get(Project, OWNING if target == "owning" else SOURCE)
        )
        row.archived_at = utcnow()
    with pytest.raises(NotFound):
        CanvasCharacterCatalogService(session).list(KEY)


def test_missing_actor_rejected_before_any_query(catalog_session):
    session, _ = catalog_session
    session.info.pop("actor")
    with pytest.raises(WorkflowError) as rejected:
        CanvasCharacterCatalogService(session).list(KEY)
    assert rejected.value.code == "authentication_required"
    assert not session.in_transaction()


def test_null_trusted_binding_does_not_use_json_or_numeric_canvas_id(catalog_session):
    session, baseline = catalog_session
    with baseline.begin():
        baseline.get(ProjectCanvas, CANVAS).source_project_id = None
    with pytest.raises(WorkflowError) as rejected:
        CanvasCharacterCatalogService(session).list(KEY)
    assert rejected.value.code == "canvas_character_source_required"
    with pytest.raises(NotFound):
        CanvasCharacterCatalogService(session).list(str(CANVAS))


def test_empty_means_trusted_project_without_links(catalog_session):
    session, baseline = catalog_session
    with baseline.begin():
        baseline.delete(baseline.scalar(select(ProjectAsset)))
    assert CanvasCharacterCatalogService(session).list(KEY).items == []


def test_unlinked_character_and_linked_noncharacter_are_not_details(catalog_session):
    session, baseline = catalog_session
    seed_character(baseline, identifier=ASSET + 100, position=1, linked=False)
    seed_character(baseline, identifier=ASSET + 200, position=1, kind="prop")
    service = CanvasCharacterCatalogService(session)
    assert len(service.list(KEY).items) == 1
    for identifier in [ASSET + 100, ASSET + 200]:
        with pytest.raises(NotFound):
            service.get(KEY, identifier)


def test_real_relation_order_and_unillustrated_character_are_preserved(catalog_session):
    session, baseline = catalog_session
    seed_character(baseline, identifier=ASSET + 100, position=1)
    read = CanvasCharacterCatalogService(session).list(KEY)
    assert [item.asset_id for item in read.items] == [ASSET + 100, ASSET]
    assert read.items[0].representations == []


@pytest.mark.parametrize("damage", ["missing_pointer", "flat_drift"])
def test_get_does_not_repair_missing_or_drifted_version(catalog_session, damage):
    session, baseline = catalog_session
    # 仅在隔离夹具通过 Core 模拟库内损坏；正式实体写入门禁不被关闭。
    changes = (
        {"current_version_id": None}
        if damage == "missing_pointer"
        else {"description": "outside-version-write"}
    )
    with baseline.get_bind().begin() as connection:
        connection.execute(Asset.__table__.update().where(Asset.id == ASSET).values(**changes))
    with pytest.raises(Conflict):
        CanvasCharacterCatalogService(session).get(KEY, ASSET)
    with baseline.begin():
        baseline.expire_all()
        row = baseline.get(Asset, ASSET)
        assert (
            row.current_version_id is None
            if damage == "missing_pointer"
            else row.description == "outside-version-write"
        )


@pytest.mark.parametrize("damage", ["private", "scope", "missing", "type"])
def test_inaccessible_or_invalid_media_fails_explicitly(catalog_session, damage):
    session, baseline = catalog_session
    with baseline.begin():
        media = baseline.get(MediaFile, MEDIA)
        if damage == "private":
            media.published_at = None
        elif damage == "scope":
            media.project_id = OWNING
        elif damage == "missing":
            baseline.delete(media)
        else:
            media.format_code = "video/mp4"
    session.info["actor"] = actor(2)
    expected = WorkflowError if damage == "type" else NotFound
    with pytest.raises(expected):
        CanvasCharacterCatalogService(session).get(KEY, ASSET)


def test_private_asset_link_does_not_grant_visibility_or_empty_result(catalog_session):
    session, baseline = catalog_session
    with baseline.begin():
        asset = baseline.get(Asset, ASSET)
        asset.project_id = None
        asset.scope_user_id = 1
    session.info["actor"] = actor(2)
    with pytest.raises(NotFound):
        CanvasCharacterCatalogService(session).list(KEY)


@pytest.mark.parametrize("damage", ["owning_mode", "source_mode", "same_project"])
def test_invalid_project_modes_do_not_form_a_catalog(catalog_session, damage):
    session, baseline = catalog_session
    with baseline.begin():
        if damage == "owning_mode":
            baseline.get(Project, OWNING).workspace_mode = "standard"
        elif damage == "source_mode":
            baseline.get(Project, SOURCE).workspace_mode = "infinite_canvas"
        else:
            baseline.get(ProjectCanvas, CANVAS).source_project_id = OWNING
    with pytest.raises(WorkflowError) as rejected:
        CanvasCharacterCatalogService(session).list(KEY)
    assert rejected.value.code == "canvas_binding_invalid"


@pytest.mark.parametrize("damage", ["invalid_snapshot", "hash", "missing_version"])
def test_corrupt_version_has_explicit_error_and_is_not_repaired(catalog_session, damage):
    session, baseline = catalog_session
    with baseline.get_bind().begin() as connection:
        if damage == "invalid_snapshot":
            connection.execute(AssetVersion.__table__.update().values(snapshot_json={}))
        elif damage == "hash":
            connection.execute(AssetVersion.__table__.update().values(content_hash="0" * 64))
        else:
            connection.execute(AssetVersion.__table__.delete())
    with pytest.raises(Conflict):
        CanvasCharacterCatalogService(session).get(KEY, ASSET)
    assert not session.new and not session.dirty and not session.deleted


def test_binding_is_checked_again_after_project_locks(catalog_session, monkeypatch):
    session, _ = catalog_session
    service = CanvasCharacterCatalogService(session)
    original = service.canvas_dao.source_canvas

    def changed(source_key, *, lock=False):
        value = original(source_key, lock=lock)
        if lock:
            # 模拟锁定重读结果变化；不执行真实并发或数据写入。
            from types import SimpleNamespace

            return SimpleNamespace(id=value.id, project_id=value.project_id, source_project_id=None)
        return value

    monkeypatch.setattr(service.canvas_dao, "source_canvas", changed)
    with pytest.raises(WorkflowError) as rejected:
        service.list(KEY)
    assert rejected.value.code == "canvas_binding_changed"


def test_revoked_member_cannot_reuse_an_already_loaded_catalog(catalog_session):
    session, baseline = catalog_session
    session.info["actor"] = actor(2)
    service = CanvasCharacterCatalogService(session)
    assert service.list(KEY).items
    with baseline.begin():
        member = baseline.scalar(select(ProjectMember).where(ProjectMember.project_id == SOURCE))
        member.status = "removed"
    with pytest.raises(NotFound):
        service.list(KEY)
