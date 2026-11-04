import json
from types import SimpleNamespace

import pytest

from short_drama.agent.runtime.events import text_parts_for_turn
from short_drama.agent.tools.contracts import ToolSpec
from short_drama.agent.tools.presentation import label, project_display
from short_drama.agent.tools.trace_visibility import ToolTraceVisibility, tool_trace_visibility


def test_internal_tools_are_hidden_and_unknown_tools_default_to_visible():
    assert tool_trace_visibility("runtime__search_skills") is ToolTraceVisibility.HIDDEN
    assert tool_trace_visibility("runtime__read_memory") is ToolTraceVisibility.HIDDEN
    assert tool_trace_visibility("runtime__load_skill") is ToolTraceVisibility.VISIBLE
    assert tool_trace_visibility("runtime__read_skill_resource") is ToolTraceVisibility.VISIBLE
    assert (
        tool_trace_visibility("workbench__submit_generation_batch") is ToolTraceVisibility.VISIBLE
    )
    assert tool_trace_visibility("not_a_real_tool") is ToolTraceVisibility.VISIBLE
    assert label("runtime__read_skill_resource") == "读取创作方法"


def test_tool_spec_defaults_to_visible_trace():
    spec = ToolSpec("test", "test", {}, lambda: None)
    assert spec.trace_visibility is ToolTraceVisibility.VISIBLE


def test_catalog_trace_kinds_cover_every_registered_tool():
    from short_drama.agent.tools.registry import TOOL_SPECS

    groups = {
        "subtask": {"task"},
        "skill": {
            "runtime__search_skills",
            "runtime__load_skill",
            "runtime__read_skill_resource",
            "runtime__release_skill",
            "runtime__materialize_skill_resource",
        },
        "sandbox": {"execute", "ls", "read_file", "write_file", "edit_file", "glob", "grep"},
        "wait": {"runtime__ask_user", "runtime__wait_generation"},
    }
    names = {spec.name for spec in TOOL_SPECS}
    assert all(group <= names for group in groups.values())
    for spec in TOOL_SPECS:
        expected = next((kind for kind, group in groups.items() if spec.name in group), "tool")
        assert spec.trace_kind == expected


@pytest.mark.parametrize(
    "name,registered_kind,expected",
    [("future_skill_probe", "sandbox", "sandbox"), ("retired_skill_probe", None, "tool")],
)
def test_operation_kind_uses_registered_metadata_and_unknown_history_is_safe(
    monkeypatch, name, registered_kind, expected
):
    from short_drama.agent.runtime import events
    from short_drama.core.time import utcnow

    now = utcnow()
    operation = SimpleNamespace(
        id=1,
        scope_id=2,
        tool_call_id=3,
        tool_name=name,
        result={"_display": {}},
        arguments={},
        finished_at=None,
        created_at=now,
        status="running",
        error=None,
        result_refs=[],
    )
    session = SimpleNamespace(get=lambda *_: SimpleNamespace(parent_tool_call_id=None, turn_id=4))
    monkeypatch.setattr(
        events.AssistantTraceDAO,
        "latest",
        lambda *_: SimpleNamespace(payload={"channels": {}, "source": "business"}),
    )
    monkeypatch.setattr(events, "save_block_locked", lambda _s, _c, _r, block, **_: block)
    spec = (
        ToolSpec(name, "test", {}, lambda: None, trace_kind=registered_kind)
        if registered_kind
        else None
    )
    projected = events.operation_block_locked(
        session,
        SimpleNamespace(id=5, next_event_seq=1),
        SimpleNamespace(id=6),
        operation,
        spec=spec,
    )
    assert projected["kind"] == expected and projected["trace_visibility"] == "visible"


def test_streamed_message_parts_require_contiguous_offsets_and_exact_content():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    def parts(rows, content="雨🙂声"):
        session = SimpleNamespace(execute=lambda _statement: Result(rows))
        return text_parts_for_turn(
            session,
            SimpleNamespace(id=1),
            SimpleNamespace(id=2),
            3,
            content,
        )

    assert parts([(10, {"offset": 0, "delta": "雨🙂"}), (12, {"offset": 7, "delta": "声"})]) == [
        {"turn_id": "3", "event_seq": 10, "content": "雨🙂声"}
    ]
    assert parts([(10, {"offset": 0, "delta": "雨"}), (12, {"offset": 7, "delta": "声"})]) == []
    assert parts([(10, {"offset": 0, "delta": "雨🙂声"})], "其他内容") == []


def test_generation_and_image_read_summaries_are_safe_and_bounded():
    from short_drama.agent.tools.presentation import project_display

    task = SimpleNamespace(id=1, goal="写一场夜雨中的重逢", execution_target=None)
    generation, _ = project_display(
        spec=SimpleNamespace(name="workbench__submit_generation_batch"),
        session=None,
        task=task,
        run=None,
        operation=SimpleNamespace(
            arguments={
                "requests": [
                    {"media_kind": "image", "count": 2},
                    {"media_kind": "video", "count": 1},
                    {"media_kind": "unknown", "count": 100},
                ]
            }
        ),
        state={},
    )
    assert generation.summary == "2张图片、1段视频"

    image, _ = project_display(
        spec=SimpleNamespace(name="workbench__read_image_content"),
        session=None,
        task=task,
        run=None,
        operation=SimpleNamespace(arguments={"purpose": "分析人物的服装与表情"}),
        state={},
    )
    assert image.summary == "用途：分析人物的服装与表情"


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("task", {"description": "Use token=trace-secret to access the fixture"}),
        ("runtime__export_artifacts", {"paths": ["token=trace-secret.txt"]}),
    ],
    ids=["subtask-description", "workspace-path"],
)
def test_entire_public_display_redacts_target_names_and_paths(name, arguments):
    display, bindings = project_display(
        spec=SimpleNamespace(name=name),
        session=None,
        task=None,
        run=None,
        operation=SimpleNamespace(arguments=arguments, scope_id=1, tool_call_id=2),
        state={},
    )
    assert "trace-secret" not in json.dumps(display.model_dump(mode="json"))
    assert "[已隐藏]" in display.target.display_name and "[已隐藏]" in display.title
    if display.target.path is not None:
        assert display.target.path == "[已隐藏]"
    assert bindings == []
