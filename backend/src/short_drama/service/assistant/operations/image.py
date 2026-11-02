"""Frozen image identities and native primary adoption inside an assistant transaction."""

from copy import deepcopy

from sqlalchemy import or_, select

from short_drama.core.exceptions import BusinessError, Conflict, NotFound
from short_drama.dao.media.image_groups.groups import ImageGroupDAO
from short_drama.domain import (
    Asset,
    ImageGroup,
    ImageGroupItem,
    MediaFile,
    Project,
    ShotScript,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.service.identity.authentication.actor import business_actor_locked
from short_drama.service.media.image_groups.groups import ImageGroupService
from short_drama.service.media.image_tools.adoption import (
    _group_state,
)
from short_drama.service.media.image_tools.adoption import (
    image_adoption_state as image_adoption_state,
)


def image_target(session, owner, project_id, kind, identifier, *, lock=False, lock_project=True):
    project_query = select(Project).where(
        Project.id == int(project_id),
        Project.owner_user_id == owner,
        Project.workspace_mode == "standard",
        Project.archived_at.is_(None),
    )
    if lock and lock_project:
        project_query = project_query.with_for_update().execution_options(populate_existing=True)
    project = session.scalar(project_query)
    model = Asset if kind == "asset" else ShotScript
    query = select(model).where(
        model.id == int(identifier),
        model.project_id == int(project_id),
        model.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query) if project is not None and kind in {"asset", "shot"} else None
    if row is None:
        raise NotFound("图片所属主体已删除或不再可访问")
    return project, row


def freeze_image_catalog(
    session, owner, project_id, kind, identifier, *, lock=False, lock_project=True
):
    """Keep identities, versions and saved status only; never private prompts or media bytes."""
    if kind not in {"asset", "shot", "image_group", "image_slot"}:
        return None
    slot = None
    selected_group = None
    if kind in {"image_group", "image_slot"}:
        if kind == "image_slot":
            slot = session.get(ImageGroupItem, int(identifier))
        group_id = slot.group_id if slot else int(identifier)
        selected_group = session.scalar(
            select(ImageGroup).where(
                ImageGroup.id == group_id, ImageGroup.project_id == int(project_id)
            )
        )
        if selected_group is None or kind == "image_slot" and slot is None:
            raise NotFound("图片组或图位不存在或不再可访问")
        target_kind = "asset" if selected_group.asset_id is not None else "shot"
        target_id = selected_group.asset_id or selected_group.shot_id
    else:
        target_kind, target_id = kind, int(identifier)
    _project, target = image_target(
        session, owner, project_id, target_kind, target_id, lock=lock, lock_project=lock_project
    )
    dao = ImageGroupDAO(session)
    groups = list(dao.for_target(target_kind, target.id, lock=lock))
    if selected_group is not None:
        groups = [group for group in groups if group.id == selected_group.id]
    result = []
    for group in groups:
        items = dao.items(group.id, lock=lock)
        if slot is not None:
            items = [item for item in items if item.id == slot.id]
        projected = []
        for item in items:
            history = []
            for identifier in (item.origin or {}).get("history", []):
                try:
                    history.append(parse_identifier(identifier))
                except ValueError:
                    continue
            identifiers = set(history) | ({item.media_id} if item.media_id else set())
            accessible = (
                set(
                    session.scalars(
                        select(MediaFile.id).where(
                            MediaFile.id.in_(identifiers),
                            or_(
                                MediaFile.scope_user_id == owner,
                                (MediaFile.project_id == int(project_id))
                                & (
                                    (MediaFile.created_by == owner)
                                    | MediaFile.published_at.is_not(None)
                                ),
                            ),
                            or_(
                                MediaFile.format_code.like("image/%"),
                                MediaFile.format_code == "demo:image",
                            ),
                        )
                    )
                )
                if identifiers
                else set()
            )
            projected.append(
                {
                    "id": str(item.id),
                    "output_index": item.output_index,
                    "item_version": str(item.item_version),
                    "media_id": str(item.media_id) if item.media_id in accessible else None,
                    "history_media_ids": list(
                        dict.fromkeys(str(i) for i in history if i in accessible)
                    ),
                    "status": "ready"
                    if item.media_id in accessible
                    else "generating"
                    if group.status == "generating"
                    else "failed",
                }
            )
        result.append(
            {
                "id": str(group.id),
                "kind": group.kind,
                "status": group.status,
                "is_current": group.active_slot == 1,
                "expected_count": group.expected_count,
                "primary_index": group.primary_index,
                "primary_version": str(group.primary_version),
                "created_at": group.created_at.isoformat(),
                "items": projected,
            }
        )
    return {
        "target_kind": target_kind,
        "target_id": str(target.id),
        "target_version": str(target.row_version),
        "groups": result,
    }


class AssistantImageService:
    def __init__(self, session):
        self.session = session

    def set_primary_locked(self, owner, project_id, values, *, operation_id, trusted):
        if not self.session.in_transaction():
            raise BusinessError("Image adoption requires the caller's transaction")
        project, target = image_target(
            self.session, owner, project_id, trusted["target_kind"], trusted["target_id"], lock=True
        )
        actor = self.session.info.get("actor")
        if actor is not None and actor.user_id != owner:
            raise NotFound("图片主图写入账号不匹配")
        original = {
            key: self.session.info[key]
            for key in ("actor", "resource_scope")
            if key in self.session.info
        }
        self.session.info["actor"] = actor or business_actor_locked(
            self.session, user_id=owner, request_id=f"assistant:{operation_id}"
        )
        try:
            native = ImageGroupService(self.session)
            row, _scope = native.target(trusted["target_kind"], target.id)
            groups = list(native.groups.for_target(trusted["target_kind"], target.id, lock=True))
            group = next((item for item in groups if item.id == values.group_id), None)
            item = native._require(ImageGroupItem, values.item_id)
            if group is None or item.group_id != group.id:
                raise NotFound("图片不属于已授权的图片组")
            if (
                str(group.primary_version) != trusted["expected_primary_version"]
                or str(item.item_version) != trusted["expected_item_version"]
                or str(row.row_version) != trusted["expected_target_version"]
                or (group.active_slot == 1) != trusted["is_current"]
                or str(item.media_id or "") != str(trusted["media_id"] or "")
            ):
                raise Conflict("图片、主图或所属主体在选入后已变化，请核对并主动刷新引用")
            if (
                group.primary_version == UINT64_MAX
                or row.row_version == UINT64_MAX
                or trusted["target_kind"] == "shot"
                and project.storyboard_version == UINT64_MAX
            ):
                raise Conflict("图片或作品版本已达到上限，无法继续写入")
            before_groups = {str(entry.id): _group_state(entry) for entry in groups}
            before = image_adoption_state(self.session, row, trusted["target_kind"])
            storyboard_before = str(project.storyboard_version)
            native.primary_locked(
                values.group_id,
                values.model_dump(
                    include={
                        "item_id",
                        "expected_primary_version",
                        "expected_item_version",
                        "expected_target_version",
                    }
                ),
            )
            after = image_adoption_state(self.session, row, trusted["target_kind"])
            changes = [
                {
                    "kind": "image_group",
                    "id": str(entry.id),
                    "before": before_groups[str(entry.id)],
                    "after": _group_state(entry),
                }
                for entry in groups
                if before_groups[str(entry.id)] != _group_state(entry)
            ]
            if before != after:
                changes.append(
                    {
                        "kind": trusted["target_kind"],
                        "id": str(row.id),
                        "media_role": "image",
                        "before": before,
                        "after": after,
                    }
                )
            ref = {
                "kind": trusted["target_kind"],
                "id": str(row.id),
                "project_id": str(project.id),
                "name": f"图片组 {group.id} · 图位 {item.output_index}",
                "status": "adopted",
                "row_version": str(row.row_version),
                "image_group_id": str(group.id),
                "image_slot_id": str(item.id),
                "media_id": str(item.media_id),
                "primary_version": str(group.primary_version),
                "item_version": str(item.item_version),
            }
            self.session.flush()
            return {
                "status": "applied",
                "adopted": True,
                "project_id": str(project.id),
                "result_refs": [ref],
                "changes": changes,
                **(
                    {
                        "before_storyboard_version": storyboard_before,
                        "storyboard_version": str(project.storyboard_version),
                    }
                    if trusted["target_kind"] == "shot"
                    else {}
                ),
            }
        finally:
            for key in ("actor", "resource_scope"):
                if key in original:
                    self.session.info[key] = original[key]
                else:
                    self.session.info.pop(key, None)


def _business_catalog(session, identity, values, *, lock=False):
    current = freeze_image_catalog(
        session,
        identity.actor.user_id,
        values.project_id,
        values.target_kind,
        values.target_id,
        lock=lock,
    )
    if identity.scope.subject_policy == "selected":
        frozen = identity.scope.image_catalogs.get(f"{values.target_kind}:{values.target_id}")
        if frozen is None:
            raise NotFound("目标没有获准的冻结图片目录，请主动刷新引用")
        return deepcopy(frozen)
    return current


def read_image_group_business_locked(session, identity, values, receipt_id, settings):
    import json

    catalog = _business_catalog(session, identity, values)
    groups = [
        group
        for group in catalog["groups"]
        if (
            (values.group_id is None or group["id"] == str(values.group_id))
            and (values.view != "current" or group["is_current"])
        )
    ]
    page = groups[values.offset : values.offset + values.limit]
    for group in page:
        for item in group["items"]:
            item["read_content_args"] = (
                {
                    "project_id": str(values.project_id),
                    "target_kind": values.target_kind,
                    "target_id": str(values.target_id),
                    "group_id": group["id"],
                    "item_id": item["id"],
                    "media_id": item["media_id"],
                }
                if item["media_id"]
                else None
            )
    content = json.dumps({**catalog, "groups": page}, ensure_ascii=False, sort_keys=True)
    end = min(values.character_offset + values.character_limit, len(content))
    return {
        "project_id": str(values.project_id),
        "target_kind": values.target_kind,
        "target_id": str(values.target_id),
        "content": content[values.character_offset : end],
        "range": {
            "offset": values.character_offset,
            "length": end - values.character_offset,
            "total": len(content),
        },
        "next": {**values.model_dump(mode="json"), "character_offset": end}
        if end < len(content)
        else {
            **values.model_dump(mode="json"),
            "offset": values.offset + values.limit,
            "character_offset": 0,
        }
        if values.offset + values.limit < len(groups)
        else None,
    }


def authorized_business_image_locked(session, identity, values):
    from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO

    if values.target_kind == "canvas_node":
        return _authorized_canvas_image_locked(session, identity, values)
    catalog = _business_catalog(session, identity, values)
    group = next((item for item in catalog["groups"] if item["id"] == str(values.group_id)), None)
    slot = next(
        (item for item in (group or {}).get("items", []) if item["id"] == str(values.item_id)), None
    )
    if slot is None or str(values.media_id) not in {slot["media_id"], *slot["history_media_ids"]}:
        raise NotFound("图片不属于获准目标的指定图位")
    media = BusinessInvocationDAO(session).media(values.media_id)
    if media is None:
        raise NotFound("图片不存在或不再可访问")
    return media


def _authorized_canvas_image_locked(session, identity, values):
    from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
    from short_drama.service.assistant.operations.invocation import require_invocation_subject

    dao = BusinessInvocationDAO(session)
    target = dao.canvas_image_target(
        identity.actor.user_id, values.project_id, values.canvas_id, values.target_id
    )
    if target is None:
        raise NotFound("图片节点不存在或不属于获准作品和画布")
    if identity.scope.subject_policy == "selected":
        require_invocation_subject(identity, "canvas_node", values.target_id)
        snapshot = identity.scope.subject_snapshots.get(f"canvas_node:{values.target_id}")
        if snapshot is None or snapshot["source_id"] != values.canvas_id:
            raise NotFound("节点不属于获准画布快照")
        identifiers = {
            int(item["media_id"]) for item in snapshot.get("media", []) if item["kind"] == "image"
        }
    else:
        identifiers = dao.canvas_node_images(identity.actor.user_id, target[0].id, values.target_id)
    media = dao.media(values.media_id) if values.media_id in identifiers else None
    if media is None or not media.format_code.startswith("image/"):
        raise NotFound("图片不属于获准节点的冻结媒体或本范围生成成果")
    return media


def read_image_content_business_locked(session, identity, values, receipt_id, settings):
    media = authorized_business_image_locked(session, identity, values)
    return {
        "media_id": str(media.id),
        "project_id": str(values.project_id),
        "mime_type": media.format_code,
        "byte_size": media.byte_size,
        "checksum_sha256": media.checksum_sha256,
        "image_ref": {
            "schema_version": 1,
            "kind": "image",
            "ref_id": f"media:{media.id}",
            "uri": f"artifact://{media.id}",
            "source": "business_image",
            "checksum": media.checksum_sha256,
            "metadata": {"media_id": str(media.id), "mime_type": media.format_code},
        },
    }


def read_business_image_bytes(factory, identity, values, storage, *, max_bytes=20 * 1024**2):
    """Task-independent authorized image download; storage I/O never holds a DB transaction."""
    import hashlib

    from short_drama.service.assistant.operations.invocation import BusinessInvocationService
    from short_drama.storage.models import ObjectLocation

    with factory() as session:
        service = BusinessInvocationService(session)

        def descriptor(session, identity, values, receipt_id, settings):
            media = authorized_business_image_locked(session, identity, values)
            return {
                "locator": media.storage_locator,
                "checksum": media.checksum_sha256,
                "mime_type": media.format_code,
            }

        result = service.execute(
            identity, "workbench__read_image_content", values, descriptor, mutating=False
        )
    location = ObjectLocation.parse(
        result["locator"],
        {
            storage.settings.minio_image_bucket,
        },
    )
    with storage.open(location.bucket, location.object_name) as stream:
        data = stream.read(max_bytes + 1)
    if (
        not data
        or len(data) > max_bytes
        or (result["checksum"] and hashlib.sha256(data).hexdigest() != result["checksum"])
    ):
        raise Conflict("图片内容或校验值不一致")
    return data, result["mime_type"]


def set_image_group_primary_business_locked(session, identity, values, receipt_id, settings):
    catalog = _business_catalog(session, identity, values, lock=True)
    group = next((item for item in catalog["groups"] if item["id"] == str(values.group_id)), None)
    slot = next(
        (item for item in (group or {}).get("items", []) if item["id"] == str(values.item_id)), None
    )
    if slot is None or slot["status"] != "ready":
        raise Conflict("图位不存在或未就绪")
    trusted = {
        "target_kind": values.target_kind,
        "target_id": str(values.target_id),
        "expected_primary_version": group["primary_version"],
        "expected_item_version": slot["item_version"],
        "expected_target_version": catalog["target_version"],
        "is_current": group["is_current"],
        "media_id": slot["media_id"],
    }
    return AssistantImageService(session).set_primary_locked(
        identity.actor.user_id, values.project_id, values, operation_id=receipt_id, trusted=trusted
    )
