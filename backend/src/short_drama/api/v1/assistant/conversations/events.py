import asyncio
import time
from typing import Annotated

from fastapi import Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.schemas.base import Identifier
from short_drama.service.assistant.execution.run import AgentRunService


def event_cursor(cursor, last_event_id):
    if last_event_id is not None:
        if (
            not last_event_id.isascii()
            or not last_event_id.isdecimal()
            or int(last_event_id) > 2**64 - 1
        ):
            raise WorkflowError("invalid_event_cursor", "Invalid event cursor", 422)
        cursor = max(cursor, int(last_event_id))
    return cursor


async def conversation_events(
    conversation_id: Identifier,
    request: Request,
    cursor: Annotated[int, Query(ge=0, le=2**64 - 1)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
):
    cursor = event_cursor(cursor, last_event_id)
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    actor = getattr(request.state, "actor", None)

    # 每轮独立创建 Session，重新校验会话权限，避免长连接占用事务。
    def read(after):
        with factory() as session:
            session.info["actor"] = actor
            return AgentRunService(
                session, settings, getattr(request.app.state, "storage", None)
            ).events(conversation_id, after, verify_session=True)

    initial = await run_in_threadpool(read, cursor)

    async def stream():
        after, rows, heartbeat = cursor, initial, time.monotonic()
        while not await request.is_disconnected():
            for event in rows:
                yield f"id: {event.seq}\nevent: agent\ndata: {event.model_dump_json()}\n\n"
                after = event.seq
            if time.monotonic() - heartbeat >= 15:
                yield ": heartbeat\n\n"
                heartbeat = time.monotonic()
            await asyncio.sleep(1)
            try:
                rows = await run_in_threadpool(read, after)
            except BusinessError as error:
                if error.code == "agent_event_resync_required":
                    yield 'event: resync\ndata: {"code":"agent_event_resync_required"}\n\n'
                elif error.status_code in {401, 403, 404} or error.code in {
                    "agent_disabled",
                    "agent_scope_mismatch",
                }:
                    # 注销、权限撤销或功能关闭后立即终止事件流，不能继续泄露私有内容。
                    yield 'event: access-ended\ndata: {"code":"agent_access_ended"}\n\n'
                else:
                    yield 'event: retry\ndata: {"code":"agent_stream_unavailable"}\n\n'
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
