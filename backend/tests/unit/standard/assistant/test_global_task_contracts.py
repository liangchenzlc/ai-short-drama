"""Global task inputs cannot silently inherit a page or increase reference scope."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.tools import ToolDefinition

from short_drama.agent.memory.context import validated_facts
from short_drama.agent.memory.sources import token_estimate
from short_drama.agent.gateway.protocol import AgentGatewayError
from short_drama.agent.coordinator.runtime import freeze_input
from short_drama.agent.core.events import budget_limits, initial_usage
from short_drama.schemas.agent_memory import MemoryMaintenanceResult
from short_drama.schemas.assistant_task import (
    AssistantTaskMessage,
    GlobalConversationCreate,
    TaskControlRequest,
    TaskExecutionTarget,
)


def test_manual_method_availability_rechecks_target_and_required_tools_without_changing_version():
    from short_drama.agent.skill.runtime import apply_manual_skills, refresh_skill_context

    task = SimpleNamespace(
        id=1,
        effective_state={},
        plan_revision=1,
        row_version=1,
        execution_target={"mode": "infinite_canvas"},
    )
    skill = {
        "id": "script.v1",
        "name": "对白方法",
        "content_version": "2",
        "instructions": "使用明确选入的正文。",
        "modes": ["standard"],
        "required_tools": ["apply_standard_patch"],
        "resources": [],
    }
    apply_manual_skills(task, [skill], 2)
    blocked = refresh_skill_context(task, ["describe_creation_target"])
    assert blocked[0]["issue"] and blocked[0]["instructions"] is None
    activation = blocked[0]["activation_id"]
    task.execution_target = {"mode": "standard"}
    still_blocked = refresh_skill_context(task, ["describe_creation_target"])
    assert "apply_standard_patch" in still_blocked[0]["issue"]
    ready = refresh_skill_context(task, ["describe_creation_target", "apply_standard_patch"])
    assert ready[0]["activation_id"] == activation and ready[0]["content_version"] == "2"
    assert ready[0]["instructions"] == skill["instructions"] and ready[0]["issue"] is None


def test_task_conversation_accepts_project_scope_and_preserves_legacy_creation():
    assert GlobalConversationCreate(title="  创作讨论  ").title == "创作讨论"
    assert GlobalConversationCreate().project_id is None
    assert GlobalConversationCreate(project_id="9007199254740993").project_id == 9007199254740993
    with pytest.raises(ValidationError):
        GlobalConversationCreate(project_id="1.5")


def test_skill_tools_are_bounded_and_never_accept_owner_storage_or_executable_inputs():
    from short_drama.agent.tools.contracts import TOOL_SCHEMAS

    assert {"search_skills", "load_skill", "read_skill_resource"}.issubset(TOOL_SCHEMAS)
    search = TOOL_SCHEMAS["search_skills"][0]
    load = TOOL_SCHEMAS["load_skill"][0]
    resource = TOOL_SCHEMAS["read_skill_resource"][0]
    assert search().limit <= 20
    assert load(skill_id="script.v1", content_version="1").skill_id == "script.v1"
    assert resource(activation_id="9007199254740993", path="assets/example.json").offset == 0
    for schema, body in (
        (search, {"limit": 10000}),
        (search, {"owner_user_id": "2"}),
        (load, {"skill_id": "script.v1", "content_version": "1", "source": "manual"}),
        (resource, {"activation_id": "1", "path": "../secret.txt"}),
        (resource, {"activation_id": "1", "path": "https://example.invalid/secret.txt"}),
        (resource, {"activation_id": "1", "path": "scripts/run.py"}),
        (resource, {"activation_id": "1", "path": "assets/example.json", "bucket": "private"}),
    ):
        with pytest.raises(ValidationError):
            schema.model_validate(body)


def test_image_content_read_requires_exact_frozen_identity_and_one_source():
    from short_drama.agent.tools.contracts import ReadImageContent

    fields = {"group_id": "1", "item_id": "2", "media_id": "3"}
    key = "subject:" + "a" * 64
    assert ReadImageContent(subject_key=key, **fields).media_id == 3
    assert ReadImageContent(operation_id="4", **fields).operation_id == 4
    assert ReadImageContent(operation_id="4", media_id="3").group_id is None
    for invalid in (
        fields,
        {**fields, "subject_key": key, "operation_id": "4"},
        {**fields, "subject_key": key, "url": "https://example.invalid/image.png"},
        {"subject_key": key, "group_id": "1", "media_id": "3"},
        {**fields, "subject_key": key, "media_id": True},
    ):
        with pytest.raises(ValidationError):
            ReadImageContent.model_validate(invalid)


def test_generation_policy_is_versioned_and_cannot_be_changed_by_a_tool():
    from short_drama.agent.tools.contracts import TOOL_SCHEMAS
    from short_drama.schemas.assistant_policy import GenerationPolicyPatch, ReadGenerationPolicy

    saved = GenerationPolicyPatch(media_generation_enabled=False, policy_version="0")
    assert saved.model_dump(mode="json") == {
        "media_generation_enabled": False,
        "policy_version": "0",
    }
    assert TOOL_SCHEMAS["get_generation_policy"][0] is ReadGenerationPolicy
    assert "set_generation_policy" not in TOOL_SCHEMAS
    for value in ("false", 0, None):
        with pytest.raises(ValidationError):
            GenerationPolicyPatch(media_generation_enabled=value, policy_version="0")
    with pytest.raises(ValidationError):
        ReadGenerationPolicy(media_generation_enabled=True)
    with pytest.raises(ValidationError):
        GenerationPolicyPatch(media_generation_enabled=True, policy_version="0", owner_user_id="2")


@pytest.mark.parametrize(
    ("content", "kind", "selector", "count"),
    [
        ("为已选素材生成图片：冷色人物设定", "asset", "selected", 1),
        ("请给已选分镜各生成两张图片", "shot", "selected", 2),
        ("为本任务素材生成4张图", "asset", "results", 4),
        ("为分镜 ID 9007199254740993生成图片", "shot", "ID 9007199254740993", 1),
    ],
)
def test_media_directive_defaults_to_one_per_explicit_target(content, kind, selector, count):
    from short_drama.agent.tools.media import parse_media_directive

    directive = parse_media_directive(content)
    assert directive.kind == kind and directive.selector == selector and directive.count == count


@pytest.mark.parametrize(
    "content",
    [
        "只搭建图片流程",
        "为已选素材生成图片？",
        "不要为已选素材生成图片",
        "引用：为已选素材生成图片",
        "为所有素材生成图片",
        "为已选素材生成0张图",
        "为已选素材生成5张图",
        "为已选素材生成图片并生成视频",
    ],
)
def test_media_directive_never_grants_quoted_ambiguous_or_excessive_generation(content):
    from short_drama.agent.tools.media import parse_media_directive

    assert parse_media_directive(content) is None


def test_media_tool_schema_keeps_versions_and_rejects_private_or_unbounded_parameters():
    from short_drama.agent.tools.contracts import TOOL_SCHEMAS
    from short_drama.schemas.assistant_media import SubmitGenerationBatch

    item = {
        "target_kind": "asset",
        "target_id": "9007199254740993",
        "row_version": "1",
        "model_config_id": "9007199254740995",
        "model_version": "1",
        "prompt": "人物设定",
    }
    body = {"request_message_id": "3", "request_hash": "a" * 64, "requests": [item]}
    assert TOOL_SCHEMAS["submit_generation_batch"][0] is SubmitGenerationBatch
    assert SubmitGenerationBatch.model_validate(body).requests[0].count == 1
    for invalid in (
        {**body, "owner_user_id": "2"},
        {**body, "requests": [item, item]},
        {**body, "requests": [{**item, "count": True}]},
        {**body, "requests": [{**item, "count": 5}]},
        {**body, "requests": [{**item, "media_url": "https://invalid/image.png"}]},
    ):
        with pytest.raises(ValidationError):
            SubmitGenerationBatch.model_validate(invalid)


@pytest.mark.parametrize(
    ("content", "selector", "index"),
    [
        ("把第二张设为主图", "selected", 2),
        ("请把已选图片组的第25张设为主图：保留其他历史", "image_group", 25),
        ("将已选图位设为主图", "image_slot", None),
        ("把已选素材的第一张设为主图", "asset", 1),
        ("把已选分镜的第三张设为主图", "shot", 3),
        ("把图片组 ID 9007199254740993的第二张设为主图", "9007199254740993", 2),
        ("把图片组 ID 123 的第二张设为主图", "123", 2),
    ],
)
def test_image_primary_directive_has_one_explicit_image_choice(content, selector, index):
    from short_drama.agent.tools.image import parse_primary_directive

    directive = parse_primary_directive(content)
    assert directive is not None
    assert directive.selector == selector and directive.output_index == index


@pytest.mark.parametrize(
    "content",
    [
        "把第二张设为主图？",
        "能不能把第二张设为主图",
        "不要把第二张设为主图",
        "引用：把第二张设为主图",
        "“把第二张设为主图”",
        "把所有图片设为主图",
        "把第0张设为主图",
        "把第26张设为主图",
        "把第二张和第三张设为主图",
        "把第二张设为主图并生成视频",
    ],
)
def test_image_primary_directive_does_not_infer_ambiguous_or_quoted_authority(content):
    from short_drama.agent.tools.image import parse_primary_directive

    assert parse_primary_directive(content) is None


def test_image_tools_keep_real_identifiers_and_reject_unbounded_or_untrusted_inputs():
    from short_drama.agent.tools.contracts import ReadImageGroup, SetImageGroupPrimary

    values = {
        "request_message_id": "9007199254740993",
        "request_hash": "a" * 64,
        "subject_key": "subject:" + "b" * 64,
        "group_id": "9007199254740995",
        "item_id": "9007199254740997",
        "expected_primary_version": "2",
        "expected_item_version": "3",
        "expected_target_version": "4",
    }
    assert SetImageGroupPrimary.model_validate(values).model_dump(mode="json") == values
    read = {"subject_key": values["subject_key"], "view": "history", "limit": 10}
    assert ReadImageGroup.model_validate(read).limit == 10
    generated = {"operation_id": "9007199254740993", "group_id": "9007199254740995"}
    assert (
        ReadImageGroup.model_validate(generated).model_dump(mode="json")["operation_id"]
        == generated["operation_id"]
    )
    for invalid in (
        {**values, "owner_user_id": "2"},
        {**values, "media_url": "https://invalid/image.png"},
        {**values, "item_id": "second"},
        {**values, "expected_item_version": "0"},
    ):
        with pytest.raises(ValidationError):
            SetImageGroupPrimary.model_validate(invalid)
    for invalid in (
        {**read, "view": "all_projects"},
        {**read, "limit": 11},
        {**read, "character_limit": 6001},
        {**read, "subject_key": "not-selected"},
        {**read, "include_private_prompt": True},
        {**read, **generated},
        {"operation_id": generated["operation_id"]},
        {},
    ):
        with pytest.raises(ValidationError):
            ReadImageGroup.model_validate(invalid)


def test_new_task_has_no_implicit_selection_target_memory_or_skill():
    message = AssistantTaskMessage(content="聊聊人物动机")
    assert message.context is None and message.execution_target is None
    assert message.memory_project_ids == [] and message.skills == []
    assert message.task_id is None and message.new_task is False


def test_reference_context_cannot_request_an_entire_project_or_canvas():
    for kind in ("project", "canvas"):
        value = {"kind": kind, "project_id": "1", "id": "1", "revision": "1"}
        context = AssistantTaskMessage(content="分析", context=value).context
        assert context.include_document is False
        with pytest.raises(ValidationError):
            AssistantTaskMessage(content="分析", context={**value, "include_document": True})


def test_multi_source_selection_is_explicit_versioned_and_cannot_expand_source_kind():
    from short_drama.schemas.assistant_task import TaskSubjectSelection

    reference = {
        "project_id": "9007199254740993",
        "source_kind": "project",
        "source_id": "9007199254740993",
        "kind": "asset",
        "id": "3",
        "revision": "1",
    }
    selection = {"expected_input_revision": "0", "subjects": [reference]}
    message = AssistantTaskMessage(content="讨论", subject_selection=selection)
    assert message.subject_selection.subjects[0].project_id == 9007199254740993
    assert (
        message.subject_selection.model_dump(mode="json")["subjects"][0]["project_id"]
        == "9007199254740993"
    )
    for invalid in (
        {**selection, "subjects": [reference, reference]},
        {**selection, "subjects": [{**reference, "source_id": "1"}]},
        {**selection, "subjects": [{**reference, "kind": "node"}]},
        {**selection, "subjects": [{**reference, "include_document": True}]},
        {**selection, "retain_keys": ["invented"]},
    ):
        with pytest.raises(ValidationError):
            TaskSubjectSelection.model_validate(invalid)
    with pytest.raises(ValidationError):
        AssistantTaskMessage(content="讨论", subject_selection=selection, context=None)


def test_attachment_selection_is_independent_and_requires_a_known_input_revision():
    selection = {"expected_input_revision": "4", "retain_ids": ["9007199254740993"]}
    message = AssistantTaskMessage(content="只保留另一份资料", attachment_selection=selection)
    assert message.attachment_selection.model_dump(mode="json") == selection
    assert message.subject_selection is None
    for invalid in (
        {"retain_ids": []},
        {**selection, "retain_ids": ["2", "2"]},
        {**selection, "retain_ids": ["node:2"]},
        {**selection, "retain_ids": [str(i) for i in range(1, 18)]},
    ):
        with pytest.raises(ValidationError):
            AssistantTaskMessage(content="讨论", attachment_selection=invalid)


def test_standard_patch_resolves_typed_earlier_references_and_preserves_large_ids():
    from short_drama.schemas.assistant_standard import StandardPatch

    payload = {
        "storyboard_version": "9007199254740993",
        "actions": [
            {
                "action": "create_asset",
                "key": "hero",
                "values": {"kind": "character", "name": "林晚"},
            },
            {
                "action": "create_shot",
                "key": "opening",
                "values": {"script": "林晚走进车站"},
                "assets": ["$hero", "9007199254740995"],
            },
            {
                "action": "update_shot",
                "shot": "$opening",
                "patch": {"row_version": "1", "shot_size": "wide_shot"},
            },
        ],
    }
    batch = StandardPatch.model_validate(payload)
    assert batch.model_dump(mode="json")["storyboard_version"] == "9007199254740993"
    assert batch.actions[1].assets[-1] == "9007199254740995"
    for invalid in (
        {**payload, "storyboard_version": None},
        {**payload, "actions": payload["actions"][1:]},
        {**payload, "actions": [payload["actions"][0], payload["actions"][0]]},
        {**payload, "actions": [payload["actions"][0], {**payload["actions"][2], "shot": "$hero"}]},
        {
            "actions": [
                {
                    "action": "create_asset",
                    "key": "hero",
                    "values": {"kind": "character", "name": "林晚", "media_id": "1"},
                }
            ]
        },
        {
            **payload,
            "actions": [
                {
                    "action": "update_shot",
                    "shot": "1",
                    "patch": {"row_version": "1", "asset_ids": []},
                }
            ],
        },
        {
            **payload,
            "actions": [
                {
                    "action": "update_shot",
                    "shot": "1",
                    "patch": {"row_version": "1", "script": None},
                }
            ],
        },
        {"actions": []},
        {"actions": [{"action": "delete_project", "id": "1"}]},
    ):
        with pytest.raises(ValidationError):
            StandardPatch.model_validate(invalid)


def test_created_result_reader_uses_receipt_identity_and_bounded_ranges():
    from short_drama.agent.tools.contracts import ReadTaskResults

    values = ReadTaskResults.model_validate(
        {
            "operation_id": "9007199254740993",
            "ids": [str(value) for value in range(1, 51)],
            "character_offset": 1048576,
            "character_limit": 6000,
        }
    )
    assert values.model_dump(mode="json")["operation_id"] == "9007199254740993"
    for invalid in (
        {},
        {"operation_id": "1", "ids": [str(value) for value in range(1, 52)]},
        {"operation_id": "1", "ids": ["$hero"]},
        {"operation_id": "1", "project_id": "2"},
        {"operation_id": "1", "character_offset": -1},
        {"operation_id": "1", "character_limit": 6001},
    ):
        with pytest.raises(ValidationError):
            ReadTaskResults.model_validate(invalid)


def test_canvas_increment_rejects_replacement_documents_untrusted_media_and_bad_references():
    from short_drama.schemas.assistant_canvas import AssistantCanvasPatch

    create = {"action": "create_node", "key": "note", "values": {"type": "text", "content": "正文"}}
    connect = {"action": "connect_nodes", "source": "$note", "target": "existing-node"}
    valid = {"expected_revision": "9007199254740993", "actions": [create, connect]}
    assert (
        AssistantCanvasPatch.model_validate(valid).model_dump(mode="json")["expected_revision"]
        == "9007199254740993"
    )
    for invalid in (
        {**valid, "source_document": {}},
        {**valid, "actions": [connect, create]},
        {**valid, "actions": [create, create]},
        {
            **valid,
            "actions": [
                {**create, "values": {"type": "image", "content": "https://fake.invalid/image.png"}}
            ],
        },
        {**valid, "actions": [{**create, "values": {"type": "skill"}}]},
        {**valid, "actions": [{**create, "values": {"type": "text", "metadata": {"taskId": "1"}}}]},
        {**valid, "actions": [{**connect, "source": "same", "target": "same"}]},
        {**valid, "actions": [{**connect, "relation": "fake"}]},
        {
            **valid,
            "actions": [
                {
                    "action": "update_node",
                    "node": "existing",
                    "row_version": "1",
                    "private_revision": "0",
                    "patch": {"prompt": None},
                }
            ],
        },
    ):
        with pytest.raises(ValidationError):
            AssistantCanvasPatch.model_validate(invalid)


@pytest.mark.parametrize(
    ("target", "sources", "profile", "error"),
    [
        ("image", ["text"], None, None),
        ("image", ["video"], None, "assistant_canvas_input_type"),
        ("image", ["audio"], None, "assistant_canvas_input_type"),
        ("audio", ["text", "character"], None, None),
        ("audio", ["character", "character"], None, "assistant_canvas_input_type"),
        ("audio", ["image"], None, "assistant_canvas_input_type"),
        ("text", ["audio"], None, "assistant_canvas_input_type"),
        ("video", ["image"], None, "assistant_canvas_model_required"),
        (
            "video",
            ["image", "image"],
            {"capability_config": {"video": {"references": {"maxImages": 1}}}},
            "assistant_canvas_input_capacity",
        ),
        (
            "video",
            ["image"],
            {"capability_config": {"video": {"references": {"maxImages": 1}}}},
            None,
        ),
        (
            "image",
            ["image"],
            {"logical_capability_spec": {"capability": "image", "inputs": {"image": {"max": 1}}}},
            None,
        ),
    ],
)
def test_canvas_increment_uses_native_input_types_and_the_selected_model_capacity(
    target, sources, profile, error
):
    from short_drama.core.exceptions import WorkflowError
    from short_drama.service.assistant_canvas_service import check_canvas_inputs

    nodes = [
        {
            "id": str(index),
            "type": "image" if kind == "character" else kind,
            "metadata": {"workflowKind": "character"} if kind == "character" else {},
        }
        for index, kind in enumerate(sources)
    ]
    if error:
        with pytest.raises(WorkflowError) as raised:
            check_canvas_inputs({"type": target}, nodes, profile)
        assert raised.value.code == error
    else:
        check_canvas_inputs({"type": target}, nodes + nodes, profile)


def test_canvas_increment_layout_is_deterministic_and_preserves_existing_nested_positions():
    from short_drama.service.assistant_canvas_service import layout_new_nodes

    nodes = {
        "frame": {"id": "frame", "position": {"x": 400, "y": 200}, "width": 300, "height": 200},
        "child": {
            "id": "child",
            "parentId": "frame",
            "position": {"x": 600, "y": -80},
            "width": 350,
            "height": 350,
        },
        "a": {"id": "a", "position": {"x": 0, "y": 0}, "width": 350, "height": 350},
        "b": {"id": "b", "position": {"x": 0, "y": 0}, "width": 720, "height": 405},
        "c": {"id": "c", "position": {"x": 0, "y": 0}, "width": 320, "height": 320},
    }
    edges = [{"fromNodeId": "a", "toNodeId": "b"}, {"fromNodeId": "a", "toNodeId": "c"}]
    saved = deepcopy(nodes)
    layout_new_nodes(nodes, edges, ["a", "b", "c"])
    assert nodes["frame"] == saved["frame"] and nodes["child"] == saved["child"]
    assert nodes["a"]["position"]["x"] >= 400 + 600 + 350 + 120
    assert nodes["b"]["position"]["x"] == nodes["c"]["position"]["x"] > nodes["a"]["position"]["x"]
    assert nodes["b"]["position"]["y"] + nodes["b"]["height"] < nodes["c"]["position"]["y"]
    layout_new_nodes(saved, edges, ["a", "b", "c"])
    assert nodes == saved


@pytest.mark.parametrize("location", ["metadata", "nested", "directorScenes", "timeline"])
def test_canvas_delete_rejects_references_outside_its_increment(location):
    from short_drama.core.exceptions import WorkflowError
    from short_drama.service.assistant_canvas_service import AssistantCanvasService

    nodes = {"target": {"id": "target"}, "dependent": {"id": "dependent", "metadata": {}}}
    document = {"connections": [], "directorScenes": [], "timeline": None}
    if location == "metadata":
        nodes["dependent"]["metadata"]["versionOfNodeId"] = "target"
    elif location == "nested":
        nodes["dependent"]["metadata"]["storyboard"] = {
            "rows": [{"assetBindings": [{"nodeId": "target", "role": "character"}]}]
        }
    elif location == "directorScenes":
        document[location] = [{"id": "scene", "objects": [{"sourceNodeId": "target"}]}]
    else:
        document[location] = {"clips": [{"id": "clip", "nodeId": "target"}]}
    with pytest.raises(WorkflowError) as error:
        AssistantCanvasService._delete(nodes, document, "target", {"target", "dependent"})
    assert error.value.code == "assistant_delete_impact"
    assert "target" in nodes and "assetBindings" not in str(error.value.details)


@pytest.mark.parametrize(
    "text,action,limits,selector,fields,connect",
    [
        (
            "请创建一个文本节点和两个图片节点并连线：雨夜",
            "create",
            {"text": 1, "image": 2},
            None,
            None,
            True,
        ),
        (
            "搭建文本节点、图片节点、视频节点和音频节点",
            "create",
            {"text": 1, "image": 1, "video": 1, "audio": 1},
            None,
            None,
            False,
        ),
        ("修改已选节点的提示词：逆光", "update_node", {}, "selected", {"prompt"}, False),
        ("更新本任务节点的正文：故事", "update_node", {}, "results", {"content"}, False),
        ("删除节点“旧开场”", "delete_node", {}, "旧开场", None, False),
        ("移动已选节点：向右", "move_node", {}, "selected", None, False),
        ("连接已选节点", "connect_nodes", {}, "selected", None, False),
        ("断开本任务节点", "disconnect_nodes", {}, "results", None, False),
        ("改为创建一个音频节点：补充", "create", {"audio": 1}, None, None, False),
    ],
)
def test_canvas_directives_bind_only_the_explicit_effects(
    text, action, limits, selector, fields, connect
):
    from short_drama.agent.tools.canvas import parse_canvas_directive

    value = parse_canvas_directive(text)
    assert value is not None
    assert (value.action, value.limits, value.selector, value.fields, value.connect) == (
        action,
        limits,
        selector,
        frozenset(fields) if fields else None,
        connect,
    )


@pytest.mark.parametrize(
    "text",
    [
        "只讨论创建一个文本节点",
        "不要删除已选节点",
        "请翻译：删除已选节点",
        "创建一个图片节点是什么意思？",
        "他说：连接已选节点",
        "“删除已选节点”",
        "创建所有节点",
        "创建零个文本节点",
        "创建51个图片节点",
        "删除已选节点的正文",
        "创建一个分镜节点",
        "创建一个图片节点并生成",
        "移动全部节点",
    ],
)
def test_canvas_directives_do_not_infer_authority_from_discussion_or_data(text):
    from short_drama.agent.tools.canvas import parse_canvas_directive

    assert parse_canvas_directive(text) is None


@pytest.mark.parametrize("speed", ["nan", "inf", "0", "0.1", "5", "快"])
def test_canvas_settings_reject_non_native_audio_speed(speed):
    from short_drama.schemas.assistant_canvas import AssistantNodeSettings

    with pytest.raises(ValidationError):
        AssistantNodeSettings(audioSpeed=speed)


def test_subject_identity_distinguishes_same_node_key_in_two_canvases_and_preserves_legacy_scope():
    from short_drama.agent.context.subjects import (
        snapshot_subjects,
        subject_key,
        subject_memory_keys
    )

    assert subject_key("1", "canvas", "a", "node", "same") != subject_key(
        "1", "canvas", "b", "node", "same"
    )
    legacy = {
        "kind": "canvas",
        "id": "a",
        "project": {"id": "1"},
        "selected": [
            {"kind": "node", "id": "same", "revision": "3", "content": {"title": "主体"}},
        ],
    }
    subjects = snapshot_subjects(legacy)
    assert subjects[0]["name"] == "主体"
    assert subject_memory_keys(legacy) == {"1:a:node:same"}
    assert subject_memory_keys({"schema_version": 2, "subjects": subjects}) == subject_memory_keys(
        legacy
    )


def test_historical_reply_keeps_its_original_selection_scope_after_removal():
    from short_drama.agent.memory.sources import message_subject_keys

    message = SimpleNamespace(
        task_id=1, references=[{"type": "subject_scope", "keys": ["original"]}]
    )
    assert message_subject_keys(
        message, {1: SimpleNamespace(context_snapshot=None, effective_state={})}
    ) == ["original"]


def test_explicit_new_task_does_not_also_address_an_old_task():
    with pytest.raises(ValidationError):
        AssistantTaskMessage(content="新任务", new_task=True, task_id="1")


def test_canvas_target_is_explicit_and_ids_keep_full_precision():
    target = TaskExecutionTarget(
        mode="infinite_canvas", project_id="9007199254740993", canvas_id="canvas-key"
    )
    assert target.model_dump(mode="json")["project_id"] == "9007199254740993"
    with pytest.raises(ValidationError):
        TaskExecutionTarget(mode="infinite_canvas", project_id="1")
    with pytest.raises(ValidationError):
        TaskExecutionTarget(mode="standard", project_id="1", canvas_id="canvas-key")


def test_resume_requires_the_exact_stop_or_pause_control_version():
    with pytest.raises(ValidationError):
        TaskControlRequest(kind="resume")
    request = TaskControlRequest(kind="resume", expected_control_seq="0")
    assert request.model_dump(mode="json")["expected_control_seq"] == "0"
    with pytest.raises(ValidationError):
        TaskControlRequest(kind="redirect")


def test_whole_segment_budget_includes_history_skills_tools_and_pending_results():
    run = SimpleNamespace(
        checkpoint={"purpose": "global_assistant"},
        budget=budget_limits("global_assistant"),
        usage=initial_usage(),
    )
    values = {
        "instructions": "确认约束" * 3000,
        "user_prompt": "最新补充不能丢失",
        "history": {"messages": [{"parts": [{"content": "历史" * 16000}]}]},
        "deferred_results": {"calls": {"pending": {"content": "完整工具结果" * 1000}}},
        "tools": [
            ToolDefinition(
                name="method", parameters_json_schema={"description": "Skill 资料" * 3000}
            )
        ],
        "max_output_tokens": 8192,
    }
    before = deepcopy(values)
    with pytest.raises(AgentGatewayError) as caught:
        freeze_input(values, run)
    assert caught.value.code == "agent_context_budget_exhausted"
    assert values == before and run.checkpoint == {"purpose": "global_assistant"}


def test_task_constraint_projection_preserves_tool_pairs_and_unrelated_user_content():
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from short_drama.agent.gateway.protocol import deserialize_history, serialize_history
    from short_drama.agent.task.compaction import COMPACTED_GOAL, compacted_history

    goal = "不能丢失的早期要求" * 1000
    history = serialize_history(
        [
            ModelRequest(parts=[UserPromptPart(content=goal)]),
            ModelResponse(parts=[ToolCallPart("describe_creation_target", {}, "pair-1")]),
            ModelRequest(parts=[ToolReturnPart("describe_creation_target", {"id": "1"}, "pair-1")]),
            ModelRequest(parts=[UserPromptPart(content="未压缩的近期纠正")]),
            ModelResponse(parts=[ToolCallPart("read_operation_receipt", {"id": "2"}, "pair-2")]),
        ]
    )
    before = deepcopy(history)
    projected = compacted_history(history, goal)
    restored = deserialize_history(projected)
    assert restored[0].parts[0].content == COMPACTED_GOAL
    assert projected["messages"][1:] == before["messages"][1:]
    assert restored[-1].parts[0].tool_call_id == "pair-2"
    assert history == before


def test_context_budget_preserves_a_complete_tool_pair_and_counts_tool_json_as_text():
    from short_drama.agent.context.budget import segment_budget

    values = {
        "instructions": "边界",
        "history": {
            "messages": [
                {"parts": [{"part_kind": "tool-call", "tool_call_id": "pair", "args": {"id": "1"}}]}
            ]
        },
        "deferred_results": {"calls": {"pair": {"content": "实际回执"}}},
        "max_output_tokens": 8192,
    }
    before = deepcopy(values)
    admitted = segment_budget(values)
    assert admitted["estimated_input_tokens"] < admitted["input_limit_tokens"]
    assert admitted["reserved_tool_tokens"] > 0 and admitted["reserved_safety_tokens"] > 0
    assert values == before
    values["deferred_results"]["calls"]["pair"] = {
        "kind": "binary",
        "media_type": "image/png",
        "data": "不能把工具正文伪装为媒体以绕过预算" * 12000,
    }
    with pytest.raises(AgentGatewayError):
        segment_budget(values)


def test_context_budget_discounts_binary_only_in_a_real_user_prompt():
    from short_drama.agent.context.budget import provider_budget, segment_budget

    binary = {"kind": "binary", "media_type": "image/png", "data": "A" * 400000}
    values = {
        "instructions": "分析选入的图片",
        "user_prompt": ["这幅图", binary],
        "max_output_tokens": 8192,
    }
    first = segment_budget(values)
    assert 2000 <= first["estimated_input_tokens"] < 4000
    provider = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这幅图"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + "A" * 400000},
                    },
                ],
            }
        ]
    }
    assert provider_budget(provider, 8192)["estimated_input_tokens"] < 4000
    provider["messages"].append({"role": "tool", "content": "工具正文" * 20000})
    with pytest.raises(AgentGatewayError):
        provider_budget(provider, 8192)


def test_phase_summary_preserves_a_long_source_chain_without_growing_model_input():
    prior = []
    for index in range(80):
        identifier = str(index + 1)
        job = SimpleNamespace(
            base_summary_id=1000 + index - 1 if prior else None,
            source_snapshot={
                "messages": [
                    {
                        "id": identifier,
                        "role": "user",
                        "input_applied": True,
                        "subject_keys": [],
                        "completed_operation_ids": [],
                    }
                ],
                "prior_facts": prior,
            },
        )
        facts = validated_facts(
            job,
            MemoryMaintenanceResult.model_validate(
                {
                    "facts": [
                        {
                            "kind": "user_fact",
                            "text": "保留对白节奏与人物关系",
                            "source_message_ids": [identifier],
                            "prior_fact_indexes": [0] if prior else [],
                        }
                    ]
                }
            ),
        )
        assert facts[0]["source_message_ids"] == [identifier]
        assert facts[0]["prior_fact_indexes"] == ([0] if prior else [])
        assert facts[0]["source_summary_id"] == (str(job.base_summary_id) if prior else None)
        assert token_estimate(facts) < 200
        prior = facts


def test_phase_summary_rejects_missing_or_fabricated_previous_fact_references():
    job = SimpleNamespace(
        base_summary_id=100,
        source_snapshot={
            "messages": [],
            "prior_facts": [
                {
                    "kind": "user_fact",
                    "text": "明确约束",
                    "source_message_ids": ["1"],
                    "subject_keys": [],
                },
                {
                    "kind": "proposal",
                    "text": "未采用的建议",
                    "source_message_ids": ["2"],
                    "subject_keys": ["private"],
                },
            ],
        },
    )

    def payload(indexes):
        return MemoryMaintenanceResult.model_validate(
            {"facts": [{"kind": "user_fact", "text": "合并", "prior_fact_indexes": indexes}]}
        )

    for indexes in ([0], [0, 2]):
        with pytest.raises(ValueError):
            validated_facts(job, payload(indexes))
    result = validated_facts(job, payload([0, 1]))
    assert result[0]["kind"] == "proposal"
    assert result[0]["subject_keys"] == ["private"]
    assert result[0]["source_message_ids"] == []


def test_phase_summary_requires_a_real_unique_source_and_preserves_explicit_input_coverage():
    for fact in (
        {"kind": "pending", "text": "空来源"},
        {"kind": "pending", "text": "重复", "prior_fact_indexes": [0, 0]},
    ):
        with pytest.raises(ValidationError):
            MemoryMaintenanceResult.model_validate({"facts": [fact]})
    job = SimpleNamespace(
        base_summary_id=None,
        source_snapshot={
            "messages": [
                {
                    "id": "1",
                    "role": "user",
                    "input_applied": True,
                    "subject_keys": [],
                    "completed_operation_ids": [],
                },
                {
                    "id": "2",
                    "role": "user",
                    "input_applied": True,
                    "subject_keys": [],
                    "completed_operation_ids": [],
                },
            ]
        },
    )
    values = MemoryMaintenanceResult.model_validate(
        {"facts": [{"kind": "completed", "text": "未执行的提议", "source_message_ids": ["1"]}]}
    )
    with pytest.raises(ValueError, match="user message"):
        validated_facts(job, values)
    values.facts[0].source_message_ids.append(2)
    assert validated_facts(job, values)[0]["kind"] == "proposal"


def test_phase_summary_replays_legacy_citations_but_sends_only_indexed_prior_text():
    import json

    from short_drama.agent.memory.runtime import maintenance_input

    job = SimpleNamespace(
        kind="summary",
        base_summary_id=100,
        source_snapshot={
            "messages": [],
            "prior_facts": [
                {
                    "kind": "user_fact",
                    "text": "旧回执中的事实",
                    "source_message_ids": ["1"],
                    "subject_keys": [],
                    "prior_fact_indexes": [0],
                    "source_summary_id": "99",
                }
            ],
        },
    )
    values = MemoryMaintenanceResult.model_validate(
        {"facts": [{"kind": "user_fact", "text": "仍保留事实", "source_message_ids": ["1"]}]}
    )
    result = validated_facts(job, values)
    assert result[0]["source_message_ids"] == []
    assert result[0]["source_summary_id"] == "100" and result[0]["prior_fact_indexes"] == [0]
    prompt = json.loads(maintenance_input(job)["user_prompt"])
    assert prompt["prior_facts"] == [
        {"fact_index": 0, "kind": "user_fact", "text": "旧回执中的事实"}
    ]


@pytest.mark.parametrize(
    "text,action,scope,body,target",
    [
        ("请帮我记住：对白简短", "remember", None, "对白简短", None),
        ("记住：对白简短\n转折明确", "remember", None, "对白简短\n转折明确", None),
        ("记住账号是角色遗失的线索", "remember", None, "账号是角色遗失的线索", None),
        ("记住账号偏好：对白简短", "remember", "user", "对白简短", None),
        ("在当前作品记忆里记住：林晚来自南城", "remember", "project", "林晚来自南城", None),
        ("修改记忆“对白节奏”为：长短按场景", "update", None, "长短按场景", "对白节奏"),
        ("忘掉账号记忆“对白节奏”", "forget", "user", None, "对白节奏"),
        ("重新记住本会话记忆“对白节奏”", "restore", "conversation", None, "对白节奏"),
    ],
)
def test_direct_memory_commands_keep_the_original_body_and_explicit_scope(
    text, action, scope, body, target
):
    from short_drama.agent.tools.memory_ops import parse_memory_directive

    value = parse_memory_directive(text)
    assert value is not None
    assert (value.action, value.scope_type, value.content, value.target) == (
        action,
        scope,
        body,
        target,
    )


@pytest.mark.parametrize(
    "text",
    [
        "不要记住这条",
        "记住是什么意思？",
        "请翻译：记住对白简短",
        "他说：记住对白简短",
        "“记住：对白简短”",
        "```\n记住：对白简短\n```",
        "> 记住：对白简短",
        "举例\n记住：对白简短",
        "忘掉所有记忆",
        "重新记住",
        "修改记忆为：无对象",
        "记住：甲\n忘掉记忆乙",
    ],
)
def test_memory_commands_cannot_be_authorized_by_quoted_negated_or_ambiguous_data(text):
    from short_drama.agent.tools.memory_ops import parse_memory_directive

    assert parse_memory_directive(text) is None


@pytest.mark.parametrize(
    "text,action,limits,selector,fields,associate",
    [
        (
            "请创建一个角色和两个分镜并关联：雨夜开场",
            "create",
            {"character": 1, "shot": 2},
            None,
            None,
            True,
        ),
        ("新增十一张素材卡", "create", {"asset": 11}, None, None, False),
        (
            "修改已选素材的提示词：逆光",
            "update_asset",
            {},
            "selected",
            {"prompt", "video_prompt"},
            False,
        ),
        ("删除素材“林晚”", "delete_asset", {}, "林晚", None, False),
        (
            "更新分镜 ID 9007199254740993的正文：开场",
            "update_shot",
            {},
            "ID 9007199254740993",
            {"script"},
            False,
        ),
        ("修改本任务素材：统一命名", "update_asset", {}, "results", None, False),
        ("关联已选分镜和素材", "set_shot_assets", {}, "selected", None, True),
        ("调整已选分镜顺序：末尾移到开头", "reorder_shots", {}, "selected", None, False),
    ],
)
def test_standard_direct_commands_bound_effects_and_keep_large_ids(
    text, action, limits, selector, fields, associate
):
    from short_drama.agent.tools.standard import parse_standard_directive

    value = parse_standard_directive(text)
    assert value is not None
    assert (value.action, value.limits, value.selector, value.fields, value.associate) == (
        action,
        limits,
        selector,
        frozenset(fields) if fields else None,
        associate,
    )


@pytest.mark.parametrize(
    "text",
    [
        "讨论一下角色",
        "不要创建角色",
        "创建角色是什么意思？",
        "创建角色后会怎样",
        "他说：创建一个角色",
        "请翻译：删除素材甲",
        "“创建一个角色”",
        "> 删除已选素材",
        "```\n创建一个角色\n```",
        "创建51个分镜",
        "创建零个角色",
        "创建全部角色",
        "修改未选素材",
        "删除素材",
        "删除已选素材的名称",
        "创建一个角色并删除其他素材",
    ],
)
def test_standard_directives_cannot_come_from_data_questions_or_ambiguous_scope(text):
    from short_drama.agent.tools.standard import parse_standard_directive

    assert parse_standard_directive(text) is None


def test_standard_dialogue_uses_typed_temporary_characters_and_native_document_rules():
    from short_drama.schemas.assistant_standard import StandardPatch

    prefix = [
        {"action": "create_asset", "key": "hero", "values": {"kind": "character", "name": "林晚"}},
        {"action": "create_shot", "key": "opening", "values": {}, "assets": ["$hero"]},
    ]
    action = {
        "action": "edit_dialogue",
        "shot": "$opening",
        "row_version": "1",
        "dialogue_version": "0",
        "document": {"lines": [{"character_id": "$hero", "text": "车来了。"}]},
    }
    batch = StandardPatch(storyboard_version="1", actions=[*prefix, action])
    assert batch.model_dump(mode="json")["actions"][2]["dialogue_version"] == "0"
    assert batch.actions[2].document.lines[0].character_id == "$hero"
    for invalid in (
        {**action, "dialogue_version": "-1"},
        {**action, "document": {"lines": [{"character_id": "$opening", "text": "错误类型"}]}},
        {**action, "document": {"lines": [{"character_id": "$hero", "text": "  "}]}},
        {
            **action,
            "document": {
                "lines": [{"character_id": str(index), "text": "三位角色"} for index in (1, 2, 3)]
            },
        },
    ):
        with pytest.raises(ValidationError):
            StandardPatch(storyboard_version="1", actions=[*prefix, invalid])


@pytest.mark.parametrize(
    "content,kind,count",
    [
        ("为已选图片节点生成2个结果：雨中的人物", "image", 2),
        ("为本任务图片节点生成两张图片", "image", 2),
        ("为已选视频节点生成视频", "video", 1),
        ("为已选音频节点生成一段音频", "audio", 1),
    ],
)
def test_canvas_media_directive_keeps_explicit_kind_and_native_quantity(content, kind, count):
    from short_drama.agent.tools.media import parse_media_directive

    directive = parse_media_directive(content)
    assert directive.kind == "node" and directive.media_kind == kind and directive.count == count


@pytest.mark.parametrize(
    "content",
    [
        "为已选图片节点生成视频",
        "为已选节点生成结果",
        "为已选音频节点生成两段音频",
        "为已选节点生成视频吗？",
        "只搭建图片节点",
        "不要为已选图片节点生成图片",
    ],
)
def test_canvas_media_directive_rejects_ambiguous_or_mismatched_generation(content):
    from short_drama.agent.tools.media import parse_media_directive

    assert parse_media_directive(content) is None


def test_canvas_media_schema_preserves_string_keys_and_rejects_untrusted_transport():
    from short_drama.schemas.assistant_media import SubmitGenerationBatch

    item = {
        "target_kind": "node",
        "target_id": "canvas-node-001",
        "row_version": "9007199254740993",
        "private_revision": "0",
        "model_config_id": "1",
        "model_version": "2",
        "media_kind": "image",
        "prompt": "雨中的人物",
    }
    body = {"request_message_id": "3", "request_hash": "a" * 64, "requests": [item]}
    saved = SubmitGenerationBatch.model_validate(body)
    assert saved.requests[0].target_id == "canvas-node-001"
    assert saved.model_dump(mode="json")["requests"][0]["row_version"] == "9007199254740993"
    for patch in (
        {"media_url": "https://invalid/image.png"},
        {"config": {"apiKey": "untrusted"}},
        {"private_revision": "-1"},
        {"media_kind": "audio", "count": 2},
        {"media_kind": "audio", "reference_media_ids": ["1"]},
    ):
        with pytest.raises(ValidationError):
            SubmitGenerationBatch.model_validate({**body, "requests": [{**item, **patch}]})


@pytest.mark.parametrize(
    "content,selector",
    [
        ("为已选角色生成一个音色：沉稳男声", "selected"),
        ("给本任务角色各生成音色", "results"),
        ("请为角色 ID 9007199254740993 生成音色", "ID 9007199254740993"),
    ],
)
def test_character_voice_directive_requires_one_explicit_sample(content, selector):
    from short_drama.agent.tools.media import parse_media_directive

    directive = parse_media_directive(content)
    assert (directive.kind, directive.media_kind, directive.count, directive.selector) == (
        "asset",
        "audio",
        1,
        selector,
    )


@pytest.mark.parametrize(
    "content",
    [
        "为已选角色生成两个音色",
        "为已选角色生成音色吗？",
        "不要为已选角色生成音色",
        "考虑给已选角色生成音色",
        "为已选场景生成音色",
        "为全部角色生成音色",
    ],
)
def test_character_voice_directive_does_not_infer_generation_authority(content):
    from short_drama.agent.tools.media import parse_media_directive

    assert parse_media_directive(content) is None


def test_character_voice_schema_keeps_own_version_and_bounded_explicit_input():
    from short_drama.schemas.assistant_media import AssistantCharacterVoiceRequest

    values = {
        "target_kind": "character_voice",
        "target_id": "1",
        "row_version": "2",
        "voice_version": "9007199254740993",
        "model_config_id": "3",
        "model_version": "1",
        "prompt": "沉稳自然的男声",
        "preview_text": "清晨的风吹过窗前，今天又是新的开始。",
    }
    assert (
        AssistantCharacterVoiceRequest.model_validate(values).model_dump(mode="json")[
            "voice_version"
        ]
        == "9007199254740993"
    )
    for patch in (
        {"count": 2},
        {"reference_media_ids": ["1"]},
        {"preview_text": "过短"},
        {"prompt": "描述" * 251},
        {"voice_version": "-1"},
        {"voice_id": "untrusted"},
        {"media_kind": "video"},
        {"sample_url": "https://invalid/sample.wav"},
    ):
        with pytest.raises(ValidationError):
            AssistantCharacterVoiceRequest.model_validate({**values, **patch})


def test_native_shot_video_directive_requires_explicit_speech_authority():
    from short_drama.agent.tools.media import parse_media_directive

    for text in ("为已选分镜生成一段有声视频", "为本任务分镜生成原生有声视频"):
        directive = parse_media_directive(text)
        assert directive is not None and directive.native_speech
        assert directive.kind == "shot" and directive.media_kind == "video"
    assert not parse_media_directive("为已选分镜生成视频").native_speech
    assert parse_media_directive("为已选分镜生成两段有声视频") is None
    assert parse_media_directive("考虑为已选分镜生成有声视频") is None


def test_native_shot_video_schema_bounds_dialogue_and_voice_versions():
    from short_drama.schemas.assistant_media import AssistantShotVideoRequest

    values = {
        "target_kind": "shot_video",
        "target_id": "1",
        "row_version": "2",
        "model_config_id": "3",
        "model_version": "1",
        "prompt": "雨中的人物",
        "reference_media_ids": ["4"],
        "native_dialogue_version": "9007199254740993",
        "native_voice_versions": [{"character_id": "5", "row_version": "6", "voice_version": "7"}],
    }
    saved = AssistantShotVideoRequest.model_validate(values).model_dump(mode="json")
    assert saved["native_dialogue_version"] == "9007199254740993"
    for patch in (
        {"native_dialogue_version": "-1"},
        {"native_dialogue_version": None},
        {"native_voice_versions": values["native_voice_versions"] * 2},
        {"audio_reference_media_ids": ["8"]},
        {"native_voice_versions": [{**values["native_voice_versions"][0], "voice_id": "fake"}]},
    ):
        with pytest.raises(ValidationError):
            AssistantShotVideoRequest.model_validate({**values, **patch})


def test_native_video_source_refresh_keeps_newer_voice_than_earlier_task_adoption():
    from types import SimpleNamespace

    from short_drama.agent.tools.media import _native_sources

    subject = {
        "subject_key": "asset:1",
        "kind": "asset",
        "id": "1",
        "project_id": "2",
        "revision": "5",
        "fields": {"name": "林晚"},
        "voice_binding": {"row_version": "3", "record_id": "4", "media_id": "5"},
    }
    snapshot = {"schema_version": 2, "subjects": [subject]}
    task = SimpleNamespace(execution_target={"project_id": "2"}, context_snapshot=snapshot)
    prior = SimpleNamespace(
        id=1,
        result={"project_id": "2"},
        changes=[
            {"kind": "asset", "id": "1", "before": {}, "after": {"row_version": "2"}},
            {
                "kind": "character_voice",
                "id": "1",
                "before": {},
                "after": {"row_version": "1", "record_id": "6", "media_id": "7"},
            },
        ],
    )
    source = _native_sources(task, {"snapshot": snapshot}, [prior])[("asset", "1")]
    assert source["revision"] == "5" and source["voice_binding"] == subject["voice_binding"]
    task.context_snapshot = {"schema_version": 2, "subjects": []}
    assert _native_sources(task, {"snapshot": snapshot}, [prior]) == {}


def test_sound_line_reference_preserves_real_string_identity():
    from short_drama.schemas.assistant_task import TaskSubjectReference

    values = {
        "project_id": "9007199254740993",
        "source_kind": "project",
        "source_id": "9007199254740993",
        "kind": "sound_line",
        "id": "line_a-9",
        "revision": "2",
    }
    assert TaskSubjectReference.model_validate(values).id == "line_a-9"
    for patch in (
        {"id": "../line"},
        {"id": "台词"},
        {"id": "a" * 65},
        {"content_revision": "1"},
        {"source_kind": "canvas"},
    ):
        with pytest.raises(ValidationError):
            TaskSubjectReference.model_validate({**values, **patch})


def test_dialogue_audio_schema_accepts_only_identity_and_trusted_versions():
    from short_drama.schemas.assistant_media import AssistantDialogueAudioRequest

    values = {
        "target_kind": "dialogue_audio",
        "target_id": "line_a-9",
        "row_version": "2",
        "assembly_version": "9007199254740993",
        "timeline_hash": "a" * 64,
        "model_config_id": "3",
        "model_version": "1",
    }
    assert AssistantDialogueAudioRequest.model_validate(values).target_id == "line_a-9"
    for patch in (
        {"count": 2},
        {"reference_media_ids": ["1"]},
        {"prompt": "新台词"},
        {"voice": "fake"},
        {"text": "fake"},
        {"timeline_hash": "bad"},
        {"assembly_version": "0"},
        {"target_id": True},
    ):
        with pytest.raises(ValidationError):
            AssistantDialogueAudioRequest.model_validate({**values, **patch})


def test_dialogue_audio_directives_do_not_authorize_discussion_or_unselected_sources():
    from short_drama.agent.tools.media import parse_media_directive

    for content in (
        "为已选台词生成配音",
        "请为已选台词各生成一段配音",
        "为台词 ID line_a-9生成配音",
    ):
        directive = parse_media_directive(content)
        assert directive is not None and directive.kind == "sound_line"
        assert directive.media_kind == "audio" and directive.count == 1
    for content in (
        "为已选分镜生成配音",
        "考虑为已选台词生成配音",
        "为已选台词生成两段配音",
        "为已选台词生成配音？",
        "引用：为已选台词生成配音",
    ):
        assert parse_media_directive(content) is None


def test_dialogue_audio_adoptions_follow_committed_versions_instead_of_item_order():
    from short_drama.service.assistant_dialogue_service import advance_sound_binding

    original = {
        "assembly_id": "1",
        "assembly_version": "4",
        "row_version": "3",
        "timeline_hash": "a" * 64,
        "line_hash": "b" * 64,
        "line_checksum": "c" * 64,
    }
    second = {**original, "row_version": "4", "assembly_version": "5"}
    third = {**original, "row_version": "5", "assembly_version": "6"}
    changes = [
        {"kind": "sound_line", "id": "other_a", "before": second, "after": third},
        {"kind": "sound_line", "id": "other_b", "before": original, "after": second},
    ]
    assert advance_sound_binding(original, "line", changes) == third
    # A human write between the selected source and task effects breaks the chain.
    assert advance_sound_binding(original, "line", changes[:1]) == original


def test_sound_text_patch_is_incremental_and_requires_both_saved_versions():
    from short_drama.schemas.assistant_standard import StandardPatch

    revision = {"row_version": "0", "assembly_version": "2", "timeline_hash": "a" * 64}
    create = {
        "action": "create_sound_line",
        "key": "opening",
        "values": {"text": "门外有人。", "character": "旁白", "voice": "calm", "config_id": "3"},
    }
    update = {"action": "update_sound_line", "line": "$opening", "patch": {"text": "请开门。"}}
    value = {"sound_revision": revision, "actions": [create, update]}
    assert StandardPatch.model_validate(value).sound_revision.row_version == 0
    for invalid in (
        {"actions": [create]},
        {**value, "sound_revision": {**revision, "assembly_version": "0"}},
        {**value, "actions": [{**create, "values": {**create["values"], "media_id": "5"}}]},
        {**value, "actions": [{**update, "line": "$missing"}]},
        {**value, "actions": [{**update, "line": "line_a-9", "patch": {"start_ms": 40}}]},
        {**value, "actions": [{**update, "line": "line_a-9", "patch": {"text": "  "}}]},
        {**value, "actions": [{**update, "line": "line_a-9", "patch": {}}]},
        {**value, "actions": [{**update, "line": "line_a-9", "patch": {"text": None}}]},
    ):
        with pytest.raises(ValidationError):
            StandardPatch.model_validate(invalid)


def test_sound_text_directive_limits_fields_and_keeps_generation_separate():
    from short_drama.agent.tools.media import parse_media_directive
    from short_drama.agent.tools.standard import parse_standard_directive

    directive = parse_standard_directive("请创建两条台词：雨夜敲门")
    assert directive is not None and directive.limits == {"sound_line": 2}
    edit = parse_standard_directive("修改台词 ID line_a-9的音色：calm")
    assert edit is not None and edit.kind == "sound_line" and edit.fields == {"voice"}
    assert edit.selector == "line_a-9"
    assert parse_standard_directive("编辑本任务台词的正文").selector == "results"
    assert parse_media_directive("为本任务台词生成配音").selector == "results"
    for text in ("讨论创建两条台词", "创建台词？", "删除已选台词", "修改全部台词", "创建301条台词"):
        assert parse_standard_directive(text) is None


def test_sound_text_batch_transitions_advance_all_lines_without_reading_neighbours():
    from short_drama.service.assistant_dialogue_service import advance_sound_binding

    original = {
        "assembly_id": "1",
        "assembly_version": "4",
        "row_version": "3",
        "timeline_hash": "a" * 64,
        "line_hash": "b" * 64,
        "line_checksum": "c" * 64,
    }
    common = {"assembly_version": "5", "row_version": "4"}
    changed = {**original, **common, "line_hash": "d" * 64, "line_checksum": "e" * 64}
    changes = [
        {"kind": "sound_line", "id": "created", "before": None, "after": changed},
        {"kind": "sound_document", "id": "1", "before": original, "after": changed},
        {"kind": "sound_line", "id": "line", "before": original, "after": changed},
    ]
    assert advance_sound_binding(original, "line", changes) == changed
    assert advance_sound_binding(original, "unmodified", changes) == {**original, **common}
