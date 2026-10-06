"""显式升级统一模型运行配置，保留旧模型 ID、本人归属与历史任务。"""

import argparse
import json
import logging
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateColumn

from short_drama.core.crypto import KeyCipher
from short_drama.core.logging import configure_logging
from short_drama.db.readiness import _normalize_sql, _type_signature
from short_drama.domain import AIModelConfig, Base, CanvasChannelModel, CanvasModelCatalog
from short_drama.service.base import utcnow

logger = logging.getLogger(__name__)
TABLE_NAME = "ai_model_configs"
RUNTIME_COLUMNS = {
    TABLE_NAME: ("runtime_profile", "runtime_credentials_cipher"),
    "canvas_channel_models": ("runtime_migrated_at",),
}
SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "数据库模型"
    / "migrations"
    / "2026-10-06-host-model-runtime"
    / "001-host-model-runtime.sql"
)


def runtime_statements():
    return [
        (
            f"{table_name}.{name}",
            f"ALTER TABLE {table_name} ADD COLUMN "
            + str(
                CreateColumn(Base.metadata.tables[table_name].c[name]).compile(dialect=dialect())
            ).strip(),
        )
        for table_name, columns in RUNTIME_COLUMNS.items()
        for name in columns
    ]


def runtime_schema_sql():
    return (
        "-- 统一宿主模型运行配置：两列可空，原标准配置保持原行为。\n"
        "-- 旧绑定增加迁移标记，用户清空高级配置后重入不能恢复旧目录。\n"
        "-- 先选择目标数据库；从 AIModelConfig ORM 生成。\n"
        "-- 安全重入及旧目录回填使用 scripts/host_model_runtime_migration.py --apply。\n"
        "-- SQL 仅新增字段，不复制凭据；Python 在服务端加密回填且保留模型 ID。\n\n"
        + "\n\n".join(statement + ";" for _, statement in runtime_statements())
        + "\n"
    )


def inspect_runtime_schema(connection):
    inspector = inspect(connection)
    database = connection.scalar(text("SELECT DATABASE()"))
    missing, changed = [], []
    absent = set(RUNTIME_COLUMNS) - set(inspector.get_table_names())
    if absent:
        return {"database": database, "status": "absent", "missing": sorted(absent), "changed": []}
    for table_name, columns in RUNTIME_COLUMNS.items():
        actual_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        table = Base.metadata.tables[table_name]
        for name in columns:
            actual, expected = actual_columns.get(name), table.c[name]
            identity = f"{table_name}.{name}"
            if actual is None:
                missing.append(identity)
                continue
            default = str(expected.server_default.arg) if expected.server_default else None
            if (
                _type_signature(actual["type"]) != _type_signature(expected.type)
                or actual["nullable"] != expected.nullable
                or _normalize_sql(actual.get("default")) != _normalize_sql(default)
            ):
                changed.append(identity)
    return {
        "database": database,
        "status": "partial" if missing or changed else "ready",
        "missing": missing,
        "changed": changed,
    }


def apply_schema(engine):
    with engine.connect() as connection:
        before = inspect_runtime_schema(connection)
        if before["status"] == "absent" or before["changed"]:
            raise RuntimeError("模型表缺失或运行配置字段不兼容，请先处理预检差异")
        logger.info("统一模型迁移目标数据库：%s", before["database"])
        for name, statement in runtime_statements():
            if name not in inspect_runtime_schema(connection)["missing"]:
                continue
            connection.exec_driver_sql(statement)
            connection.commit()
            logger.info("已执行 DDL：%s", statement)
        return inspect_runtime_schema(connection)


def backfill_catalogs(engine, cipher):
    """运维系统 Session；完整校验后原子回填，不读取 HTTP 身份或改写旧任务。"""
    from short_drama.service.model_runtime_config import (
        encrypt_runtime_credentials,
        legacy_runtime_profile,
    )

    report = {"migrated": 0, "already_migrated": 0, "retired": 0, "api_key_difference_ids": []}
    with Session(engine, expire_on_commit=False, autoflush=False) as session, session.begin():
        catalogs = list(
            session.scalars(
                select(CanvasModelCatalog).order_by(CanvasModelCatalog.id).with_for_update()
            )
        )
        orphan_binding_id = session.scalar(
            select(CanvasChannelModel.id)
            .where(CanvasChannelModel.user_id.not_in([catalog.user_id for catalog in catalogs]))
            .order_by(CanvasChannelModel.id)
            .limit(1)
            .with_for_update()
        )
        if orphan_binding_id is not None:
            raise RuntimeError(f"目录绑定 {orphan_binding_id} 的本人目录缺失，回填已回滚")
        prepared = []
        migrated_bindings = []
        for catalog in catalogs:
            bindings = list(
                session.scalars(
                    select(CanvasChannelModel)
                    .where(CanvasChannelModel.user_id == catalog.user_id)
                    .order_by(CanvasChannelModel.id)
                    .with_for_update()
                )
            )
            configs = {
                row.id: row
                for row in session.scalars(
                    select(AIModelConfig)
                    .where(AIModelConfig.id.in_([binding.model_config_id for binding in bindings]))
                    .order_by(AIModelConfig.id)
                    .with_for_update()
                )
            }
            pending = [
                binding
                for binding in bindings
                if (config := configs.get(binding.model_config_id)) is not None
                and config.runtime_profile is None
                and binding.runtime_migrated_at is None
            ]
            report["already_migrated"] += sum(
                bool(configs.get(binding.model_config_id))
                and (
                    binding.runtime_migrated_at is not None
                    or configs[binding.model_config_id].runtime_profile is not None
                )
                for binding in bindings
            )
            secrets = {}
            if pending and catalog.credentials_cipher:
                try:
                    secrets = json.loads(cipher.decrypt(catalog.credentials_cipher))
                    if not isinstance(secrets, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    raise RuntimeError(
                        f"本人目录 {catalog.id} 的扩展凭据无法解密，回填已回滚"
                    ) from None
            channels = {channel["id"]: channel for channel in catalog.channels_json}
            for binding in bindings:
                config = configs.get(binding.model_config_id)
                if config is None or config.owner_user_id != catalog.user_id:
                    raise RuntimeError(f"目录绑定 {binding.id} 的本人执行配置缺失，回填已回滚")
                if binding.runtime_migrated_at is not None:
                    continue
                migrated_bindings.append(binding)
                if config.runtime_profile is not None:
                    continue
                channel = channels.get(binding.channel_key)
                profile = next(
                    (
                        item
                        for item in (channel.get("modelProfiles", []) if channel else [])
                        if item["model"] == binding.model_key
                    ),
                    None,
                )
                if profile is None and config.is_deleted:
                    report["retired"] += 1
                    continue
                if profile is None or profile.get("capability") != config.service_type:
                    raise RuntimeError(f"模型 {config.id} 的旧能力目录缺失或不一致，回填已回滚")
                try:
                    runtime = legacy_runtime_profile(channel, profile)
                    secret = secrets.get(binding.channel_key, {})
                    ciphertext = encrypt_runtime_credentials(
                        secret.get("secretKey", ""), secret.get("headers", {}), cipher
                    )
                    if binding.channel_key != "beefapi" and "apiKey" in secret:
                        current = cipher.decrypt(config.apikey) if config.apikey else ""
                        if current != secret["apiKey"]:
                            report["api_key_difference_ids"].append(str(config.id))
                except (ValueError, TypeError):
                    raise RuntimeError(
                        f"模型 {config.id} 的运行配置或凭据无效，回填已回滚"
                    ) from None
                if config.row_version == 2**64 - 1:
                    raise RuntimeError(f"模型 {config.id} 版本已耗尽，回填已回滚")
                prepared.append((config, runtime, ciphertext))
        for config, runtime, ciphertext in prepared:
            config.runtime_profile = runtime
            config.runtime_credentials_cipher = ciphertext
            config.capability_cache = None
            config.row_version += 1
            config.updated_at = utcnow()
        for binding in migrated_bindings:
            binding.runtime_migrated_at = utcnow()
        report["migrated"] = len(prepared)
        session.flush()
    return report


def main():
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--precheck", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--export", action="store_true")
    action.add_argument("--check-export", action="store_true")
    args = parser.parse_args()
    if args.export or args.check_export:
        source = runtime_schema_sql()
        if args.check_export:
            if not SQL_PATH.exists() or SQL_PATH.read_text(encoding="utf-8") != source:
                logger.error("统一模型增量 SQL 未同步，请执行 --export")
                return 1
        else:
            SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
            SQL_PATH.write_text(source, encoding="utf-8", newline="\n")
        return 0
    from short_drama.core.config import Settings
    from short_drama.db.session import build_engine

    settings = Settings()
    engine = build_engine(settings)
    try:
        state = apply_schema(engine) if args.apply else None
        if state is None:
            with engine.connect() as connection:
                state = inspect_runtime_schema(connection)
        logger.info("统一模型结构：%s", json.dumps(state, ensure_ascii=False))
        if args.apply and state["status"] == "ready":
            cipher = KeyCipher(
                settings.encryption_key.get_secret_value() if settings.encryption_key else None
            )
            logger.info("统一模型回填：%s", json.dumps(backfill_catalogs(engine, cipher)))
        return 0 if state["status"] == "ready" else 1
    except (RuntimeError, ValueError) as error:
        logger.error("统一模型迁移失败：%s", error)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
