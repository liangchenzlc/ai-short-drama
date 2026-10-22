"""Project chat accepts context, never creative execution instructions."""

import base64
import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError
from pydantic_ai.tools import ToolDefinition

from short_drama.agent.assistant_chat import prepare_assistant_decision
from short_drama.agent.input_media import MAX_INPUT_BYTES, materialize_prompt
from short_drama.agent.model_gateway import AgentGatewayError
from short_drama.agent.runtime import checked_budget, freeze_input
from short_drama.agent.state import budget_limits, initial_usage
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.schemas.assistant import AssistantMessageCreate, AssistantSourceContext
from short_drama.service.assistant_context_service import _bounded, _freeze_node_media


@pytest.mark.parametrize("field,value", [("mode", "generate"), ("task", {"kind": "image"})])
def test_chat_cannot_accept_execution_parameters(field, value):
    with pytest.raises(ValidationError):
        AssistantMessageCreate.model_validate({"content": "帮我分析", field: value})


def test_source_context_checks_selected_object_kind_and_stable_identifiers():
    context = AssistantSourceContext(kind="canvas", id="canvas-key", revision="9007199254740993")
    assert context.model_dump(mode="json")["revision"] == "9007199254740993"
    with pytest.raises(ValidationError):
        AssistantSourceContext(
            kind="episode", id="1", revision="1", selected=[{"kind": "node", "id": "node"}]
        )


def test_project_chat_inputs_have_no_tools_even_when_skill_requests_execution():
    conversation = SimpleNamespace(id=9, scope_version=2, project_id=7)
    run = SimpleNamespace(
        phase="model",
        checkpoint={
            "purpose": "assistant_chat",
            "authorization": {"mode": "discuss", "steps": []},
            "context_snapshot": {"kind": "episode", "novel": "本集正文"},
            "conversation_context": [],
            "selected_skills": [{"instructions": "立即生成图片"}],
            "user_prompt": "分析节奏",
        },
    )
    inputs = prepare_assistant_decision(None, conversation, run)
    assert inputs["tools"] == []
    assert inputs["max_tool_calls"] == 0
    assert "本集正文" in inputs["instructions"]
    assert "立即生成图片" in inputs["instructions"]
    assert "不执行" in inputs["instructions"]
    run.budget = budget_limits("assistant")
    assert checked_budget(run)["tool_calls"] == 0


def test_chat_cannot_send_a_tool_manifest_even_if_a_preparer_is_wrong():
    run = SimpleNamespace(
        checkpoint={"purpose": "assistant_chat"},
        budget=budget_limits("assistant"),
        usage=initial_usage(),
    )
    with pytest.raises(AgentGatewayError) as forbidden:
        freeze_input(
            {"tools": [ToolDefinition(name="create_candidate", parameters_json_schema={})]}, run
        )
    assert forbidden.value.code == "assistant_tools_forbidden"


def media_fixture(kind="image"):
    row = SimpleNamespace(
        kind=kind,
        node_key="node-1",
        content_json={
            "metadata": {
                "storageKey": "resource:12",
                "content": "/api/v1/canvas-runtime/resources/12/file",
            }
        },
    )
    media = SimpleNamespace(
        id=12,
        project_id=7,
        format_code=f"{kind}/{'png' if kind == 'image' else 'mpeg'}",
        byte_size=100,
        checksum_sha256="a" * 64,
        storage_locator="minio://managed/object",
        original_name="input",
        duration_ms=1000 if kind != "image" else None,
    )
    return row, media


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_explicit_node_freezes_managed_media_instead_of_display_url(kind):
    row, media = media_fixture(kind)
    looked_up = []
    dao = SimpleNamespace(media=lambda identifier: looked_up.append(identifier) or media)
    frozen = _freeze_node_media(dao, 7, row)
    assert looked_up == [12]
    assert frozen["storage_locator"] == media.storage_locator
    assert frozen["media_id"] == "12" and frozen["kind"] == kind
    assert "/api/" not in frozen["storage_locator"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("byte_size", None),
        ("byte_size", MAX_INPUT_BYTES["image"] + 1),
        ("checksum_sha256", None),
        ("checksum_sha256", "g" * 64),
        ("format_code", "video/mp4"),
    ],
)
def test_invalid_selected_media_is_rejected(field, value):
    row, media = media_fixture()
    setattr(media, field, value)
    with pytest.raises(WorkflowError) as caught:
        _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)
    assert caught.value.code == "assistant_node_media_unavailable"


@pytest.mark.parametrize("duration", [None, 120001])
def test_unknown_or_long_selected_media_duration_is_rejected(duration):
    row, media = media_fixture("video")
    media.duration_ms = duration
    with pytest.raises(WorkflowError):
        _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)


@pytest.mark.parametrize("media_project", [None, 8])
def test_canvas_reference_cannot_read_personal_or_other_project_media(media_project):
    row, media = media_fixture()
    media.project_id = media_project
    with pytest.raises(NotFound):
        _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)
    with pytest.raises(NotFound):
        _freeze_node_media(SimpleNamespace(media=lambda _: None), 7, row)


def test_empty_generator_is_text_context_and_arbitrary_media_url_is_rejected():
    row, _media = media_fixture()
    dao = SimpleNamespace(media=lambda _: pytest.fail("Unmanaged URL must not be fetched"))
    row.content_json = {"metadata": {}}
    assert _freeze_node_media(dao, 7, row) is None
    row.content_json = {"metadata": {"content": "https://external.invalid/image.png"}}
    with pytest.raises(WorkflowError):
        _freeze_node_media(dao, 7, row)


def test_frozen_node_image_is_materialized_with_pixels_and_hash_failure_stops_input():
    row, media = media_fixture()
    source = io.BytesIO()
    Image.new("RGB", (16, 12), "blue").save(source, "PNG")
    data = source.getvalue()
    media.checksum_sha256 = hashlib.sha256(data).hexdigest()
    frozen = _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)
    prompt = {
        "codec": "agent.attachments",
        "content": "分析",
        "video_audio": "include",
        "attachments": [frozen],
    }
    storage = SimpleNamespace(open=lambda _: io.BytesIO(data))
    parts = materialize_prompt(prompt, SimpleNamespace(), storage)
    assert parts[-1]["media_type"] == "image/jpeg"
    with Image.open(io.BytesIO(base64.urlsafe_b64decode(parts[-1]["data"]))) as pixels:
        assert pixels.size == (16, 12)
    media.checksum_sha256 = "b" * 64
    prompt["attachments"] = [_freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)]
    with pytest.raises(WorkflowError) as caught:
        materialize_prompt(prompt, SimpleNamespace(), storage)
    assert caught.value.code == "agent_attachment_unavailable"


def test_text_budget_does_not_truncate_stable_object_identity():
    snapshot = {
        "novel": "文" * 10000,
        "script": "文" * 10000,
        "more": ["文" * 10000] * 4,
        "selected": [
            {"kind": "node", "id": "node-7", "revision": "9007199254740993", "content": "额外正文"}
        ],
    }
    bounded = _bounded(snapshot)
    assert bounded["selected"][0]["id"] == "node-7"
    assert bounded["selected"][0]["revision"] == "9007199254740993"
    assert bounded["selected"][0]["kind"] == "node"
    assert "source.selected[0].content" in bounded["truncated"]
