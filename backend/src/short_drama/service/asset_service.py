from typing import Literal

from short_drama.core.exceptions import BusinessError
from short_drama.domain import Asset
from short_drama.schemas import AssetCreate, AssetRead, AssetUpdate

from .base import BaseService
from .episode_asset_service import EpisodeAssetService
from .global_asset_service import GlobalAssetService
from .project_asset_service import ProjectAssetService


class AssetService(BaseService):
    model = Asset
    create_schema = AssetCreate
    update_schema = AssetUpdate
    read_schema = AssetRead

    def _validate_create(self, values):
        self._validate_model(values.get("model_id"), "image")
        self._validate_media(values.get("media_id"), "image")

    def _validate_update(self, entity, values):
        if "model_id" in values and values["model_id"] != entity.model_id:
            self._validate_model(values["model_id"], "image")
        if "media_id" in values and values["media_id"] != entity.media_id:
            self._validate_media(values["media_id"], "image")

    def copy_for_library(
        self,
        library: Literal["global", "project", "episode"],
        link_id: int | str,
        changes: AssetUpdate | dict,
    ) -> AssetRead:
        """Copy shared content and atomically detach one library association from it."""
        library_services = {
            "global": GlobalAssetService,
            "project": ProjectAssetService,
            "episode": EpisodeAssetService,
        }
        if not isinstance(library, str) or library not in library_services:
            raise BusinessError("Unknown asset library")
        patch = self._payload(AssetUpdate, changes)
        association_service = library_services[library](self.session)
        # The association's transaction also owns the global-library named lock when
        # applicable. _get_locked orders the parent lock before the association lock.
        with association_service._transaction():
            association = association_service._get_locked(link_id)
            original = self._require(Asset, association.asset_id)
            values = {field: getattr(original, field) for field in AssetCreate.model_fields}
            values.update(patch)
            values = AssetCreate.model_validate(values).model_dump()
            # Historical model selections are preserved; newly selected references
            # receive exactly the same validation as an explicit asset edit.
            self._validate_update(original, values)
            copied = self.dao.create(self._creation_audit(values))
            association_service._apply_update(association, {"asset_id": copied.id})
            return self._read(copied)
