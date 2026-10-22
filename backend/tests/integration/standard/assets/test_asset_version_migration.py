"""随机临时 MySQL 库中的结构与当前快照回填；候选阶段不执行真实连接。"""

import json
import re

import pytest
from sqlalchemy import func, select, text

from scripts.asset_version_migration import apply, inspect_schema, statements
from scripts.backfill_asset_versions import backfill, verify
from short_drama.db.session import AppSession
from short_drama.domain import Asset, AssetVersion, AssetVersionMediaReference

pytestmark = pytest.mark.integration


def _assert_disposable(connection) -> None:
    assert re.fullmatch(
        r"short_drama_[a-f0-9]{32}_test", connection.scalar(text("SELECT DATABASE()"))
    )


def _remove_version_schema(engine) -> None:
    with engine.begin() as connection:
        _assert_disposable(connection)
        # Explicit disposable-schema cleanup, never an application maintenance permission.
        for sql in (
            "UPDATE assets SET current_version_id = NULL",
            "ALTER TABLE assets DROP FOREIGN KEY fk_assets_current_version",
            "ALTER TABLE assets DROP CHECK ck_assets_character_version",
            "ALTER TABLE assets DROP INDEX idx_assets_current_version",
            "ALTER TABLE assets DROP COLUMN current_version_id",
            "DROP TABLE asset_version_media_references",
            "DROP TABLE asset_versions",
        ):
            connection.execute(text(sql))


def _seed_character(engine, *, reference_ids=(), media_id=None) -> None:
    with engine.begin() as connection:
        _assert_disposable(connection)
        connection.execute(
            text(
                "INSERT INTO projects (id,owner_user_id,name) VALUES (90001,1,'Version migration')"
            )
        )
        if media_id is not None:
            connection.execute(
                text(
                    "INSERT INTO media_files (id,project_id,format_code,storage_locator) "
                    "VALUES (:id,90001,'image/png',:locator)"
                ),
                {"id": media_id, "locator": f"minio://migration-test/{media_id}.png"},
            )
        connection.execute(
            text(
                "INSERT INTO assets "
                "(id,project_id,position,kind,name,description,prompt,row_version,"
                "media_id,reference_media_ids,created_at,updated_at,created_by,updated_by) "
                "VALUES (91001,90001,1,'character','existing role','existing description',"
                "'existing prompt',10,:media_id,:references,NULL,NULL,NULL,NULL)"
            ),
            {"media_id": media_id, "references": json.dumps(list(reference_ids))},
        )


@pytest.mark.parametrize(
    "partial", ["all_missing", "tables_only", "pointer_only", "pointer_index", "full"]
)
def test_schema_additive_partial_and_reentry(migration_mysql_engine, partial):
    engine = migration_mysql_engine
    _seed_character(engine)
    if partial != "full":
        _remove_version_schema(engine)
        with engine.begin() as connection:
            for kind, target, sql in statements():
                if partial == "tables_only" and kind == "table":
                    connection.execute(text(sql))
                elif partial in {"pointer_only", "pointer_index"} and (
                    target == "assets.current_version_id"
                    or partial == "pointer_index"
                    and target == "assets.idx_assets_current_version"
                ):
                    connection.execute(text(sql))
    with engine.connect() as connection:
        before = inspect_schema(connection)
        row_before = dict(
            connection.execute(text("SELECT * FROM assets WHERE id=91001")).mappings().one()
        )
    assert before["changed"] == []
    assert before["status"] == ("ready" if partial == "full" else "partial")
    result = apply(engine)
    assert result["status"] == "ready"
    assert bool(result["executed"]) == (partial != "full")
    assert apply(engine)["executed"] == []
    with engine.connect() as connection:
        migrated = dict(
            connection.execute(text("SELECT * FROM assets WHERE id=91001")).mappings().one()
        )
        assert migrated.pop("current_version_id") is None
        row_before.pop("current_version_id", None)
        assert migrated == row_before
        assert connection.scalar(text("SELECT COUNT(*) FROM asset_versions")) == 0


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "ALTER TABLE assets MODIFY COLUMN current_version_id BIGINT UNSIGNED NOT NULL",
            "assets.current_version_id:column",
        ),
        (
            "ALTER TABLE asset_versions ALTER CHECK ck_asset_versions_hash NOT ENFORCED",
            "asset_versions.ck_asset_versions_hash:check",
        ),
        (
            "CREATE INDEX unexpected_pointer ON assets (id,current_version_id)",
            "assets:extra_pointer_indexes",
        ),
        (
            "ALTER TABLE asset_versions MODIFY COLUMN content_hash VARCHAR(64) NOT NULL",
            "asset_versions.content_hash:column",
        ),
    ],
)
def test_unknown_drift_stops_before_additive_ddl(migration_mysql_engine, sql, expected):
    engine = migration_mysql_engine
    with engine.begin() as connection:
        _assert_disposable(connection)
        if expected == "assets.current_version_id:column":
            connection.execute(
                text("ALTER TABLE assets DROP FOREIGN KEY fk_assets_current_version")
            )
        connection.execute(text(sql))
        before = inspect_schema(connection)
    assert expected in before["changed"]
    if expected == "assets.current_version_id:column":
        assert "constraint:assets.fk_assets_current_version" in before["missing"]
    with pytest.raises(RuntimeError, match="结构漂移"):
        apply(engine)
    with engine.connect() as connection:
        assert inspect_schema(connection) == before


def _run_backfill(engine, *, write=False):
    with AppSession(engine, autoflush=False) as session, session.begin():
        return backfill(session, apply=write)


def test_backfill_current_edit_number_is_one_version_and_reentry_is_noop(migration_mysql_engine):
    engine = migration_mysql_engine
    _seed_character(engine, reference_ids=["91002"], media_id=91002)
    preview = _run_backfill(engine)
    assert preview["counts"] == {"characters": 1, "would_create": 1}
    assert preview["applied"] is False and preview["failures"] == []
    with AppSession(engine) as session, session.begin():
        assert session.get(Asset, 91001).current_version_id is None
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == 0
    result = _run_backfill(engine, write=True)
    assert result["counts"] == {"characters": 1, "created": 1}
    with AppSession(engine) as session, session.begin():
        version = session.scalars(select(AssetVersion)).one()
        assert version.version == 1 and version.source_row_version == 10
        assert version.created_by is None and version.created_at is not None
        assert version.snapshot_json["definition_origin"] == "legacy_current"
        assert version.snapshot_json["definition"] == {
            "description": "existing description",
            "prompt": "existing prompt",
        }
        assert version.snapshot_json["source_audit"]["source_created_at"] is None
        assert session.get(Asset, 91001).current_version_id == version.id
        references = list(session.scalars(select(AssetVersionMediaReference)))
        assert {(row.purpose, row.role, row.ordinal, row.media_id) for row in references} == {
            ("representation", "primary", 0, 91002),
            ("generation_reference", "", 0, 91002),
        }
        original_id = version.id
    replay = _run_backfill(engine, write=True)
    assert replay["counts"] == {"characters": 1, "unchanged": 1}
    with AppSession(engine) as session, session.begin():
        checked = verify(session)
        assert checked["failures"] == []
        assert checked["counts"] == {
            "characters": 1,
            "media_references": 2,
            "verified": 1,
            "versions": 1,
        }
        assert session.scalars(select(AssetVersion.id)).one() == original_id
        assert session.get(Asset, 91001).project_id == 90001


def test_backfill_missing_reference_fails_without_partial_version(migration_mysql_engine):
    engine = migration_mysql_engine
    _seed_character(engine, reference_ids=["99999"])
    for write in (False, True):
        result = _run_backfill(engine, write=write)
        assert result["counts"] == {"characters": 1, "failed": 1}
        assert result["failures"] == [{"asset_id": "91001", "code": "missing_media"}]
        with AppSession(engine) as session, session.begin():
            assert session.get(Asset, 91001).current_version_id is None
            assert session.scalar(select(func.count()).select_from(AssetVersion)) == 0
            assert session.scalar(select(func.count()).select_from(AssetVersionMediaReference)) == 0


def test_text_only_backfill_preserves_empty_media_and_verify_never_writes(migration_mysql_engine):
    engine = migration_mysql_engine
    _seed_character(engine)
    with AppSession(engine) as session, session.begin():
        checked = verify(session)
        assert checked["failures"]
        assert session.scalar(select(func.count()).select_from(AssetVersion)) == 0
    assert _run_backfill(engine, write=True)["failures"] == []
    with AppSession(engine) as session, session.begin():
        assert verify(session)["failures"] == []
        version = session.scalars(select(AssetVersion)).one()
        assert version.snapshot_json["representations"] == []
        assert version.snapshot_json["generation_reference_ids"] == []
        assert session.scalar(select(func.count()).select_from(AssetVersionMediaReference)) == 0
