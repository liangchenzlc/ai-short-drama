"""SQLite 行为验证；MySQL 外键、并发和真实回填另由隔离集成验证。"""

import copy
import json
from collections import UserDict
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    delete,
    event,
    func,
    insert,
    inspect,
    lambda_stmt,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.dialects import mysql, sqlite

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.db.session import session_factory
from short_drama.domain import (
    Asset,
    AssetVersion,
    AssetVersionMediaReference,
    MediaFile,
)
from short_drama.schemas.asset_version import snapshot_content_hash
from short_drama.service.asset_image_service import AssetImageService
from short_drama.service.asset_service import AssetService
from short_drama.service.asset_version_service import AssetVersionService, canonical_current
from short_drama.service.base import utcnow
from short_drama.service.generation_reference_service import GenerationReferenceService
from short_drama.service.project_service import ProjectService
from short_drama.service.resource_import_service import (
    ResourceImportService,
    _complete_import,
)
from short_drama.utils.snowflake import next_id
from tests.support.generation_fixtures import generation_session


@pytest.fixture
def session():
    with generation_session() as baseline:
        with session_factory(baseline.get_bind())() as value:
            value.info.update(baseline.info)
            value.info["test_project_id"] = int(
                ProjectService(value).create({"name": "Version history"}).id
            )
            yield value


def character(session, **changes):
    return AssetService(session).create(
        {"kind": "character", "name": "Hero", "description": "lead", **changes},
        project_id=session.info["test_project_id"],
    )


def versions(session, asset_id):
    with session.begin():
        return list(
            session.scalars(
                select(AssetVersion)
                .where(AssetVersion.asset_id == int(asset_id))
                .order_by(AssetVersion.version)
            )
        )


def media(session, *, owner=1, format_code="image/png", published=False, personal=False):
    now = utcnow()
    row = MediaFile(
        id=next_id(),
        scope_user_id=owner if owner != 1 or personal else None,
        project_id=session.info["test_project_id"] if owner == 1 and not personal else None,
        format_code=format_code,
        storage_locator=f"minio://image/{next_id()}.png",
        original_name="image.png",
        created_at=now,
        updated_at=now,
        width=2,
        height=3,
        published_at=now if published else None,
    )
    with session.begin():
        session.add(row)
    return row


def test_create_and_noop_replay_do_not_fabricate_history(session):
    item = character(session)
    first = versions(session, item.id)
    assert len(first) == 1 and first[0].version == first[0].source_row_version == 1
    assert first[0].snapshot_json["definition"] == {"description": "lead", "prompt": ""}
    AssetService(session).get(item.id)
    AssetService(session).update(item.id, {"row_version": "1", "description": "lead"})
    assert [row.id for row in versions(session, item.id)] == [first[0].id]


def test_reverting_content_is_new_version_and_state_only_is_not(session):
    image = media(session)
    item = character(session, media_id=str(image.id))
    AssetService(session).update(item.id, {"row_version": "1", "description": "changed"})
    AssetService(session).update(item.id, {"row_version": "2", "description": "lead"})
    history = versions(session, item.id)
    assert [row.version for row in history] == [1, 2, 3]
    assert history[0].content_hash == history[2].content_hash
    assert history[0].id != history[2].id
    with session.begin():
        asset = session.get(Asset, int(item.id))
        asset.state = "confirmed"
        asset.row_version += 1
        session.flush()
        AssetVersionService(session).append_locked(asset)
        AssetVersionService(session).verify_locked(asset)
    assert len(versions(session, item.id)) == 3


def test_noncharacter_paths_never_create_version(session):
    item = AssetService(session).create(
        {"kind": "prop", "name": "Umbrella"}, project_id=session.info["test_project_id"]
    )
    AssetService(session).update(item.id, {"row_version": "1", "prompt": "red"})
    assert versions(session, item.id) == []
    with session.begin():
        assert session.get(Asset, int(item.id)).current_version_id is None


def test_hash_preserves_array_order_origin_and_extensions_excludes_audit(session):
    item = character(session, tags=["a", "b"])
    snapshot = versions(session, item.id)[0].snapshot_json
    modified = copy.deepcopy(snapshot)
    modified["source_audit"] = {"different_author": "3", "state": "confirmed"}
    assert snapshot_content_hash(modified) == snapshot_content_hash(snapshot)
    modified["asset"]["tags"].reverse()
    assert snapshot_content_hash(modified) != snapshot_content_hash(snapshot)
    modified = copy.deepcopy(snapshot)
    modified["definition_origin"] = "structured"
    assert snapshot_content_hash(modified) != snapshot_content_hash(snapshot)


def test_structured_definition_empty_object_and_multiview_survive_flat_edit(session):
    item = character(session)
    front, side = media(session), media(session)
    with session.begin():
        asset = session.get(Asset, int(item.id))
        asset.media_id = front.id
        asset.row_version += 1
        session.flush()
        snapshot = canonical_current(asset)
        snapshot["definition_origin"] = "structured"
        snapshot["definition"] = {"extensions": {"unknown": ["x", {}]}, "text_id": str(side.id)}
        snapshot["representations"] = [
            {"role": "side", "media_id": str(side.id)},
            {"role": "front", "media_id": str(front.id)},
        ]
        AssetVersionService(session).append_locked(asset, snapshot=snapshot)
    AssetService(session).update(item.id, {"row_version": "2", "name": "Renamed"})
    current = versions(session, item.id)[-1].snapshot_json
    assert current["definition"] == snapshot["definition"]
    assert current["representations"] == snapshot["representations"]
    with session.begin():
        asset = session.get(Asset, int(item.id))
        empty = canonical_current(asset, previous=current)
        empty["definition"] = {}
        AssetVersionService(session).append_locked(asset, snapshot=empty)
    AssetService(session).update(item.id, {"row_version": "3", "prompt": "new"})
    assert versions(session, item.id)[-1].snapshot_json["definition"] == {}


def test_primary_replacement_preserves_multiview_and_history_media_slots(session):
    item = character(session)
    first, second, side = media(session), media(session), media(session)
    with session.begin():
        asset = session.get(Asset, int(item.id))
        asset.media_id, asset.row_version = first.id, 2
        session.flush()
        snapshot = canonical_current(asset)
        snapshot["representations"].append({"role": "side", "media_id": str(side.id)})
        AssetVersionService(session).append_locked(asset, snapshot=snapshot)
    AssetService(session).update(item.id, {"row_version": "2", "media_id": str(second.id)})
    history = versions(session, item.id)
    assert history[-1].snapshot_json["representations"] == [
        {"role": "primary", "media_id": str(second.id)},
        {"role": "side", "media_id": str(side.id)},
    ]
    with session.begin():
        assert (
            session.scalar(
                select(func.count())
                .select_from(AssetVersionMediaReference)
                .where(AssetVersionMediaReference.media_id == first.id)
            )
            == 1
        )


@pytest.mark.parametrize("system", [False, True])
@pytest.mark.parametrize(
    "operation", ["update", "delete", "bulk_update", "bulk_delete", "core_insert"]
)
def test_history_is_immutable_even_in_system_session(session, system, operation):
    item = character(session)
    history = versions(session, item.id)[0]
    if system:
        session.info.pop("legacy_user_id")
    with pytest.raises(WorkflowError):
        with session.begin():
            row = session.get(AssetVersion, history.id)
            if operation == "update":
                row.snapshot_json = {**row.snapshot_json, "source_audit": {"rewritten": True}}
                session.flush()
            elif operation == "delete":
                session.delete(row)
                session.flush()
            elif operation == "bulk_update":
                session.execute(update(AssetVersion).values(version=99))
            elif operation == "bulk_delete":
                session.execute(delete(AssetVersion.__table__))
            else:
                session.execute(insert(AssetVersion).values(id=next_id()))


@pytest.mark.parametrize("system", [False, True])
def test_pointer_orm_and_bulk_changes_are_guarded(session, system):
    item = character(session)
    other = character(session, name="Other")
    other_version = versions(session, other.id)[0]
    if system:
        session.info.pop("legacy_user_id")
    with pytest.raises(WorkflowError):
        with session.begin():
            session.get(Asset, int(item.id)).current_version_id = other_version.id
            session.flush()
    with pytest.raises(WorkflowError):
        with session.begin():
            session.execute(update(Asset).values(current_version_id=None))


def test_wrong_scope_or_type_rolls_back_edit_and_version_together(session):
    item = character(session)
    audio = media(session, format_code="audio/wav")
    with pytest.raises(WorkflowError):
        with session.begin():
            asset = session.get(Asset, int(item.id))
            asset.media_id, asset.row_version = audio.id, 2
            session.flush()
            AssetVersionService(session).append_locked(asset)
    assert len(versions(session, item.id)) == 1
    with session.begin():
        asset = session.get(Asset, int(item.id))
        assert asset.media_id is None and asset.row_version == 1
    foreign = media(session, owner=2)
    with pytest.raises(NotFound):
        AssetService(session).update(item.id, {"row_version": "1", "media_id": str(foreign.id)})
    assert len(versions(session, item.id)) == 1


def test_candidate_and_repeated_adoption_have_exactly_one_content_append(session):
    item = character(session)
    image = media(session, published=True)
    storage = SimpleNamespace(
        upload=lambda *_args, **_kwargs: None,
        download_url=lambda locator: "https://signed.test/image",
    )
    service = AssetImageService(session, SimpleNamespace(), storage)
    service.add_candidate(item.id, image.id)
    assert len(versions(session, item.id)) == 1
    result = service.confirm(
        item.id, {"row_version": "1", "media_id": str(image.id), "expected_media_id": None}
    )
    assert result.row_version == 2
    assert len(versions(session, item.id)) == 2
    service.confirm(
        item.id, {"row_version": "2", "media_id": str(image.id), "expected_media_id": str(image.id)}
    )
    assert len(versions(session, item.id)) == 2


def test_generation_reference_dedup_and_remove_append_only_real_changes(session):
    item = character(session)
    storage = SimpleNamespace(
        upload=lambda *_args, **_kwargs: None,
        download_url=lambda locator: "https://signed.test/reference",
    )
    inspected = SimpleNamespace(
        content_type="image/png", checksum_sha256="a" * 64, byte_size=6, width=2, height=3
    )
    stored = SimpleNamespace(storage_locator="minio://image/reference.png")
    first = GenerationReferenceService(session, SimpleNamespace(), storage, "asset", "1")
    output, created = first._persist_upload(int(item.id), inspected, stored, "ref.png")
    assert created and len(versions(session, item.id)) == 2
    second = GenerationReferenceService(session, SimpleNamespace(), storage, "asset", "2")
    _, created = second._persist_upload(int(item.id), inspected, stored, "same.png")
    assert created is False and len(versions(session, item.id)) == 2
    second.remove_reference(item.id, output["items"][0]["media_id"])
    assert len(versions(session, item.id)) == 3
    third = GenerationReferenceService(session, SimpleNamespace(), storage, "asset", "3")
    third.remove_reference(item.id, output["items"][0]["media_id"])
    assert len(versions(session, item.id)) == 3


def test_readonly_current_version_refuses_unversioned_legacy_without_writing(session):
    now = utcnow()
    with session.begin():
        legacy = Asset(
            id=next_id(),
            project_id=session.info["test_project_id"],
            position=1,
            kind="character",
            name="Legacy",
            created_at=now,
            updated_at=now,
        )
        session.add(legacy)
        session.flush()
        with pytest.raises(Conflict):
            AssetVersionService(session).current_snapshot_locked(legacy)
        assert legacy.current_version_id is None
    assert versions(session, legacy.id) == []


@pytest.mark.parametrize("operation", ["orm_update", "orm_delete", "bulk_update", "bulk_delete"])
def test_media_slots_are_immutable_without_actor(session, operation):
    image = media(session)
    item = character(session, media_id=str(image.id))
    session.info.pop("legacy_user_id")
    with pytest.raises(WorkflowError):
        with session.begin():
            slot = session.scalar(
                select(AssetVersionMediaReference).where(
                    AssetVersionMediaReference.asset_id == int(item.id)
                )
            )
            if operation == "orm_update":
                slot.role = "back"
                session.flush()
            elif operation == "orm_delete":
                session.delete(slot)
                session.flush()
            elif operation == "bulk_update":
                session.execute(update(AssetVersionMediaReference).values(role="back"))
            else:
                session.execute(delete(AssetVersionMediaReference))


@pytest.mark.parametrize(
    "operation", ["raw_history", "raw_pointer", "bulk_parameters", "bulk_insert"]
)
def test_direct_sql_and_parameter_bulk_pointer_cannot_bypass_system_guards(session, operation):
    item = character(session)
    session.info.pop("legacy_user_id")
    with pytest.raises(WorkflowError):
        with session.begin():
            if operation == "raw_history":
                session.execute(text("DELETE FROM asset_versions"))
            elif operation == "raw_pointer":
                session.execute(text("UPDATE assets SET current_version_id=NULL"))
            elif operation == "bulk_parameters":
                session.execute(update(Asset), [{"id": int(item.id), "current_version_id": None}])
            else:
                session.execute(insert(Asset).values(current_version_id=None))


def test_current_backfill_is_explicit_reentrant_and_does_not_invent_old_versions(session):
    from scripts.backfill_asset_versions import backfill, verify

    with session.begin():
        for ordinal in range(4):
            session.add(
                Asset(
                    id=next_id(),
                    kind="character",
                    name=f"Legacy {ordinal}",
                    project_id=session.info["test_project_id"],
                    position=ordinal + 1,
                    row_version=10,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    created_by=None,
                )
            )
    session.info.pop("legacy_user_id")
    with session.begin():
        dry = backfill(session)
        assert dry["counts"] == {"characters": 4, "would_create": 4}
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == 0
    with session.begin():
        result = backfill(session, apply=True)
        assert result["counts"] == {"characters": 4, "created": 4}
        assert result["failures"] == []
    with session.begin():
        replay = backfill(session, apply=True)
        assert replay["counts"] == {"characters": 4, "unchanged": 4}
        assert verify(session)["failures"] == []
        history = list(session.scalars(select(AssetVersion)))
        assert len(history) == 4
        assert all(row.version == 1 and row.source_row_version == 10 for row in history)
        assert all(row.created_by is None for row in history)


def test_backfill_missing_media_is_reported_without_pointer_or_false_history(session):
    from scripts.backfill_asset_versions import backfill

    with session.begin():
        legacy = Asset(
            id=next_id(),
            kind="character",
            name="Missing reference",
            project_id=session.info["test_project_id"],
            position=1,
            reference_media_ids=["999"],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(legacy)
    session.info.pop("legacy_user_id")
    with session.begin():
        result = backfill(session, apply=True)
        assert result["counts"] == {"characters": 1, "failed": 1}
        assert result["failures"] == [{"asset_id": str(legacy.id), "code": "missing_media"}]
        assert legacy.current_version_id is None
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == 0


@pytest.mark.parametrize("published_at", [None, datetime(2026, 10, 11, 2, 39, 5, 441710)])
def test_media_import_freezes_publication_time_and_preserves_metadata(session, published_at):
    from short_drama.core.identity import ActorContext
    from short_drama.domain import ResourceImport

    source = media(session, personal=True)
    with session.begin():
        source.published_at = published_at
        source.byte_size, source.checksum_sha256 = 4, "b" * 64
    project = ProjectService(session).create({"name": "Target"})
    session.info["actor"] = ActorContext(
        user_id=1,
        username="legacy_fixture",
        email="legacy@example.test",
        verified=True,
        session_id=1,
        csrf_hash="test-only",
        request_id="media-publication-import",
    )
    receipt = ResourceImportService(session).create(
        project.id, "media", str(source.id), "media-copy"
    )
    session.info.pop("actor")
    with session.begin():
        job = session.get(ResourceImport, int(receipt["id"]))
        snapshot = json.loads(json.dumps(job.snapshot, allow_nan=False))
        expected = "2026-10-11T02:39:05.441710Z" if published_at is not None else None
        assert snapshot["resource"]["published_at"] == expected
        assert snapshot["media"][0]["published_at"] == expected
        entry = snapshot["media"][0]
        assert type(entry["id"]) is int and entry["id"] == source.id > 2**53
        assert type(entry["copy_id"]) is int
        assert (entry["byte_size"], entry["width"], entry["height"]) == (4, 2, 3)
        assert entry["checksum_sha256"] == "b" * 64
        copies = {
            entry["id"]: (
                entry["copy_id"],
                f"minio://image/import/{entry['copy_id']}",
                {k: v for k, v in entry.items() if k not in {"id", "copy_id", "storage_locator"}},
            )
        }
        before_copy = utcnow()
        _complete_import(session, job, snapshot, copies)
        target = session.get(MediaFile, job.result_id)
        assert before_copy <= target.published_at <= utcnow()
        assert target.published_at != published_at
        assert target.id != source.id and target.storage_locator != source.storage_locator
        assert (target.byte_size, target.width, target.height) == (4, 2, 3)
        assert target.checksum_sha256 == "b" * 64


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (datetime(2026, 10, 11, 2, 39, 5, 441710), "2026-10-11T02:39:05.441710Z"),
        (datetime(2026, 10, 11, 2, 39, 5, 441710, tzinfo=UTC), "2026-10-11T02:39:05.441710Z"),
        (
            datetime(2026, 10, 11, 10, 39, 5, 441710, tzinfo=timezone(timedelta(hours=8))),
            "2026-10-11T02:39:05.441710Z",
        ),
    ],
)
def test_publication_json_preserves_utc_instant_microseconds_and_null(value, expected):
    from short_drama.service.resource_import_service import _publication_json

    encoded = _publication_json(value)
    assert json.loads(json.dumps(encoded, allow_nan=False)) == expected
    if value is not None:
        decoded = datetime.fromisoformat(encoded)
        source = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        assert decoded == source and decoded.microsecond == source.microsecond


def test_history_scope_follows_asset_and_private_owner_cannot_be_bypassed(session):
    from short_drama.core.identity import ActorContext

    item = character(session)
    session.info["actor"] = ActorContext(
        user_id=2,
        username="outsider",
        email="outsider@example.test",
        verified=True,
        session_id=2,
        csrf_hash="test-only",
        request_id="read-isolation",
    )
    with session.begin():
        assert (
            session.scalar(select(AssetVersion).where(AssetVersion.asset_id == int(item.id)))
            is None
        )
        assert session.scalar(select(Asset).where(Asset.id == int(item.id))) is None


def test_legacy_model_selection_updates_edit_number_without_content_version(session):
    from tests.support.generation_fixtures import config

    model = config(session)
    item = character(session)
    result = AssetService(session).update(item.id, {"row_version": "1", "model_id": str(model.id)})
    assert result.row_version == 2
    history = versions(session, item.id)
    assert len(history) == 1 and history[0].source_row_version == 1
    with session.begin():
        AssetVersionService(session).verify_locked(session.get(Asset, int(item.id)))


def test_factory_creates_session_with_legacy_bulk_guards(session):
    assert type(session).__name__ == "AppSession"


@pytest.mark.parametrize("system", [False, True])
@pytest.mark.parametrize("core", [False, True])
@pytest.mark.parametrize("operation", ["ordered_update", "multi_insert", "insert_from_select"])
def test_additional_dml_shapes_cannot_write_pointer(session, system, core, operation):
    item = character(session)
    if system:
        session.info.pop("legacy_user_id")
    table = Asset.__table__ if core else Asset
    with pytest.raises(WorkflowError) as rejected:
        with session.begin():
            if operation == "ordered_update":
                statement = (
                    update(table)
                    .where(Asset.id == int(item.id))
                    .ordered_values((Asset.current_version_id, None))
                )
            elif operation == "multi_insert":
                statement = insert(table).values(
                    [
                        {"id": next_id(), "kind": "character", "current_version_id": None},
                        {"id": next_id(), "kind": "character", "current_version_id": None},
                    ]
                )
            else:
                statement = insert(table).from_select(
                    ["id", "kind", "name", "current_version_id"],
                    select(
                        literal(next_id()), literal("character"), literal("Injected"), literal(None)
                    ),
                )
            session.execute(statement)
    assert rejected.value.code == "asset_version_pointer_guarded"


@pytest.mark.parametrize("system", [False, True])
@pytest.mark.parametrize("model", [Asset, AssetVersion, AssetVersionMediaReference])
@pytest.mark.parametrize(
    "operation", ["bulk_update_mappings", "bulk_insert_mappings", "bulk_save_objects"]
)
def test_legacy_bulk_apis_cannot_bypass_version_guards(session, system, model, operation):
    reference = media(session)
    item = character(session, media_id=str(reference.id))
    with session.begin():
        if model is Asset:
            entity = session.get(Asset, int(item.id))
            changes = {"current_version_id": None}
        elif model is AssetVersion:
            entity = session.scalar(select(model).where(model.asset_id == int(item.id)))
            changes = {"version": 99}
        else:
            entity = session.scalar(select(model).where(model.asset_id == int(item.id)))
            changes = {"role": "rewritten"}
        mapping = {
            attribute.key: getattr(entity, attribute.key)
            for attribute in inspect(model).column_attrs
        }
        session.expunge(entity)
    if system:
        session.info.pop("legacy_user_id")
    with pytest.raises(WorkflowError) as rejected:
        with session.begin():
            if operation == "bulk_update_mappings":
                session.bulk_update_mappings(
                    model, ({"id": entity.id, **changes} for _ in range(1))
                )
            elif operation == "bulk_insert_mappings":
                session.bulk_insert_mappings(model, [{**mapping, "id": next_id()}])
            else:
                for key, value in changes.items():
                    setattr(entity, key, value)
                session.bulk_save_objects(iter([entity]))
    assert rejected.value.code == "asset_version_bulk_forbidden"


@pytest.mark.parametrize(
    "operation", ["bulk_update_mappings", "bulk_insert_mappings", "bulk_save_objects"]
)
def test_legacy_bulk_preserves_unprotected_mapper_behavior(session, operation):
    from short_drama.domain.collaboration import User

    row = User(
        id=22,
        username="ordinary",
        display_name="Ordinary",
        email="ordinary@example.test",
        password_hash="test-only",
        status="active",
        email_verified_at=utcnow(),
        created_at=utcnow(),
    )
    with session.begin():
        if operation == "bulk_insert_mappings":
            session.bulk_insert_mappings(
                User,
                [
                    {
                        attribute.key: getattr(row, attribute.key)
                        for attribute in inspect(User).column_attrs
                    }
                ],
            )
        else:
            session.bulk_save_objects(iter([row]))
    if operation == "bulk_update_mappings":
        with session.begin():
            session.bulk_update_mappings(User, [{"id": 22, "display_name": "Updated"}])
    with session.begin():
        assert session.get(User, 22).display_name == (
            "Updated" if operation == "bulk_update_mappings" else "Ordinary"
        )


def test_mixed_legacy_bulk_validates_every_object_before_any_write(session):
    from short_drama.domain.collaboration import User

    item = character(session)
    with session.begin():
        entity = session.get(Asset, int(item.id))
        session.expunge(entity)
    entity.current_version_id = None
    row = User(
        id=23,
        username="mixed",
        display_name="Mixed",
        email="mixed@example.test",
        password_hash="test-only",
        status="active",
        email_verified_at=utcnow(),
        created_at=utcnow(),
    )
    with session.begin():
        with pytest.raises(WorkflowError):
            session.bulk_save_objects(iter([row, entity]))
        assert session.get(User, 23) is None


@pytest.mark.parametrize(
    "operation",
    [
        "mysql_upsert",
        "mysql_ordered_upsert",
        "sqlite_upsert",
        "alias_pointer",
        "alias_history",
        "wrapped_pointer",
        "multitable_pointer",
        "multitable_history",
        "multi_tuple_pointer",
        "lambda_pointer",
    ],
)
def test_obscured_dml_targets_are_guarded_before_binding(session, operation):
    assets = Asset.__table__
    if operation == "mysql_upsert":
        statement = (
            mysql.insert(assets).values(id=1).on_duplicate_key_update(current_version_id=None)
        )
    elif operation == "mysql_ordered_upsert":
        statement = (
            mysql.insert(assets)
            .values(id=1)
            .on_duplicate_key_update([("current_version_id", None)])
        )
    elif operation == "sqlite_upsert":
        statement = (
            sqlite.insert(assets)
            .values(id=1)
            .on_conflict_do_update(index_elements=[assets.c.id], set_={"current_version_id": None})
        )
    elif operation == "alias_pointer":
        statement = update(assets.alias("target")).values(current_version_id=None)
    elif operation == "alias_history":
        statement = delete(AssetVersion.__table__.alias("target"))
    elif operation == "wrapped_pointer":
        statement = select(Asset).from_statement(update(Asset).values(current_version_id=None))
    elif operation in {"multitable_pointer", "multitable_history"}:
        from short_drama.domain.collaboration import User

        target = (
            assets.c.current_version_id
            if operation == "multitable_pointer"
            else AssetVersion.__table__.c.version
        )
        statement = (
            update(User.__table__).where(User.id == target.table.c.id).values({target: None})
        )
    elif operation == "multi_tuple_pointer":
        statement = insert(assets).values([tuple(None for column in assets.c)])
    else:
        statement = lambda_stmt(lambda: update(Asset).values(current_version_id=None))

    class StopBeforeBinding(Exception):
        pass

    def stop_before_binding(state):
        raise StopBeforeBinding

    with session_factory(session.get_bind())() as probe:
        event.listen(probe, "do_orm_execute", stop_before_binding)
        with pytest.raises(WorkflowError) as rejected:
            probe.execute(statement)
    expected = (
        "asset_version_bulk_forbidden"
        if operation.endswith("history")
        else "asset_version_pointer_guarded"
    )
    assert rejected.value.code == expected


@pytest.mark.parametrize("operation", ["update", "ordered_update", "mysql_upsert", "sqlite_upsert"])
def test_nonpointer_asset_dml_remains_allowed_before_binding(session, operation):
    assets = Asset.__table__
    if operation == "update":
        statement = update(assets).values(name="Changed")
    elif operation == "ordered_update":
        statement = update(assets).ordered_values((assets.c.name, "Changed"))
    elif operation == "mysql_upsert":
        statement = mysql.insert(assets).values(id=1).on_duplicate_key_update(name="Changed")
    else:
        statement = (
            sqlite.insert(assets)
            .values(id=1)
            .on_conflict_do_update(index_elements=[assets.c.id], set_={"name": "Changed"})
        )

    class StopBeforeBinding(Exception):
        pass

    def stop_before_binding(state):
        raise StopBeforeBinding

    with session_factory(session.get_bind())() as probe:
        event.listen(probe, "do_orm_execute", stop_before_binding)
        with pytest.raises(StopBeforeBinding):
            probe.execute(statement)


@pytest.mark.parametrize("multiple", [False, True])
def test_mapping_parameters_cannot_write_pointer(session, multiple):
    parameters = UserDict({"id": 1, "current_version_id": None})

    class StopBeforeBinding(Exception):
        pass

    def stop_before_binding(state):
        raise StopBeforeBinding

    with session_factory(session.get_bind())() as probe:
        event.listen(probe, "do_orm_execute", stop_before_binding)
        with pytest.raises(WorkflowError) as rejected:
            probe.execute(update(Asset), [parameters] if multiple else parameters)
    assert rejected.value.code == "asset_version_pointer_guarded"


@pytest.mark.parametrize(
    "operation", ["asset_read", "history_join_read", "wrapped_read", "ordinary_update"]
)
def test_reads_and_unprotected_dml_remain_allowed_before_binding(session, operation):
    from short_drama.domain.collaboration import User

    if operation == "asset_read":
        statement = select(Asset)
    elif operation == "history_join_read":
        statement = select(Asset).join(AssetVersion, AssetVersion.asset_id == Asset.id)
    elif operation == "wrapped_read":
        statement = select(Asset).from_statement(select(Asset))
    else:
        statement = update(User).values(display_name="Changed")

    class StopBeforeBinding(Exception):
        pass

    def stop_before_binding(state):
        raise StopBeforeBinding

    with session_factory(session.get_bind())() as probe:
        event.listen(probe, "do_orm_execute", stop_before_binding)
        with pytest.raises(StopBeforeBinding):
            probe.execute(statement)


@pytest.mark.parametrize("method", ["bulk_update_mappings", "bulk_insert_mappings"])
@pytest.mark.parametrize("mapper_form", ["mapper", "alias"])
def test_legacy_bulk_guards_accept_sqlalchemy_mapper_forms(session, method, mapper_form):
    from sqlalchemy.orm import aliased

    target = inspect(Asset) if mapper_form == "mapper" else aliased(Asset)
    with pytest.raises(WorkflowError) as rejected:
        getattr(session, method)(target, [])
    assert rejected.value.code == "asset_version_bulk_forbidden"
