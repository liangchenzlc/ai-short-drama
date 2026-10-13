import copy
from typing import Literal

from short_drama.core.exceptions import BusinessError, Conflict, WorkflowError
from short_drama.dao.asset_library_dao import AssetLibraryDAO
from short_drama.domain import Asset
from short_drama.schemas.asset import AssetCreate, AssetRecordRead, AssetUpdate
from short_drama.schemas.base import UINT64_MAX

from .asset_version_service import AssetVersionService, canonical_current
from .base import BaseService
from .episode_asset_service import EpisodeAssetService
from .global_asset_service import GlobalAssetService
from .project_asset_service import ProjectAssetService


class AssetService(BaseService):
    model = Asset
    create_schema = AssetCreate
    update_schema = AssetUpdate
    read_schema = AssetRecordRead

    def create(self, payload):
        values = self._payload(self.create_schema, payload)
        with self._transaction():
            self._lock_parent(values)
            self._validate_create(values)
            asset = self.dao.create(self._creation_audit(values))
            AssetVersionService(self.session).append_locked(asset)
            return self._read(asset)

    def _validate_create(self, values):
        self._validate_model(values.get("model_id"), "image")
        self._validate_media(values.get("media_id"), "image")

    def _validate_update(self, entity, values):
        from .asset_kind import validate_asset_kind_change

        validate_asset_kind_change(self.session, entity, values)
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
            self.apply_patch_locked(entity, values, expected, confirm_shared=confirm_shared)
            return self._read(entity)

    def apply_patch_locked(self, entity, values, expected, *, confirm_shared=False):
        """Caller owns project/asset locks and the transaction, including any receipt."""
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
        references = AssetLibraryDAO(self.session).reference_count(entity.id)
        if references > 1 and not confirm_shared:
            raise WorkflowError(
                "shared_asset_confirmation_required",
                "Confirm updating every reference to this shared asset",
                details={"reference_count": references},
            )
        if entity.row_version >= UINT64_MAX:
            raise Conflict("Asset version exhausted")
        changes.update(row_version=entity.row_version + 1, state="unconfirmed")
        self._apply_update(entity, changes)
        AssetVersionService(self.session).append_locked(entity)
        return entity

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
            version_service = AssetVersionService(self.session)
            source_snapshot = None
            if original.kind == "character":
                source_snapshot = version_service.snapshot_for_copy_locked(original)
            model_id = patch.pop("model_id", original.model_id)
            values = {field: getattr(original, field) for field in AssetCreate.model_fields}
            values.update(patch)
            values = AssetCreate.model_validate(values).model_dump()
            values["reference_media_ids"] = copy.deepcopy(original.reference_media_ids or [])
            values["model_id"] = model_id
            # Historical model selections are preserved; newly selected references
            # receive exactly the same validation as an explicit asset edit.
            self._validate_update(original, values)
            copied = self.dao.create(self._creation_audit(values))
            if copied.kind == "character":
                version_service.append_locked(
                    copied,
                    snapshot=canonical_current(copied, previous=source_snapshot),
                    source_audit={
                        "operation": "copy_for_library",
                        "source_asset_id": str(original.id),
                        "source_version_id": (
                            str(original.current_version_id)
                            if original.current_version_id
                            else None
                        ),
                    },
                )
            association_service._apply_update(association, {"asset_id": copied.id})
            return self._read(copied)
