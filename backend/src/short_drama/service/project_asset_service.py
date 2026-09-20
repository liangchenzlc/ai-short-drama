from short_drama.domain import Asset, Project, ProjectAsset
from short_drama.schemas import ProjectAssetCreate, ProjectAssetRead, ProjectAssetUpdate

from .base import BaseService


class ProjectAssetService(BaseService):
    model = ProjectAsset
    create_schema = ProjectAssetCreate
    update_schema = ProjectAssetUpdate
    read_schema = ProjectAssetRead
    parent_model = Project
    parent_field = "project_id"

    def _validate_create(self, values):
        self._require(Asset, values["asset_id"])

    def _validate_update(self, entity, values):
        if "asset_id" in values and values["asset_id"] != entity.asset_id:
            self._require(Asset, values["asset_id"])
