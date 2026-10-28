"""Explicit, resumable additive migration for global Agent conversations and task receipts."""

import argparse
import json
from pathlib import Path

from sqlalchemy import CheckConstraint, create_engine, inspect, text
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import AddConstraint

from scripts.export_schema import schema_sql
from short_drama.core.config import Settings
from short_drama.db.readiness import _normalize_sql, inspect_agent_schema
from short_drama.domain import AgentConversation, Base

DIRECTORY = Path(__file__).resolve().parents[2] / "docs/database/migrations/2026-10-21-agent-system"
NEW_TABLES = ("agent_tasks", "agent_task_inputs", "agent_task_controls", "agent_operations")

# Historical bytes used only by the explicit, repeatable database migration.
LEGACY_BUILTIN_SOURCE = "平台内置方法；保留历史兼容身份"
LEGACY_BUILTIN_TEXTS = {
    "novel.v1": (
        "小说创作：保持当前作品风格、人物动机和情节因果。按用户要求提供完整小说候选正文，不改作品。"
    ),
    "script.v1": (
        "剧本创作：明确场景、人物动作、对白与节奏；忠实于小说和已确认要求。"
        "提供可编辑剧本候选，不确认定稿。"
    ),
    "extract.v1": "资产提取：从当前剧本提取角色、场景、道具；匹配项目既有资产；保留创建/复用审核。",
    "storyboard.v1": (
        "分镜：以当前已确认剧本为依据，镜头保持连续原文依据和时长；候选需用户选择追加或替换。"
    ),
    "asset_patch.v1": (
        "资产修改：仅提出允许字段的差异建议，保留来源版本和共享影响；禁止直接确认、删除或覆盖。"
    ),
    "shot_patch.v1": "镜头修改：仅提出脚本、时长、视频提示词的差异建议；保留原文依据及来源版本。",
    "image.v1": "图片：固定对象、模型、画幅、数量和参考图；复用原生候选任务；图片完成不自动采用。",
    "video.v1": (
        "视频：固定镜头、模型、分辨率、时长、数量和参考图；按批准数量受理；视频完成不自动采用。"
    ),
}


def foundation_steps():
    steps = [
        (
            "nullable:agent_conversations.project_id",
            "ALTER TABLE agent_conversations MODIFY COLUMN project_id BIGINT UNSIGNED NULL",
        )
    ]
    for constraint in sorted(AgentConversation.__table__.constraints, key=lambda c: c.name or ""):
        if isinstance(constraint, CheckConstraint) and constraint.name in {
            "ck_agent_conversations_scope",
            "ck_agent_conversations_status",
        }:
            rule = constraint._create_rule
            addition = str(AddConstraint(constraint).compile(dialect=dialect())).split(" ADD ", 1)[
                1
            ]
            constraint._create_rule = rule
            steps.append(
                (
                    f"check:agent_conversations.{constraint.name}",
                    f"ALTER TABLE agent_conversations DROP CHECK {constraint.name}, ADD {addition}",
                )
            )
    steps.append(
        (
            "index:agent_conversations.idx_agent_conversations_account",
            "ALTER TABLE agent_conversations ADD INDEX idx_agent_conversations_account "
            "(owner_user_id, status, updated_at, id)",
        )
    )
    source = schema_sql()
    creates = {}
    for statement in source.split(";"):
        start = statement.find("CREATE TABLE `")
        if start >= 0:
            statement = statement[start:].strip()
            creates[statement.split("`", 2)[1]] = statement
    steps.append(("table:agent_tasks", creates["agent_tasks"]))
    for table in ("agent_messages", "agent_runs"):
        steps.append(
            (
                f"column:{table}.task_id",
                f"ALTER TABLE {table} ADD COLUMN task_id BIGINT UNSIGNED NULL, "
                f"ADD INDEX idx_{table}_task "
                f"(task_id, {'seq' if table == 'agent_messages' else 'id'}), "
                f"ADD CONSTRAINT fk_{table}_task FOREIGN KEY (task_id, conversation_id) "
                "REFERENCES agent_tasks (id, conversation_id) "
                "ON DELETE RESTRICT ON UPDATE RESTRICT",
            )
        )
    steps.extend((f"table:{name}", creates[name]) for name in NEW_TABLES[1:])
    return steps


def memory_steps():
    steps = [
        (
            f"column:agent_conversations.{name}",
            f"ALTER TABLE agent_conversations ADD COLUMN {name} {definition}",
        )
        for name, definition in (
            ("summary_covers_until_seq", "BIGINT UNSIGNED NOT NULL DEFAULT 0"),
            ("active_summary_id", "BIGINT UNSIGNED NULL"),
            ("summary_generation", "BIGINT UNSIGNED NOT NULL DEFAULT 0"),
        )
    ]
    for statement in schema_sql().split(";"):
        start = statement.find("CREATE TABLE `")
        if start < 0:
            continue
        statement = statement[start:].strip()
        name = statement.split("`", 2)[1]
        if name in {
            "agent_memory_items",
            "agent_memory_revisions",
            "agent_memory_sources",
            "agent_memory_forget_rules",
            "agent_conversation_summaries",
            "agent_memory_jobs",
        }:
            steps.append((f"table:{name}", statement))
    constraint = next(
        c
        for c in AgentConversation.__table__.foreign_key_constraints
        if c.name == "fk_agent_conversations_summary"
    )
    rule = constraint._create_rule
    try:
        statement = str(AddConstraint(constraint).compile(dialect=dialect()))
    finally:
        constraint._create_rule = rule
    steps.append(("foreign_key:agent_conversations.fk_agent_conversations_summary", statement))
    check = next(
        c
        for c in Base.metadata.tables["agent_memory_items"].constraints
        if c.name == "ck_agent_memory_scope"
    )
    rule = check._create_rule
    try:
        addition = str(AddConstraint(check).compile(dialect=dialect())).split(" ADD ", 1)[1]
    finally:
        check._create_rule = rule
    steps.append(
        (
            "check:agent_memory_items.ck_agent_memory_scope",
            "ALTER TABLE agent_memory_items DROP CHECK ck_agent_memory_scope, ADD " + addition,
        )
    )
    return steps


def preference_steps():
    for statement in schema_sql().split(";"):
        start = statement.find("CREATE TABLE `agent_preferences`")
        if start >= 0:
            return [("table:agent_preferences", statement[start:].strip())]
    raise RuntimeError("Assistant preference table is missing from the domain schema")


def migration_steps():
    return (
        foundation_steps()
        + memory_steps()
        + preference_steps()
        + media_steps()
        + skill_steps()
        + discussion_steps()
        + undo_steps()
        + media_undo_steps()
        + task_compaction_steps()
        + memory_job_check_steps("ck_agent_memory_job_status")
    )


def task_compaction_steps():
    return memory_job_check_steps("ck_agent_memory_job_kind")


def memory_job_check_steps(name):
    constraint = next(
        item for item in Base.metadata.tables["agent_memory_jobs"].constraints if item.name == name
    )
    rule = constraint._create_rule
    try:
        addition = str(AddConstraint(constraint).compile(dialect=dialect())).split(" ADD ", 1)[1]
    finally:
        constraint._create_rule = rule
    return [
        (
            f"check:agent_memory_jobs.{name}",
            f"ALTER TABLE agent_memory_jobs DROP CHECK {name}, ADD " + addition,
        )
    ]


def media_undo_steps():
    return [
        (
            f"nullable:character_voices.{field}",
            f"ALTER TABLE character_voices MODIFY COLUMN {field} BIGINT UNSIGNED NULL",
        )
        for field in ("record_id", "media_id")
    ] + [
        (
            "add_check:character_voices.ck_character_voice_binding_pair",
            "ALTER TABLE character_voices ADD CONSTRAINT ck_character_voice_binding_pair "
            "CHECK ((record_id IS NULL AND media_id IS NULL) OR "
            "(record_id IS NOT NULL AND media_id IS NOT NULL))",
        )
    ]


def undo_steps():
    for statement in schema_sql().split(";"):
        start = statement.find("CREATE TABLE `agent_batch_undos`")
        if start >= 0:
            return [("table:agent_batch_undos", statement[start:].strip())]
    raise RuntimeError("Batch undo table is missing from the domain schema")


def discussion_steps():
    steps = [
        (
            "column:agent_runs.trigger_revision",
            "ALTER TABLE agent_runs ADD COLUMN trigger_revision BIGINT UNSIGNED NOT NULL DEFAULT 0",
        ),
        (
            "replace_index:agent_runs.uk_agent_runs_trigger",
            "ALTER TABLE agent_runs DROP INDEX uk_agent_runs_trigger, ADD UNIQUE INDEX "
            "uk_agent_runs_trigger (trigger_message_id, trigger_revision)",
        ),
        (
            "add_check:agent_runs.ck_agent_runs_trigger_revision",
            "ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_trigger_revision "
            "CHECK (trigger_revision = 0 OR task_id IS NOT NULL)",
        ),
        (
            "column:agent_messages.row_version",
            "ALTER TABLE agent_messages ADD COLUMN row_version BIGINT UNSIGNED NOT NULL DEFAULT 1",
        ),
        (
            "add_check:agent_messages.ck_agent_messages_version",
            "ALTER TABLE agent_messages ADD CONSTRAINT ck_agent_messages_version "
            "CHECK (row_version > 0)",
        ),
    ]
    for statement in schema_sql().split(";"):
        start = statement.find("CREATE TABLE `agent_discussion_revisions`")
        if start >= 0:
            return [*steps, ("table:agent_discussion_revisions", statement[start:].strip())]
    raise RuntimeError("Discussion revision table is missing from the domain schema")


def skill_steps():
    steps = [
        (
            f"column:agent_skills.{name}",
            f"ALTER TABLE agent_skills ADD COLUMN {name} "
            "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL",
        )
        for name in ("create_key", "create_hash")
    ]
    for constraint in sorted(
        Base.metadata.tables["agent_skills"].constraints, key=lambda item: item.name or ""
    ):
        if constraint.name not in {
            "uk_agent_skill_owner_identity",
            "uk_agent_skill_create",
            "ck_agent_skill_create_pair",
        }:
            continue
        rule = constraint._create_rule
        try:
            statement = str(AddConstraint(constraint).compile(dialect=dialect()))
        finally:
            constraint._create_rule = rule
        kind = "add_check" if isinstance(constraint, CheckConstraint) else "index"
        steps.append((f"{kind}:agent_skills.{constraint.name}", statement))
    for statement in schema_sql().split(";"):
        start = statement.find("CREATE TABLE `")
        if start >= 0:
            statement = statement[start:].strip()
            name = statement.split("`", 2)[1]
            if name in {"agent_skill_versions", "agent_skill_settings"}:
                steps.append((f"table:{name}", statement))
    for name in ("agent_skill_versions", "agent_skill_settings"):
        for constraint in Base.metadata.tables[name].constraints:
            if not isinstance(constraint, CheckConstraint) or not constraint.name.endswith(
                "identity"
            ):
                continue
            rule = constraint._create_rule
            try:
                addition = str(AddConstraint(constraint).compile(dialect=dialect())).split(
                    " ADD ", 1
                )[1]
            finally:
                constraint._create_rule = rule
            steps.append(
                (
                    f"check:{name}.{constraint.name}",
                    f"ALTER TABLE {name} DROP CHECK {constraint.name}, ADD {addition}",
                )
            )
    return steps


def media_steps():
    for statement in schema_sql().split(";"):
        start = statement.find("CREATE TABLE `agent_media_requests`")
        if start >= 0:
            return [("table:agent_media_requests", statement[start:].strip())]
    raise RuntimeError("Assistant media intent table is missing from the domain schema")


def needed(inspector, key, statement):
    kind, name = key.split(":", 1)
    if kind == "table":
        return name not in inspector.get_table_names()
    table, field = name.split(".", 1)
    if kind in {"column", "nullable"}:
        column = next((c for c in inspector.get_columns(table) if c["name"] == field), None)
        return column is None if kind == "column" else column is not None and not column["nullable"]
    if kind == "index":
        return not any(index["name"] == field for index in inspector.get_indexes(table))
    if kind == "replace_index":
        index = next((item for item in inspector.get_indexes(table) if item["name"] == field), None)
        return index is None or index["column_names"] != ["trigger_message_id", "trigger_revision"]
    if kind == "foreign_key":
        return not any(
            foreign_key["name"] == field for foreign_key in inspector.get_foreign_keys(table)
        )
    constraint = next(
        (c for c in inspector.get_check_constraints(table) if c["name"] == field), None
    )
    if kind == "add_check":
        return constraint is None
    if constraint is None:
        raise RuntimeError(f"Required baseline constraint is missing: {name}")
    expected = next(
        c
        for c in Base.metadata.tables[table].constraints
        if isinstance(c, CheckConstraint) and c.name == field
    )
    return _normalize_sql(constraint["sqltext"]) != _normalize_sql(expected.sqltext)


def apply_migration(engine):
    executed = []
    with engine.connect() as connection:
        database = connection.scalar(text("SELECT DATABASE()"))
        if not database:
            raise RuntimeError("An explicit application database is required")
        before = connection.scalar(text("SELECT COUNT(*) FROM agent_conversations"))
        for key, statement in migration_steps():
            if needed(inspect(connection), key, statement):
                connection.exec_driver_sql(statement)
                connection.commit()
                executed.append(key)
        skill_backfill = backfill_skill_packages(connection)
        connection.commit()
        after = connection.scalar(text("SELECT COUNT(*) FROM agent_conversations"))
        readiness = inspect_agent_schema(connection)
        if before != after or readiness["status"] != "ready":
            raise RuntimeError(f"Agent migration verification failed: {readiness}")
        return {
            "database": database,
            "executed": executed,
            "conversations_before": before,
            "conversations_after": after,
            "readiness": readiness,
            "skill_backfill": skill_backfill,
        }


def backfill_skill_packages(connection):
    from short_drama.core.skill_catalog import BUILTIN_NAMES
    from short_drama.schemas.skills.catalog.skill import SkillMetadata
    from short_drama.service.base import utcnow
    from short_drama.service.skills.packages.skill_package import package_digest
    from short_drama.utils.snowflake import next_id

    skills = Base.metadata.tables["agent_skills"]
    versions = Base.metadata.tables["agent_skill_versions"]
    settings = Base.metadata.tables["agent_skill_settings"]
    counts = {"personal_versions": 0, "builtin_versions": 0, "settings": 0}
    packages = []
    for row in connection.execute(skills.select()).mappings():
        metadata = SkillMetadata(
            name=row["name"],
            slug=f"personal-{row['id']}",
            description=f"个人创作方法：{row['name']}",
        ).model_dump(mode="json")
        metadata["issues"] = []
        packages.append(
            dict(
                skill_key=str(row["id"]),
                skill_id=row["id"],
                owner_user_id=row["owner_user_id"],
                content_version=row["content_version"],
                package_metadata=metadata,
                instructions=row["instructions"],
                resources=[],
            )
        )
        exists = connection.scalar(
            settings.select()
            .with_only_columns(settings.c.id)
            .where(
                settings.c.owner_user_id == row["owner_user_id"],
                settings.c.skill_key == str(row["id"]),
            )
        )
        if exists is None:
            connection.execute(
                settings.insert().values(
                    id=next_id(),
                    owner_user_id=row["owner_user_id"],
                    skill_key=str(row["id"]),
                    skill_id=row["id"],
                    enabled=0 if row["deleted_at"] else row["enabled"],
                    row_version=1,
                    updated_at=utcnow(),
                )
            )
            counts["settings"] += 1
    for key, instructions in LEGACY_BUILTIN_TEXTS.items():
        metadata = SkillMetadata.model_construct(
            name=BUILTIN_NAMES[key],
            slug=key.replace(".", "-"),
            description=instructions,
            source=LEGACY_BUILTIN_SOURCE,
        ).model_dump(mode="json")
        metadata["issues"] = []
        packages.append(
            dict(
                skill_key=key,
                skill_id=None,
                owner_user_id=None,
                content_version=1,
                package_metadata=metadata,
                instructions=instructions,
                resources=[],
            )
        )
    for package in packages:
        exists = (
            connection.execute(
                versions.select().where(
                    versions.c.skill_key == package["skill_key"],
                    versions.c.content_version == package["content_version"],
                )
            )
            .mappings()
            .first()
        )
        checksum = package_digest(
            package["package_metadata"], package["instructions"], package["resources"]
        )
        if exists is not None:
            if package["owner_user_id"] is None and exists["checksum_sha256"] != checksum:
                raise RuntimeError(
                    f"Builtin skill {package['skill_key']} changed without a new content version"
                )
            continue
        connection.execute(
            versions.insert().values(
                id=next_id(),
                **package,
                checksum_sha256=checksum,
                created_at=utcnow(),
            )
        )
        counts["builtin_versions" if package["owner_user_id"] is None else "personal_versions"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Generate the reviewed incremental SQL"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Apply missing DDL to Settings database"
    )
    args = parser.parse_args()
    if not args.write and not args.apply:
        parser.error("Choose --write or --apply")
    if args.write:
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        for filename, steps in (
            ("001-global-tasks.sql", foundation_steps()),
            ("002-memory.sql", memory_steps()),
            ("003-preferences.sql", preference_steps()),
            ("004-media-requests.sql", media_steps()),
            ("005-skill-packages.sql", skill_steps()),
            ("006-discussion-revisions.sql", discussion_steps()),
            ("007-batch-undo.sql", undo_steps()),
            ("008-media-undo.sql", media_undo_steps()),
            ("009-task-compaction.sql", task_compaction_steps()),
        ):
            (DIRECTORY / filename).write_text(
                "-- Explicit offline migration for the account assistant.\n\n"
                + ";\n\n".join(statement for _key, statement in steps)
                + ";\n",
                encoding="utf-8",
                newline="\n",
            )
    if args.apply:
        engine = create_engine(Settings().database_url, hide_parameters=True)
        try:
            result = apply_migration(engine)
        finally:
            engine.dispose()
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
