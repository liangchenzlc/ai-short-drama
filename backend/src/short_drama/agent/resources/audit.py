"""Resource delivery facts recorded only after a successful model receipt."""

from sqlalchemy import select

from short_drama.domain.assistant.execution.models import AgentToolCall


def _inline_resources(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"image_ref", "audio_ref"} and isinstance(item, dict):
                yield item
            else:
                yield from _inline_resources(item)
    elif isinstance(value, list):
        for item in value:
            yield from _inline_resources(item)


def record_deliveries_locked(session, turn):
    for reference in _inline_resources(turn.request_messages):
        call = session.scalar(
            select(AgentToolCall).where(
                AgentToolCall.run_id == turn.run_id,
                AgentToolCall.scope_id == turn.scope_id,
                AgentToolCall.provider_call_id == reference["metadata"]["tool_call_id"],
                AgentToolCall.status == "succeeded",
            )
        )
        if call is None:
            continue
        delivery = {"turn_id": str(turn.id), "resource": reference}
        previous = (call.result or {}).get("resource_deliveries", [])
        if delivery not in previous:
            call.result = {**call.result, "resource_deliveries": [*previous, delivery]}


def pixels_delivered(session, operation):
    call = session.get(AgentToolCall, operation.tool_call_id)
    return any(
        item["resource"]["kind"] == "image"
        for item in (call.result or {}).get("resource_deliveries", [])
    )
