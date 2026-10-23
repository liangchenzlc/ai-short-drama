from short_drama.agent.tools.presentation import label, visibility


def test_skill_search_is_silent_but_skill_reads_are_steps():
    assert visibility("search_skills") == "silent"
    assert visibility("load_skill") == "step"
    assert visibility("read_skill_resource") == "step"
    assert label("read_skill_resource") == "读取创作方法"


def test_artifacts_and_unknown_tools():
    assert visibility("apply_standard_patch") == "artifact"
    assert visibility("submit_generation_batch") == "artifact"
    assert visibility("not_a_real_tool") == "step"
    assert label("not_a_real_tool") == "执行创作操作"
