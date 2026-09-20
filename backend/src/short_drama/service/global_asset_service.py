from short_drama.domain import Asset, GlobalAsset
from short_drama.schemas import GlobalAssetCreate, GlobalAssetRead, GlobalAssetUpdate

from .base import BaseService


class GlobalAssetService(BaseService):
    model = GlobalAsset
    create_schema = GlobalAssetCreate
    update_schema = GlobalAssetUpdate
    read_schema = GlobalAssetRead

    global_ordered = True

    def _validate_create(self, values):
        self._require(Asset, values["asset_id"])

    def _validate_update(self, entity, values):
        if "asset_id" in values and values["asset_id"] != entity.asset_id:
            self._require(Asset, values["asset_id"])
