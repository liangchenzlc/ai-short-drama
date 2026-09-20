"""Confirmed-media operations share the parent shot lock with the recycle bin."""

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.dao.base import BaseDAO
from short_drama.domain import AIModelConfig, MediaRecycleBin, ShotScript

from .base import BaseService, utcnow


def recycle_snapshot(session, entity, reason):
    """Preserve the original parameters; recycle records are immutable."""
    values = {
        name: getattr(entity, name)
        for name in ("shot_id", "media_id", "model_id", "prompt", "resolution")
    }
    values.update(
        layout=getattr(entity, "layout", None),
        aspect=getattr(entity, "aspect", None),
        duration=getattr(entity, "duration", None),
        reason=reason,
    )
    existing = session.scalar(
        select(MediaRecycleBin)
        .where(
            MediaRecycleBin.shot_id == entity.shot_id,
            MediaRecycleBin.media_id == entity.media_id,
        )
        .with_for_update()
    )
    if existing is not None:
        raise Conflict("Media is already in the recycle bin")
    return BaseDAO(session, MediaRecycleBin).create(
        {
            **values,
            "created_at": utcnow(),
            "created_by": None,
        }
    )


class ShotMediaService(BaseService):
    parent_model = ShotScript
    parent_field = "shot_id"
    media_kind = None

    def _check_values(self, values, shot, historical=False, previous=None):
        if values["episode_id"] != shot.episode_id:
            raise BusinessError("Media and shot must belong to the same episode")
        self._validate_media(values["media_id"], self.media_kind)
        model_id = values.get("model_id")
        if model_id is not None:
            if historical or (previous is not None and model_id == previous.model_id):
                model = self._require(AIModelConfig, model_id)
                if model.service_type != self.media_kind:
                    raise BusinessError("Model type does not match media")
            else:
                self._validate_model(model_id, self.media_kind)

    def _remove_recycle(self, shot_id, media_id):
        entry = self.session.scalar(
            select(MediaRecycleBin)
            .where(
                MediaRecycleBin.shot_id == shot_id,
                MediaRecycleBin.media_id == media_id,
            )
            .with_for_update()
        )
        if entry is not None:
            BaseDAO(self.session, MediaRecycleBin).delete(entry)

    def _write_confirmed(self, values, shot, existing=None, historical=False):
        self._check_values(values, shot, historical=historical, previous=existing)
        if existing is not None and existing.media_id != values["media_id"]:
            recycle_snapshot(self.session, existing, "replaced")
        self._remove_recycle(shot.id, values["media_id"])
        if existing is None:
            return self.dao.create(self._creation_audit(values))
        return self._apply_update(existing, values)

    def create(self, payload):
        values = self.create_schema.model_validate(
            self._payload(self.create_schema, payload)
        ).model_dump()
        with self._transaction():
            shot = self._lock_parent(values)
            current = self.session.scalar(
                select(self.model).where(self.model.shot_id == shot.id).with_for_update()
            )
            if current is not None:
                raise Conflict("Shot already has confirmed media; update the existing result")
            return self._read(self._write_confirmed(values, shot))

    def update(self, identifier, payload):
        changes = self._payload(self.update_schema, payload)
        with self._transaction():
            entity = self._get_locked(identifier)
            shot = self._require(ShotScript, entity.shot_id)
            values = {name: getattr(entity, name) for name in self.create_schema.model_fields}
            values.update(changes)
            values = self.create_schema.model_validate(values).model_dump()
            return self._read(self._write_confirmed(values, shot, existing=entity))

    def delete(self, identifier):
        """Discard the confirmed slot, preserving its media and original parameters."""
        with self._transaction():
            entity = self._get_locked(identifier)
            recycle_snapshot(self.session, entity, "discarded")
            self.dao.delete(entity)
