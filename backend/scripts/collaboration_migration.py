"""Expand -> explicit legacy ownership -> constraints. Never runs at API startup.

Run from backend. --precheck is read-only; application data is touched only with
--backfill --legacy-owner-user-id. Stop API/scheduler/workers and back up first.
"""

import argparse
import copy
import getpass
import json

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import AddConstraint, CreateColumn

from short_drama.core.config import Settings
from short_drama.db.readiness import OWNERS, ownership_gaps, schema_gaps
from short_drama.db.session import build_engine, session_factory
from short_drama.domain import Base
from short_drama.domain.collaboration import ResourceScope, User, UserProjectState
from short_drama.schemas.identity import Registration
from short_drama.service.auth_service import PASSWORDS
from short_drama.service.base import utcnow
from short_drama.storage.minio import MinioStorage
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

ROOTS = {"assets", "media_files", "async_tasks", "generation_batches"}
IDENTITY = {
    "users",
    "user_sessions",
    "email_challenges",
    "project_members",
    "project_invitations",
    "audit_events",
    "user_model_preferences",
    "user_project_states",
    "email_outbox",
    "auth_rate_limits",
    "resource_imports",
}


def expand(engine):
    """DDL is intentionally separate from backfill; MySQL DDL commits implicitly."""
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        # users is the only new parent required for existing tables' new columns.
        Base.metadata.tables["users"].create(connection, checkfirst=True)
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                table.create(connection, checkfirst=True)
                continue
            actual = {c["name"] for c in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if column.name not in actual:
                    transitional = copy.copy(column)
                    if OWNERS.get(table.name) == column.name:
                        transitional.nullable = True
                    ddl = str(CreateColumn(transitional).compile(dialect=dialect())).replace(
                        "%%", "%"
                    )
                    connection.execute(text(f"ALTER TABLE `{table.name}` ADD COLUMN {ddl}"))


def precheck(engine):
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        missing = [
            f"{t.name}.{c.name}"
            for t in Base.metadata.sorted_tables
            if t.name in tables
            for c in t.columns
            if c.name not in {x["name"] for x in inspector.get_columns(t.name)}
        ]
        counts = {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM `{name}`"))
            for name in (ROOTS | {"projects", "ai_model_configs"}) & tables
        }
        active = (
            connection.scalar(
                text("SELECT COUNT(*) FROM async_tasks WHERE status IN ('queued','running')")
            )
            if "async_tasks" in tables
            else 0
        )
        active_render = (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM episode_render_jobs WHERE status IN ('queued','running')"
                )
            )
            if "episode_render_jobs" in tables
            else 0
        )
        active_batches = (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM generation_batches "
                    "WHERE status IN ('running','paused','needs_review')"
                )
            )
            if "generation_batches" in tables
            else 0
        )
        gaps = schema_gaps(connection)
        return {
            "missing_tables": sorted(set(Base.metadata.tables) - tables),
            "missing_columns": missing,
            "counts": counts,
            "active_generation_tasks": active,
            "active_render_jobs": active_render,
            "active_generation_batches": active_batches,
            "unresolved_ownership": ownership_gaps(connection) if not gaps else "expand_required",
        }


MEDIA_FIELDS = {
    "media_id",
    "first_frame_media_id",
    "last_frame_media_id",
    "current_media_id",
    "output_media_id",
    "proxy_media_id",
}
ASSET_FIELDS = {"asset_id", "character_id"}
JSON_FIELDS = {
    "request_data",
    "snapshot",
    "document",
    "voices",
    "manifest",
    "last_receipt",
    "receipt",
}


def json_references(value):
    if isinstance(value, list):
        for item in value:
            yield from json_references(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if (
                key in MEDIA_FIELDS | ASSET_FIELDS
                and isinstance(item, (str, int))
                and str(item).isdigit()
            ):
                yield ("assets" if key in ASSET_FIELDS else "media_files"), int(item)
            elif key in {"reference_media_ids", "audio_reference_media_ids"} and isinstance(
                item, list
            ):
                for identifier in item:
                    yield "media_files", int(identifier)
            else:
                yield from json_references(item)


def rewrite_json(value, scope, assets, media, locators):
    """Only rewrite known reference fields, never user prose or provider task IDs."""
    if isinstance(value, list):
        return [rewrite_json(v, scope, assets, media, locators) for v in value]
    if not isinstance(value, dict):
        return value
    output = {}
    for key, item in value.items():
        mapping = (
            assets
            if key in {"asset_id", "character_id"}
            else media
            if key
            in {
                "media_id",
                "first_frame_media_id",
                "last_frame_media_id",
                "output_media_id",
                "proxy_media_id",
            }
            else None
        )
        if mapping is not None and isinstance(item, (str, int)) and str(item).isdigit():
            identifier = mapping.get((int(item), scope), int(item))
            output[key] = str(identifier) if isinstance(item, str) else identifier
        elif key in {"reference_media_ids", "audio_reference_media_ids"} and isinstance(item, list):
            output[key] = [str(media.get((int(v), scope), int(v))) for v in item]
        elif key in {"locator", "storage_locator"} and isinstance(item, str):
            output[key] = locators.get((item, scope), item)
        else:
            output[key] = rewrite_json(item, scope, assets, media, locators)
    return output


def backfill(factory, legacy_user_id, storage=None, settings=None):
    written = []
    try:
        with factory.begin() as session:
            if schema_gaps(session.connection()):
                raise ValueError("Run --expand before --backfill")
            user = session.get(User, legacy_user_id)
            if not user or user.status != "active" or not user.email_verified_at:
                raise ValueError("Legacy owner must be an active, verified account")
            classes = {
                m.local_table.name: m.class_
                for m in Base.registry.mappers
                if m.local_table.name not in IDENTITY
            }
            rows = {name: list(session.scalars(select(model))) for name, model in classes.items()}
            legacy_projects = {p.id for p in rows["projects"] if p.owner_user_id is None}
            if (
                any(t.status in {"queued", "running"} for t in rows["async_tasks"])
                or any(j.status in {"queued", "running"} for j in rows["episode_render_jobs"])
                or any(
                    b.status in {"running", "paused", "needs_review"}
                    for b in rows["generation_batches"]
                )
            ):
                raise ValueError(
                    "Drain or explicitly cancel active generation/render/batch jobs "
                    "before migration"
                )
            by_id = {
                name: {
                    getattr(r, "id", getattr(r, "project_id", getattr(r, "assembly_id", None))): r
                    for r in values
                }
                for name, values in rows.items()
            }
            for project in rows["projects"]:
                project.owner_user_id = project.owner_user_id or user.id
                if project.last_opened_at and not session.scalar(
                    select(UserProjectState.id).where(
                        UserProjectState.user_id == user.id,
                        UserProjectState.project_id == project.id,
                    )
                ):
                    session.add(
                        UserProjectState(
                            id=next_id(),
                            user_id=user.id,
                            project_id=project.id,
                            last_opened_at=project.last_opened_at,
                        )
                    )
            for config in rows["ai_model_configs"]:
                config.owner_user_id = config.owner_user_id or user.id
            for link in rows["global_assets"]:
                link.user_id = link.user_id or user.id
            personal = (user.id, None)
            asset_scopes = {r.id: set() for r in rows["assets"]}

            def project_scope(identifier):
                if identifier not in by_id["projects"]:
                    raise ValueError("Unresolved project reference; manual repair required")
                return (None, identifier)

            def scope(name, row):
                if name == "projects":
                    return project_scope(row.id)
                if name == "assets":
                    choices = asset_scopes[row.id]
                    return (
                        personal
                        if personal in choices or not choices
                        else sorted(choices, key=lambda s: s[1] or 0)[0]
                    )
                if name == "async_tasks":
                    return (row.scope_user_id, row.project_id)
                if name == "generation_batches":
                    return (
                        project_scope(int(row.scope["scope"]["project_id"]))
                        if row.scope.get("scope", {}).get("project_id")
                        else personal
                    )
                if name == "global_assets":
                    return (row.user_id, None)
                if hasattr(row, "project_id") and row.project_id:
                    return project_scope(row.project_id)
                for field, parent in [
                    ("episode_id", "episodes"),
                    ("shot_id", "shot_scripts"),
                    ("assembly_id", "episode_assemblies"),
                    ("asset_id", "assets"),
                    ("task_id", "async_tasks"),
                    ("record_id", "ai_generation_records"),
                    ("script_id", "episode_scripts"),
                    ("novel_id", "episode_novels"),
                    ("batch_id", "generation_batches"),
                ]:
                    value = getattr(row, field, None)
                    if value is not None:
                        parent_row = by_id[parent].get(value)
                        if parent_row is None:
                            raise ValueError("Unresolved ownership path; manual repair required")
                        return scope(parent, parent_row)
                return personal

            for name in ("global_assets", "project_assets", "episode_assets", "shot_assets"):
                for link in rows[name]:
                    asset_scopes[link.asset_id].add(scope(name, link))
            for asset in rows["assets"]:
                if asset.scope_user_id or asset.project_id:
                    asset_scopes[asset.id].add((asset.scope_user_id, asset.project_id))
                if not asset_scopes[asset.id]:
                    asset_scopes[asset.id].add(personal)
            records = rows["ai_generation_records"]
            first_records = {
                r.task_id: r for r in sorted(records, key=lambda r: r.call_no, reverse=True)
            }
            for task in rows["async_tasks"]:
                task.initiated_by = task.initiated_by or user.id
                if task.scope_user_id or task.project_id:
                    continue
                source = (
                    first_records.get(task.id).request_data.get("source", {})
                    if task.id in first_records
                    else {}
                )
                target = source.get("project_id") or (
                    first_records[task.id].request_data.get("project_id")
                    if task.id in first_records
                    else None
                )
                if target:
                    target_scope = project_scope(int(target))
                else:
                    target_scope = personal
                    for field, parent in [
                        ("shot_id", "shot_scripts"),
                        ("script_id", "episode_scripts"),
                        ("novel_id", "episode_novels"),
                        ("asset_id", "assets"),
                    ]:
                        if source.get(field):
                            original = by_id[parent].get(int(source[field]))
                            if original:
                                target_scope = scope(parent, original)
                            break
                task.scope_user_id, task.project_id = target_scope
                task.initiated_by = user.id
            for batch in rows["generation_batches"]:
                if not batch.scope_user_id and not batch.project_id:
                    batch.scope_user_id, batch.project_id = scope("generation_batches", batch)
                batch.initiated_by = batch.initiated_by or user.id
            for job in rows["episode_render_jobs"]:
                job.initiated_by = job.initiated_by or user.id
            media_scopes = {
                r.id: {(r.scope_user_id, r.project_id)}
                if r.scope_user_id or r.project_id
                else set()
                for r in rows["media_files"]
            }
            media_fields = MEDIA_FIELDS
            for name, values in rows.items():
                if name in {"media_files", "ai_model_configs"}:
                    continue
                for row in values:
                    choices = (
                        asset_scopes[row.id]
                        if name == "assets"
                        else asset_scopes[row.asset_id]
                        if name == "asset_image_candidates"
                        else {scope(name, row)}
                    )
                    for field in media_fields:
                        mid = getattr(row, field, None)
                        if mid in media_scopes:
                            media_scopes[mid].update(choices)
                    for field in ("reference_media_ids",):
                        for mid in getattr(row, field, []) or []:
                            if int(mid) in media_scopes:
                                media_scopes[int(mid)].update(choices)
                    for field in JSON_FIELDS:
                        for kind, identifier in json_references(getattr(row, field, None)):
                            if kind == "media_files":
                                if identifier not in media_scopes:
                                    raise ValueError(
                                        f"Unresolved media reference in {name}.{field}"
                                    )
                                media_scopes[identifier].update(choices)
            # Task inputs are retained even when their original business content changed.
            for record in records:
                request_scope = scope("ai_generation_records", record)
                inputs = record.request_data.get("input") or {}
                for field in ("reference_media_ids", "audio_reference_media_ids"):
                    for mid in inputs.get(field, []):
                        if int(mid) in media_scopes:
                            media_scopes[int(mid)].add(request_scope)
            asset_map, media_map, locator_map = {}, {}, {}
            for kind, wanted, mapping in [
                ("assets", asset_scopes, asset_map),
                ("media_files", media_scopes, media_map),
            ]:
                for original in rows[kind]:
                    choices = wanted[original.id] or {personal}
                    old_scope = (original.scope_user_id, original.project_id)
                    primary = (
                        old_scope
                        if old_scope in choices
                        else personal
                        if personal in choices
                        else sorted(choices, key=lambda s: s[1] or 0)[0]
                    )
                    original.scope_user_id, original.project_id = primary
                    mapping[(original.id, primary)] = original.id
                    for target_scope in choices - {primary}:
                        values = {
                            c.key: copy.deepcopy(getattr(original, c.key))
                            for c in inspect(type(original)).columns
                        }
                        values.update(
                            id=next_id(), scope_user_id=target_scope[0], project_id=target_scope[1]
                        )
                        if kind == "assets":
                            values.update(model_id=None, creation_key=None, creation_hash=None)
                        else:
                            if storage is None or settings is None:
                                raise ValueError(
                                    "Shared media requires configured storage for physical copies"
                                )
                            loc = ObjectLocation.parse(
                                original.storage_locator,
                                {
                                    settings.minio_image_bucket,
                                    settings.minio_video_bucket,
                                    settings.minio_audio_bucket,
                                },
                            )
                            target_name = f"migration/{values['id']}"
                            written.append(ObjectLocation(loc.bucket, target_name).locator)
                            stored = storage.copy(loc.bucket, loc.object_name, target_name)
                            values["storage_locator"], values["video_metadata"] = (
                                stored.storage_locator,
                                None,
                            )
                            locator_map[(original.storage_locator, target_scope)] = (
                                stored.storage_locator
                            )
                        clone = type(original)(**values)
                        session.add(clone)
                        mapping[(original.id, target_scope)] = clone.id
            session.flush()
            changed_projects = legacy_projects | {
                target[1]
                for (original, target), identifier in [*asset_map.items(), *media_map.items()]
                if original != identifier and target[1]
            }
            # Rewire relational and JSON references; no model credentials or prose rewriting.
            for name, values in rows.items():
                if name in {"media_files", "ai_model_configs"}:
                    continue
                for row in values:
                    target_scope = (
                        (row.scope_user_id, row.project_id)
                        if isinstance(row, ResourceScope)
                        else scope(name, row)
                    )
                    if hasattr(row, "asset_id"):
                        row.asset_id = asset_map.get((row.asset_id, target_scope), row.asset_id)
                    for field in media_fields:
                        if getattr(row, field, None):
                            setattr(
                                row,
                                field,
                                media_map.get(
                                    (getattr(row, field), target_scope), getattr(row, field)
                                ),
                            )
                    if hasattr(row, "reference_media_ids"):
                        row.reference_media_ids = [
                            str(media_map.get((int(mid), target_scope), int(mid)))
                            for mid in row.reference_media_ids or []
                        ]
                    for column in inspect(type(row)).columns:
                        if column.key in JSON_FIELDS and getattr(row, column.key, None) is not None:
                            setattr(
                                row,
                                column.key,
                                rewrite_json(
                                    getattr(row, column.key),
                                    target_scope,
                                    asset_map,
                                    media_map,
                                    locator_map,
                                ),
                            )
                    if (
                        name in {"shot_images", "shot_videos", "character_voices"}
                        and target_scope[1] in changed_projects
                    ):
                        if hasattr(row, "context_hash"):
                            row.context_hash = (
                                "0" * 64
                            )  # Preserved history requires explicit stale-source review.
                    if name == "ai_generation_records" and "media_manifest" in (
                        row.response_data or {}
                    ):
                        # Provider payload IDs are opaque. Only our own archive manifest is rewired.
                        row.response_data = {
                            **row.response_data,
                            "media_manifest": rewrite_json(
                                row.response_data.get("media_manifest", []),
                                target_scope,
                                {},
                                media_map,
                                locator_map,
                            ),
                        }
            # New asset clones were not in the original row lists.
            for (original_id, target_scope), identifier in asset_map.items():
                if identifier != original_id:
                    clone = session.get(classes["assets"], identifier)
                    clone.media_id = media_map.get((clone.media_id, target_scope), clone.media_id)
                    clone.reference_media_ids = [
                        str(media_map.get((int(mid), target_scope), int(mid)))
                        for mid in clone.reference_media_ids or []
                    ]
                    for candidate in rows["asset_image_candidates"]:
                        # Candidate rows may already have been rewired above; use the
                        # original asset lookup from the primary asset's current ID.
                        primary = asset_map.get(
                            (original_id, scope("assets", by_id["assets"][original_id])),
                            original_id,
                        )
                        if candidate.asset_id == primary:
                            original_mid = next(
                                (
                                    mid
                                    for (mid, sc), mapped in media_map.items()
                                    if mapped == candidate.media_id
                                ),
                                candidate.media_id,
                            )
                            session.add(
                                type(candidate)(
                                    id=next_id(),
                                    asset_id=identifier,
                                    media_id=media_map.get(
                                        (original_mid, target_scope), original_mid
                                    ),
                                    created_at=candidate.created_at,
                                )
                            )
            session.flush()
            validate_scopes(session)
        return {
            "assets_copied": len(asset_map) - len(rows["assets"]),
            "media_copied": len(media_map) - len(rows["media_files"]),
        }
    except Exception:
        if storage and written:
            from short_drama.service.storage_service import StorageService

            with factory() as session:
                from short_drama.domain import MediaFile

                retained = set(
                    session.scalars(
                        select(MediaFile.storage_locator).where(
                            MediaFile.storage_locator.in_(written)
                        )
                    )
                )
            for locator in set(written) - retained:
                try:
                    StorageService(storage, settings).delete(locator)
                except Exception:
                    pass
        raise


def finalize(engine):
    with engine.connect() as connection:
        if schema_gaps(connection) or ownership_gaps(connection):
            raise ValueError("Expand and explicitly backfill all ownership before finalizing")
    with session_factory(engine)() as session:
        validate_scopes(session)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            actual = inspect(connection)
            for column in actual.get_columns(table.name):
                if column["name"] == OWNERS.get(table.name) and column["nullable"]:
                    ddl = str(CreateColumn(table.c[column["name"]]).compile(dialect=dialect()))
                    connection.execute(text(f"ALTER TABLE `{table.name}` MODIFY COLUMN {ddl}"))
            fks = {f["name"] for f in actual.get_foreign_keys(table.name)}
            checks = {c["name"] for c in actual.get_check_constraints(table.name)}
            indexes = {i["name"]: i["column_names"] for i in actual.get_indexes(table.name)}
            for constraint in table.constraints:
                if (
                    isinstance(constraint, ForeignKeyConstraint)
                    and constraint.name not in fks
                    or isinstance(constraint, CheckConstraint)
                    and constraint.name not in checks
                ):
                    original_rule = constraint._create_rule
                    ddl = str(AddConstraint(constraint).compile(dialect=dialect())).replace(
                        "%%", "%"
                    )
                    constraint._create_rule = original_rule
                    connection.execute(text(ddl))
                if isinstance(constraint, UniqueConstraint) and constraint.name:
                    fields = [c.name for c in constraint.columns]
                    if constraint.name in indexes and indexes[constraint.name] != fields:
                        connection.execute(
                            text(f"ALTER TABLE `{table.name}` DROP INDEX `{constraint.name}`")
                        )
                        indexes.pop(constraint.name)
                    if constraint.name not in indexes:
                        columns = ", ".join(f"`{name}`" for name in fields)
                        connection.execute(
                            text(
                                f"ALTER TABLE `{table.name}` "
                                f"ADD UNIQUE KEY `{constraint.name}` ({columns})"
                            )
                        )


def validate_scopes(session):
    from short_drama.db.access import scope_of

    classes = {m.local_table.name: m.class_ for m in Base.registry.mappers}
    for name, model in classes.items():
        if name in IDENTITY or name == "ai_model_configs":
            continue
        for row in session.scalars(select(model)):
            expected = scope_of(session, row)
            if expected == (None, None) or (expected[0] is None) == (expected[1] is None):
                raise ValueError(f"Unresolved scope in {name}")
            references = []
            for field in MEDIA_FIELDS | {"asset_id"}:
                identifier = getattr(row, field, None)
                if identifier:
                    references.append(
                        ("assets" if field == "asset_id" else "media_files", int(identifier))
                    )
            references.extend(
                ("media_files", int(mid)) for mid in getattr(row, "reference_media_ids", []) or []
            )
            for field in JSON_FIELDS:
                references.extend(json_references(getattr(row, field, None)))
            for kind, identifier in references:
                target = session.get(classes[kind], identifier)
                if target is None or scope_of(session, target) != expected:
                    raise ValueError(
                        f"Cross-scope or missing reference in {name}; manual repair required"
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precheck", action="store_true")
    parser.add_argument("--expand", action="store_true")
    parser.add_argument("--bootstrap-user", action="store_true")
    parser.add_argument(
        "--verified-email",
        action="store_true",
        help="Operator has independently verified ownership of the bootstrap email",
    )
    parser.add_argument("--username")
    parser.add_argument("--email")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--legacy-owner-user-id", type=int)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    engine = build_engine(settings)
    factory = session_factory(engine)
    storage = MinioStorage(settings) if args.backfill else None
    try:
        if args.expand:
            expand(engine)
        if args.bootstrap_user:
            if not args.verified_email or not args.username or not args.email:
                parser.error("Bootstrap requires username, email and --verified-email")
            payload = Registration(
                username=args.username,
                display_name=args.username,
                email=args.email,
                password=getpass.getpass("Bootstrap password (12+ characters): "),
            )
            with factory.begin() as session:
                user = User(
                    id=next_id(),
                    username=payload.username,
                    display_name=payload.display_name,
                    email=payload.email,
                    password_hash=PASSWORDS.hash(payload.password),
                    status="active",
                    email_verified_at=utcnow(),
                    created_at=utcnow(),
                )
                session.add(user)
                print("Bootstrap user ID:", user.id)
        if args.backfill:
            if not args.legacy_owner_user_id:
                parser.error("--backfill requires --legacy-owner-user-id")
            print(json.dumps(backfill(factory, args.legacy_owner_user_id, storage, settings)))
        if args.finalize:
            finalize(engine)
        print(json.dumps(precheck(engine), ensure_ascii=False))
    finally:
        if storage:
            storage.close()
        engine.dispose()


if __name__ == "__main__":
    main()
