from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.dao.episode_storyboard_dao import advance_shot_version, advance_storyboard_version
from short_drama.domain import Asset, Episode, EpisodeAsset, ShotAsset, ShotScript
from short_drama.schemas import ShotAssetCreate, ShotAssetRead, ShotAssetUpdate
from short_drama.schemas.base import parse_identifier

from .base import BaseService, utcnow


class ShotAssetService(BaseService):
    model = ShotAsset
    create_schema = ShotAssetCreate
    update_schema = ShotAssetUpdate
    read_schema = ShotAssetRead
    parent_model = ShotScript
    parent_field = "shot_id"

    def _locked_scope(self, shot_id):
        shot_id = parse_identifier(shot_id)
        episode_id = self.session.scalar(
            select(ShotScript.episode_id).where(ShotScript.id == shot_id)
        )
        if episode_id is None:
            raise NotFound("Shot does not exist")
        episode = self._require(Episode, episode_id)
        shot = self._require(ShotScript, shot_id)
        if shot.deleted_at is not None:
            raise WorkflowError("shot_archived", "Archived shots cannot be changed")
        return episode, shot

    def _validate_asset(self, episode_id, asset_id):
        self._require(Asset, asset_id)
        link = self.session.scalar(
            select(EpisodeAsset.id).where(
                EpisodeAsset.episode_id == episode_id,
                EpisodeAsset.asset_id == asset_id,
            )
        )
        if link is None:
            raise BusinessError("Shot assets must belong to the episode library")

    @staticmethod
    def _touch(episode, shot):
        shot.updated_at, shot.updated_by = utcnow(), None
        advance_shot_version(shot)
        advance_storyboard_version(episode)

    def create(self, payload):
        values = self._payload(self.create_schema, payload)
        with self._transaction():
            episode, shot = self._locked_scope(values["shot_id"])
            if values["episode_id"] != shot.episode_id:
                raise BusinessError("Shot and asset link must belong to the same episode")
            self._validate_asset(shot.episode_id, values["asset_id"])
            row = self.dao.create(self._creation_audit(values))
            self._touch(episode, shot)
            self.session.flush()
            return self._read(row)

    def update(self, identifier, payload):
        values = self._payload(self.update_schema, payload)
        with self._transaction():
            entity = self.dao.get(parse_identifier(identifier), for_update=True)
            if entity is None:
                raise NotFound("Record does not exist")
            episode, shot = self._locked_scope(entity.shot_id)
            if "asset_id" in values and values["asset_id"] != entity.asset_id:
                self._validate_asset(shot.episode_id, values["asset_id"])
                entity.asset_id = values["asset_id"]
                entity.updated_at, entity.updated_by = utcnow(), None
                self._touch(episode, shot)
                self.session.flush()
            return self._read(entity)

    def delete(self, identifier):
        with self._transaction():
            entity = self.dao.get(parse_identifier(identifier), for_update=True)
            if entity is None:
                raise NotFound("Record does not exist")
            episode, shot = self._locked_scope(entity.shot_id)
            self.dao.delete(entity)
            self._touch(episode, shot)
            self.session.flush()

    def _validate_create(self, values):
        shot = self._require(ShotScript, values["shot_id"])
        if shot.episode_id != values["episode_id"]:
            raise BusinessError("Shot and asset link must belong to the same episode")
        self._validate_asset(shot.episode_id, values["asset_id"])

    def _validate_update(self, entity, values):
        if "asset_id" in values and values["asset_id"] != entity.asset_id:
            self._validate_asset(entity.episode_id, values["asset_id"])
