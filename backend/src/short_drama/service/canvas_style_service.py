"""画风原件、完整来源快照与资源引用在同一事务中保存。"""

import json

from pydantic import ValidationError

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.dao.canvas_style_dao import CanvasStyleDAO
from short_drama.db.canvas_style_access import canvas_style_write
from short_drama.domain import (
    CanvasStyleMediaReference,
    CanvasStylePreference,
    CanvasStyleProfile,
    MediaFile,
)
from short_drama.schemas.canvas_style import CanvasStyleSnapshot

from .base import utcnow
from .canvas_document import content_hash, media_references
from .canvas_service import CanvasService, iso


def parse_style(value: str) -> CanvasStyleSnapshot:
    try:
        return CanvasStyleSnapshot.model_validate_json(value)
    except ValidationError as error:
        raise WorkflowError(
            "canvas_style_invalid", "画风快照格式无效，请检查必填内容与执行资产", 422
        ) from error


def style_resources(profile: CanvasStyleSnapshot) -> set[int]:
    value = profile.model_dump(mode="json", by_alias=True, exclude_none=True)
    result = {identifier for _, identifier in media_references(value)}
    for asset in profile.assets:
        result.update(asset.reference_resource_ids or [])
    return result


class CanvasStyleService(CanvasService):
    def __init__(self, session):
        super().__init__(session)
        self.styles = CanvasStyleDAO(session)
        self.resources = CanvasResourceDAO(session)

    def _personal(self, identifier: int):
        entity = self.styles.get(identifier, lock=True)
        if entity is None or entity.scope_user_id != self.actor_id:
            raise NotFound("本人风格不存在或已删除")
        return entity

    @staticmethod
    def _version(entity, expected: int):
        if entity.row_version != expected:
            raise WorkflowError(
                "canvas_style_conflict", "风格已在其他会话修改，请保留草稿并重新读取", 409
            )

    def _references(self, entity, profile):
        identifiers = style_resources(profile)
        media = self.resources.resource_map(identifiers, lock=True)
        if any(not isinstance(media.get(identifier), MediaFile) for identifier in identifiers):
            raise NotFound("画风引用的媒体已失效或无权访问")
        current = {item.media_id: item for item in self.styles.references(entity.id)}
        for identifier, reference in current.items():
            if identifier not in identifiers:
                self.session.delete(reference)
        for identifier in identifiers - current.keys():
            self.session.add(
                CanvasStyleMediaReference(**self.audit(), style_id=entity.id, media_id=identifier)
            )

    def _store(self, entity, profile):
        entity.profile_json = profile.model_dump(mode="json", by_alias=True, exclude_none=True)
        entity.updated_at, entity.updated_by = utcnow(), self.actor_id
        self.session.flush()
        self._references(entity, profile)
        self.session.flush()

    @staticmethod
    def _read_style(entity):
        snapshot = CanvasStyleSnapshot.model_validate(entity.profile_json)
        profile = snapshot.model_dump(mode="json", by_alias=True, exclude_none=True)
        return {
            "id": str(entity.id),
            "userId": str(entity.scope_user_id),
            "name": snapshot.title,
            "description": snapshot.description,
            "coverUrl": snapshot.cover_url or "",
            "tagsJson": json.dumps(snapshot.tags, ensure_ascii=False),
            "profileJson": json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
            "favorite": bool(entity.favorite),
            "lastUsedAt": iso(entity.last_used_at) if entity.last_used_at else None,
            "revision": snapshot.revision,
            "rowVersion": str(entity.row_version),
            "createdAt": iso(entity.created_at),
            "updatedAt": iso(entity.updated_at),
        }

    def list_personal(self):
        with self._transaction(read_only=True):
            return {
                "profiles": [self._read_style(item) for item in self.styles.personal(self.actor_id)]
            }

    def _preferences(self):
        items = self.styles.preferences(self.actor_id)
        recent = sorted(
            (item for item in items if item.last_used_at is not None),
            key=lambda item: (item.last_used_at, item.id),
            reverse=True,
        )
        return {
            "favorites": sorted(item.preset_id for item in items if item.favorite),
            "recent": [item.preset_id for item in recent[:12]],
        }

    def preferences(self):
        with self._transaction(read_only=True):
            return self._preferences()

    def write_preference(self, payload):
        with self._global_order_lock(), self._transaction(), canvas_style_write(self.session):
            entity = self.styles.preference(self.actor_id, payload.preset_id)
            if entity is None:
                entity = CanvasStylePreference(
                    **self.audit(), user_id=self.actor_id, preset_id=payload.preset_id, favorite=0
                )
                self.session.add(entity)
            if payload.action == "favorite":
                entity.favorite = int(payload.favorite)
            else:
                entity.last_used_at = utcnow()
            entity.updated_at, entity.updated_by = utcnow(), self.actor_id
            self.session.flush()
            return self._preferences()

    def create_personal(self, payload):
        snapshot = parse_style(payload.profile_json)
        key = payload.import_key or content_hash(snapshot.model_dump(mode="json", by_alias=True))
        with self._global_order_lock(), self._transaction(), canvas_style_write(self.session):
            previous = self.styles.imported(self.actor_id, key)
            if previous is not None:
                if previous.deleted_at is not None:
                    raise WorkflowError(
                        "canvas_style_import_deleted",
                        "该风格先前已导入后删除，请复制为新风格再保存",
                        409,
                    )
                return {"profile": self._read_style(previous)}
            entity = CanvasStyleProfile(
                **self.audit(),
                scope_user_id=self.actor_id,
                profile_json={},
                row_version=1,
                import_key=key,
                favorite=0,
            )
            self.session.add(entity)
            snapshot = snapshot.model_copy(
                update={
                    "preset_id": str(entity.id),
                    "source_profile_id": str(entity.id),
                    "source": "user",
                }
            )
            self._store(entity, snapshot)
            return {"profile": self._read_style(entity)}

    def update_personal(self, identifier: int, payload):
        snapshot = parse_style(payload.profile_json)
        with self._global_order_lock(), self._transaction(), canvas_style_write(self.session):
            entity = self._personal(identifier)
            self._version(entity, payload.expected_row_version)
            entity.row_version += 1
            snapshot = snapshot.model_copy(
                update={
                    "preset_id": str(entity.id),
                    "source_profile_id": str(entity.id),
                    "source": "user",
                    "revision": int(entity.profile_json["revision"]) + 1,
                }
            )
            self._store(entity, snapshot)
            return {"profile": self._read_style(entity)}

    def personal_action(self, identifier: int, action: str, payload):
        with self._global_order_lock(), self._transaction(), canvas_style_write(self.session):
            entity = self._personal(identifier)
            self._version(entity, payload.expected_row_version)
            if action == "favorite":
                entity.favorite = int(payload.favorite)
            elif action == "use":
                entity.last_used_at = utcnow()
            elif action == "delete":
                entity.deleted_at = utcnow()
                for reference in self.styles.references(entity.id):
                    self.session.delete(reference)
            else:
                raise ValueError("Unknown style action")
            entity.row_version += 1
            entity.updated_at, entity.updated_by = utcnow(), self.actor_id
            self.session.flush()
            return {
                "id": str(entity.id),
                "rowVersion": str(entity.row_version),
                "favorite": bool(entity.favorite),
            }
