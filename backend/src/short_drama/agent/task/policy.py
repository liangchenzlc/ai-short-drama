"""Per-task execution limits and stop policy frozen at task creation."""


def policy(task, key, default=None):
    state = getattr(task, "effective_state", None) or {}
    frozen = state.get("agent_policy")
    if not isinstance(frozen, dict):
        return default
    return frozen.get(key, default)


def budget_caps(task):
    """Return the task's frozen per-Run caps."""
    caps = policy(task, "budget_caps")
    return caps if isinstance(caps, dict) else {}


def new_policy(
    decision_cap: int,
    output_token_cap: int,
    *,
    window_operations: int = 24,
    zero_windows: int = 2,
    requirement_attempt_cap: int = 4,
) -> dict:
    return {
        "stop": "boundary.v1",
        "budget_caps": {
            "decision_calls": int(decision_cap),
            "output_tokens": int(output_token_cap),
        },
        "progress": {
            "window": int(window_operations),
            "zero_windows": int(zero_windows),
        },
        "requirement_attempts": int(requirement_attempt_cap),
    }


def new_policy_from_settings(settings) -> dict:
    """Read the tunables with defaults so partial settings objects still work."""
    return new_policy(
        getattr(settings, "agent_run_decision_cap", 240),
        getattr(settings, "agent_run_output_token_cap", 2_000_000),
        window_operations=getattr(settings, "agent_progress_window_operations", 24),
        zero_windows=getattr(settings, "agent_progress_zero_windows", 2),
        requirement_attempt_cap=getattr(settings, "agent_requirement_attempt_cap", 4),
    )
