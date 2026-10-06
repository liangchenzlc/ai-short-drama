"""租约内供应商增量归档；只接内部回调，不接受浏览器伪造模型正文。"""

import time
from datetime import timedelta

from sqlalchemy import delete, select, text, update

from short_drama.ai.types import GenerationError
from short_drama.dao.canvas_text_dao import CanvasTextDAO
from short_drama.dao.task_runtime_dao import owned_task
from short_drama.domain import AIGenerationRecord, CanvasTaskBinding, CanvasTaskTextDelta
from short_drama.utils.snowflake import next_id

from .base import utcnow

MAX_TASK_BYTES = 2 * 1024**2
MAX_USER_BYTES = 64 * 1024**2
MAX_EVENT_BYTES = 64 * 1024


class CanvasTextStreamWriter:
    def __init__(self, factory, task, record, version: int, token: str):
        self.factory, self.task, self.record = factory, task, record
        self.version, self.token = version, token
        self.pending = ""
        self.last_flush = 0.0

    def append(self, content: str) -> None:
        self.pending += content
        if time.monotonic() - self.last_flush >= 0.1 or len(self.pending.encode()) >= 4096:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        # Advisory author quota lock does not lock the user's audit row and is
        # released after each short database transaction, never across HTTP I/O.
        with self.factory() as session, session.get_bind().connect() as connection:
            name = "canvas-text:" + str(self.task.initiated_by)
            if connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": name}) != 1:
                raise GenerationError("canvas_text_archive_busy", accepted_unknown=True)
            try:
                self._persist()
            finally:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
        self.pending = ""
        self.last_flush = time.monotonic()

    def _persist(self) -> None:
        with self.factory.begin() as session:
            current = owned_task(session, self.task.id, self.version, self.token)
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == current.id)
            )
            record = session.get(AIGenerationRecord, self.record.id)
            if binding is None or record.task_id != current.id or current.service_type != "text":
                raise GenerationError("canvas_text_archive_invalid", accepted_unknown=True)
            dao = CanvasTextDAO(session)
            sequence, task_bytes = dao.task_usage(binding.id)
            size = len(self.pending.encode())
            if (
                task_bytes + size > MAX_TASK_BYTES
                or dao.user_bytes(binding.initiated_by) + size > MAX_USER_BYTES
            ):
                raise GenerationError("canvas_text_replay_quota", accepted_unknown=True)
            remaining = self.pending
            while remaining:
                content = remaining[:MAX_EVENT_BYTES]
                while len(content.encode()) > MAX_EVENT_BYTES:
                    content = content[: len(content) * 3 // 4]
                remaining = remaining[len(content) :]
                sequence += 1
                if sequence > 4096:
                    raise GenerationError("canvas_text_replay_quota", accepted_unknown=True)
                now = utcnow()
                session.add(
                    CanvasTaskTextDelta(
                        id=next_id(),
                        project_id=binding.project_id,
                        canvas_id=binding.canvas_id,
                        task_binding_id=binding.id,
                        generation_record_id=record.id,
                        sequence=sequence,
                        content=content,
                        byte_count=len(content.encode()),
                        expires_at=now + timedelta(days=7),
                        created_at=now,
                        updated_at=now,
                        created_by=binding.initiated_by,
                        updated_by=binding.initiated_by,
                    )
                )
            data = record.response_data or {}
            record.response_data = {
                **data,
                "canvas_text_draft": data.get("canvas_text_draft", "") + self.pending,
            }


def finalize_canvas_text(session, task) -> None:
    if task.service_type != "text" or task.status not in {"succeeded", "failed", "cancelled"}:
        return
    binding_id = session.scalar(
        select(CanvasTaskBinding.id).where(CanvasTaskBinding.async_task_id == task.id)
    )
    if binding_id is not None:
        session.execute(
            update(CanvasTaskTextDelta)
            .where(CanvasTaskTextDelta.task_binding_id == binding_id)
            .values(expires_at=utcnow() + timedelta(days=1 if task.status == "succeeded" else 7))
        )


def cleanup_canvas_text(factory) -> int:
    with factory.begin() as session:
        return session.execute(
            delete(CanvasTaskTextDelta).where(CanvasTaskTextDelta.expires_at <= utcnow())
        ).rowcount
