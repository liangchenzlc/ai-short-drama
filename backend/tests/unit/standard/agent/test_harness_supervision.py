"""Evidence checks and local supervision; no real model or database is used."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.agent.context.budget import provider_budget
from short_drama.agent.context.capabilities import context_profile
from short_drama.agent.gateway.protocol import AgentGatewayError
from short_drama.agent.harness import supervision
from short_drama.agent.task import contract
from short_drama.agent.tools.registry import tool_catalog, tool_manifest


def requirement(**values):
    return {
        "key": "save",
        "description": "保存两个角色",
        "kind": "write",
        "source_message_id": "101",
        "source_quote": "保存两个角色",
        "minimum_count": 2,
        "artifact_kind": "asset",
        "asset_kind": "character",
        "satisfied": True,
        "evidence_operation_ids": ["201"],
        **values,
    }


@pytest.fixture
def evidence(monkeypatch):
    source = SimpleNamespace(id=101, content="保存两个角色，并回读。", input_revision=1)
    operation = SimpleNamespace(
        id=201,
        status="succeeded",
        tool_name="apply_standard_patch",
        result_refs=[{"kind": "asset", "id": "301"}, {"kind": "asset", "id": "302"}],
        changes=[{"id": str(i), "after": {"kind": "character"}} for i in (301, 302)],
        result={},
        turn_id=10,
    )
    data = SimpleNamespace(messages=[source], operations=[operation], restricted=set())

    class DAO:
        def __init__(self, session):
            pass

        def input_messages(self, task, revision, identifiers):
            return [
                row
                for row in data.messages
                if row.id in identifiers and row.input_revision <= revision
            ]

        def evidence(self, task, identifiers):
            return [row for row in data.operations if row.id in identifiers]

    monkeypatch.setattr(contract, "AgentTranscriptDAO", DAO)
    monkeypatch.setattr(contract, "restricted_messages", lambda *args: (data.restricted, None))
    data.task = SimpleNamespace(id=1, applied_input_revision=1, effective_state={})
    data.run = SimpleNamespace(checkpoint={})
    data.conversation = SimpleNamespace(id=1)
    return data


def evaluate(data, checks):
    return contract.evaluate_review(
        None,
        data.conversation,
        data.task,
        data.run,
        contract.CompletionReview(intent="execution", requirements=checks),
        "实际交付",
    )


def test_requirement_sources_keep_bounded_verbatim_text_and_skip_forgotten_inputs():
    sources = [
        {"id": "101", "seq": 1, "content": "原始用户要求\n" * 3000},
        *[
            {"id": str(101 + i), "seq": i + 1, "content": f"更新要求{i}：补充真实约束。" * 8}
            for i in range(1, 40)
        ],
    ]
    result = contract.source_projection(sources, {139})
    assert len(result["items"]) <= 32 and result["omitted_messages"] > 0
    assert result["estimated_tokens"] <= result["quota_tokens"] == 4000
    original = result["items"][0]
    assert original["id"] == "101" and original["truncated"]
    assert sources[0]["content"].startswith(original["content"])
    assert result["items"][-1]["id"] == "140"
    assert all(item["id"] != "139" for item in result["items"])
    assert not contract.source_projection(sources[:1], {101})["items"]


def test_saved_count_deduplicates_and_checks_asset_category(evidence):
    assert evaluate(evidence, [requirement()])["approved"]
    evidence.operations[0].result_refs[1]["id"] = "301"
    assert not evaluate(evidence, [requirement()])["approved"]
    evidence.operations[0].result_refs[1]["id"] = "302"
    evidence.operations[0].changes[1]["after"]["kind"] = "scene"
    assert not evaluate(evidence, [requirement()])["approved"]


def test_creation_directory_is_read_evidence_but_does_not_count_as_saved_output(evidence):
    evidence.operations[0].tool_name = "describe_creation_target"
    check = requirement(kind="read", minimum_count=1, artifact_kind=None, asset_kind=None)
    assert evaluate(evidence, [check])["approved"]
    evidence.task.effective_state = {}
    rejected = evaluate(evidence, [requirement()])
    assert not rejected["approved"] and rejected["missing"][0]["reason"] == "证据不是实际保存操作"


def test_readback_supports_write_review_without_increasing_saved_count(evidence):
    read = SimpleNamespace(
        id=202,
        status="succeeded",
        tool_name="read_task_results",
        result_refs=[{"kind": "asset", "id": "303"}],
        changes=[{"id": "303", "after": {"kind": "character"}}],
        result={},
        turn_id=11,
    )
    evidence.operations.append(read)
    checks = [requirement(evidence_operation_ids=["201", "202"])]
    assert evaluate(evidence, checks)["approved"]
    assert not evaluate(
        evidence, [requirement(evidence_operation_ids=["201", "202"], minimum_count=3)]
    )["approved"]
    assert not evaluate(evidence, [requirement(evidence_operation_ids=["202"])])["approved"]


@pytest.mark.parametrize("change", ["foreign", "failed", "floor", "error", "forged", "forgotten"])
def test_invalid_evidence_cannot_close_task(evidence, change):
    check = requirement()
    if change == "foreign":
        check["evidence_operation_ids"] = ["999"]
    elif change == "failed":
        evidence.operations[0].status = "failed"
    elif change == "floor":
        evidence.run.checkpoint["history_floor_turn_id"] = 10
    elif change == "error":
        evidence.operations[0].result = {"error": {"code": "unavailable"}}
    elif change == "forged":
        check["source_quote"] = "并不存在的原话"
    else:
        evidence.restricted.add(101)
    assert not evaluate(evidence, [check])["approved"]


def test_continue_preserves_requirements_and_new_user_input_can_revise(evidence):
    assert evaluate(evidence, [requirement()])["approved"]
    evidence.task.applied_input_revision = 2
    missing = requirement(
        key="other", kind="answer", artifact_kind=None, asset_kind=None, answer_quote="实际交付"
    )
    assert not evaluate(evidence, [missing])["approved"]
    shrunk = requirement(minimum_count=1)
    assert not evaluate(evidence, [shrunk])["approved"]
    evidence.messages.append(SimpleNamespace(id=102, input_revision=3, content="只保存一个角色"))
    evidence.task.applied_input_revision = 3
    revised = requirement(
        minimum_count=1,
        description="保存一个角色",
        source_message_id="102",
        source_quote="只保存一个角色",
    )
    # The extra requirement was also frozen; changes cannot silently omit it.
    assert not evaluate(evidence, [revised])["approved"]


def test_image_intent_is_insufficient_until_pixels_delivered(evidence):
    check = requirement(kind="image", minimum_count=1, artifact_kind=None, asset_kind=None)
    assert not evaluate(evidence, [check])["approved"]
    evidence.task.effective_state["media_evidence"] = {"201": {"turn_id": "11"}}
    assert evaluate(evidence, [check])["approved"]


def test_image_directory_supports_inspection_without_counting_as_delivered_pixels(evidence):
    evidence.operations[0].tool_name = "read_image_content"
    evidence.operations.append(
        SimpleNamespace(
            id=202, status="succeeded", tool_name="read_image_group", result={}, turn_id=11
        )
    )
    evidence.task.effective_state["media_evidence"] = {"201": {"turn_id": "11"}}
    check = requirement(
        kind="image",
        minimum_count=1,
        artifact_kind=None,
        asset_kind=None,
        evidence_operation_ids=["201", "202"],
    )
    assert evaluate(evidence, [check])["approved"]
    assert not evaluate(evidence, [{**check, "minimum_count": 2}])["approved"]
    assert not evaluate(evidence, [{**check, "evidence_operation_ids": ["202"]}])["approved"]


def test_empty_citations_and_false_answer_quotes_are_rejected(evidence):
    with pytest.raises(ValidationError):
        contract.RequirementCheck.model_validate(requirement(source_quote="  "))
    check = requirement(kind="answer", artifact_kind=None, asset_kind=None, answer_quote="已保存")
    assert not evaluate(evidence, [check])["approved"]


def test_rewording_keeps_canonical_requirement_but_cannot_reduce_count(evidence):
    assert evaluate(evidence, [requirement()])["approved"]
    assert evaluate(evidence, [requirement(description="保留两名人物")])["approved"]
    current = evidence.task.effective_state["task_contract"]["requirements"][0]
    assert current["description"] == "保存两个角色"
    assert not evaluate(evidence, [requirement(minimum_count=1)])["approved"]


def test_reviewed_evidence_preserves_real_ids_without_bypassing_future_checks(evidence):
    assert evaluate(evidence, [requirement()])["approved"]
    previous = evidence.task.effective_state["task_contract"]
    retained = contract.reviewed_projection(previous)
    assert retained["save"]["evidence_operation_ids"] == ["201"]
    assert not contract.reviewed_projection(previous, floor=10)
    altered = {**previous, "requirements": [{**previous["requirements"][0], "minimum_count": 3}]}
    assert not contract.reviewed_projection(altered)
    evidence.operations[0].status = "failed"
    assert not evaluate(evidence, [requirement()])["approved"]
    assert evidence.task.effective_state["task_contract"]["reviewed_evidence"]


def test_keyed_review_uses_authoritative_definition_and_rejects_unknown_or_overwritten_keys(
    evidence,
):
    assert evaluate(evidence, [requirement()])["approved"]
    check = {"key": "save", "satisfied": True, "evidence_operation_ids": ["201"]}

    def assess(**values):
        return contract.evaluate_review(
            None,
            evidence.conversation,
            evidence.task,
            evidence.run,
            contract.CompletionAssessment(intent="execution", **values),
            "实际交付",
        )

    assert assess(checks=[check])["approved"]
    evidence.operations[0].result_refs.pop()
    assert not assess(checks=[check])["approved"], "Keyed review must still verify saved counts"
    assert not assess(checks=[{**check, "key": "unknown"}])["approved"]
    assert not assess(additional_requirements=[requirement(minimum_count=1)])["approved"]
    with pytest.raises(ValidationError):
        contract.RegisteredRequirementCheck.model_validate({**check, "minimum_count": 1})


def test_state_requirements_count_live_observations_and_registered_contract(evidence):
    item = requirement(kind="contract", minimum_count=1, artifact_kind=None, asset_kind=None)
    assert not evaluate(evidence, [item])["approved"]
    assert evaluate(evidence, [item])["approved"]
    item = requirement(kind="observation", minimum_count=2, artifact_kind=None, asset_kind=None)
    evidence.task.effective_state = {
        "working_set": {
            "items": [
                {"operation_id": "201", "turn_id": "10", "kind": "observation"},
                {"operation_id": "202", "turn_id": "11", "kind": "inference"},
            ]
        }
    }
    assert not evaluate(evidence, [item])["approved"]
    evidence.task.effective_state["working_set"]["items"][1]["kind"] = "observation"
    assert evaluate(evidence, [item])["approved"]
    evidence.run.checkpoint["history_floor_turn_id"] = 10
    assert not evaluate(evidence, [item])["approved"]


def test_notes_support_read_review_without_increasing_actual_reads(evidence):
    evidence.operations[0].tool_name = "read_resource"
    evidence.operations.append(
        SimpleNamespace(
            id=202,
            status="succeeded",
            turn_id=11,
            tool_name="record_task_observation",
            result={"recorded": True},
        )
    )
    item = requirement(
        kind="read",
        artifact_kind=None,
        asset_kind=None,
        minimum_count=1,
        evidence_operation_ids=["201", "202"],
    )
    assert evaluate(evidence, [item])["approved"]
    assert not evaluate(evidence, [{**item, "minimum_count": 2}])["approved"]
    assert not evaluate(evidence, [{**item, "evidence_operation_ids": ["202"]}])["approved"]


@pytest.mark.parametrize("version", [1, 2])
def test_frozen_review_versions_remain_replayable(evidence, monkeypatch, version):
    monkeypatch.setattr(supervision, "candidate_text", lambda *args: "实际交付")
    if version == 1:
        arguments = {"intent": "execution", "requirements": [requirement()]}
    else:
        assert evaluate(evidence, [requirement()])["approved"]
        arguments = {
            "intent": "execution",
            "checks": [{"key": "save", "satisfied": True, "evidence_operation_ids": ["201"]}],
        }
    evidence.run.checkpoint["completion_candidate"] = {"turn_id": "10", "memory_epoch": 0}
    result = supervision.process_completion(
        None,
        evidence.conversation,
        evidence.task,
        evidence.run,
        SimpleNamespace(response={}),
        SimpleNamespace(
            inputs={
                "kwargs": {"supervision_version": version, "decision_purpose": "completion_review"}
            }
        ),
        {
            "output_kind": "tool_requests",
            "output": {"calls": [{"tool_name": "submit_completion_review", "args": arguments}]},
        },
    )
    assert result["output_kind"] == "text" and result["output"] == "实际交付"


@pytest.mark.parametrize("change", ["input", "memory", "scope"])
def test_stale_candidate_is_removed_before_its_content_can_reach_a_model(change):
    task = SimpleNamespace(applied_input_revision=2)
    run = SimpleNamespace(
        checkpoint={
            "memory_epoch": 1,
            "completion_candidate": {
                "turn_id": "10",
                "input_revision": "2",
                "memory_epoch": 1,
            },
        }
    )
    if change == "input":
        task.applied_input_revision = 3
    elif change == "memory":
        run.checkpoint["memory_epoch"] = 2
    else:
        run.checkpoint["history_floor_turn_id"] = 10
    assert supervision.prepare_review(None, task, run, {}) == {}
    assert "completion_candidate" not in run.checkpoint


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *_args, **_kwargs):
        return _Rows(self.rows)


def _progress_row(identifier, digest, kind="read"):
    return SimpleNamespace(id=identifier, result_digest=digest, evidence_kind=kind)


def _progress_task(**profile):
    return SimpleNamespace(
        id=1,
        applied_input_revision=1,
        effective_state={"agent_policy": {"progress": {"window": 3, "zero_windows": 2, **profile}}},
    )


def test_progress_accepts_new_pages_and_ignores_legitimate_waits(monkeypatch):
    stopped = []
    monkeypatch.setattr(supervision, "finish_locked", lambda *args: stopped.append(args[-1]))
    task = _progress_task()
    rows = []
    operation = SimpleNamespace(id=1, tool_name="read_resource")
    for index in range(20):
        rows.insert(0, _progress_row(index + 1, f"{index:064x}"))
        supervision.record_progress(
            _Session(rows[:6]), None, task, None, operation, {"content": "真实线索"}
        )
    assert task.effective_state["progress"]["zero_windows"] == 0 and not stopped

    waiting = SimpleNamespace(id=99, tool_name="get_generation_status")
    before = task.effective_state["progress"]["zero_windows"]
    for _ in range(5):
        supervision.record_progress(
            _Session(rows[:6]), None, task, None, waiting, {"settled": False}
        )
    assert task.effective_state["progress"]["zero_windows"] == before


def test_repeated_identical_evidence_pauses_after_two_zero_windows(monkeypatch):
    stopped = []
    monkeypatch.setattr(supervision, "finish_locked", lambda *args: stopped.append(args[-1]))
    task = _progress_task()
    rows = [_progress_row(index + 1, "a" * 64) for index in range(6)]
    supervision.record_progress(
        _Session(rows),
        None,
        task,
        None,
        SimpleNamespace(id=1, tool_name="read_task_results"),
        {"content": "same"},
    )
    assert task.effective_state["progress"]["zero_windows"] == 1
    supervision.record_progress(
        _Session(rows),
        None,
        task,
        None,
        SimpleNamespace(id=2, tool_name="read_task_results"),
        {"content": "same"},
    )
    assert stopped and stopped[-1]["code"] == "agent_progress_stalled"


def test_correctable_errors_do_not_stall_before_a_complete_observation_window(monkeypatch):
    stopped = []
    monkeypatch.setattr(supervision, "finish_locked", lambda *args: stopped.append(args[-1]))
    task = _progress_task(window=24)
    for identifier in range(1, 24):
        rows = [_progress_row(index, str(index), "none") for index in range(identifier, 0, -1)]
        supervision.record_progress(
            _Session(rows),
            None,
            task,
            None,
            SimpleNamespace(id=identifier, tool_name="read_resource"),
            {"error": "correctable"},
        )
    assert not stopped and task.effective_state["progress"]["zero_windows"] == 0
    supervision.record_progress(
        _Session([_progress_row(index, str(index), "none") for index in range(24, 0, -1)]),
        None,
        task,
        None,
        SimpleNamespace(id=24, tool_name="read_resource"),
        {"error": "correctable"},
    )
    assert not stopped and task.effective_state["progress"]["zero_windows"] == 1
    supervision.record_progress(
        _Session([_progress_row(index, str(index), "none") for index in range(25, 0, -1)]),
        None,
        task,
        None,
        SimpleNamespace(id=25, tool_name="read_resource"),
        {"error": "correctable"},
    )
    assert stopped[-1]["code"] == "agent_progress_stalled"


def test_progressive_schemas_are_isolated_cached_and_append_in_activation_order():
    task = SimpleNamespace(
        execution_target={"mode": "standard"}, context_snapshot=None, effective_state={}
    )
    base = tool_manifest(task, media_enabled=False, progressive=True)
    full = tool_manifest(task, media_enabled=False)
    assert len(json.dumps(base)) < len(json.dumps(full)) * 0.8
    assert "standard_write" in {group["group"] for group in tool_catalog(task, media_enabled=False)}
    original = base[0]["parameters"]
    base[0]["parameters"] = {"corrupt": True}
    assert tool_manifest(task, media_enabled=False, progressive=True)[0]["parameters"] == original
    task.effective_state["enabled_tool_groups"] = ["standard_write"]
    first = tool_manifest(task, media_enabled=False, progressive=True)
    task.effective_state["enabled_tool_groups"].append("memory_write")
    second = tool_manifest(task, media_enabled=False, progressive=True)
    assert second[: len(first)] == first


def test_capacity_declaration_is_bound_to_identity_and_platform_ceiling():
    snapshot = {
        "row_version": 2,
        "model_key": "test",
        "base_url": "local",
        "credential_identity": "hash",
    }
    snapshot["capability_cache"] = {"agent_inputs": {**snapshot, "context_window_tokens": 1048576}}
    assert context_profile(snapshot) == {
        "context_window_tokens": 262144,
        "context_window_evidence": "declared",
    }
    snapshot["row_version"] = 3
    assert context_profile(snapshot)["context_window_tokens"] == 65536
    with pytest.raises(AgentGatewayError, match="agent_context_budget_exhausted"):
        provider_budget({"text": "a" * 90000}, 8192, context_window=16384)
