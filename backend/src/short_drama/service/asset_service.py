from typing import Literal

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.dao.asset_library_dao import AssetLibraryDAO
from short_drama.domain import Asset
from short_drama.schemas.asset import AssetCreate, AssetRecordRead, AssetUpdate

from .base import BaseService
from .episode_asset_service import EpisodeAssetService
from .global_asset_service import GlobalAssetService
from .project_asset_service import ProjectAssetService


class AssetService(BaseService):
    model = Asset
    create_schema = AssetCreate
    update_schema = AssetUpdate
    read_schema = AssetRecordRead

    def _validate_create(self, values):
        self._validate_model(values.get("model_id"), "image")
        self._validate_media(values.get("media_id"), "image")

    def _validate_update(self, entity, values):
        if "kind" in values and values["kind"] != entity.kind:
            raise BusinessError("Asset kind cannot be changed")
        if entity.kind != "scene" and values.get("scene_time"):
            raise BusinessError("scene_time is only allowed for scene assets")
        if "model_id" in values and values["model_id"] != entity.model_id:
            self._validate_model(values["model_id"], "image")
        if "media_id" in values and values["media_id"] != entity.media_id:
            self._validate_media(values["media_id"], "image")

    def update(self, identifier, payload):
        """Legacy internal edits use the same optimistic/shared semantics as the public API."""
        confirm_shared = False
        if isinstance(payload, dict):
            payload = dict(payload)
            confirm_shared = payload.pop("confirm_shared", False)
        values = self._payload(self.update_schema, payload)
        expected = values.pop("row_version", None)
        if expected is None:
            raise BusinessError("Asset updates require row_version")
        with self._transaction():
            entity = self._get_locked(identifier)
            if entity.row_version != expected:
                raise WorkflowError(
                    "asset_version_conflict",
                    "Asset changed; refresh before saving",
                    details={"current_version": entity.row_version},
                )
            self._validate_update(entity, values)
            changes = {key: value for key, value in values.items() if getattr(entity, key) != value}
            if not changes:
                return self._read(entity)
            references = AssetLibraryDAO(self.session).reference_count(entity.id)
            if references > 1 and not confirm_shared:
                raise WorkflowError(
                    "shared_asset_confirmation_required",
                    "Confirm updating every reference to this shared asset",
                    details={"reference_count": references},
                )
            changes.update(row_version=entity.row_version + 1, state="unconfirmed")
            return self._read(self._apply_update(entity, changes))

    def copy_for_library(
        self,
        library: Literal["global", "project", "episode"],
        link_id: int | str,
        changes: AssetUpdate | dict,
    ) -> AssetRecordRead:
        """Copy shared content and atomically detach one library association from it."""
        library_services = {
            "global": GlobalAssetService,
            "project": ProjectAssetService,
            "episode": EpisodeAssetService,
        }
        if not isinstance(library, str) or library not in library_services:
            raise BusinessError("Unknown asset library")
        patch = self._payload(AssetUpdate, changes)
        patch.pop("row_version", None)
        patch.pop("confirm_shared", None)
        association_service = library_services[library](self.session)
        # The association's transaction also owns the global-library named lock when
        # applicable. _get_locked orders the parent lock before the association lock.
        with association_service._transaction():
            association = association_service._get_locked(link_id)
            original = self._require(Asset, association.asset_id)
            model_id = patch.pop("model_id", original.model_id)
            values = {field: getattr(original, field) for field in AssetCreate.model_fields}
            values.update(patch)
            values = AssetCreate.model_validate(values).model_dump()
            values["model_id"] = model_id
            # Historical model selections are preserved; newly selected references
            # receive exactly the same validation as an explicit asset edit.
            self._validate_update(original, values)
            copied = self.dao.create(self._creation_audit(values))
            association_service._apply_update(association, {"asset_id": copied.id})
            return self._read(copied)
