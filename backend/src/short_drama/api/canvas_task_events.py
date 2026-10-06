"""原版文本事件合同；每次读取重验当前会话、作者与项目权限。"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.service.auth_service import AuthService
from short_drama.service.canvas_generation_service import CanvasGenerationService


def event(name: str, payload: dict, sequence: int = 0) -> str:
    return (
        (f"id: {sequence}\n" if sequence else "")
        + f"event: {name}\ndata: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )


async def canvas_task_text_events(
    request: Request, task_id: int, expected_actor: int, after: int, initial: dict
) -> AsyncIterator[str]:
    factory, settings = request.app.state.session_factory, request.app.state.settings
    token = request.cookies.get("sd_session")

    def read(cursor: int) -> dict:
        actor = AuthService(factory, settings).authenticate(token, request.state.request_id)
        if actor.user_id != expected_actor:
            raise WorkflowError("canvas_actor_changed", "画布账号已切换", 409)
        with factory() as session:
            session.info["actor"] = actor
            return CanvasGenerationService(session, settings).text_replay(task_id, cursor)

    replay = initial
    previous = None
    yield ": connected\n\n"
    # Periodic reconnect gives cookies and reverse proxy shutdowns a bounded lifetime.
    for iteration in range(40):
        if await request.is_disconnected():
            return
        state = {key: replay[key] for key in ("status", "stage", "progress")}
        if state != previous:
            yield event("progress", state)
            previous = state
        for delta in replay["deltas"]:
            yield event(
                "delta",
                {"sequence": delta["sequence"], "content": delta["content"]},
                delta["sequence"],
            )
            after = delta["sequence"]
        if replay["complete"]:
            yield event("terminal", replay)
            return
        if iteration and iteration % 20 == 0:
            yield ": heartbeat\n\n"
        await asyncio.sleep(0.75)
        try:
            replay = await run_in_threadpool(read, after)
        except BusinessError:
            yield event("error", {"message": "任务文本流不可用，请检查登录与项目权限"})
            return
