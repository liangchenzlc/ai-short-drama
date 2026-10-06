"""Short lived revision notifications; every poll rechecks session and membership."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.service.auth_service import AuthService
from short_drama.service.canvas_service import CanvasService


async def canvas_events(
    request: Request, source_key: str, expected_actor: int
) -> AsyncIterator[str]:
    token = request.cookies.get("sd_session")
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    previous = None

    def current_revision() -> str:
        actor = AuthService(factory, settings).authenticate(token, request.state.request_id)
        if actor.user_id != expected_actor:
            raise WorkflowError("canvas_actor_changed", "The canvas account changed", 409)
        with factory() as session:
            session.info["actor"] = actor
            return CanvasService(session).resolve(source_key)["row_version"]

    # Reconnect periodically so cookies and reverse-proxy shutdowns take effect.
    for _ in range(10):
        if await request.is_disconnected():
            return
        try:
            revision = await run_in_threadpool(current_revision)
        except BusinessError as error:
            event = (
                "session.expired"
                if error.status_code == 401 or error.code == "canvas_actor_changed"
                else "canvas.unavailable"
            )
            yield f"event: {event}\ndata: " + json.dumps({"code": error.code}) + "\n\n"
            return
        if revision != previous:
            yield "event: canvas.updated\ndata: " + json.dumps({"revision": revision}) + "\n\n"
            previous = revision
        else:
            yield ": heartbeat\n\n"
        await asyncio.sleep(3)
