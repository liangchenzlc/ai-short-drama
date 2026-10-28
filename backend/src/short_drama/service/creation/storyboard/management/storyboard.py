"""Versioned CRUD for the creation storyboard aggregate."""

import hashlib
import json
from collections.abc import Iterable, Mapping

from pydantic import TypeAdapter

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.creation.storyboard.management.storyboard import (
    ProjectStoryboardDAO,
    advance_shot_version,
    advance_storyboard_version,
    require_shot_version,
    require_storyboard_version,
)
from short_drama.domain.creation.storyboard.management.script import ShotScript
from short_drama.domain.projects.management.project import Project
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.creation.storyboard.management.storyboard import (
    ShotImageSettings,
    ShotVideoSettings,
    StoryboardCreate,
    StoryboardDelete,
    StoryboardGeneratedShot,
    StoryboardOrder,
    StoryboardShotRead,
    StoryboardUpdate,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.service.creation.storyboard.management.context import (
    compute_shot_context_hash,
    normalize_shot_context,
)
from short_drama.service.creation.storyboard.videos.video_context import (
    DEFAULT_VIDEO_SETTINGS,
    video_context_hash,
)
from short_drama.service.creation.writing.script.publication import public_voice_context
from short_drama.service.media.files.storage import StorageService

DEFAULT_IMAGE_SETTINGS = {"resolution": "2K", "aspect": None, "layout": "single"}
MAX_ACTIVE_SHOTS = 500
_UNSET = object()


def normalize_creation_key(value: str) -> str:
    if not isinstance(value, str):
        raise BusinessError("Idempotency-Key must be text")
    value = value.strip()
    if not value or len(value) > 128:
        raise BusinessError("Idempotency-Key must contain 1 to 128 characters")
    return value


def storyboard_creation_hash(project_id: int, payload: dict) -> str:
    intent = {
        "project_id": str(project_id),
        "script": payload["script"],
        "duration_ms": payload["duration_ms"],
        "asset_ids": sorted(str(item) for item in payload["asset_ids"]),
        "image_settings": payload["image_settings"],
    }
    if payload.get("title"):
        intent["title"] = payload["title"]
    if payload.get("shot_size") is not None:
        intent["shot_size"] = payload["shot_size"]
    encoded = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ProjectStoryboardService(BaseService):
    model = ShotScript

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.dao = ProjectStoryboardDAO(session)
        self.settings = settings
        self.storage = storage

    def lock_project(self, project_id, expected_storyboard_version=None, *, for_update=True):
        creation = self.dao.scoped_project(parse_identifier(project_id), for_update=for_update)
        if expected_storyboard_version is not None:
            require_storyboard_version(creation, parse_identifier(expected_storyboard_version))
        return creation

    def list_active_for_update(self, project_id):
        return self.dao.list_active_for_update(parse_identifier(project_id))

    @staticmethod
    def advance_storyboard_version(creation):
        return advance_storyboard_version(creation)

    def archive_active_locked(self, rows: Iterable[ShotScript]):
        now = utcnow()
        rows = list(rows)
        for row in rows:
            if row.deleted_at is None:
                row.deleted_at = now
                row.updated_at = now
                advance_shot_version(row)
        self.session.flush()
        return rows

    def create_generated_locked(
        self, creation: Project, shots: Iterable[Mapping], *, append=True
    ) -> list[ShotScript]:
        parsed = TypeAdapter(list[StoryboardGeneratedShot]).validate_python(list(shots))
        if len(parsed) > 100:
            raise WorkflowError("shot_limit_exceeded", "单次最多生成100个分镜", 422)
        active = self.dao.list_active_for_update(creation.id)
        if len(active) + len(parsed) > MAX_ACTIVE_SHOTS:
            raise WorkflowError("shot_limit_exceeded", "活动分镜最多500个", 422)
        position = max((row.position for row in active), default=0) + 1 if append else 1
        now = utcnow()
        rows = []
        for item in parsed:
            values = item.model_dump()
            asset_ids = values.pop("asset_ids")
            self.dao.require_project_assets(creation.id, asset_ids)
            row = BaseDAO(self.session, ShotScript).create(
                {
                    "project_id": creation.id,
                    "position": position,
                    "script": values["script"],
                    "title": values["title"],
                    "shot_size": values["shot_size"],
                    "duration_ms": values["duration_ms"],
                    "source_excerpt": values["source_excerpt"],
                    "row_version": 1,
                    "image_settings": None,
                    "deleted_at": None,
                    "creation_key": None,
                    "creation_hash": None,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": None,
                    "updated_by": None,
                }
            )
            self.dao.replace_assets(row, asset_ids, now)
            rows.append(row)
            position += 1
        return rows

    def _context(self, creation, shot, assets=None):
        assets = self.dao.assets(shot.id) if assets is None else assets
        values = {
            "shot_id": shot.id,
            "reference_media_ids": getattr(shot, "reference_media_ids", None) or [],
            "script": shot.script,
            "duration_ms": shot.duration_ms,
            "assets": assets,
        }
        return normalize_shot_context(**values), compute_shot_context_hash(**values)

    def get_shot_context(self, project_id, shot_id):
        with self._transaction(read_only=True):
            creation = self.lock_project(project_id, for_update=False)
            shot = self.dao.scoped_shot(creation.id, parse_identifier(shot_id), for_update=False)
            context, digest = self._context(creation, shot)
            return {"context": context, "context_hash": digest}

    def _image(self, creation, shot, current_hash, row=_UNSET):
        result = self.dao.image_row(shot.id) if row is _UNSET else row
        if result is None:
            return None
        image, media, media_asset_id = result
        url = None
        if self.storage is not None and self.settings is not None:
            url = StorageService(self.storage, self.settings).download_url(media.storage_locator)
        return {
            "media_id": str(media.id),
            "media_asset_id": str(media_asset_id) if media_asset_id is not None else None,
            "url": url,
            "width": media.width,
            "height": media.height,
            "layout": image.layout,
            "aspect": image.aspect,
            "resolution": image.resolution,
            "is_stale": False,
        }

    def _shot(
        self,
        creation,
        shot,
        *,
        assets=None,
        image_row=_UNSET,
        video_row=_UNSET,
        native_speech=_UNSET,
    ):
        from short_drama.ai.business_prompts import video_default_prompt
        from short_drama.ai.prompts.registry import shot_video_system_prompt
        from short_drama.service.creation.sound.voices.context import native_context

        asset_ids = (
            self.dao.asset_ids(shot.id) if assets is None else [asset.id for asset in assets]
        )
        assets = self.dao.assets(shot.id) if assets is None else assets
        _context, digest = self._context(creation, shot, assets)
        settings = shot.image_settings or DEFAULT_IMAGE_SETTINGS
        image = self._image(creation, shot, digest, image_row)
        prompt = getattr(shot, "video_prompt", "") or ""
        video_settings = getattr(shot, "video_settings", None) or DEFAULT_VIDEO_SETTINGS
        if native_speech is _UNSET:
            native_speech = native_context(self.session, shot, settings=self.settings)
        video_hash = video_context_hash(
            digest,
            image["media_id"] if image else None,
            prompt,
            video_settings,
            session=self.session,
            shot=shot,
            native_speech=native_speech,
        )
        result = self.dao.video_rows([shot.id]).get(shot.id) if video_row is _UNSET else video_row
        video = None
        if result:
            current, media, asset_id = result
            actor = self.session.info.get("actor")
            video = {
                "media_id": str(media.id),
                "media_asset_id": str(asset_id) if asset_id else None,
                "url": StorageService(self.storage, self.settings).download_url(
                    media.storage_locator
                )
                if self.storage is not None and self.settings is not None
                else None,
                "resolution": current.resolution,
                "duration_ms": media.duration_ms or current.duration,
                "first_frame_media_id": current.first_frame_media_id
                if actor is None or media.created_by == actor.user_id
                else None,
                "is_stale": False,
            }
        return StoryboardShotRead(
            id=shot.id,
            position=shot.position,
            title=getattr(shot, "title", "") or "",
            shot_size=getattr(shot, "shot_size", None),
            script=shot.script,
            duration_ms=shot.duration_ms,
            source_excerpt=shot.source_excerpt,
            row_version=shot.row_version,
            asset_ids=asset_ids,
            image_settings=ShotImageSettings.model_validate(settings),
            context_hash=digest,
            image=image,
            video_prompt=prompt,
            video_default_prompt=video_default_prompt(_context),
            video_system_prompt=shot_video_system_prompt(),
            video_settings=video_settings,
            video_context_hash=video_hash,
            native_speech=public_voice_context(native_speech),
            video=video,
            deleted_at=shot.deleted_at,
        ).model_dump(mode="json")

    def list(self, project_id, offset=0, limit=100):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            creation = self.lock_project(project_id, for_update=False)
            rows, total = self.dao.list_rows(creation.id, offset, limit)
            assets_by_shot, images_by_shot = self.dao.list_details([row.id for row in rows])
            videos_by_shot = self.dao.video_rows([row.id for row in rows])
            from short_drama.service.creation.sound.voices.context import native_contexts

            speech_by_shot = native_contexts(self.session, creation, rows, settings=self.settings)
            return {
                "project_id": str(creation.id),
                "storyboard_version": str(creation.storyboard_version),
                "items": [
                    self._shot(
                        creation,
                        row,
                        assets=assets_by_shot[row.id],
                        image_row=images_by_shot.get(row.id),
                        video_row=videos_by_shot.get(row.id),
                        native_speech=speech_by_shot[row.id],
                    )
                    for row in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def get(self, project_id, shot_id):
        with self._transaction(read_only=True):
            creation = self.lock_project(project_id, for_update=False)
            shot = self.dao.scoped_shot(creation.id, parse_identifier(shot_id), for_update=False)
            return {
                "shot": self._shot(creation, shot),
                "storyboard_version": str(creation.storyboard_version),
            }

    def create(self, project_id, payload, idempotency_key):
        # Creation defaults are part of the immutable idempotency intent.
        raw = payload.model_dump() if isinstance(payload, StoryboardCreate) else payload
        data = StoryboardCreate.model_validate(raw).model_dump()
        from short_drama.db.access.identity import scoped_key

        key = scoped_key(self.session, normalize_creation_key(idempotency_key))
        project_id = parse_identifier(project_id)
        data["image_settings"] = ShotImageSettings.model_validate(
            data["image_settings"]
        ).model_dump()
        digest = storyboard_creation_hash(project_id, data)
        with self._transaction():
            creation = self.lock_project(project_id)
            replay = self.dao.by_creation_key(key)
            if replay is not None:
                if replay.project_id != creation.id or replay.creation_hash != digest:
                    raise WorkflowError("idempotency_conflict", "该幂等键已用于不同的新建请求")
                return {
                    "shot": self._shot(creation, replay),
                    "storyboard_version": str(creation.storyboard_version),
                    "created": False,
                }
            require_storyboard_version(creation, data["storyboard_version"])
            active = self.dao.list_active_for_update(creation.id)
            if len(active) >= MAX_ACTIVE_SHOTS:
                raise WorkflowError("shot_limit_exceeded", "活动分镜最多500个", 422)
            self.dao.require_project_assets(creation.id, data["asset_ids"])
            now = utcnow()
            shot = BaseDAO(self.session, ShotScript).create(
                {
                    "project_id": creation.id,
                    "position": max((row.position for row in active), default=0) + 1,
                    "script": data["script"],
                    "title": data["title"],
                    "shot_size": data["shot_size"],
                    "duration_ms": data["duration_ms"],
                    "source_excerpt": "",
                    "row_version": 1,
                    "image_settings": data["image_settings"],
                    "deleted_at": None,
                    "creation_key": key,
                    "creation_hash": digest,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": None,
                    "updated_by": None,
                }
            )
            self.dao.replace_assets(shot, data["asset_ids"], now)
            advance_storyboard_version(creation)
            self.session.flush()
            return {
                "shot": self._shot(creation, shot),
                "storyboard_version": str(creation.storyboard_version),
                "created": True,
            }

    def update(self, project_id, shot_id, payload):
        data = self._payload(StoryboardUpdate, payload)
        with self._transaction():
            creation = self.lock_project(project_id)
            shot = self.dao.scoped_shot(creation.id, parse_identifier(shot_id))
            if shot.deleted_at is not None:
                raise WorkflowError("shot_archived", "归档分镜不能再编辑")
            require_shot_version(shot, data.pop("row_version"))
            now = utcnow()
            changed = False
            for field in ("title", "shot_size"):
                if field in data and getattr(shot, field) != data[field]:
                    setattr(shot, field, data[field])
                    changed = True
            if "video_prompt" in data and shot.video_prompt != data["video_prompt"]:
                shot.video_prompt = data["video_prompt"]
                changed = True
            if "video_settings" in data:
                settings = ShotVideoSettings.model_validate(data["video_settings"]).model_dump(
                    exclude_none=True
                )
                if (shot.video_settings or DEFAULT_VIDEO_SETTINGS) != settings:
                    shot.video_settings = settings
                    changed = True
            if "script" in data and shot.script != data["script"]:
                shot.script = data["script"]
                changed = True
            if "duration_ms" in data and shot.duration_ms != data["duration_ms"]:
                shot.duration_ms = data["duration_ms"]
                changed = True
            if "image_settings" in data:
                settings = ShotImageSettings.model_validate(data["image_settings"]).model_dump()
                if (shot.image_settings or DEFAULT_IMAGE_SETTINGS) != settings:
                    shot.image_settings = settings
                    changed = True
            if "asset_ids" in data:
                current = self.dao.asset_ids(shot.id)
                requested = sorted(data["asset_ids"])
                if current != requested:
                    self.dao.require_project_assets(creation.id, requested)
                    self.dao.replace_assets(shot, requested, now)
                    changed = True
            shot.updated_at, shot.updated_by = now, None
            advance_shot_version(shot)
            if changed:
                advance_storyboard_version(creation)
            self.session.flush()
            return {
                "shot": self._shot(creation, shot),
                "storyboard_version": str(creation.storyboard_version),
            }

    def reorder(self, project_id, payload):
        data = self._payload(StoryboardOrder, payload)
        with self._transaction():
            creation = self.lock_project(project_id, data["storyboard_version"])
            rows = self.dao.list_active_for_update(creation.id)
            identifiers = data["shot_ids"]
            by_id = {row.id: row for row in rows}
            if set(identifiers) != set(by_id) or len(identifiers) != len(rows):
                raise BusinessError("排序必须完整且仅包含当前活动分镜")
            changed = {
                identifier
                for position, identifier in enumerate(identifiers, 1)
                if by_id[identifier].position != position
            }
            if changed:
                maximum = max(row.position for row in rows)
                if maximum + len(rows) > 2**32 - 1:
                    raise Conflict("Insufficient temporary ordering space")
                now = utcnow()
                for position, identifier in enumerate(identifiers, maximum + 1):
                    by_id[identifier].position = position
                self.session.flush()
                for position, identifier in enumerate(identifiers, 1):
                    row = by_id[identifier]
                    row.position = position
                    if identifier in changed:
                        row.updated_at, row.updated_by = now, None
                        advance_shot_version(row)
                advance_storyboard_version(creation)
                self.session.flush()
            return {
                "storyboard_version": str(creation.storyboard_version),
                "ordered_ids": [str(item) for item in identifiers],
            }

    def delete(
        self,
        project_id: int | str,
        shot_id: int | str,
        payload: StoryboardDelete | dict[str, object],
    ) -> dict[str, str]:
        data = self._payload(StoryboardDelete, payload)
        with self._transaction():
            creation = self.lock_project(project_id, data["storyboard_version"])
            shot = self.dao.scoped_shot(creation.id, parse_identifier(shot_id))
            if shot.deleted_at is not None:
                raise NotFound("分镜不存在或已删除")
            require_shot_version(shot, data["row_version"])
            rows = self.dao.list_active_for_update(creation.id)
            remaining = [row for row in rows if row.id != shot.id]
            changed = [
                (row, position)
                for position, row in enumerate(remaining, 1)
                if row.position != position
            ]
            maximum = max(row.position for row in rows)
            if changed and maximum + len(changed) > 2**32 - 1:
                raise Conflict("Insufficient temporary ordering space")
            now = utcnow()
            shot.deleted_at, shot.updated_at = now, now
            advance_shot_version(shot)
            advance_storyboard_version(creation)
            self.dao.replace_assets(shot, [], now)
            self.session.flush()
            if changed:
                # MySQL checks active-position uniqueness for each changed row.
                for temporary, (row, _position) in enumerate(changed, maximum + 1):
                    row.position = temporary
                self.session.flush()
                for row, position in changed:
                    row.position = position
                    row.updated_at, row.updated_by = now, None
                    advance_shot_version(row)
                self.session.flush()
            return {
                "deleted_id": str(shot.id),
                "storyboard_version": str(creation.storyboard_version),
            }

    def move(self, project_id, shot_id, payload):
        from short_drama.schemas.creation.storyboard.management.storyboard import StoryboardMove

        data = StoryboardMove.model_validate(payload)
        with self._transaction():
            creation = self.lock_project(project_id, data.storyboard_version)
            rows = self.dao.list_active_for_update(creation.id)
            index = next(
                (i for i, row in enumerate(rows) if row.id == parse_identifier(shot_id)), None
            )
            if index is None:
                raise NotFound("分镜不存在")
            neighbor = index + data.direction
            if 0 <= neighbor < len(rows):
                first, second = rows[index], rows[neighbor]
                old_first, old_second = first.position, second.position
                if max(row.position for row in rows) >= 2**32 - 1:
                    raise Conflict("Insufficient temporary ordering space")
                first.position = max(row.position for row in rows) + 1
                self.session.flush()
                second.position = old_first
                self.session.flush()
                first.position = old_second
                for row in (first, second):
                    row.updated_at = utcnow()
                    advance_shot_version(row)
                advance_storyboard_version(creation)
                self.session.flush()
            return {"storyboard_version": str(creation.storyboard_version)}
