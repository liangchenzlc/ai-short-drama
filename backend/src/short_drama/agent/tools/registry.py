"""Tool dispatch registry — each domain registers handlers by tool name."""

# Tool name → handler callable mapping.
# Populated by tools/ submodules at import time via register().
_REGISTRY: dict = {}


def register(tool_name: str):
    """Decorator: register a tool handler function by tool name.

    Usage::

        @register("my_tool")
        def handle_my_tool(session, conversation, run, task, operation, settings):
            ...
    """

    def decorator(fn):
        _REGISTRY[tool_name] = fn
        return fn

    return decorator


def get_handler(tool_name: str):
    """Return the registered handler or None if not registered."""
    return _REGISTRY.get(tool_name)


def registered_names() -> frozenset:
    """Return an immutable snapshot of all registered tool names."""
    return frozenset(_REGISTRY)
