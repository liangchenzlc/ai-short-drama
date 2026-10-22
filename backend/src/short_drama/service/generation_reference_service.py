"""Persistent input images, separate from generated candidates and adopted images."""

from sqlalchemy import select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.domain import Asset, MediaFile, Project, ShotScript
from short_drama.schemas.base import parse_identifier

from .asset_image_service import AssetImageService
from .base import utcnow
from .publication import publish


class GenerationReferenceService(AssetImageService):
    def __init__(self, session, settings, storage, kind, version=None):
        super().__init__(session, settings, storage)
        self.owner_model = Asset if kind == "asset" else ShotScript
        self.assets = BaseDAO(session, self.owner_model)
        self.expected_version = parse_identifier(version) if version is not None else None

    def _owner(self, identifier, *, lock=False):
        creation = None
        if lock and self.owner_model is ShotScript:
            project_id = self.session.scalar(
                select(ShotScript.project_id).where(ShotScript.id == identifier)
            )
            if project_id is None:
                raise NotFound("分镜不存在")
            creation = self._require(Project, project_id)
        owner = self.assets.get(identifier, for_update=lock)
        if owner is None or getattr(owner, "deleted_at", None) is not None:
            raise NotFound("参考图片所属内容不存在")
        if lock and owner.row_version != self.expected_version:
            raise WorkflowError("reference_version_conflict", "内容已修改，请刷新后重试")
        if self.session.info.get("actor"):
            from short_drama.db.access import scope_of, set_scope

            set_scope(self.session, scope_of(self.session, owner))
        return owner, creation

    def _read_references(self, owner):
        ids = [int(value) for value in owner.reference_media_ids or []]
        rows = (
            list(self.session.scalars(select(MediaFile).where(MediaFile.id.in_(ids))))
            if ids
            else []
        )
        by_id = {row.id: row for row in rows}
        return {
            "row_version": str(owner.row_version),
            "items": [
                {
                    "media_id": str(row.id),
                    "name": row.original_name,
                    "url": self.storage.download_url(row.storage_locator),
                    "width": row.width,
                    "height": row.height,
                }
                for identifier in ids
                if (row := by_id.get(identifier)) is not None
            ],
        }

    def list_references(self, identifier):
        with self._transaction(read_only=True):
            owner, _ = self._owner(parse_identifier(identifier))
            return self._read_references(owner)

    def _ensure_asset_exists(self, asset_id):
        with self._transaction():
            self._owner(asset_id, lock=True)

    def _advance(self, owner, creation):
        owner.row_version += 1
        owner.updated_at = utcnow()
        if creation is not None:
            creation.storyboard_version += 1
            creation.updated_at = utcnow()
        self.session.flush()

        if isinstance(owner, Asset):
            from .asset_version_service import AssetVersionService

            AssetVersionService(self.session).append_locked(owner)

    def _persist_upload(self, asset_id, inspected, stored, original_name):
        with self._transaction():
            owner, creation = self._owner(asset_id, lock=True)
            ids = list(owner.reference_media_ids or [])
            existing = (
                list(
                    self.session.scalars(
                        select(MediaFile).where(MediaFile.id.in_([int(value) for value in ids]))
                    )
                )
                if ids
                else []
            )
            if any(
                row.checksum_sha256 == inspected.checksum_sha256
                and row.byte_size == inspected.byte_size
                for row in existing
            ):
                return self._read_references(owner), False
            if len(ids) >= 16:
                raise WorkflowError("reference_limit_exceeded", "最多上传 16 张参考图片", 422)
            now = utcnow()
            media = self.media.create(
                {
                    "format_code": inspected.content_type,
                    "storage_locator": stored.storage_locator,
                    "original_name": (original_name or "参考图片")[:255],
                    "byte_size": inspected.byte_size,
                    "width": inspected.width,
                    "height": inspected.height,
                    "duration_ms": None,
                    "checksum_sha256": inspected.checksum_sha256,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            owner.reference_media_ids = [*ids, str(media.id)]
            publish(media)
            self._advance(owner, creation)
            return self._read_references(owner), True

    def remove_reference(self, identifier, media_id):
        with self._transaction():
            owner, creation = self._owner(parse_identifier(identifier), lock=True)
            media_id = str(parse_identifier(media_id))
            ids = [str(value) for value in owner.reference_media_ids or []]
            if media_id in ids:
                owner.reference_media_ids = [value for value in ids if value != media_id]
                self._advance(owner, creation)
            # Keep the file: historical generation requests may still reference it.
            return self._read_references(owner)
