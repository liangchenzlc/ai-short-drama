"""Persist supplied generation outputs, without making remote AI calls.

Allocate a batch ID before starting a retryable workflow and supply it on every retry.
An exact replay returns the original output/record IDs. With no historical content
snapshot in the schema, replay after output editing is rejected as a content conflict.
"""

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, Conflict
from short_drama.dao.creation.storyboard.management.storyboard import advance_storyboard_version
from short_drama.domain import Project, ShotScript
from short_drama.schemas.base import Identifier, MediumText, parse_identifier
from short_drama.schemas.creation.storyboard.management.records import ScriptShotRecordRead
from short_drama.schemas.creation.storyboard.management.script import ShotScriptRead
from short_drama.service.base import utcnow
from short_drama.service.creation.storyboard.management.records import ScriptShotRecordService
from short_drama.service.creation.storyboard.management.script import ShotScriptService
from short_drama.utils.snowflake import next_id


class GenerationBatch(BaseModel):
    batch_id: Identifier
    outputs: list[ShotScriptRead]
    records: list[ScriptShotRecordRead]


class GenerationService:
    def __init__(self, session):
        self.session = session

    @staticmethod
    def allocate_batch_id():
        """Return a Snowflake batch identifier that callers can preserve across retries."""
        return next_id()

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
        # 分镜和来源记录必须一起提交，重试复用 batch_id，避免重复追加。
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
                records._validate_model(model_id, "text")
                storyboard_creation = (
                    records._require(Project, source.project_id)
                    if outputs.model is ShotScript
                    else None
                )
                maximum = (
                    self.session.scalar(
                        select(outputs.model.position)
                        .where(outputs.model.project_id == source.project_id)
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
                            "project_id": source.project_id,
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
                if storyboard_creation is not None:
                    advance_storyboard_version(storyboard_creation)
                    self.session.flush()
            return GenerationBatch(
                batch_id=batch_id,
                outputs=[outputs._read(row) for row in output_rows],
                records=[records._read(row) for row in record_rows],
            )
