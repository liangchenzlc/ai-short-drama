import hashlib
import json
from contextlib import contextmanager

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.asset_library_dao import AssetLibraryDAO
from short_drama.dao.base import BaseDAO
from short_drama.domain.asset import Asset
from short_drama.domain.episode import Episode
from short_drama.domain.episode_asset import EpisodeAsset
from short_drama.domain.global_asset import GlobalAsset
from short_drama.domain.project import Project
from short_drama.schemas.asset import AssetImageRead, AssetPatch, AssetRead
from short_drama.schemas.asset_library import AssetLibraryCreate, LibraryAssetRead
from short_drama.schemas.base import parse_identifier

from .base import BaseService
from .global_asset_service import GlobalAssetService
from .storage_service import StorageService


def creation_fingerprint(kind, parent_id, payload):
    if hasattr(payload, "model_dump") and not isinstance(payload, AssetLibraryCreate):
        payload = payload.model_dump(exclude={"model_id", "media_id"})
    values = AssetLibraryCreate.model_validate(payload).model_dump(
        mode="json", exclude={"model_id", "media_id"}
    )
    raw = json.dumps(
        {"scope": kind, "parent_id": parent_id, "asset": values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AssetLibraryService(BaseService):
    model = Asset

    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.assets = BaseDAO(session, Asset)
        self.library = AssetLibraryDAO(session)
        self.storage = (
            storage
            if callable(getattr(storage, "download_url", None))
            else StorageService(storage, settings)
            if settings is not None and storage
            else None
        )

    def _validate_scope(self, kind, parent_id=None, project_id=None, *, for_update=False):
        if kind == "global":
            if parent_id is not None:
                raise Conflict("Global library has no parent")
            return None
        parent_id = parse_identifier(parent_id)
        if kind == "project":
            project = self._require(Project, parent_id, for_update=for_update)
            if project_id is not None and parse_identifier(project_id) != project.id:
                raise NotFound("Project does not exist")
            return project
        if kind == "episode":
            episode = self._require(Episode, parent_id, for_update=for_update)
            if project_id is not None and parse_identifier(project_id) != episode.project_id:
                raise NotFound("Episode does not belong to this project")
            return episode
        raise NotFound("Asset library does not exist")

    @contextmanager
    def _library_transaction(self, kind):
        if kind == "global":
            with GlobalAssetService(self.session)._transaction():
                yield
        else:
            with self._transaction():
                yield

    def _lock_scope(self, kind, parent_id, project_id=None):
        parent = self._validate_scope(kind, parent_id, project_id, for_update=True)
        if kind == "global":
            list(
                self.session.scalars(
                    select(GlobalAsset).order_by(GlobalAsset.position).with_for_update()
                )
            )
        return parent

    def _url(self, media):
        if media is None or self.storage is None:
            return None
        return self.storage.download_url(media.storage_locator)

    def _read(self, asset, media=None, *, link=None, library=False, reference_count=None):
        schema = LibraryAssetRead if library else AssetRead
        values = {
            name: getattr(asset, name) for name in schema.model_fields if hasattr(asset, name)
        }
        values["reference_count"] = (
            self.library.reference_count(asset.id) if reference_count is None else reference_count
        )
        values["image"] = None
        if media is not None:
            values["image"] = AssetImageRead(
                media_id=media.id,
                url=self._url(media) or "",
                width=media.width,
                height=media.height,
            )
        if library:
            values.update(
                link_id=getattr(link, "id", None), position=getattr(link, "position", None)
            )
            return LibraryAssetRead.model_validate(values)
        return AssetRead.model_validate(values)

    def list(
        self, kind, parent_id=None, project_id=None, *, asset_kind=None, q="", offset=0, limit=20
    ):
        self.library.scopes[kind][0]  # validate before opening a transaction
        self.assets.validate_pagination(offset, limit)
        with self._transaction():
            self._validate_scope(kind, parent_id, project_id, for_update=False)
            rows, total = self.library.links(
                kind, parent_id, asset_kind=asset_kind, query=q, offset=offset, limit=limit
            )
            counts = self.library.reference_counts([asset.id for _link, asset, _media in rows])
            return {
                "items": [
                    self._read(
                        asset, media, link=link, library=True, reference_count=counts[asset.id]
                    )
                    for link, asset, media in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def create(self, kind, parent_id, project_id, payload, idempotency_key):
        key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if not 1 <= len(key) <= 128:
            raise WorkflowError(
                "validation_error", "Idempotency-Key must contain 1 to 128 characters", 422
            )
        parsed = AssetLibraryCreate.model_validate(payload)
        digest = creation_fingerprint(kind, parent_id, parsed)
        try:
            with self._library_transaction(kind):
                self._lock_scope(kind, parent_id, project_id)
                existing = self.library.creation(key, for_update=True)
                if existing is not None:
                    return self._creation_replay(kind, parent_id, existing, digest)
                asset, link = self.create_locked(kind, parent_id, parsed, key, digest)
                return self._read(asset, link=link, library=True), True
        except Conflict:
            # Different scope parents do not share a row lock. Resolve a creation-key
            # unique race after rollback instead of surfacing an unstable SQL conflict.
            with self._transaction():
                existing = self.library.creation(key)
                if existing is None:
                    raise
                return self._creation_replay(kind, parent_id, existing, digest)

    def create_locked(self, kind, parent_id, parsed, key, digest, *, model_id=None):
        """Caller owns the scope lock, idempotency check, and transaction."""
        values = parsed.model_dump(exclude={"model_id", "media_id"})
        values.update(
            model_id=model_id,
            media_id=None,
            state="unconfirmed",
            row_version=1,
            creation_key=key,
            creation_hash=digest,
        )
        asset = self.assets.create(self._creation_audit(values))
        return asset, self._create_link(kind, parent_id, asset.id)

    def link_locked(self, kind, parent, asset_id):
        """Link without committing; caller has locked the parent scope."""
        existing = self.library.link(kind, parent.id, asset_id, for_update=True)
        if existing is None:
            if not self._may_link(kind, parent, asset_id):
                raise NotFound("Asset is not available through this sharing path")
            existing = self._create_link(kind, parent.id, asset_id)
        return existing

    def _creation_replay(self, kind, parent_id, existing, digest):
        if existing.creation_hash != digest:
            raise WorkflowError(
                "idempotency_conflict", "Idempotency-Key was used for another request"
            )
        link = self.library.link(kind, parent_id, existing.id)
        media = self.library.media(existing.media_id) if existing.media_id else None
        return self._read(existing, media, link=link, library=True), False

    def _create_link(self, kind, parent_id, asset_id):
        model, parent_field = self.library.scopes[kind]
        values = {"asset_id": asset_id, "position": self.library.next_position(kind, parent_id)}
        if parent_field:
            values[parent_field] = parent_id
        if values["position"] > 2**32 - 1:
            raise Conflict("Asset library order is full")
        values["created_at"] = self._creation_audit({}).get("created_at")
        values["created_by"] = None
        return BaseDAO(self.session, model).create(values)

    def _may_link(self, kind, parent, asset_id):
        if kind == "global":
            return self.library.link("global", None, asset_id) is not None
        if kind == "project":
            if self.library.link("global", None, asset_id) is not None:
                return True
            return (
                self.session.scalar(
                    select(EpisodeAsset.id)
                    .join(Episode, Episode.id == EpisodeAsset.episode_id)
                    .where(Episode.project_id == parent.id, EpisodeAsset.asset_id == asset_id)
                    .limit(1)
                )
                is not None
            )
        return self.library.link("project", parent.project_id, asset_id) is not None

    def link(self, kind, parent_id, project_id, asset_id):
        asset_id = parse_identifier(asset_id)
        with self._library_transaction(kind):
            parent = self._lock_scope(kind, parent_id, project_id)
            existing = self.library.link(kind, parent_id, asset_id, for_update=True)
            if existing is None:
                asset = self.assets.get(asset_id, for_update=False)
                if asset is None or not self._may_link(kind, parent, asset_id):
                    raise NotFound("Asset is not available through this sharing path")
                existing = self._create_link(kind, parent_id, asset_id)
            else:
                asset = self.assets.get(asset_id, for_update=False)
            media = self.library.media(asset.media_id) if asset.media_id else None
            return self._read(asset, media, link=existing, library=True)

    def unlink(self, kind, parent_id, project_id, asset_id, row_version):
        asset_id, row_version = parse_identifier(asset_id), parse_identifier(row_version)
        with self._library_transaction(kind):
            self._lock_scope(kind, parent_id, project_id)
            link = self.library.link(kind, parent_id, asset_id, for_update=True)
            if link is None:
                return
            asset = self.assets.get(asset_id, for_update=True)
            if asset.row_version != row_version:
                raise WorkflowError(
                    "asset_version_conflict",
                    "Asset changed; refresh before removing",
                    details={"current_version": asset.row_version},
                )
            if kind == "episode":
                shot_ids = self.library.active_shot_ids(parent_id, asset_id)
                if shot_ids:
                    raise WorkflowError(
                        "asset_in_use",
                        "Asset is used by active shots",
                        details={"references": [str(value) for value in shot_ids]},
                    )
            BaseDAO(self.session, type(link)).delete(link)

    def get(self, asset_id):
        asset_id = parse_identifier(asset_id)
        with self._transaction():
            asset = self.assets.get(asset_id)
            if asset is None:
                raise NotFound("Asset does not exist")
            media = self.library.media(asset.media_id) if asset.media_id else None
            return self._read(asset, media)

    def patch(self, asset_id, payload):
        parsed = AssetPatch.model_validate(payload)
        values = parsed.model_dump(exclude_unset=True, exclude={"row_version", "confirm_shared"})
        with self._transaction():
            asset = self.assets.get(parse_identifier(asset_id), for_update=True)
            if asset is None:
                raise NotFound("Asset does not exist")
            if asset.row_version != parsed.row_version:
                raise WorkflowError(
                    "asset_version_conflict",
                    "Asset changed; refresh before saving",
                    details={"current_version": asset.row_version},
                )
            if asset.kind != "scene" and values.get("scene_time"):
                raise WorkflowError(
                    "invalid_asset", "scene_time is only allowed for scene assets", 400
                )
            changes = {
                name: value for name, value in values.items() if getattr(asset, name) != value
            }
            if not changes:
                media = self.library.media(asset.media_id) if asset.media_id else None
                return self._read(asset, media)
            references = self.library.reference_count(asset.id)
            if references > 1 and not parsed.confirm_shared:
                raise WorkflowError(
                    "shared_asset_confirmation_required",
                    "Confirm updating every reference to this shared asset",
                    details={"reference_count": references},
                )
            changes.update(row_version=asset.row_version + 1, state="unconfirmed")
            self._apply_update(asset, changes)
            media = self.library.media(asset.media_id) if asset.media_id else None
            return self._read(asset, media)
