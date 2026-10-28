"""角色历史不可变性独立于 Actor；系统 Worker 同样不能绕过。"""

import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import inspect, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Asset, AssetVersion, AssetVersionMediaReference, MediaFile

_KEY = "_asset_version_append"
_TOKEN = object()
_TABLES = {"asset_versions", "asset_version_media_references"}


@dataclass(frozen=True)
class _AppendPermit:
    token: object
    asset_id: int
    version_id: int


@contextmanager
def _append_context(session, asset_id: int, version_id: int):
    """只供内部追加服务使用，不接受来自 HTTP 的维护开关。"""
    if _KEY in session.info:
        raise WorkflowError("asset_version_append_nested", "角色版本追加上下文不能嵌套", 409)
    session.info[_KEY] = _AppendPermit(_TOKEN, asset_id, version_id)
    try:
        yield
    finally:
        session.info.pop(_KEY, None)


def _permitted(session, asset_id, version_id):
    permit = session.info.get(_KEY)
    return (
        isinstance(permit, _AppendPermit)
        and permit.token is _TOKEN
        and (permit.asset_id, permit.version_id) == (asset_id, version_id)
    )


def _parent(session, model, identifier):
    row = next(
        (entity for entity in session.new if isinstance(entity, model) and entity.id == identifier),
        None,
    )
    if row is None:
        query = select(model).where(model.id == identifier)
        if model is MediaFile:
            query = query.with_for_update()
        row = session.scalar(query)
    if row is None:
        raise NotFound("角色版本引用的资源不存在")
    return row


def guard_version_writes(session) -> None:
    from short_drama.db.scope.resource_scope import scope_of
    from short_drama.schemas.creation.assets.management.version import (
        snapshot_content_hash,
        validate_snapshot,
    )

    changed = list(session.new) + list(session.dirty) + list(session.deleted)
    with session.no_autoflush:
        for entity in changed:
            if isinstance(entity, (AssetVersion, AssetVersionMediaReference)):
                if entity not in session.new:
                    if entity in session.deleted or session.is_modified(entity):
                        raise WorkflowError(
                            "asset_version_immutable", "角色历史版本及媒体槽位不可修改或删除", 403
                        )
                    continue
                version_id = entity.id if isinstance(entity, AssetVersion) else entity.version_id
                if not _permitted(session, entity.asset_id, version_id):
                    raise WorkflowError(
                        "asset_version_append_required", "角色历史只能由锁内追加服务创建", 403
                    )
                asset = _parent(session, Asset, entity.asset_id)
                if asset.kind != "character":
                    raise WorkflowError(
                        "invalid_asset_version_owner", "只有角色可以保存作品版本", 422
                    )
                if isinstance(entity, AssetVersion):
                    validate_snapshot(entity.snapshot_json)
                    if entity.content_hash != snapshot_content_hash(entity.snapshot_json):
                        raise WorkflowError("invalid_asset_version_hash", "角色快照摘要不匹配", 422)
                else:
                    version = _parent(session, AssetVersion, entity.version_id)
                    media = _parent(session, MediaFile, entity.media_id)
                    if version.asset_id != asset.id or scope_of(session, media) != scope_of(
                        session, asset
                    ):
                        raise NotFound("角色版本媒体不属于角色范围")
                    if not (
                        media.format_code.startswith("image/") or media.format_code == "demo:image"
                    ):
                        raise WorkflowError(
                            "invalid_asset_version_media", "角色版本媒体必须是图片", 422
                        )
            elif isinstance(entity, Asset):
                pointer = entity.current_version_id
                if pointer is not None and entity.kind != "character":
                    raise WorkflowError(
                        "invalid_asset_version_owner", "非角色不能持有作品版本", 422
                    )
                history = inspect(entity).attrs.current_version_id.history
                if history.has_changes() and (pointer is not None or entity not in session.new):
                    if not _permitted(session, entity.id, pointer):
                        raise WorkflowError(
                            "asset_version_pointer_guarded",
                            "角色版本指针只能由锁内追加服务更新",
                            403,
                        )
                    version = _parent(session, AssetVersion, pointer)
                    if entity.kind != "character" or version.asset_id != entity.id:
                        raise WorkflowError(
                            "invalid_asset_version_owner", "角色版本指针所属不匹配", 422
                        )


def _table_name(table):
    while (original := getattr(table, "original", None)) is not None and original is not table:
        table = original
    return getattr(table, "name", None)


def _assignment_columns(statement, parameters):
    columns = list(getattr(statement, "_values", None) or {})
    columns.extend(column for column, _ in getattr(statement, "_ordered_values", None) or [])
    for rows in getattr(statement, "_multi_values", ()):
        for row in rows:
            if isinstance(row, Mapping):
                columns.extend(row)
            else:
                columns.extend(column for column, _ in zip(statement.table.c, row, strict=False))
    columns.extend(getattr(statement, "_select_names", None) or [])
    post_values = getattr(statement, "_post_values_clause", None)
    updates = getattr(post_values, "update", None) or {}
    columns.extend(updates if isinstance(updates, Mapping) else (column for column, _ in updates))
    columns.extend(column for column, _ in getattr(post_values, "update_values_to_set", ()))
    if isinstance(parameters, Mapping):
        parameters = [parameters]
    for values in parameters or []:
        if isinstance(values, Mapping):
            columns.extend(values)
    return columns


def guard_version_bulk(state) -> None:
    """ORM 与 Core 批量写均拒绝；底层迁移使用独立 Connection。"""
    statement = state.statement
    while (element := getattr(statement, "element", None)) is not None and element is not statement:
        statement = element
    raw = getattr(statement, "text", None)
    if isinstance(raw, str) and re.search(
        r"\b(insert|update|delete|replace|truncate|alter|drop)\b", raw, re.I
    ):
        if re.search(r"\basset_versions\b|\basset_version_media_references\b", raw, re.I) or (
            re.search(r"\bassets\b", raw, re.I) and re.search(r"\bcurrent_version_id\b", raw, re.I)
        ):
            raise WorkflowError(
                "asset_version_bulk_forbidden", "角色历史和指针不允许直接 SQL 写入", 403
            )
    table = getattr(statement, "table", None)
    names = {_table_name(mapper.local_table) for mapper in state.all_mappers}
    if table is not None:
        names.add(_table_name(table))
    columns = _assignment_columns(statement, state.parameters)
    names.update(_table_name(getattr(column, "table", None)) for column in columns)
    if (
        state.is_update or state.is_delete or getattr(statement, "is_insert", False)
    ) and names & _TABLES:
        raise WorkflowError(
            "asset_version_bulk_forbidden", "角色历史不允许批量写入、修改或删除", 403
        )
    if (state.is_update or getattr(statement, "is_insert", False)) and "assets" in names:
        if any(getattr(column, "key", column) == "current_version_id" for column in columns):
            raise WorkflowError("asset_version_pointer_guarded", "角色版本指针不允许批量更新", 403)
