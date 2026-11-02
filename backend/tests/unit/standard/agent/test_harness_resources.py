"""Resource boundary, exact method retention, and read-only package resources."""

import base64
import hashlib
import io
import json
import subprocess
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PIL import Image

from short_drama.agent.resources.contracts import (
    HydrationIntent,
    ResourceRef,
    assert_reference_payload,
)
from short_drama.agent.resources.resolver import ResourceResolver, request_template
from short_drama.agent.tools.registry import get_spec
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.skills.catalog.skill import SkillCreate
from short_drama.service.skills.packages.skill_package import parse_skill_import


def png():
    stream = io.BytesIO()
    Image.new("RGB", (64, 40), "red").save(stream, "PNG")
    return stream.getvalue()


class ObjectStore:
    def __init__(self):
        self.objects = {"minio://test/original.png": png()}
        self.reads = 0

    def open(self, uri):
        self.reads += 1
        return io.BytesIO(self.objects[uri])

    def upload(self, stream, *, length, content_type):
        data = stream.read()
        assert len(data) == length
        uri = f"minio://test/{hashlib.sha256(data).hexdigest()}.jpg"
        self.objects[uri] = data
        return SimpleNamespace(storage_locator=uri)

    def freeze_agent_resource(self, data, *, invocation_id, content_type):
        return self.upload(io.BytesIO(data), length=len(data), content_type=content_type)


@pytest.mark.parametrize("protocol", ["chat", "responses", "claude"])
@pytest.mark.parametrize("detail,allowance", [("low", 2048), ("high", 4096)])
@pytest.mark.parametrize("position", ["current", "stale", "withdrawn"])
def test_graph_media_projection_counts_only_the_current_frozen_group_without_io(
    protocol, detail, allowance, position
):
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from short_drama.agent.context.budget import estimate_input_tokens
    from short_drama.agent.resources.graph import GraphResources
    from tests.unit.standard.agent.test_assistant_model_protocols import configured

    resolver = ResourceResolver(ObjectStore())
    intents = resolver.freeze(
        HydrationIntent(
            resource=ResourceRef(
                kind="image",
                ref_id="image:1",
                uri="minio://test/original.png",
                source="task_attachment",
                checksum=hashlib.sha256(png()).hexdigest(),
            ),
            purpose="比较细节",
            tool_call_id="pixels",
            invocation_id="turn:1",
            detail=detail,
        )
    )
    call = AIMessage(
        content="",
        id="old-group",
        tool_calls=[{"name": "workbench__read_image_content", "id": "pixels", "args": {}}],
    )
    result = ToolMessage(
        name="workbench__read_image_content",
        tool_call_id="pixels",
        id="old-image",
        content=json.dumps(
            {"hydration_intents": [intent.model_dump(mode="json") for intent in intents]}
        ),
    )
    messages = [HumanMessage(content="核对"), call, result]
    if position == "stale":
        messages.extend(
            [
                AIMessage(
                    content="已交付上一轮像素观察",
                    id="new-group",
                    tool_calls=[
                        {"name": "runtime__get_generation_policy", "id": "pixels", "args": {}}
                    ],
                ),
                ToolMessage(
                    content="{}",
                    id="new-policy",
                    tool_call_id="pixels",
                    name="runtime__get_generation_policy",
                ),
            ]
        )
    if position == "withdrawn":
        messages = messages[:1]
    messages.extend(
        [
            AIMessage(content="工作笔记", additional_kwargs={"task_context_kind": "working_notes"}),
            HumanMessage(content="当前目标"),
        ]
    )
    resources = GraphResources(
        None,
        configured("http://127.0.0.1:9", protocol),
        lambda: pytest.fail("容量计量不得读取媒体或访问存储"),
    )
    projection = {"messages": resources.project_input(messages)}
    assert_reference_payload(projection)
    text = json.dumps(projection, ensure_ascii=False)
    assert "data:image/" not in text
    if position == "current":
        assert '"image_ref"' in text
        assert intents[0].resource.checksum in text
        assert (
            estimate_input_tokens(projection, references=True) - estimate_input_tokens(projection)
            == allowance
        )
    else:
        assert '"image_ref"' not in text
        assert estimate_input_tokens(projection, references=True) == estimate_input_tokens(
            projection
        )
    if position == "stale":
        import asyncio

        candidate = [
            message for message in messages if message.id not in {"new-group", "new-policy"}
        ]

        async def scoped_projection(original):
            with resources.input_scope(original):
                await asyncio.sleep(0)
                value = await asyncio.to_thread(resources.project_input, candidate)
                return '"image_ref"' in json.dumps(value)

        async def parallel_candidates():
            return await asyncio.gather(scoped_projection(messages), scoped_projection(candidate))

        assert asyncio.run(parallel_candidates()) == [False, True]
        with pytest.raises(RuntimeError, match="cancelled input"):
            with resources.input_scope(messages):
                assert '"image_ref"' not in json.dumps(resources.project_input(candidate))
                raise RuntimeError("cancelled input")
        assert '"image_ref"' in json.dumps(resources.project_input(candidate))


def test_different_views_of_same_image_remain_distinct_and_ephemeral():
    from short_drama.agent.resources.resolver import image_block

    storage = ObjectStore()
    resolver = ResourceResolver(storage)
    original = ResourceRef(
        kind="image",
        ref_id="image:1",
        uri="minio://test/original.png",
        source="task_attachment",
        checksum=hashlib.sha256(png()).hexdigest(),
    )
    assert storage.reads == 0
    intents = [
        resolver.freeze_image(
            HydrationIntent(
                resource=original,
                purpose="比较细节",
                tool_call_id=f"call_{i}",
                invocation_id="turn:1",
                crop=crop,
            )
        )
        for i, crop in enumerate([(0, 0, 20, 20), (20, 20, 60, 40)])
    ]
    hydrated = [resolver.hydrate(item, protocol="claude_messages.v1") for item in intents]
    entities = [item.content for item in hydrated]
    assert entities[0] != entities[1]
    template = request_template(
        {"messages": [{"content": [image_block(item, "claude_messages.v1")]} for item in hydrated]},
        hydrated,
    )
    assert_reference_payload(template)
    assert all(entity not in json.dumps(template) for entity in entities)
    assert [item.reference.metadata["tool_call_id"] for item in hydrated] == ["call_0", "call_1"]


@pytest.mark.parametrize(
    "entity",
    [
        b"bytes",
        {"source": {"type": "base64", "data": "pixels"}},
        'arbitrary JSON {"image_url":"data:image/jpeg;base64,aW1hZ2U="}',
        {"kind": "binary", "data": "pixels"},
    ],
)
def test_all_persistence_boundaries_reject_media_entities(entity):
    with pytest.raises(WorkflowError, match="媒体实体"):
        assert_reference_payload(entity)


def test_schema_fields_named_type_are_not_mistaken_for_media_blocks():
    assert_reference_payload({"properties": {"type": {"type": "string"}}})
    assert_reference_payload({"type": ["string", "null"]})


def test_runtime_budget_requires_finite_decisions_and_output_but_allows_unlimited_time():
    from short_drama.agent.core import ENGINE_VERSION
    from short_drama.agent.core.events import budget_limits, initial_usage
    from short_drama.agent.gateway.contracts import AgentGatewayError
    from short_drama.agent.runtime.claims import checked_budget

    frozen = budget_limits("harness", caps={"decision_calls": 32, "output_tokens": 131072})
    available = SimpleNamespace(
        checkpoint={"engine": ENGINE_VERSION},
        budget=frozen,
        usage={**initial_usage(), "tool_calls": 400, "active_seconds": 7200},
    )
    assert checked_budget(available) == frozen
    for key in ("decision_calls", "output_tokens"):
        available.budget = {**frozen, key: None}
        with pytest.raises(AgentGatewayError, match="invalid_agent_budget"):
            checked_budget(available)


def test_skill_zip_import_keeps_scripts_inert_and_images_managed():
    archive = io.BytesIO()
    with ZipFile(archive, "w") as package:
        package.writestr(
            "SKILL.md",
            "---\nname: harness-method\ndescription: 用于资料核对\n---\n"
            "按需读取[示例图](assets/example.png)。",
        )
        package.writestr("assets/example.png", png())
    preview = parse_skill_import(archive.getvalue(), "method.zip", tool_lookup=get_spec)
    assert preview.compatible, preview.issues
    content = SkillCreate.model_validate(preview.draft.model_dump())
    (image,) = content.resources
    assert image.kind == "image" and image.encoding == "base64"
    assert image.resource_bytes() == png()
    assert base64.b64decode(image.content) == png()
    with ZipFile(archive, "a") as package:
        package.writestr("scripts/check.py", "raise RuntimeError('must never execute')")
    imported = parse_skill_import(archive.getvalue(), "method.zip", tool_lookup=get_spec)
    assert imported.compatible, imported.issues
    resources = SkillCreate.model_validate(imported.draft.model_dump()).resources
    script = next(item for item in resources if item.path == "scripts/check.py")
    assert script.kind == "script"
    assert script.resource_bytes() == b"raise RuntimeError('must never execute')"


@pytest.mark.parametrize("kind,visual_only", [("audio", False), ("video", False), ("video", True)])
def test_local_media_progression_freezes_frames_and_audio_without_persistent_entities(
    tmp_path, kind, visual_only
):
    from short_drama.core.config import Settings
    from short_drama.service.media.processing.render_tools import executable

    settings = Settings()
    target = tmp_path / ("clip.mp4" if kind == "video" else "voice.wav")
    command = [executable(settings.render_ffmpeg_path), "-nostdin", "-v", "error"]
    if kind == "video":
        command += ["-f", "lavfi", "-i", "color=c=blue:s=64x40:r=2"]
    command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "1"]
    if kind == "video":
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    subprocess.run(command + [str(target)], check=True, capture_output=True, timeout=30)
    data = target.read_bytes()
    storage = ObjectStore()
    uri = f"minio://test/{target.name}"
    storage.objects[uri] = data
    reference = ResourceRef(
        kind=kind,
        ref_id="attachment:media",
        uri=uri,
        source="task_attachment",
        checksum=hashlib.sha256(data).hexdigest(),
    )
    resolver = ResourceResolver(storage, settings)
    intents = resolver.freeze(
        HydrationIntent(
            resource=reference,
            purpose="根据实际画面和声音核对节奏",
            tool_call_id="media_read",
            invocation_id="turn:42",
            video_audio="visual_only" if visual_only else "include",
        )
    )
    assert [item.resource.kind for item in intents] == (
        ["image"] if visual_only else ["image", "audio"] if kind == "video" else ["audio"]
    )
    assert_reference_payload([item.model_dump(mode="json") for item in intents])
    if kind == "video":
        assert "采样时间" in intents[0].resource.metadata["caption"]
    hydrated = [resolver.hydrate(item, protocol="openai_chat.v1") for item in intents]
    from short_drama.agent.resources.resolver import image_block

    template = request_template(
        [image_block(item, "openai_chat.v1") for item in hydrated], hydrated
    )
    assert_reference_payload(template)
    assert all(item.content not in json.dumps(template) for item in hydrated)
    for intent in intents:
        if intent.resource.kind == "audio":
            with pytest.raises(WorkflowError, match="音频理解"):
                resolver.hydrate(intent, protocol="claude_messages.v1")
