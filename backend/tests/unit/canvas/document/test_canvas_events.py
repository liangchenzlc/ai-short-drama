import asyncio
from types import SimpleNamespace

import pytest

from short_drama.api import canvas_events as events_module
from short_drama.core.exceptions import NotFound, WorkflowError


@pytest.mark.parametrize(
    ("failure", "event", "code"),
    [
        (NotFound(), "canvas.unavailable", "not_found"),
        (
            WorkflowError("canvas_actor_changed", "changed", 409),
            "session.expired",
            "canvas_actor_changed",
        ),
        (
            WorkflowError("authentication_required", "expired", 401),
            "session.expired",
            "authentication_required",
        ),
    ],
)
def test_event_stream_revocation_ends_without_leaking_document(monkeypatch, failure, event, code):
    async def disconnected():
        return False

    async def rejected(_read):
        raise failure

    monkeypatch.setattr(events_module, "run_in_threadpool", rejected)
    request = SimpleNamespace(
        cookies={"sd_session": "test-only"},
        app=SimpleNamespace(state=SimpleNamespace(session_factory=None, settings=None)),
        state=SimpleNamespace(request_id="events-test"),
        is_disconnected=disconnected,
    )

    async def collect():
        return [part async for part in events_module.canvas_events(request, "canvas", 123)]

    output = asyncio.run(collect())
    assert len(output) == 1
    assert f"event: {event}" in output[0]
    assert f'"code": "{code}"' in output[0]
    assert "nodes" not in output[0] and "test-only" not in output[0]
