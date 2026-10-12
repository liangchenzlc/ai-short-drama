"""隔离 SQLite 夹具；系统 Session 仅建夹具，读取使用可信 Actor 的 AppSession。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from short_drama.core.identity import ActorContext
from short_drama.db.session import session_factory
from short_drama.domain import Asset, MediaFile, Project, ProjectAsset, ProjectCanvas
from short_drama.domain.collaboration import ProjectMember, User
from short_drama.service.asset_version_service import AssetVersionService, canonical_current
from short_drama.service.base import utcnow
from tests.unit import generation_fixtures
from tests.unit.generation_fixtures import generation_session

OWNING = 9100000000000001
SOURCE = 9100000000000002
ASSET = 9200000000000001
CANVAS = 9300000000000001
MEDIA = 9400000000000001
KEY = "catalog-fixture-key"


def actor(user_id=1):
    return ActorContext(user_id, "fixture", "fixture@example.test", True, 0, "", "catalog")


@pytest.fixture
def catalog_session(monkeypatch):
    monkeypatch.setattr(
        generation_fixtures,
        "create_engine",
        lambda _url: create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ),
    )
    with generation_session() as baseline:
        now = utcnow()
        with baseline.begin():
            baseline.add(
                User(
                    id=2,
                    username="catalog_member",
                    display_name="Member",
                    email="member@example.test",
                    password_hash="test-only",
                    status="active",
                    email_verified_at=now,
                    created_at=now,
                )
            )
            for identifier, mode in [(OWNING, "infinite_canvas"), (SOURCE, "standard")]:
                baseline.add(
                    Project(
                        id=identifier,
                        owner_user_id=1,
                        name=mode,
                        synopsis="",
                        style="",
                        aspect="16:9",
                        workspace_mode=mode,
                        row_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                baseline.add(
                    ProjectMember(
                        id=identifier + 10,
                        project_id=identifier,
                        user_id=2,
                        status="active",
                        joined_at=now,
                    )
                )
            baseline.add(
                ProjectCanvas(
                    id=CANVAS,
                    project_id=OWNING,
                    source_project_id=SOURCE,
                    source_key=KEY,
                    title="Catalog",
                    position=1,
                    schema_version=1,
                    row_version=7,
                    properties_json={"projectId": "untrusted-json"},
                    created_at=now,
                    updated_at=now,
                    created_by=1,
                    updated_by=1,
                )
            )
            baseline.add(
                MediaFile(
                    id=MEDIA,
                    project_id=SOURCE,
                    format_code="image/png",
                    storage_locator="minio://fixture-only/image.png",
                    original_name="view.png",
                    byte_size=12,
                    width=2,
                    height=3,
                    created_by=1,
                    published_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            asset = Asset(
                id=ASSET,
                project_id=SOURCE,
                kind="character",
                name="Hero",
                label="Lead",
                description="description",
                prompt="portrait",
                tags=["hero"],
                scene_time="",
                media_id=MEDIA,
                reference_media_ids=[MEDIA],
                row_version=3,
                state="confirmed",
                created_at=now,
                updated_at=now,
                created_by=1,
                updated_by=1,
            )
            baseline.add(asset)
            baseline.flush()
            snapshot = canonical_current(asset)
            snapshot["definition_origin"] = "structured"
            snapshot["definition"] = {"description": "full", "extensions": {"nested": [{}, 4]}}
            snapshot["representations"] = [
                {"role": "side", "media_id": str(MEDIA)},
                {"role": "front", "media_id": str(MEDIA)},
            ]
            AssetVersionService(baseline).append_locked(asset, snapshot=snapshot)
            baseline.add(ProjectAsset(id=ASSET + 10, project_id=SOURCE, asset_id=ASSET, position=2))
        with session_factory(baseline.get_bind())() as session:
            session.info["actor"] = actor()
            yield session, baseline


def seed_character(baseline, *, identifier, position, kind="character", linked=True):
    now = utcnow()
    with baseline.begin():
        asset = Asset(
            id=identifier,
            project_id=SOURCE,
            kind=kind,
            name=f"Item {identifier}",
            label="",
            description="",
            prompt="",
            tags=[],
            scene_time="",
            reference_media_ids=[],
            row_version=1,
            state="unconfirmed",
            created_at=now,
            updated_at=now,
        )
        baseline.add(asset)
        baseline.flush()
        AssetVersionService(baseline).append_locked(asset)
        if linked:
            baseline.add(
                ProjectAsset(
                    id=identifier + 10, project_id=SOURCE, asset_id=identifier, position=position
                )
            )
