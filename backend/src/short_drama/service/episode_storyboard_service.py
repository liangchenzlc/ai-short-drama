"""Versioned CRUD for the episode storyboard aggregate."""

import hashlib
import json
from collections.abc import Iterable, Mapping

from pydantic import TypeAdapter

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.episode_storyboard_dao import (
    EpisodeStoryboardDAO,
    advance_shot_version,
    advance_storyboard_version,
    require_shot_version,
    require_storyboard_version,
)
from short_drama.domain.episode import Episode
from short_drama.domain.shot_script import ShotScript
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.episode_storyboard import (
    ShotImageSettings,
    StoryboardCreate,
    StoryboardGeneratedShot,
    StoryboardOrder,
    StoryboardShotRead,
    StoryboardUpdate,
)
from short_drama.service.storage_service import StorageService

from .base import BaseService, utcnow
from .shot_context import compute_shot_context_hash, normalize_shot_context

DEFAULT_IMAGE_SETTINGS = {"resolution": "2K", "aspect": "inherit", "layout": "single"}
MAX_ACTIVE_SHOTS = 500
_UNSET = object()


def normalize_creation_key(value: str) -> str:
    if not isinstance(value, str):
        raise BusinessError("Idempotency-Key must be text")
    value = value.strip()
    if not value or len(value) > 128:
        raise BusinessError("Idempotency-Key must contain 1 to 128 characters")
    return value


def storyboard_creation_hash(project_id: int, episode_id: int, payload: dict) -> str:
    intent = {
        "project_id": str(project_id),
        "episode_id": str(episode_id),
        "script": payload["script"],
        "duration_ms": payload["duration_ms"],
        "asset_ids": sorted(str(item) for item in payload["asset_ids"]),
        "image_settings": payload["image_settings"],
    }
    encoded = json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class EpisodeStoryboardService(BaseService):
    model = ShotScript

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.dao = EpisodeStoryboardDAO(session)
        self.settings = settings
        self.storage = storage

    def lock_episode(self, project_id, episode_id, expected_storyboard_version=None):
        episode = self.dao.scoped_episode(
            parse_identifier(project_id), parse_identifier(episode_id)
        )
        if expected_storyboard_version is not None:
            require_storyboard_version(episode, parse_identifier(expected_storyboard_version))
        return episode

    def list_active_for_update(self, episode_id):
        return self.dao.list_active_for_update(parse_identifier(episode_id))

    @staticmethod
    def advance_storyboard_version(episode):
        return advance_storyboard_version(episode)

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
        self, episode: Episode, shots: Iterable[Mapping], *, append=True
    ) -> list[ShotScript]:
        parsed = TypeAdapter(list[StoryboardGeneratedShot]).validate_python(list(shots))
        if len(parsed) > 100:
            raise WorkflowError("shot_limit_exceeded", "单次最多生成100个分镜", 422)
        active = self.dao.list_active_for_update(episode.id)
        if len(active) + len(parsed) > MAX_ACTIVE_SHOTS:
            raise WorkflowError("shot_limit_exceeded", "活动分镜最多500个", 422)
        position = max((row.position for row in active), default=0) + 1 if append else 1
        now = utcnow()
        rows = []
        for item in parsed:
            values = item.model_dump()
            asset_ids = values.pop("asset_ids")
            self.dao.require_episode_assets(episode.id, asset_ids)
            row = BaseDAO(self.session, ShotScript).create(
                {
                    "episode_id": episode.id,
                    "position": position,
                    "script": values["script"],
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

    def _context(self, episode, shot, assets=None):
        assets = self.dao.assets(shot.id) if assets is None else assets
        values = {
            "shot_id": shot.id,
            "reference_media_ids": getattr(shot, "reference_media_ids", None) or [],
            "script": shot.script,
            "duration_ms": shot.duration_ms,
            "episode_aspect": episode.aspect,
            "episode_style": episode.style,
            "assets": assets,
        }
        return normalize_shot_context(**values), compute_shot_context_hash(**values)

    def get_shot_context(self, project_id, episode_id, shot_id):
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id)
            shot = self.dao.scoped_shot(episode.id, parse_identifier(shot_id), for_update=False)
            context, digest = self._context(episode, shot)
            return {"context": context, "context_hash": digest}

    def _image(self, episode, shot, current_hash, row=_UNSET):
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
            "is_stale": image.context_hash is None or image.context_hash != current_hash,
        }

    def _shot(self, episode, shot, *, assets=None, image_row=_UNSET):
        asset_ids = (
            self.dao.asset_ids(shot.id) if assets is None else [asset.id for asset in assets]
        )
        assets = self.dao.assets(shot.id) if assets is None else assets
        _context, digest = self._context(episode, shot, assets)
        settings = shot.image_settings or DEFAULT_IMAGE_SETTINGS
        return StoryboardShotRead(
            id=shot.id,
            position=shot.position,
            script=shot.script,
            duration_ms=shot.duration_ms,
            source_excerpt=shot.source_excerpt,
            row_version=shot.row_version,
            asset_ids=asset_ids,
            image_settings=ShotImageSettings.model_validate(settings),
            context_hash=digest,
            image=self._image(episode, shot, digest, image_row),
            deleted_at=shot.deleted_at,
        ).model_dump(mode="json")

    def list(self, project_id, episode_id, offset=0, limit=100, include_archived=False):
        self.dao.validate_pagination(offset, limit)
        if type(include_archived) is not bool:
            raise BusinessError("include_archived must be boolean")
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id)
            rows, total = self.dao.list_rows(episode.id, include_archived, offset, limit)
            assets_by_shot, images_by_shot = self.dao.list_details([row.id for row in rows])
            return {
                "episode_id": str(episode.id),
                "storyboard_version": str(episode.storyboard_version),
                "items": [
                    self._shot(
                        episode,
                        row,
                        assets=assets_by_shot[row.id],
                        image_row=images_by_shot.get(row.id),
                    )
                    for row in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def get(self, project_id, episode_id, shot_id):
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id)
            shot = self.dao.scoped_shot(episode.id, parse_identifier(shot_id), for_update=False)
            return {
                "shot": self._shot(episode, shot),
                "storyboard_version": str(episode.storyboard_version),
            }

    def create(self, project_id, episode_id, payload, idempotency_key):
        # Creation defaults are part of the immutable idempotency intent.
        raw = payload.model_dump() if isinstance(payload, StoryboardCreate) else payload
        data = StoryboardCreate.model_validate(raw).model_dump()
        key = normalize_creation_key(idempotency_key)
        project_id, episode_id = parse_identifier(project_id), parse_identifier(episode_id)
        data["image_settings"] = ShotImageSettings.model_validate(
            data["image_settings"]
        ).model_dump()
        digest = storyboard_creation_hash(project_id, episode_id, data)
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id)
            replay = self.dao.by_creation_key(key)
            if replay is not None:
                if replay.episode_id != episode.id or replay.creation_hash != digest:
                    raise WorkflowError("idempotency_conflict", "该幂等键已用于不同的新建请求")
                return {
                    "shot": self._shot(episode, replay),
                    "storyboard_version": str(episode.storyboard_version),
                    "created": False,
                }
            require_storyboard_version(episode, data["storyboard_version"])
            active = self.dao.list_active_for_update(episode.id)
            if len(active) >= MAX_ACTIVE_SHOTS:
                raise WorkflowError("shot_limit_exceeded", "活动分镜最多500个", 422)
            self.dao.require_episode_assets(episode.id, data["asset_ids"])
            now = utcnow()
            shot = BaseDAO(self.session, ShotScript).create(
                {
                    "episode_id": episode.id,
                    "position": max((row.position for row in active), default=0) + 1,
                    "script": data["script"],
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
            advance_storyboard_version(episode)
            self.session.flush()
            return {
                "shot": self._shot(episode, shot),
                "storyboard_version": str(episode.storyboard_version),
                "created": True,
            }

    def update(self, project_id, episode_id, shot_id, payload):
        data = self._payload(StoryboardUpdate, payload)
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id)
            shot = self.dao.scoped_shot(episode.id, parse_identifier(shot_id))
            if shot.deleted_at is not None:
                raise WorkflowError("shot_archived", "归档分镜不能再编辑")
            require_shot_version(shot, data.pop("row_version"))
            now = utcnow()
            changed = False
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
                    self.dao.require_episode_assets(episode.id, requested)
                    self.dao.replace_assets(shot, requested, now)
                    changed = True
            if changed:
                shot.updated_at, shot.updated_by = now, None
                advance_shot_version(shot)
                advance_storyboard_version(episode)
                self.session.flush()
            return {
                "shot": self._shot(episode, shot),
                "storyboard_version": str(episode.storyboard_version),
            }

    def reorder(self, project_id, episode_id, payload):
        data = self._payload(StoryboardOrder, payload)
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id, data["storyboard_version"])
            rows = self.dao.list_active_for_update(episode.id)
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
                advance_storyboard_version(episode)
                self.session.flush()
            return {
                "storyboard_version": str(episode.storyboard_version),
                "ordered_ids": [str(item) for item in identifiers],
            }

    def move(self, project_id, episode_id, shot_id, payload):
        from short_drama.schemas.episode_storyboard import StoryboardMove

        data = StoryboardMove.model_validate(payload)
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id, data.storyboard_version)
            rows = self.dao.list_active_for_update(episode.id)
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
                advance_storyboard_version(episode)
                self.session.flush()
            return {"storyboard_version": str(episode.storyboard_version)}

    def archive(self, project_id, episode_id, shot_id, row_version):
        with self._transaction():
            episode = self.lock_episode(project_id, episode_id)
            shot = self.dao.scoped_shot(episode.id, parse_identifier(shot_id))
            if shot.deleted_at is not None:
                return
            require_shot_version(shot, parse_identifier(row_version))
            rows = self.dao.list_active_for_update(episode.id)
            now = utcnow()
            old_position = shot.position
            shot.deleted_at, shot.updated_at, shot.updated_by = now, now, None
            advance_shot_version(shot)
            self.session.flush()
            for row in rows:
                if row.id != shot.id and row.position > old_position:
                    row.position -= 1
                    row.updated_at, row.updated_by = now, None
                    advance_shot_version(row)
            advance_storyboard_version(episode)
            self.session.flush()
