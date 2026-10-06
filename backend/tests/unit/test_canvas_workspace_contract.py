import pytest
from pydantic import ValidationError
from starlette.requests import Request

from short_drama.api.v1.canvases import get_canvas_service
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_workspace import CanvasWorkspacePreferencesRequest


def test_preferences_only_accept_generation_settings_without_credentials():
    for data in ({"apiKey": "secret"}, {"channels": []}, {"systemPrompt": {"secret": "x"}}):
        with pytest.raises(ValidationError):
            CanvasWorkspacePreferencesRequest(expected_row_version="0", preferences=data)
    value = CanvasWorkspacePreferencesRequest(
        expected_row_version="9007199254740993", preferences={"size": "16:9"}
    )
    assert value.model_dump(mode="json")["expected_row_version"] == "9007199254740993"


def test_canvas_actor_header_cannot_switch_the_authenticated_actor():
    from types import SimpleNamespace

    request = Request({"type": "http", "headers": [(b"x-canvas-actor", b"2")]})
    session = SimpleNamespace(info={"actor": SimpleNamespace(user_id=1)})
    with pytest.raises(WorkflowError) as error:
        get_canvas_service(request, session)
    assert error.value.code == "canvas_actor_changed"
