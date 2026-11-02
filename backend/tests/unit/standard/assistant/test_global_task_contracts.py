"""Global task inputs cannot silently inherit a page or increase reference scope."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.agent.gateway.contracts import AgentGatewayError
from short_drama.agent.memory.context import validated_facts
from short_drama.agent.memory.sources import token_estimate
from short_drama.agent.tools.registry import TOOL_SPECS, get_spec
from short_drama.schemas.assistant.memory.memory import MemoryMaintenanceResult
from short_drama.schemas.assistant.tasks.task import (
    AssistantTaskMessage,
    GlobalConversationCreate,
    TaskControlRequest,
    TaskExecutionTarget,
)


def test_task_conversation_accepts_project_scope_and_preserves_legacy_creation():
    assert GlobalConversationCreate(title="  创作讨论  ").title == "创作讨论"
    assert GlobalConversationCreate().project_id is None
    assert GlobalConversationCreate(project_id="9007199254740993").project_id == 9007199254740993
    with pytest.raises(ValidationError):
        GlobalConversationCreate(project_id="1.5")


def test_skill_tools_are_bounded_and_never_accept_owner_storage_or_execution_flags():

    assert {
        "runtime__search_skills",
        "runtime__load_skill",
        "runtime__read_skill_resource",
    }.issubset({spec.name for spec in TOOL_SPECS})
    search = get_spec("runtime__search_skills").args_schema
    load = get_spec("runtime__load_skill").args_schema
    resource = get_spec("runtime__read_skill_resource").args_schema
    assert search().limit <= 20
    assert load(name="script.v1", content_version="1", purpose="读取对白方法").name == "script.v1"
    assert resource(activation_id="9007199254740993", path="assets/example.json").offset == 0
    assert resource(activation_id="1", path="scripts/run.py").path == "scripts/run.py"
    for schema, body in (
        (search, {"limit": 10000}),
        (search, {"owner_user_id": "2"}),
        (load, {"skill_id": "script.v1", "content_version": "1", "source": "manual"}),
        (resource, {"activation_id": "1", "path": "../secret.txt"}),
        (resource, {"activation_id": "1", "path": "https://example.invalid/secret.txt"}),
        (resource, {"activation_id": "1", "path": "assets/example.json", "execute": True}),
        (resource, {"activation_id": "1", "path": "assets/example.json", "command": "run"}),
        (resource, {"activation_id": "1", "path": "assets/example.json", "bucket": "private"}),
    ):
        with pytest.raises(ValidationError):
            schema.model_validate(body)


def test_image_content_read_requires_exact_frozen_identity_and_one_source():
    from short_drama.schemas.assistant.operations.image import ReadImageContent

    fields = {
        "project_id": "4",
        "target_kind": "asset",
        "target_id": "5",
        "group_id": "1",
        "item_id": "2",
        "media_id": "3",
        "purpose": "核对构图",
    }
    assert ReadImageContent(**fields).media_id == 3
    for invalid in (
        {key: value for key, value in fields.items() if key != "purpose"},
        {**fields, "operation_id": "4"},
        {**fields, "url": "https://example.invalid/image.png"},
        {key: value for key, value in fields.items() if key != "item_id"},
        {**fields, "media_id": True},
    ):
        with pytest.raises(ValidationError):
            ReadImageContent.model_validate(invalid)


def test_generation_policy_is_versioned_and_cannot_be_changed_by_a_tool():

    from short_drama.schemas.assistant.tasks.policy import (
        GenerationPolicyPatch,
        ReadGenerationPolicy,
    )

    saved = GenerationPolicyPatch(media_generation_enabled=False, policy_version="0")
    assert saved.model_dump(mode="json") == {
        "media_generation_enabled": False,
        "policy_version": "0",
    }
    assert get_spec("runtime__get_generation_policy").args_schema is ReadGenerationPolicy
    assert "set_generation_policy" not in {spec.name for spec in TOOL_SPECS}
    for value in ("false", 0, None):
        with pytest.raises(ValidationError):
            GenerationPolicyPatch(media_generation_enabled=value, policy_version="0")
    with pytest.raises(ValidationError):
        ReadGenerationPolicy(media_generation_enabled=True)
    with pytest.raises(ValidationError):
        GenerationPolicyPatch(media_generation_enabled=True, policy_version="0", owner_user_id="2")


def test_media_tool_schema_keeps_versions_and_rejects_private_or_unbounded_parameters():

    from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch

    item = {
        "target_kind": "asset",
        "target_id": "9007199254740993",
        "row_version": "1",
        "model_config_id": "9007199254740995",
        "model_version": "1",
        "prompt": "人物设定",
    }
    body = {"project_id": "3", "requests": [item]}
    assert get_spec("workbench__submit_generation_batch").args_schema is SubmitGenerationBatch
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


def test_image_tools_keep_real_identifiers_and_reject_unbounded_or_untrusted_inputs():
    from short_drama.schemas.assistant.operations.image import ReadImageGroup, SetImageGroupPrimary

    values = {
        "project_id": "9007199254740993",
        "target_kind": "asset",
        "target_id": "9007199254740994",
        "group_id": "9007199254740995",
        "item_id": "9007199254740997",
        "expected_primary_version": "2",
        "expected_item_version": "3",
        "expected_target_version": "4",
    }
    assert SetImageGroupPrimary.model_validate(values).model_dump(mode="json") == values
    read = {key: values[key] for key in ("project_id", "target_kind", "target_id")}
    read.update(view="history", limit=10)
    assert ReadImageGroup.model_validate(read).limit == 10
    generated = {"operation_id": "9007199254740993"}
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
        {**read, "target_kind": "image_slot"},
        {**read, "include_private_prompt": True},
        {**read, **generated},
        {"operation_id": generated["operation_id"]},
        {},
    ):
        with pytest.raises(ValidationError):
            ReadImageGroup.model_validate(invalid)


def test_new_task_has_no_implicit_selection_target_memory_or_skill():
    message = AssistantTaskMessage(content="聊聊人物动机")
    assert message.subject_selection is None and message.execution_target is None
    assert message.memory_project_ids == [] and message.skills == []
    assert message.task_id is None and message.new_task is False


def test_legacy_context_is_not_a_task_message_input():
    for kind in ("project", "canvas"):
        value = {"kind": kind, "project_id": "1", "id": "1", "revision": "1"}
        with pytest.raises(ValidationError):
            AssistantTaskMessage(content="分析", context=value)


def test_multi_source_selection_is_explicit_versioned_and_cannot_expand_source_kind():
    from short_drama.schemas.assistant.tasks.task import TaskSubjectSelection

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
    from short_drama.schemas.assistant.operations.standard import StandardPatch

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
    from short_drama.schemas.assistant.operations.tool_inputs import ReadTaskResults

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
    from short_drama.schemas.assistant.operations.canvas import AssistantCanvasPatch

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
    from short_drama.service.assistant.operations.canvas import check_canvas_inputs

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
    from short_drama.service.assistant.operations.canvas import layout_new_nodes

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
    from short_drama.service.assistant.operations.canvas import AssistantCanvasService

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


@pytest.mark.parametrize("speed", ["nan", "inf", "0", "0.1", "5", "快"])
def test_canvas_settings_reject_non_native_audio_speed(speed):
    from short_drama.schemas.assistant.operations.canvas import AssistantNodeSettings

    with pytest.raises(ValidationError):
        AssistantNodeSettings(audioSpeed=speed)


def test_subject_identity_distinguishes_same_node_key_in_two_canvases():
    from short_drama.agent.context.subjects import (
        snapshot_subjects,
        subject_key,
        subject_memory_keys,
    )

    assert subject_key("1", "canvas", "a", "node", "same") != subject_key(
        "1", "canvas", "b", "node", "same"
    )
    subjects = [{"project_id": "1", "source_id": "a", "kind": "node", "id": "same"}]
    snapshot = {"schema_version": 2, "subjects": subjects}
    assert snapshot_subjects(snapshot) == subjects
    assert subject_memory_keys(snapshot) == {"1:a:node:same"}


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


def test_control_contract_accepts_only_stop_without_retired_fields():
    assert TaskControlRequest(kind="stop").model_dump(mode="json") == {"kind": "stop"}
    for values in (
        {"kind": "resume", "expected_control_seq": "0"},
        {"kind": "redirect", "content": "新方向"},
        {"kind": "stop", "message_id": "1"},
        {"kind": "stop", "content": "停止"},
    ):
        with pytest.raises(ValidationError):
            TaskControlRequest.model_validate(values)


def test_context_budget_preserves_a_complete_tool_pair_and_counts_tool_json_as_text():
    from short_drama.agent.context.budget import provider_budget

    values = {
        "messages": [
            {"role": "system", "content": "边界"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "pair",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"id":"1"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "pair", "content": "实际回执"},
        ],
    }
    before = deepcopy(values)
    admitted = provider_budget(values, 8192)
    assert admitted["estimated_input_tokens"] < admitted["input_limit_tokens"]
    assert admitted["reserved_tool_tokens"] > 0 and admitted["reserved_safety_tokens"] > 0
    assert values == before
    values["messages"][-1]["content"] = {
        "kind": "binary",
        "media_type": "image/png",
        "data": "不能把工具正文伪装为媒体以绕过预算" * 12000,
    }
    with pytest.raises(AgentGatewayError):
        provider_budget(values, 8192)


def test_context_budget_discounts_binary_only_in_a_real_user_prompt():
    from short_drama.agent.context.budget import provider_budget

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


def test_standard_dialogue_uses_typed_temporary_characters_and_native_document_rules():
    from short_drama.schemas.assistant.operations.standard import StandardPatch

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


def test_canvas_media_schema_preserves_string_keys_and_rejects_untrusted_transport():
    from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch

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
    body = {"project_id": "3", "canvas_id": "canvas-test", "requests": [item]}
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


def test_character_voice_schema_keeps_own_version_and_bounded_explicit_input():
    from short_drama.schemas.assistant.operations.media import AssistantCharacterVoiceRequest

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


def test_native_shot_video_schema_bounds_dialogue_and_voice_versions():
    from short_drama.schemas.assistant.operations.media import AssistantShotVideoRequest

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

    from short_drama.agent.context.business_scope import selected_scope_subjects

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
    source = selected_scope_subjects(snapshot["subjects"], "2", [prior])[2]["asset:1"]
    assert source["revision"] == "5" and source["voice_binding"] == subject["voice_binding"]
    task.context_snapshot = {"schema_version": 2, "subjects": []}
    assert selected_scope_subjects([], "2", [prior])[2] == {}


def test_sound_line_reference_preserves_real_string_identity():
    from short_drama.schemas.assistant.tasks.task import TaskSubjectReference

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
    from short_drama.schemas.assistant.operations.media import AssistantDialogueAudioRequest

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


def test_dialogue_audio_adoptions_follow_committed_versions_instead_of_item_order():
    from short_drama.service.assistant.operations.dialogue import advance_sound_binding

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
    from short_drama.schemas.assistant.operations.standard import StandardPatch

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


def test_sound_text_batch_transitions_advance_all_lines_without_reading_neighbours():
    from short_drama.service.assistant.operations.dialogue import advance_sound_binding

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
