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

from short_drama.agent.context import transcript
from short_drama.agent.context.transcript import model_messages, select_groups
from short_drama.agent.resources.contracts import (
    HydrationIntent,
    ResourceRef,
    assert_reference_payload,
)
from short_drama.agent.resources.resolver import ResourceResolver, request_template
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


def test_different_views_of_same_image_remain_distinct_and_ephemeral():
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
    hydrated = {
        item.tool_call_id: resolver.hydrate(item, protocol="claude_messages.v1") for item in intents
    }
    group = {
        "turn_id": "1",
        "content": "",
        "calls": [
            {
                "tool_name": "read_resource",
                "args": {"ref_id": "image:1"},
                "tool_call_id": item.tool_call_id,
            }
            for item in intents
        ],
        "results": [
            {
                "name": "read_resource",
                "operation_id": str(i),
                "tool_call_id": item.tool_call_id,
                "result": {"image_ref": original.model_dump(mode="json")},
            }
            for i, item in enumerate(intents)
        ],
    }
    messages = model_messages("Inspect", {}, [group], hydrated, intents, "claude_messages.v1")
    entities = [item.content[-1]["source"]["data"] for item in messages if item.type == "tool"]
    assert entities[0] != entities[1]
    request = {"messages": [{"content": item.content} for item in messages]}
    template = request_template(request, list(hydrated.values()))
    assert_reference_payload(template)
    assert all(entity not in json.dumps(template) for entity in entities)
    assert_reference_payload(group)


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


def test_compaction_pins_real_skill_pair_and_keeps_latest_complete_result():
    skill = {
        "turn_id": "1",
        "content": "loaded",
        "calls": [
            {"tool_name": "load_skill", "args": {}, "tool_call_id": "skill"},
            {"tool_name": "lookup", "args": {}, "tool_call_id": "other"},
        ],
        "results": [
            {
                "operation_id": "1",
                "tool_call_id": "skill",
                "result": {"instructions": "完整方法正文"},
            },
            {"operation_id": "2", "tool_call_id": "other", "result": {"content": "old data"}},
        ],
    }
    latest = {
        "turn_id": "2",
        "content": "",
        "calls": [],
        "results": [
            {"operation_id": "3", "tool_call_id": "latest", "result": {"content": "x" * 900}}
        ],
    }
    selected, metrics = select_groups([skill, latest], {"1"}, recent_tokens=100)
    assert len(selected) == 2
    assert selected[0]["calls"] == skill["calls"][:1]
    assert selected[0]["results"] == skill["results"][:1]
    assert selected[1] == latest
    assert metrics["retained_skills"] == 1


def test_compacted_progress_retains_sourced_excerpts_without_media_or_skill_copies():
    fact = "本步事实：失物是铜钥匙。"
    source = {
        "turn_id": "1",
        "content": "已看清示例图的蓝条位于左侧，继续保存角色。",
        "results": [
            {
                "operation_id": "11",
                "name": "load_skill",
                "result": {"instructions": "不能重复的完整方法正文"},
            },
            {
                "operation_id": "12",
                "name": "read_skill_resource",
                "result": {
                    "activation_id": "10",
                    "path": "references/clue.md",
                    "offset": 0,
                    "next_offset": None,
                    "content": fact + "节奏说明。" * 300,
                },
            },
            {
                "operation_id": "13",
                "name": "read_skill_resource",
                "result": {"image_ref": {"ref_id": "picture"}, "hydration_intent": {}},
            },
        ],
    }
    pinned = {**source, "content": "", "results": source["results"][:1]}
    progress = transcript.compact_progress([source], [pinned])
    payload = json.dumps(progress, ensure_ascii=False)
    assert fact in payload and "蓝条位于左侧" in payload
    note = progress["items"][0]
    assert note["turn_id"] == "1"
    assert note["reads"][0]["operation_id"] == "12"
    assert note["reads"][0]["path"] == "references/clue.md"
    assert note["reads"][0]["next_offset"] is None
    assert note["reads"][0]["excerpt"]["truncated"]
    assert source["results"][1]["result"]["content"] not in payload
    assert "完整方法正文" not in payload and "hydration_intent" not in payload
    assert_reference_payload(progress)
    assert transcript.compact_progress([source], [source])["items"] == []


def test_compacted_progress_is_bounded_and_does_not_invent_missing_observations(monkeypatch):
    monkeypatch.setattr(transcript, "PROGRESS_TOKENS", 400)
    groups = [{"turn_id": str(i), "content": "阶段结论" * 300, "results": []} for i in range(30)]
    progress = transcript.compact_progress(groups, [])
    assert 0 < len(progress["items"]) < 30
    assert progress["items"][-1]["turn_id"] == "29"
    assert progress["omitted_notes"] == 30 - len(progress["items"])
    assert progress["estimated_tokens"] <= 400
    assert transcript.compact_progress([], [])["items"] == []


def test_compacted_progress_retains_nested_read_evidence_and_original_source():
    group = {
        "turn_id": "10",
        "content": "",
        "results": [
            {
                "operation_id": "read-12",
                "name": "read_task_results",
                "result": {
                    "operation_id": "save-3",
                    "results": [
                        {
                            "id": "shot-4",
                            "kind": "shot",
                            "row_version": "2",
                            "content": "林晚持铜钥匙从左侧入画。",
                            "range": {"offset": 0, "length": 13, "total": 13},
                        }
                    ],
                    "next": None,
                },
            }
        ],
    }
    progress = transcript.compact_progress([group], [])
    read = progress["items"][0]["reads"][0]
    assert read["source_operation_id"] == "save-3"
    assert read["id"] == "shot-4" and read["row_version"] == "2"
    assert read["excerpt"]["text"] == "林晚持铜钥匙从左侧入画。"
    assert read["next"] is None
    assert_reference_payload(progress)


def test_harness_budget_requires_finite_decisions_and_output_but_allows_unlimited_time():
    import pytest

    from short_drama.agent.core.events import budget_limits, initial_usage
    from short_drama.agent.gateway.protocol import AgentGatewayError
    from short_drama.agent.harness import ENGINE_VERSION
    from short_drama.agent.runtime.claims import freeze_input

    caps = {"decision_calls": 32, "output_tokens": 131072}
    frozen = budget_limits("harness", caps=caps)
    exhausted = SimpleNamespace(
        checkpoint={"engine": ENGINE_VERSION},
        budget=frozen,
        usage={
            **initial_usage(),
            "decision_calls": 100,
            "output_tokens": 500000,
        },
    )
    with pytest.raises(AgentGatewayError, match="agent_budget_exhausted"):
        freeze_input(
            {"tools": [], "max_tool_calls": 16, "harness_version": ENGINE_VERSION}, exhausted
        )

    available = SimpleNamespace(
        checkpoint={"engine": ENGINE_VERSION},
        budget=frozen,
        usage={
            **initial_usage(),
            "tool_calls": 400,
            "active_seconds": 7200,
        },
    )
    values = freeze_input(
        {"tools": [], "max_tool_calls": 16, "harness_version": ENGINE_VERSION}, available
    )
    assert values["kwargs"]["max_tool_calls"] == 16
    assert values["kwargs"]["max_output_tokens"] == 8192
    assert values["kwargs"]["timeout_seconds"] == 120
    for key in ("decision_calls", "output_tokens"):
        invalid = SimpleNamespace(
            checkpoint={"engine": ENGINE_VERSION},
            budget={**frozen, key: None},
            usage=initial_usage(),
        )
        with pytest.raises(AgentGatewayError, match="invalid_agent_budget"):
            freeze_input(
                {"tools": [], "max_tool_calls": 16, "harness_version": ENGINE_VERSION}, invalid
            )


def test_continuation_follows_complete_tool_pairs_and_current_context():
    group = {
        "turn_id": "1",
        "content": "阶段观察",
        "calls": [{"tool_name": "lookup", "args": {}, "tool_call_id": "read"}],
        "results": [{"operation_id": "11", "tool_call_id": "read", "name": "lookup", "result": {}}],
    }
    arguments = ("System", {"task_id": "1"}, [group], {}, [], "openai_chat.v1")
    original = model_messages(*arguments)
    assert [message.type for message in original] == ["system", "human", "ai", "tool", "human"]
    assert json.loads(original[-1].content) == {"task_id": "1"}
    current = model_messages(*arguments, continuation_prompt="冻结的本轮继续指引")
    assert current[:-1] == original
    assert current[-1].type == "human" and current[-1].content == "冻结的本轮继续指引"


def test_skill_zip_import_rejects_new_scripts_and_keeps_images_managed():
    archive = io.BytesIO()
    with ZipFile(archive, "w") as package:
        package.writestr(
            "SKILL.md",
            "---\nname: harness-method\ndescription: 用于资料核对\n---\n"
            "按需读取[示例图](assets/example.png)。",
        )
        package.writestr("assets/example.png", png())
    preview = parse_skill_import(archive.getvalue(), "method.zip")
    assert preview.compatible, preview.issues
    content = SkillCreate.model_validate(preview.draft.model_dump())
    (image,) = content.resources
    assert image.kind == "image" and image.encoding == "base64"
    assert image.resource_bytes() == png()
    assert base64.b64decode(image.content) == png()
    with ZipFile(archive, "a") as package:
        package.writestr("scripts/check.py", "raise RuntimeError('must never execute')")
    with pytest.raises(WorkflowError, match="scripts/check.py"):
        parse_skill_import(archive.getvalue(), "method.zip")


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
