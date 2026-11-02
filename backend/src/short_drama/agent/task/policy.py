"""Per-task execution limits and stop policy frozen at task creation."""


def policy(task, key, default=None):
    state = getattr(task, "effective_state", None) or {}
    frozen = state.get("agent_policy")
    if not isinstance(frozen, dict):
        return default
    return frozen.get(key, default)
