"""Immutable generation provenance; create_batch accepts the entire batch on every retry."""

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict, NotFound
from short_drama.domain import (
    AIModelConfig,
    Episode,
    EpisodeNovel,
    EpisodeScript,
    NovelScriptRecord,
)
from short_drama.schemas.novel_script_record import NovelScriptRecordCreate, NovelScriptRecordRead

from .base import BaseService, utcnow


class GenerationRecordService(BaseService):
    source_model = None
    output_model = None
    source_field = None
    output_field = None

    def _lock_source(self, identifier):
        episode_id = self.session.scalar(
            select(self.source_model.episode_id).where(self.source_model.id == identifier)
        )
        if episode_id is None:
            raise NotFound("Generation source does not exist")
        self._require(Episode, episode_id)
        return self._require(self.source_model, identifier)

    def _batch_payloads(self, payloads):
        values = [self._payload(self.create_schema, payload) for payload in payloads]
        if not values:
            raise BusinessError("Generation batch must contain at least one output")
        first = values[0]
        for value in values:
            value.setdefault("model_id", None)
        if any(
            any(value[key] != first[key] for key in (self.source_field, "batch_id", "model_id"))
            for value in values
        ):
            raise BusinessError("Generation batch must share source, model and batch identifier")
        identifiers = [value[self.output_field] for value in values]
        if len(set(identifiers)) != len(identifiers):
            raise BusinessError("Generation batch output identifiers must be unique")
        return values

    def _existing_batch(self, batch_id):
        return list(
            self.session.scalars(
                select(self.model)
                .where(self.model.batch_id == batch_id)
                .order_by(self.model.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _match_existing(self, values, existing):
        by_output = {getattr(row, self.output_field): row for row in existing}
        if set(by_output) != {value[self.output_field] for value in values}:
            raise Conflict("Existing generation batch has different outputs")
        for value in values:
            row = by_output[value[self.output_field]]
            if any(getattr(row, key) != item for key, item in value.items()):
                raise Conflict("Existing generation batch has different metadata")
        return [by_output[value[self.output_field]] for value in values]

    def _validate_source(self, source):
        if self.source_model is EpisodeNovel and not source.content.strip():
            raise BusinessError("Novel content is required to generate scripts")

    def _persist_batch(self, values, source, created_at=None):
        existing = self._existing_batch(values[0]["batch_id"])
        if existing:
            return self._match_existing(values, existing)
        self._validate_source(source)
        # Existing results may acquire provenance after their model is disabled/deleted.
        # GenerationService separately enforces availability before creating new outputs.
        if values[0]["model_id"] is not None:
            model = self._require(AIModelConfig, values[0]["model_id"])
            if model.service_type != "text":
                raise BusinessError("Generation provenance requires a text model")
        for value in sorted(values, key=lambda value: value[self.output_field]):
            output = self._require(self.output_model, value[self.output_field])
            if output.episode_id != source.episode_id:
                raise BusinessError("Generation source and outputs must belong to the same episode")
        now = created_at or utcnow()
        return [
            self.dao.create({**value, "created_at": now, "created_by": None}) for value in values
        ]

    def create_batch(self, payloads):
        """Record all existing outputs, allowing historical models; retry with the full batch."""
        values = self._batch_payloads(payloads)
        with self._transaction():
            source = self._lock_source(values[0][self.source_field])
            return [self._read(row) for row in self._persist_batch(values, source)]

    def create(self, payload):
        """Record a complete one-output batch; cannot append to a previously committed batch."""
        return self.create_batch([payload])[0]

    def _get_locked(self, identifier):
        row = self._require(self.model, identifier, for_update=False)
        self._lock_source(getattr(row, self.source_field))
        return self._require(self.model, identifier)


class NovelScriptRecordService(GenerationRecordService):
    model = NovelScriptRecord
    create_schema = NovelScriptRecordCreate
    read_schema = NovelScriptRecordRead
    source_model = EpisodeNovel
    output_model = EpisodeScript
    source_field = "novel_id"
    output_field = "script_id"
