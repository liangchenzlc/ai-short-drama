"""离线切换创作模式。先备份并预演，再显式重建当前库；默认只输出计数。"""

import argparse
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text

from short_drama.core.config import Settings
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation

RETAIN = frozenset(
    {
        "users",
        "user_sessions",
        "auth_rate_limits",
        "ai_model_configs",
        "user_model_preferences",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_workspace_user_states",
        "agent_skills",
        "canvas_style_preferences",
    }
)
LOCATOR = re.compile(r'minio://[^\s"\'<>]+')


def encode(value):
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    raise TypeError(type(value).__name__)


def decode(value):
    if set(value) == {"$datetime"}:
        return datetime.fromisoformat(value["$datetime"])
    if set(value) == {"$decimal"}:
        return Decimal(value["$decimal"])
    if set(value) == {"$bytes"}:
        return bytes.fromhex(value["$bytes"])
    return value


def serialized(value):
    return json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True)


def fingerprint(value):
    return hashlib.sha256(serialized(value).encode()).hexdigest()


def write_json(path, value):
    path.write_text(serialized(value), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_hook=decode)


def quote(name):
    if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
        raise ValueError("数据库与表名仅支持字母、数字及下划线")
    return f"`{name}`"


def capture(connection):
    inspector = inspect(connection)
    result = {}
    for name in sorted(inspector.get_table_names()):
        columns = [c["name"] for c in inspector.get_columns(name) if not c.get("computed")]
        values = list(
            connection.execute(
                text(f"SELECT {','.join(quote(c) for c in columns)} FROM {quote(name)}")
            ).mappings()
        )
        rows = sorted((dict(row) for row in values), key=serialized)
        result[name] = {
            "ddl": connection.execute(text(f"SHOW CREATE TABLE {quote(name)}")).one()[1],
            "rows": rows,
        }
    return result


def locators(value):
    if isinstance(value, dict):
        return set().union(*(locators(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(locators(v) for v in value), set())
    return set(LOCATOR.findall(value)) if isinstance(value, str) else set()


def location(settings, locator):
    return ObjectLocation.parse(
        locator,
        {
            settings.minio_image_bucket,
            settings.minio_video_bucket,
            settings.minio_audio_bucket,
        },
    )


def backup(engine, settings, directory):
    directory.mkdir(parents=True, exist_ok=False)
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        connection.execute(text("START TRANSACTION WITH CONSISTENT SNAPSHOT"))
        data = capture(connection)
        database = connection.scalar(text("SELECT DATABASE()"))
        connection.rollback()
    write_json(directory / "database.json", data)
    storage = MinioStorage(settings)
    objects = []
    objects_dir = directory / "objects"
    objects_dir.mkdir()
    try:
        for locator in sorted(locators(data)):
            target = location(settings, locator)
            key = hashlib.sha256(locator.encode()).hexdigest()
            entry = {"locator": locator, "file": key}
            try:
                storage.client.fget_object(
                    target.bucket, target.object_name, str(objects_dir / key)
                )
                entry["content_type"] = storage.client.stat_object(
                    target.bucket, target.object_name
                ).content_type
                entry["sha256"] = hashlib.sha256((objects_dir / key).read_bytes()).hexdigest()
            except Exception as error:
                if getattr(error, "code", "") not in {"NoSuchKey", "NoSuchObject"}:
                    raise RuntimeError(f"备份对象失败: {locator}") from error
                entry["missing"] = True
            objects.append(entry)
    finally:
        storage.close()
    report = {
        "database": database,
        "data_sha256": fingerprint(data),
        "counts": {n: len(v["rows"]) for n, v in data.items()},
        "objects": objects,
    }
    write_json(directory / "backup.json", report)
    return report


def rows(data, name):
    return deepcopy(data.get(name, {}).get("rows", []))


def retained(data, copy_media):
    """按用户复制个人画风闭包；识别媒体字段，不替换剧本文字中的数字。"""
    from short_drama.service.canvas.document.document import (
        media_references,
        remap_resource_references,
    )

    result = {name: rows(data, name) for name in RETAIN if name in data}
    challenges = [
        r
        for r in rows(data, "email_challenges")
        if r["purpose"] in {"registration", "password_reset"}
    ]
    result["email_challenges"] = challenges
    challenge_ids = {r["id"] for r in challenges}
    result["email_outbox"] = [
        r for r in rows(data, "email_outbox") if r["challenge_id"] in challenge_ids
    ]
    profiles = [r for r in rows(data, "canvas_style_profiles") if r.get("scope_user_id")]
    old_media = {r["id"]: r for r in rows(data, "media_files")}
    old_refs = rows(data, "canvas_style_media_references")
    next_reference_id = max((r["id"] for r in old_refs), default=0)
    references, media, copied = [], {}, {}
    next_media_id = max(old_media, default=0)
    for profile in profiles:
        uid = profile["scope_user_id"]
        document = profile["profile_json"]
        document = json.loads(document) if isinstance(document, str) else document
        mids = {identifier for _, identifier in media_references(document)}
        refs = [r for r in old_refs if r["style_id"] == profile["id"]]
        mids.update(r["media_id"] for r in refs)
        mapping = {}
        for mid in mids:
            if mid not in old_media:
                raise RuntimeError(f"个人画风 {profile['id']} 引用不存在的媒体 {mid}")
            original = old_media[mid]
            if original.get("project_id") is None and original.get("scope_user_id") == uid:
                media[mid] = original
                mapping[mid] = mid
                continue
            key = uid, mid
            if key not in copied:
                next_media_id += 1
                clone = deepcopy(original)
                clone.update(
                    id=next_media_id,
                    project_id=None,
                    scope_user_id=uid,
                    created_by=uid,
                    updated_by=uid,
                )
                clone["storage_locator"] = copy_media(uid, original["storage_locator"])
                metadata = clone.get("video_metadata")
                metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
                if metadata:
                    metadata = deepcopy(metadata)
                    for field, value in metadata.items():
                        if isinstance(value, str) and value.startswith("minio://"):
                            metadata[field] = copy_media(uid, value)
                    clone["video_metadata"] = json.dumps(metadata, ensure_ascii=False)
                media[next_media_id] = clone
                copied[key] = next_media_id
            mapping[mid] = copied[key]
        profile["profile_json"] = json.dumps(
            remap_resource_references(document, mapping), ensure_ascii=False
        )
        for ref in refs:
            ref["media_id"] = mapping[ref["media_id"]]
            references.append(ref)
        known = {r["media_id"] for r in refs}
        for mid in set(mapping.values()) - known:
            next_reference_id += 1
            references.append(
                {
                    "id": next_reference_id,
                    "style_id": profile["id"],
                    "media_id": mid,
                    **{
                        key: profile[key]
                        for key in ("created_at", "updated_at", "created_by", "updated_by")
                        if key in profile
                    },
                }
            )
    result["canvas_style_profiles"] = profiles
    result["canvas_style_media_references"] = references
    result["media_files"] = list(media.values())
    return result


def insert_rows(connection, name, values, columns):
    if not values:
        return
    fields = [key for key in values[0] if key in columns]
    statement = f"INSERT INTO {quote(name)} ({','.join(quote(k) for k in fields)}) VALUES ("
    statement += ",".join("%s" for _ in fields) + ")"
    connection.exec_driver_sql(statement, [tuple(row[k] for k in fields) for row in values])


def restore_legacy(connection, data):
    connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
    try:
        for name, table in data.items():
            connection.exec_driver_sql(table["ddl"], execution_options={"no_parameters": True})
            if table["rows"]:
                insert_rows(connection, name, table["rows"], set(table["rows"][0]))
        connection.commit()
    finally:
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")


def verify_foreign_keys(connection):
    inspector = inspect(connection)
    for name in inspector.get_table_names():
        for foreign in inspector.get_foreign_keys(name):
            local, remote = foreign["constrained_columns"], foreign["referred_columns"]
            match = " AND ".join(
                f"c.{quote(a)}=p.{quote(b)}" for a, b in zip(local, remote, strict=True)
            )
            present = " AND ".join(f"c.{quote(a)} IS NOT NULL" for a in local)
            count = connection.scalar(
                text(
                    f"SELECT COUNT(*) FROM {quote(name)} c LEFT JOIN "
                    f"{quote(foreign['referred_table'])} p ON {match} WHERE {present} "
                    f"AND p.{quote(remote[0])} IS NULL"
                )
            )
            if count:
                raise RuntimeError(f"保留数据外键不完整: {name}.{foreign['name']} ({count})")


def rebuild(connection, keep):
    from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

    from short_drama.domain import Base

    retired = {
        "episodes",
        "episode_assemblies",
        "episode_assembly_clips",
        "episode_novels",
        "episode_render_jobs",
        "episode_scripts",
        "episode_sounds",
        "episode_assets",
        "project_assets",
        "global_assets",
        "project_members",
        "project_invitations",
    }
    unexpected = set(inspect(connection).get_table_names()) - set(Base.metadata.tables) - retired
    if unexpected:
        raise RuntimeError(f"发现未声明业务表，停止重建: {sorted(unexpected)}")
    ddl = []
    connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
    try:
        for name in sorted(inspect(connection).get_table_names()):
            statement = f"DROP TABLE {quote(name)}"
            connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
            ddl.append(statement)
        for table in Base.metadata.sorted_tables:
            statement = str(CreateTable(table).compile(dialect=connection.dialect)).replace(
                "%%", "%"
            )
            connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
            ddl.append(statement)
            for index in sorted(table.indexes, key=lambda i: i.name):
                statement = str(CreateIndex(index).compile(dialect=connection.dialect))
                connection.exec_driver_sql(statement)
                ddl.append(statement)
        for table in Base.metadata.sorted_tables:
            for constraint in table.foreign_key_constraints:
                if constraint.use_alter:
                    statement = str(AddConstraint(constraint).compile(dialect=connection.dialect))
                    connection.exec_driver_sql(statement)
                    ddl.append(statement)
            columns = {c.name for c in table.columns if c.computed is None}
            insert_rows(connection, table.name, keep.get(table.name, []), columns)
        connection.commit()
    finally:
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
    verify_foreign_keys(connection)
    actual = capture(connection)
    for name, expected in keep.items():
        columns = {
            c["name"] for c in inspect(connection).get_columns(name) if not c.get("computed")
        }
        expected = sorted(
            ({k: v for k, v in r.items() if k in columns} for r in expected), key=serialized
        )
        if fingerprint(actual[name]["rows"]) != fingerprint(expected):
            raise RuntimeError(f"保留数据校验不一致: {name}")
    return {
        "ddl": ddl,
        "counts": {n: len(v["rows"]) for n, v in actual.items()},
        "retained_sha256": {n: fingerprint(actual[n]["rows"]) for n in keep},
    }


def switch(engine, settings, directory, *, rehearsal):
    from scripts.export_schema import schema_sql

    schema_sha256 = hashlib.sha256(schema_sql().encode()).hexdigest()
    manifest = read_json(directory / "backup.json")
    data = read_json(directory / "database.json")
    if fingerprint(data) != manifest["data_sha256"] or manifest["database"] != settings.db_name:
        raise RuntimeError("备份校验失败或目标数据库不匹配")
    if (
        not rehearsal
        and read_json(directory / "rehearsal.json").get("schema_sha256") != schema_sha256
    ):
        raise RuntimeError("预演后目标结构发生变化，请重新完成隔离预演")
    objects = {entry["locator"]: entry for entry in manifest["objects"]}
    for entry in objects.values():
        if not entry.get("missing"):
            content = (directory / "objects" / entry["file"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise RuntimeError("对象备份校验失败，停止切换")
    storage = MinioStorage(settings)
    copied = []

    def copy_media(uid, locator):
        entry = objects.get(locator)
        if not entry or entry.get("missing"):
            raise RuntimeError("个人画风引用媒体未成功备份，停止切换")
        if rehearsal:
            return locator
        source = location(settings, locator)
        target = ObjectLocation(source.bucket, f"creation-mode/preserved/{uid}/{uuid4().hex}")
        storage.client.fput_object(
            target.bucket,
            target.object_name,
            str(directory / "objects" / entry["file"]),
            content_type=entry.get("content_type")
            or storage.client.stat_object(source.bucket, source.object_name).content_type,
        )
        copied.append({"source": locator, "target": target.locator})
        write_json(directory / "preserved-copies.json", copied)
        return target.locator

    database = f"_test_creation_{uuid4().hex[:12]}" if rehearsal else settings.db_name
    target_engine = create_engine(
        settings.database_url.set(database=database), hide_parameters=True
    )
    created = False
    try:
        with engine.connect() as source:
            if fingerprint(capture(source)) != manifest["data_sha256"]:
                raise RuntimeError("原库在备份后发生变化，请停写并重新备份")
            if rehearsal:
                source.exec_driver_sql(
                    f"CREATE DATABASE {quote(database)} CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_0900_ai_ci"
                )
                created = True
        keep = retained(data, copy_media)
        with target_engine.connect() as connection:
            if rehearsal:
                restore_legacy(connection, data)
                if fingerprint(capture(connection)) != manifest["data_sha256"]:
                    raise RuntimeError("隔离库备份恢复校验失败")
            report = rebuild(connection, keep)
            report.update(database=database, source_database=settings.db_name, rehearsal=rehearsal)
            report["schema_sha256"] = schema_sha256
            (directory / ("rehearsal-ddl.sql" if rehearsal else "applied-ddl.sql")).write_text(
                ";\n\n".join(report["ddl"]) + ";\n", encoding="utf-8"
            )
            write_json(directory / ("rehearsal.json" if rehearsal else "applied.json"), report)
            return report
    finally:
        target_engine.dispose()
        storage.close()
        if rehearsal and created:
            with engine.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(database)}")


def restore(engine, settings, directory):
    manifest = read_json(directory / "backup.json")
    data = read_json(directory / "database.json")
    if manifest["database"] != settings.db_name or fingerprint(data) != manifest["data_sha256"]:
        raise RuntimeError("备份与当前库不匹配")
    storage = MinioStorage(settings)
    try:
        for entry in manifest["objects"]:
            if entry.get("missing"):
                continue
            path = directory / "objects" / entry["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise RuntimeError("对象备份校验失败")
            target = location(settings, entry["locator"])
            storage.client.fput_object(
                target.bucket,
                target.object_name,
                str(path),
                content_type=entry.get("content_type") or "application/octet-stream",
            )
        with engine.connect() as connection:
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
            try:
                for name in inspect(connection).get_table_names():
                    connection.exec_driver_sql(f"DROP TABLE {quote(name)}")
                restore_legacy(connection, data)
            finally:
                connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
            if fingerprint(capture(connection)) != manifest["data_sha256"]:
                raise RuntimeError("还原后数据库校验失败，保持服务停机")
        return {"database": settings.db_name, "restored": True}
    finally:
        storage.close()


def prune(engine, settings, directory):
    if not (directory / "applied.json").is_file():
        raise RuntimeError("当前备份尚未完成切换")
    manifest = read_json(directory / "backup.json")
    if manifest["database"] != settings.db_name:
        raise RuntimeError("备份与当前库不匹配")
    with engine.connect() as connection:
        keep = locators(capture(connection))
    storage = MinioStorage(settings)
    removed = []
    try:
        for entry in manifest["objects"]:
            if entry["locator"] in keep or entry.get("missing"):
                continue
            path = directory / "objects" / entry["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise RuntimeError("对象备份校验失败，停止清理")
            target = location(settings, entry["locator"])
            storage.client.remove_object(target.bucket, target.object_name)
            removed.append(entry["locator"])
            write_json(directory / "removed-objects.json", removed)
        return {"database": settings.db_name, "removed_objects": len(removed)}
    finally:
        storage.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["plan", "backup", "rehearse", "apply", "restore", "prune"]
    )
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    if args.action != "plan" and (
        not args.directory or args.database != settings.db_name or not args.offline
    ):
        parser.error("需明确 --directory、匹配当前配置的 --database，以及 --offline")
    engine = create_engine(settings.database_url, hide_parameters=True)
    try:
        if args.action == "plan":
            with engine.connect() as connection:
                data = capture(connection)
            result = {
                "database": settings.db_name,
                "counts": {n: len(v["rows"]) for n, v in data.items()},
                "retained_tables": sorted(RETAIN),
            }
        elif args.action == "backup":
            result = backup(engine, settings, args.directory)
            result = {k: v for k, v in result.items() if k != "objects"}
        elif args.action == "restore":
            result = restore(engine, settings, args.directory)
        elif args.action == "prune":
            result = prune(engine, settings, args.directory)
        else:
            if args.action == "apply" and not (args.directory / "rehearsal.json").is_file():
                raise RuntimeError("必须先成功完成同一备份的隔离库预演")
            result = switch(engine, settings, args.directory, rehearsal=args.action == "rehearse")
            result = {k: v for k, v in result.items() if k != "ddl"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
