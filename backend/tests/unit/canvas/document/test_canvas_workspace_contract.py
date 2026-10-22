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


@pytest.mark.parametrize(
    ("model_key", "durations", "resolutions"),
    [
        ("seedance-2.0-mini", list(range(4, 16)), ["720p", "480p"]),
        ("minimax-h3", list(range(5, 16)), ["480p"]),
    ],
)
def test_workspace_projects_legacy_modelhub_video_capabilities(model_key, durations, resolutions):
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import Mock

    from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
    from short_drama.schemas.canvas_workspace import CanvasModelRead

    config = SimpleNamespace(
        id=17,
        name="已保存视频模型",
        model_key=model_key,
        provider="自定义供应商名称",
        service_type="video",
        enabled=True,
        apikey="encrypted-key",
        base_url="https://api.modelhub.cc",
        is_default=True,
        runtime_profile=None,
        runtime_credentials_cipher=None,
    )
    before = deepcopy(vars(config))
    session = Mock()
    session.scalars.side_effect = [[config], []]
    projected = CanvasWorkspaceDAO(session).models(Mock())[0]
    defaults = projected.get("default_capability_config")
    assert defaults is not None, "无高级 profile 的 ModelHub 也必须向画布声明参考视频能力"
    video = defaults["video"]
    assert video["operations"] == ["text_to_video", "image_to_video", "reference_to_video"]
    assert video["references"]["maxImages"] == 9
    assert video["duration"]["values"] == durations
    assert video["resolutions"] == resolutions
    assert (
        CanvasModelRead.model_validate(projected).model_dump()["default_capability_config"]
        == defaults
    )
    assert projected["runtime_profile"] is None
    assert vars(config) == before


@pytest.mark.parametrize(
    ("base_url", "model_key", "runtime_profile"),
    [
        ("https://other.example/v1", "seedance-2.0-mini", None),
        ("https://api.modelhub.cc", "unknown-model", None),
        (
            "https://api.modelhub.cc",
            "seedance-2.0-mini",
            {"version": 1, "api_format": "openai", "protocol": "openai-videos"},
        ),
    ],
)
def test_workspace_does_not_infer_modelhub_capabilities_from_name_or_override_profile(
    base_url, model_key, runtime_profile
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO

    config = SimpleNamespace(
        id=17,
        name="视频模型",
        model_key=model_key,
        provider="modelhub",
        service_type="video",
        enabled=True,
        apikey=None,
        base_url=base_url,
        is_default=False,
        runtime_profile=runtime_profile,
        runtime_credentials_cipher=None,
    )
    session = Mock()
    session.scalars.side_effect = [[config], []]
    projected = CanvasWorkspaceDAO(session).models(Mock())[0]
    assert projected.get("default_capability_config") is None
    assert projected["runtime_profile"] == runtime_profile
