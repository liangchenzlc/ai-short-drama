"""Expand/backfill/finalize against a disposable pre-identity MySQL schema."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker
from test_identity_collaboration import CopyStorage

from short_drama.core.config import Settings
from short_drama.db.readiness import assert_identity_ready
from short_drama.domain import (
    AGENT_TABLES,
    AIModelConfig,
    Asset,
    Base,
    GenerationBatchJob,
    GlobalAsset,
    MediaFile,
    Project,
    ProjectAsset,
)
from short_drama.domain.collaboration import User
from short_drama.service.base import utcnow

pytestmark = pytest.mark.integration
spec = importlib.util.spec_from_file_location(
    "collaboration_migration",
    Path(__file__).resolve().parents[2] / "scripts/collaboration_migration.py",
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (["ALTER TABLE assets ALTER CHECK ck_asset_scope NOT ENFORCED"], "scope constraints"),
        (
            [
                "ALTER TABLE assets DROP CHECK ck_asset_scope",
                "ALTER TABLE assets ADD CONSTRAINT ck_asset_scope CHECK (1=1)",
            ],
            "scope constraints",
        ),
        (
            [
                "ALTER TABLE episode_render_jobs DROP FOREIGN KEY "
                "fk_episode_render_jobs_initiated_by"
            ],
            "foreign keys",
        ),
        (["ALTER TABLE users DROP INDEX uk_users_email"], "uniqueness constraints"),
    ],
)
def test_readiness_rejects_disabled_or_changed_account_constraints(
    migration_mysql_engine,
    changes,
    message,
):
    settings = Settings(auth_enabled=True)
    assert_identity_ready(migration_mysql_engine, settings)
    with migration_mysql_engine.begin() as connection:
        for statement in changes:
            connection.exec_driver_sql(statement)
    with pytest.raises(RuntimeError, match=message):
        assert_identity_ready(migration_mysql_engine, settings)


@pytest.mark.parametrize("status", ["running", "paused", "needs_review"])
def test_migration_requires_batch_shutdown_even_without_active_child_tasks(
    migration_mysql_engine,
    status,
):
    factory = sessionmaker(migration_mysql_engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as session:
        session.add(Project(id=101, owner_user_id=1, name="Migration batch", aspect="16:9"))
        session.add(
            AIModelConfig(
                id=201,
                owner_user_id=1,
                service_type="image",
                name="Migration fixture",
                provider="fixture",
                model_key="fixture",
            )
        )
        session.flush()
        session.add(
            GenerationBatchJob(
                id=301,
                project_id=101,
                initiated_by=1,
                scene="asset_image",
                config_id=201,
                config_version=1,
                scope={"scope": {"project_id": "101"}},
                status=status,
                idempotency_key="migration-batch",
                request_hash="a" * 64,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    before = migration.precheck(migration_mysql_engine)
    assert before["active_generation_tasks"] == before["active_render_jobs"] == 0
    assert before["active_generation_batches"] == 1
    with pytest.raises(ValueError, match="cancel active generation/render/batch"):
        migration.backfill(factory, 1, CopyStorage(), Settings())
    with factory() as session:
        assert session.get(GenerationBatchJob, 301).status == status
        assert session.get(Project, 101).owner_user_id == 1


def original_schema(engine):
    """Remove only this feature's changes; retain existing production features."""
    fields = {
        "projects": {"owner_user_id", "row_version", "archived_at"},
        "ai_model_configs": {"owner_user_id"},
        "global_assets": {"user_id"},
        "episodes": {"row_version"},
        "episode_render_jobs": {"initiated_by"},
    }
    fields.update(
        {
            name: {"scope_user_id", "project_id"}
            | ({"initiated_by"} if name in {"async_tasks", "generation_batches"} else set())
            for name in migration.ROOTS
        }
    )
    with engine.begin() as connection:
        for name, columns in fields.items():
            inspector = inspect(connection)
            for fk in inspector.get_foreign_keys(name):
                if set(fk["constrained_columns"]) & columns:
                    connection.execute(
                        text(f"ALTER TABLE `{name}` DROP FOREIGN KEY `{fk['name']}`")
                    )
            for ck in inspector.get_check_constraints(name):
                if "scope" in ck["name"]:
                    connection.execute(text(f"ALTER TABLE `{name}` DROP CHECK `{ck['name']}`"))
            for index in inspector.get_indexes(name):
                if set(index["column_names"]) & columns:
                    connection.execute(text(f"ALTER TABLE `{name}` DROP INDEX `{index['name']}`"))
            for column in columns:
                connection.execute(text(f"ALTER TABLE `{name}` DROP COLUMN `{column}`"))
        # Agent依赖账号表；还原账号功能出现前的库时，先按外键顺序移除它们。
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in migration.IDENTITY | AGENT_TABLES:
                connection.execute(text(f"DROP TABLE `{table.name}`"))
        # The old global uniqueness must be replaced during finalize.
        connection.execute(
            text("ALTER TABLE global_assets ADD UNIQUE KEY uk_global_assets_position (position)")
        )
        connection.execute(
            text(
                "ALTER TABLE ai_model_configs ADD UNIQUE KEY "
                "uk_ai_default_service (default_service_type)"
            )
        )


def legacy_graph(engine):
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id,name,aspect) VALUES "
                "(101,'Legacy project A','16:9'),(102,'Legacy project B','16:9')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_files "
                "(id,format_code,storage_locator,original_name,byte_size) VALUES "
                "(201,'image/png','minio://image/personal.png','Actor',19)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO assets (id,kind,name,media_id,reference_media_ids) VALUES "
                "(301,'character','Original actor',201,JSON_ARRAY('201'))"
            )
        )
        connection.execute(
            text("INSERT INTO global_assets (id,asset_id,position) VALUES (401,301,1)")
        )
        connection.execute(
            text(
                "INSERT INTO project_assets (id,project_id,asset_id,position) VALUES "
                "(402,101,301,1),(403,102,301,1)"
            )
        )
        connection.execute(
            text("INSERT INTO asset_image_candidates (id,asset_id,media_id) VALUES (501,301,201)")
        )


def test_explicit_ownership_shared_graph_physical_copy_finalize_and_rerun(migration_mysql_engine):
    engine = migration_mysql_engine
    settings = Settings(auth_enabled=True)
    original_schema(engine)
    legacy_graph(engine)
    with pytest.raises(RuntimeError, match="schema incomplete"):
        assert_identity_ready(engine, settings)
    migration.expand(engine)
    migration.expand(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with pytest.raises(ValueError, match="Legacy owner"):
        migration.backfill(factory, 1)
    with factory.begin() as session:
        session.add(
            User(
                id=1,
                username="explicit_owner",
                display_name="Owner",
                email="explicit@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    # Creating an account alone must not take ownership of existing projects.
    with pytest.raises(RuntimeError, match="Historical ownership"):
        assert_identity_ready(engine, settings)
    storage = CopyStorage()
    result = migration.backfill(factory, 1, storage, settings)
    assert result == {"assets_copied": 2, "media_copied": 2}
    with factory() as session:
        assets = list(session.scalars(select(Asset).order_by(Asset.id)))
        assert len(assets) == 3
        assert len({asset.media_id for asset in assets}) == 3
        assert all(asset.reference_media_ids == [str(asset.media_id)] for asset in assets)
        assert session.scalar(select(GlobalAsset.asset_id)) == 301
        assert all(link.asset_id != 301 for link in session.scalars(select(ProjectAsset)))
        media = list(session.scalars(select(MediaFile)))
        assert len({row.storage_locator for row in media}) == 3
        assert session.scalar(text("SELECT COUNT(*) FROM asset_image_candidates")) == 3
    assert len(storage.objects) == 3
    assert migration.backfill(factory, 1, storage, settings) == {
        "assets_copied": 0,
        "media_copied": 0,
    }
    migration.finalize(engine)
    migration.finalize(engine)
    assert_identity_ready(engine, settings)
    assert migration.precheck(engine)["unresolved_ownership"] == {}


def test_copy_failure_rolls_back_graph_and_removes_orphan_objects(migration_mysql_engine):
    engine = migration_mysql_engine
    original_schema(engine)
    legacy_graph(engine)
    migration.expand(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as session:
        session.add(
            User(
                id=1,
                username="repair_owner",
                display_name="Owner",
                email="repair@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    storage = CopyStorage()
    storage.on_copy = lambda: (_ for _ in ()).throw(RuntimeError("Supplier unavailable"))
    with pytest.raises(RuntimeError, match="Supplier unavailable"):
        migration.backfill(factory, 1, storage, Settings())
    with factory() as session:
        assert session.scalar(text("SELECT owner_user_id FROM projects WHERE id=101")) is None
        assert session.scalar(text("SELECT COUNT(*) FROM assets")) == 1
    # A copy may fail after creating the destination; migration must clean it too.
    assert len(storage.objects) == 1
