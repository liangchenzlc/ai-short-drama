from short_drama.domain import Asset, Episode, EpisodeAsset
from short_drama.schemas import EpisodeAssetCreate, EpisodeAssetRead, EpisodeAssetUpdate

from .base import BaseService


class EpisodeAssetService(BaseService):
    model = EpisodeAsset
    create_schema = EpisodeAssetCreate
    update_schema = EpisodeAssetUpdate
    read_schema = EpisodeAssetRead
    parent_model = Episode
    parent_field = "episode_id"

    def _validate_create(self, values):
        self._require(Asset, values["asset_id"])

    def _validate_update(self, entity, values):
        if "asset_id" in values and values["asset_id"] != entity.asset_id:
            self._require(Asset, values["asset_id"])
