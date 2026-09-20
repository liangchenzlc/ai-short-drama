from short_drama.core.exceptions import BusinessError
from short_drama.domain import Asset, ShotAsset, ShotScript
from short_drama.schemas import ShotAssetCreate, ShotAssetRead, ShotAssetUpdate

from .base import BaseService


class ShotAssetService(BaseService):
    model = ShotAsset
    create_schema = ShotAssetCreate
    update_schema = ShotAssetUpdate
    read_schema = ShotAssetRead
    parent_model = ShotScript
    parent_field = "shot_id"

    def _validate_create(self, values):
        shot = self._require(ShotScript, values["shot_id"])
        if shot.episode_id != values["episode_id"]:
            raise BusinessError("Shot and asset link must belong to the same episode")
        self._require(Asset, values["asset_id"])

    def _validate_update(self, entity, values):
        if "asset_id" in values and values["asset_id"] != entity.asset_id:
            self._require(Asset, values["asset_id"])
