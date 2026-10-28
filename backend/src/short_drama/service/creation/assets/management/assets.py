from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.domain import Asset, Project
from short_drama.schemas.base import UINT64_MAX
from short_drama.schemas.creation.assets.management.assets import (
    AssetCreate,
    AssetRecordRead,
    AssetUpdate,
)
from short_drama.service.base import BaseService
from short_drama.service.creation.assets.management.version import AssetVersionService


class AssetService(BaseService):
    model = Asset
    create_schema = AssetCreate
    update_schema = AssetUpdate
    read_schema = AssetRecordRead

    def create(self, payload, *, project_id):
        values = self._payload(self.create_schema, payload)
        with self._transaction():
            from short_drama.dao.creation.assets.management.library import AssetLibraryDAO
            from short_drama.db.access.identity import set_scope

            project = self._require(Project, project_id)
            values.update(
                project_id=project.id,
                position=AssetLibraryDAO(self.session).next_position("project", project.id),
                deleted_at=None,
            )
            if values["position"] > 2**32 - 1:
                raise Conflict("Asset position exhausted")
            set_scope(self.session, (None, project.id))
            self._validate_create(values)
            if values.get("media_id") is not None:
                media = self._validate_media(values["media_id"], "image")
                if media.project_id != project.id:
                    raise BusinessError("Asset media must belong to the same project")
            asset = self.dao.create(self._creation_audit(values))
            AssetVersionService(self.session).append_locked(asset)
            return self._read(asset)

    def _validate_create(self, values):
        self._validate_model(values.get("model_id"), "image")
        self._validate_media(values.get("media_id"), "image")

    def _validate_update(self, entity, values):
        from short_drama.service.creation.assets.management.kinds import validate_asset_kind_change

        validate_asset_kind_change(self.session, entity, values)
        if "model_id" in values and values["model_id"] != entity.model_id:
            self._validate_model(values["model_id"], "image")
        if "media_id" in values and values["media_id"] != entity.media_id:
            self._validate_media(values["media_id"], "image")

    def update(self, identifier, payload):
        """Internal edits preserve optimistic locking and immutable artwork history."""
        values = self._payload(self.update_schema, payload)
        expected = values.pop("row_version", None)
        if expected is None:
            raise BusinessError("Asset updates require row_version")
        with self._transaction():
            entity = self._get_locked(identifier)
            self.apply_patch_locked(entity, values, expected)
            return self._read(entity)

    def apply_patch_locked(self, entity, values, expected):
        """Caller owns project/asset locks and the transaction, including any receipt."""
        if entity.deleted_at is not None:
            raise NotFound("Asset does not exist")
        if entity.row_version != expected:
            raise WorkflowError(
                "asset_version_conflict",
                "Asset changed; refresh before saving",
                details={"current_version": entity.row_version},
            )
        self._validate_update(entity, values)
        changes = {key: value for key, value in values.items() if getattr(entity, key) != value}
        if not changes:
            return entity
        if entity.row_version >= UINT64_MAX:
            raise Conflict("Asset version exhausted")
        changes.update(row_version=entity.row_version + 1, state="unconfirmed")
        self._apply_update(entity, changes)
        AssetVersionService(self.session).append_locked(entity)
        return entity
