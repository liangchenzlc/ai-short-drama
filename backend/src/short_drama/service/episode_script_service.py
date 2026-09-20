from sqlalchemy import select

from short_drama.domain import Episode, EpisodeScript
from short_drama.schemas.episode_script import (
    EpisodeScriptCreate,
    EpisodeScriptRead,
    EpisodeScriptUpdate,
)

from .base import BaseService


class EpisodeScriptService(BaseService):
    model = EpisodeScript
    create_schema = EpisodeScriptCreate
    update_schema = EpisodeScriptUpdate
    read_schema = EpisodeScriptRead
    parent_model = Episode
    parent_field = "episode_id"

    def _validate_update(self, entity, values):
        if "content" in values and values["content"] != entity.content:
            values["state"] = "unconfirmed"

    def confirm(self, identifier):
        """Confirm under the episode lock, clearing the previous confirmation first."""
        with self._transaction():
            target = self._get_locked(identifier)
            confirmed = list(
                self.session.scalars(
                    select(self.model)
                    .where(
                        self.model.episode_id == target.episode_id, self.model.state == "confirmed"
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for row in confirmed:
                if row.id != target.id:
                    self._apply_update(row, {"state": "unconfirmed"})
            return self._read(self._apply_update(target, {"state": "confirmed"}))
