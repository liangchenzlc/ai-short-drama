"""Persist supplied generation outputs, without making remote AI calls.

Allocate a batch ID before starting a retryable workflow and supply it on every retry.
An exact replay returns the original output/record IDs. With no historical content
snapshot in the schema, replay after output editing is rejected as a content conflict.
"""

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.schemas.base import Identifier, MediumText, parse_identifier
from short_drama.schemas.episode_script import EpisodeScriptRead
from short_drama.schemas.novel_script_record import NovelScriptRecordRead
from short_drama.schemas.script_shot_record import ScriptShotRecordRead
from short_drama.schemas.shot_script import ShotScriptRead
from short_drama.utils.snowflake import next_id

from .base import utcnow
from .episode_script_service import EpisodeScriptService
from .novel_script_record_service import NovelScriptRecordService
from .script_shot_record_service import ScriptShotRecordService
from .shot_script_service import ShotScriptService


class GenerationBatch(BaseModel):
    batch_id: Identifier
    outputs: list[EpisodeScriptRead | ShotScriptRead]
    records: list[NovelScriptRecordRead | ScriptShotRecordRead]


class GenerationService:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def allocate_batch_id():
        """Return a Snowflake batch identifier that callers can preserve across retries."""
        return next_id()

    def generate_scripts(self, novel_id, contents, model_id=None, batch_id=None):
        """Atomically append supplied script strings and their novel provenance."""
        return self._generate(
            NovelScriptRecordService(self.session),
            EpisodeScriptService(self.session),
            "content",
            novel_id,
            contents,
            model_id,
            batch_id,
        )

    def generate_shots(self, script_id, contents, model_id=None, batch_id=None):
        """Atomically append supplied shot strings and their script provenance."""
        return self._generate(
            ScriptShotRecordService(self.session),
            ShotScriptService(self.session),
            "script",
            script_id,
            contents,
            model_id,
            batch_id,
        )

    def _generate(self, records, outputs, content_field, source_id, contents, model_id, batch_id):
        contents = TypeAdapter(list[MediumText]).validate_python(contents)
        if not contents:
            raise BusinessError("Generation requires at least one output")
        source_id = parse_identifier(source_id)
        model_id = parse_identifier(model_id) if model_id is not None else None
        batch_id = parse_identifier(batch_id) if batch_id is not None else self.allocate_batch_id()
        with records._transaction():
            source = records._lock_source(source_id)
            existing = records._existing_batch(batch_id)
            if existing:
                if len(existing) != len(contents) or any(
                    getattr(row, records.source_field) != source_id or row.model_id != model_id
                    for row in existing
                ):
                    raise Conflict("Existing generation batch metadata or output count differs")
                output_rows = [
                    records._require(outputs.model, getattr(row, records.output_field))
                    for row in existing
                ]
                if any(
                    getattr(row, content_field) != content
                    for row, content in zip(output_rows, contents, strict=True)
                ):
                    raise Conflict("Existing generation batch has different output content")
                record_rows = existing
            else:
                records._validate_source(source)
                records._validate_model(model_id, "text")
                maximum = (
                    self.session.scalar(
                        select(outputs.model.position)
                        .where(outputs.model.episode_id == source.episode_id)
                        .order_by(outputs.model.position.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    or 0
                )
                if maximum + len(contents) > 2**32 - 1:
                    raise Conflict("Insufficient ordering space for generation outputs")
                now = utcnow()
                output_rows = [
                    outputs.dao.create(
                        {
                            "episode_id": source.episode_id,
                            "position": position,
                            content_field: content,
                            "created_at": now,
                            "updated_at": now,
                            "created_by": None,
                            "updated_by": None,
                        }
                    )
                    for position, content in enumerate(contents, maximum + 1)
                ]
                payloads = [
                    {
                        records.source_field: source_id,
                        records.output_field: row.id,
                        "batch_id": batch_id,
                        "model_id": model_id,
                    }
                    for row in output_rows
                ]
                record_rows = records._persist_batch(payloads, source, created_at=now)
            return GenerationBatch(
                batch_id=batch_id,
                outputs=[outputs._read(row) for row in output_rows],
                records=[records._read(row) for row in record_rows],
            )
