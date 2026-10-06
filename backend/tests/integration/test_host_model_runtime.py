"""隔离真实 MySQL 验证统一模型、旧目录回填和凭据/版本保护。"""

import base64
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_canvas_workspace import actor

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import AIModelConfig, CanvasChannelModel, CanvasModelCatalog
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.base import utcnow
from short_drama.service.canvas_model_catalog_service import CanvasModelCatalogService
from short_drama.service.canvas_workspace_service import CanvasWorkspaceService

pytestmark = pytest.mark.integration
CIPHER = KeyCipher(base64.b64encode(b"h" * 32).decode())
PROFILE = {
    "version": 1,
    "api_format": "openai",
    "protocol": "chat-completion",
    "capability_config": {
        "text": {"streaming": False, "references": {"maxImages": 2, "maxImageBytes": 1048576}}
    },
}


def migration_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "host_model_runtime_migration.py"
    spec = importlib.util.spec_from_file_location("host_model_runtime_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy_catalog(session, *, broken_second=False, corrupt_credentials=False):
    now = utcnow()
    profiles = [
        {
            "model": "legacy-model",
            "capability": "text",
            "protocol": "chat-completion",
            "capabilityConfig": deepcopy(PROFILE["capability_config"]),
        }
    ]
    if broken_second:
        profiles.append(
            {
                **profiles[0],
                "model": "broken-model",
                "capabilityConfig": {"text": {"references": {"maxImages": "invalid"}}},
            }
        )
    with session.begin():
        for index, profile in enumerate(profiles):
            session.add(
                AIModelConfig(
                    id=101 + index,
                    owner_user_id=1,
                    service_type="text",
                    name="旧配置",
                    provider="openai",
                    model_key=profile["model"],
                    base_url="https://model.example/v1",
                    apikey=CIPHER.encrypt("current-host-key"),
                    enabled=1,
                    is_deleted=0,
                    is_default=int(index == 0),
                    row_version=7,
                    created_at=now,
                    updated_at=now,
                    created_by=1,
                    updated_by=1,
                )
            )
        session.flush()
        for index, profile in enumerate(profiles):
            session.add(
                CanvasChannelModel(
                    id=201 + index,
                    user_id=1,
                    channel_key="old-channel",
                    model_key=profile["model"],
                    model_config_id=101 + index,
                    created_at=now,
                    updated_at=now,
                    created_by=1,
                    updated_by=1,
                )
            )
        session.add(
            CanvasModelCatalog(
                id=301,
                user_id=1,
                channels_json=[
                    {
                        "id": "old-channel",
                        "name": "旧渠道",
                        "apiFormat": "openai",
                        "baseUrl": "https://model.example/v1",
                        "models": [p["model"] for p in profiles],
                        "modelProfiles": profiles,
                        "enabled": True,
                    }
                ],
                credentials_cipher="broken"
                if corrupt_credentials
                else CIPHER.encrypt(
                    json.dumps(
                        {
                            "old-channel": {
                                "apiKey": "old-catalog-key",
                                "secretKey": "stored-second-key",
                                "headers": {"X-Provider-Key": "private-header"},
                            }
                        }
                    )
                ),
                created_at=now,
                updated_at=now,
                created_by=1,
                updated_by=1,
            )
        )


def test_runtime_ddl_additive_reentry_and_exact_type_check(migration_mysql_engine):
    migration = migration_module()
    engine = migration_mysql_engine
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE ai_model_configs DROP COLUMN runtime_profile")
        connection.exec_driver_sql(
            "ALTER TABLE ai_model_configs DROP COLUMN runtime_credentials_cipher"
        )
        connection.exec_driver_sql(
            "ALTER TABLE canvas_channel_models DROP COLUMN runtime_migrated_at"
        )
        assert migration.inspect_runtime_schema(connection)["missing"] == [
            "ai_model_configs.runtime_profile",
            "ai_model_configs.runtime_credentials_cipher",
            "canvas_channel_models.runtime_migrated_at",
        ]
    executed = []

    def collect(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith(
            (
                "ALTER TABLE ai_model_configs ADD COLUMN",
                "ALTER TABLE canvas_channel_models ADD COLUMN",
            )
        ):
            executed.append(statement)

    event.listen(engine, "before_cursor_execute", collect)
    try:
        assert migration.apply_schema(engine)["status"] == "ready"
        assert migration.apply_schema(engine)["status"] == "ready"
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert len(executed) == 3
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE ai_model_configs MODIFY runtime_profile TEXT NULL")
    with pytest.raises(RuntimeError, match="不兼容"):
        migration.apply_schema(engine)


def test_catalog_backfill_keeps_identity_current_key_default_alias_and_is_noop(db_session):
    legacy_catalog(db_session)
    engine = db_session.get_bind()
    migration = migration_module()
    report = migration.backfill_catalogs(engine, CIPHER)
    assert report["migrated"] == 1 and report["api_key_difference_ids"] == ["101"]
    with Session(engine) as session, session.begin():
        config = session.get(AIModelConfig, 101)
        assert config.row_version == 8 and config.is_default == 1
        assert config.owner_user_id == 1 and CIPHER.decrypt(config.apikey) == "current-host-key"
        assert config.runtime_profile == PROFILE
        assert "private-header" not in config.runtime_credentials_cipher
        encrypted = config.runtime_credentials_cipher
        extra = json.loads(CIPHER.decrypt(encrypted))
        assert extra == {
            "secretKey": "stored-second-key",
            "headers": {"X-Provider-Key": "private-header"},
        }
        assert session.scalar(select(CanvasChannelModel.model_config_id)) == 101
        catalog = session.get(CanvasModelCatalog, 301)
        assert "private-header" not in json.dumps(catalog.channels_json)
        config.runtime_profile = {**PROFILE, "default_options": {"temperature": 0.4}}
        config.row_version = 9
    assert migration.backfill_catalogs(engine, CIPHER)["migrated"] == 0
    with Session(engine) as session, session.begin():
        config = session.get(AIModelConfig, 101)
        assert config.row_version == 9 and config.runtime_credentials_cipher == encrypted
        assert config.runtime_profile["default_options"] == {"temperature": 0.4}
        assert session.scalar(select(CanvasChannelModel.runtime_migrated_at)) is not None
        config.runtime_profile = None
        config.row_version = 10
    assert migration.backfill_catalogs(engine, CIPHER)["migrated"] == 0
    with Session(engine) as session, session.begin():
        config = session.get(AIModelConfig, 101)
        assert config.runtime_profile is None and config.row_version == 10


@pytest.mark.parametrize("options", [{"broken_second": True}, {"corrupt_credentials": True}])
def test_backfill_invalid_catalog_rolls_back_every_row_without_logging_secrets(
    db_session, options, caplog
):
    legacy_catalog(db_session, **options)
    engine = db_session.get_bind()
    with pytest.raises(RuntimeError, match="回填已回滚"):
        migration_module().backfill_catalogs(engine, CIPHER)
    with Session(engine) as session, session.begin():
        rows = list(session.scalars(select(AIModelConfig)))
        assert rows and all(row.runtime_profile is None and row.row_version == 7 for row in rows)
    assert "current-host-key" not in caplog.text and "private-header" not in caplog.text


def test_backfill_binding_without_catalog_is_not_silently_skipped(db_session):
    legacy_catalog(db_session)
    with db_session.begin():
        db_session.delete(db_session.get(CanvasModelCatalog, 301))
    with pytest.raises(RuntimeError, match="本人目录缺失"):
        migration_module().backfill_catalogs(db_session.get_bind(), CIPHER)
    with db_session.begin():
        config = db_session.get(AIModelConfig, 101)
        binding = db_session.get(CanvasChannelModel, 201)
        assert config.runtime_profile is None and config.row_version == 7
        assert binding.runtime_migrated_at is None


def create_host(service, **changes):
    return service.create(
        {
            "service_type": "text",
            "name": "宿主模型",
            "provider": "自定义服务",
            "model_key": "original-model",
            "base_url": "https://model.example/v1",
            "apikey": "first-key",
            "runtime_profile": deepcopy(PROFILE),
            "headers": [{"name": "X-Provider-Key", "value": "first-header"}],
            **changes,
        }
    )


def test_host_update_runtime_key_alias_and_preferences_cannot_overwrite_models(db_session):
    legacy_catalog(db_session)
    migration_module().backfill_catalogs(db_session.get_bind(), CIPHER)
    db_session.info["actor"] = actor()
    service = AIModelConfigService(db_session, cipher=CIPHER)
    model = service.get(101)
    changed = service.update(
        101,
        {
            "row_version": model.row_version,
            "model_key": "renamed-model",
            "apikey": "fresh-key",
            "headers": [{"name": "X-Provider-Key", "value": "fresh-header"}],
            "secret_key": None,
            "runtime_profile": {
                **PROFILE,
                "capability_config": {"text": {"references": {"maxImages": 0}}},
            },
        },
    )
    catalog = CanvasModelCatalogService(db_session, cipher=CIPHER)
    with db_session.begin():
        config = db_session.get(AIModelConfig, 101)
        catalog.refresh_runtime_model_locked(config)
        credentials = catalog.runtime_credentials_locked(config.id, "openai_chat.v1")
        assert credentials["apiKey"] == "fresh-key"
        assert credentials["headers"] == {"X-Provider-Key": "fresh-header"}
        assert config.capability_cache["canvas_text_capability"]["references"]["maxImages"] == 0
    workspace = CanvasWorkspaceService(db_session, cipher=CIPHER)
    models = workspace.read_models()
    assert len(models["models"]) == 1 and models["channels"] == []
    assert models["models"][0]["selection_aliases"] == ["old-channel::legacy-model"]
    saved = workspace.save_preferences(
        CanvasWorkspacePreferencesRequest(
            expected_row_version=models["row_version"],
            preferences={"textModel": "old-channel::legacy-model"},
        )
    )
    version = service.get(101).row_version
    for channels in (
        [],
        [
            {
                "id": "old-channel",
                "name": "伪造旧更新",
                "baseUrl": "https://model.example/v1",
                "models": ["legacy-model"],
                "modelProfiles": [
                    {"model": "legacy-model", "capability": "text", "protocol": "chat-completion"}
                ],
            }
        ],
    ):
        with pytest.raises(WorkflowError):
            workspace.save_preferences(
                CanvasWorkspacePreferencesRequest(
                    expected_row_version=saved["row_version"], preferences={}, channels=channels
                )
            )
    final = service.get(101)
    assert final.row_version == version and final.model_key == "renamed-model"
    assert final.enabled == 1 and final.is_deleted == 0 and not final.has_secret_key
    assert final.row_version >= changed.row_version


def test_runtime_crud_redacts_preserves_and_clears_credentials_with_cas(db_session):
    db_session.info["actor"] = actor()
    service = AIModelConfigService(db_session, cipher=CIPHER)
    model = create_host(service, secret_key="private-second-key")
    public = json.dumps(model.model_dump(mode="json"))
    assert "private-second-key" not in public and "first-header" not in public
    assert model.has_secret_key and model.headers[0].has_value
    preserved = service.update(
        model.id,
        {"row_version": model.row_version, "headers": [{"name": "x-provider-key", "value": ""}]},
    )
    assert preserved.row_version == model.row_version
    changed = service.update(
        model.id,
        {"row_version": preserved.row_version, "secret_key": None, "headers": [], "apikey": None},
    )
    assert not changed.has_api_key and not changed.has_secret_key and changed.headers == []
    with pytest.raises(Conflict):
        service.update(model.id, {"row_version": model.row_version, "apikey": "stale-key"})
    assert service.get(model.id).row_version == changed.row_version
