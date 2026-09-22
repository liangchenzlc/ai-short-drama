from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.episode_storyboard_dao import (
    advance_shot_version,
    advance_storyboard_version,
    require_shot_version,
)
from short_drama.domain import Episode, ShotScript
from short_drama.schemas import ShotScriptCreate, ShotScriptRead, ShotScriptUpdate

from .base import BaseService, Page, utcnow


class ShotScriptService(BaseService):
    model = ShotScript
    create_schema = ShotScriptCreate
    update_schema = ShotScriptUpdate
    read_schema = ShotScriptRead
    parent_model = Episode
    parent_field = "episode_id"

    def create(self, payload):
        values = self._payload(self.create_schema, payload)
        with self._transaction():
            episode = self._lock_parent(values)
            row = self.dao.create(
                self._creation_audit(
                    {
                        **values,
                        "source_excerpt": "",
                        "row_version": 1,
                        "image_settings": None,
                        "deleted_at": None,
                        "creation_key": None,
                        "creation_hash": None,
                    }
                )
            )
            advance_storyboard_version(episode)
            self.session.flush()
            return self._read(row)

    def update(self, identifier, payload):
        values = self._payload(self.update_schema, payload)
        with self._transaction():
            shot = self._get_locked(identifier)
            episode = self._require(Episode, shot.episode_id)
            if shot.deleted_at is not None:
                raise WorkflowError("shot_archived", "Archived shots cannot be edited")
            expected = values.pop("row_version", None)
            if expected is not None:
                require_shot_version(shot, expected)
            changes = {
                name: value for name, value in values.items() if getattr(shot, name) != value
            }
            if changes:
                changes.update(updated_at=utcnow(), updated_by=None)
                self.dao.update(shot, changes)
                advance_shot_version(shot)
                advance_storyboard_version(episode)
                self.session.flush()
            return self._read(shot)

    def delete(self, identifier):
        with self._transaction():
            shot = self._get_locked(identifier)
            if shot.deleted_at is not None:
                return
            episode = self._require(Episode, shot.episode_id)
            active = list(
                self.session.scalars(
                    select(ShotScript)
                    .where(
                        ShotScript.episode_id == shot.episode_id,
                        ShotScript.deleted_at.is_(None),
                    )
                    .order_by(ShotScript.position, ShotScript.id)
                    .with_for_update()
                )
            )
            now, old_position = utcnow(), shot.position
            shot.deleted_at, shot.updated_at, shot.updated_by = now, now, None
            advance_shot_version(shot)
            self.session.flush()
            for row in active:
                if row.id != shot.id and row.position > old_position:
                    row.position -= 1
                    row.updated_at, row.updated_by = now, None
                    advance_shot_version(row)
            advance_storyboard_version(episode)
            self.session.flush()

    def list(self, offset=0, limit=20, filters=None):
        self.dao.validate_pagination(offset, limit)
        conditions = [*self.dao._conditions(dict(filters or {})), ShotScript.deleted_at.is_(None)]
        with self._transaction():
            rows = list(
                self.session.scalars(
                    select(ShotScript)
                    .where(*conditions)
                    .order_by(ShotScript.position, ShotScript.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
            total = self.session.scalar(
                select(func.count()).select_from(ShotScript).where(*conditions)
            )
            return Page(
                items=[self._read(row) for row in rows],
                total=total,
                offset=offset,
                limit=limit,
            )
