from short_drama.agent.context.prompt import build_segment_prompt, remember_digest

STABLE = "固定系统提示：只使用允许的工具。"


def test_first_segment_carries_state_then_user_text():
    result = build_segment_prompt(
        stable=STABLE, dynamic={"step": 1}, first_prompt="写第一集", has_history=False
    )
    assert result.instructions == STABLE
    assert '"step":1' in result.user_prompt
    assert result.user_prompt.endswith("写第一集")


def test_continuation_repeats_state_only_when_it_changed():
    first = build_segment_prompt(
        stable=STABLE, dynamic={"step": 1}, first_prompt="写第一集", has_history=False
    )
    same = build_segment_prompt(
        stable=STABLE,
        dynamic={"step": 1},
        first_prompt=None,
        has_history=True,
        previous_digest=first.digest,
    )
    assert same.user_prompt is None
    assert same.instructions == STABLE

    changed = build_segment_prompt(
        stable=STABLE,
        dynamic={"step": 2},
        first_prompt=None,
        has_history=True,
        previous_digest=first.digest,
    )
    assert changed.user_prompt is not None and '"step":2' in changed.user_prompt
    assert changed.digest != first.digest


def test_digest_is_independent_of_key_order():
    left = build_segment_prompt(
        stable="x", dynamic={"a": 1, "b": 2}, first_prompt="p", has_history=False
    )
    right = build_segment_prompt(
        stable="x", dynamic={"b": 2, "a": 1}, first_prompt="p", has_history=False
    )
    assert left.digest == right.digest


def test_remember_digest_persists_once():
    class Run:
        checkpoint = {}

    run = Run()
    remember_digest(run, "abc")
    assert run.checkpoint["prompt_digest"] == "abc"
    remember_digest(run, "abc")
    assert run.checkpoint["prompt_digest"] == "abc"
