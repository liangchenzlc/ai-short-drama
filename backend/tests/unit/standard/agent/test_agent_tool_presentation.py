import json
from types import SimpleNamespace

import pytest

from short_drama.agent.tools.presentation import label, project_display, visibility


def test_skill_search_is_silent_but_skill_reads_are_steps():
    assert visibility("runtime__search_skills") == "silent"
    assert visibility("runtime__load_skill") == "step"
    assert visibility("runtime__read_skill_resource") == "step"
    assert label("runtime__read_skill_resource") == "读取创作方法"


def test_artifacts_and_unknown_tools():
    assert visibility("workbench__apply_standard_patch") == "artifact"
    assert visibility("workbench__submit_generation_batch") == "artifact"
    assert visibility("not_a_real_tool") == "step"
    assert label("not_a_real_tool") == "执行创作操作"


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
