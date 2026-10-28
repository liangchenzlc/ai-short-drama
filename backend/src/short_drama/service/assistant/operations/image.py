"""Frozen image identities and native primary adoption inside an assistant transaction."""

from sqlalchemy import or_, select

from short_drama.core.exceptions import BusinessError, Conflict, NotFound
from short_drama.core.identity import ActorContext
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
from short_drama.service.media.image_groups.groups import ImageGroupService
from short_drama.service.media.image_tools.adoption import (
    _group_state,
)
from short_drama.service.media.image_tools.adoption import (
    image_adoption_state as image_adoption_state,
)


def image_target(session, owner, project_id, kind, identifier, *, lock=False):
    project_query = select(Project).where(
        Project.id == int(project_id),
        Project.owner_user_id == owner,
        Project.workspace_mode == "standard",
        Project.archived_at.is_(None),
    )
    if lock:
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


def freeze_image_catalog(session, owner, project_id, kind, identifier, *, lock=False):
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
    _project, target = image_target(session, owner, project_id, target_kind, target_id, lock=lock)
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
        self.session.info["actor"] = actor or ActorContext(
            owner, "", "", True, 0, "", f"assistant:{operation_id}"
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
