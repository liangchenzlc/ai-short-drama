"""Stable result fingerprints shared by supervision and audit projections."""

from short_drama.utils.json_digest import digest

# Fields that change on every replay and therefore must not split one result
# into "new evidence".
TRANSIENT_RESULT_FIELDS = frozenset(
    {
        "operation_id",
        "receipt_operation_id",
        "image_read_id",
        "invocation_id",
        "created_at",
        "activation_id",
        "activation_record",
        "tool_call_id",
        "purpose",
    }
)


def stable(value):
    if isinstance(value, dict):
        return {
            key: stable(child) for key, child in value.items() if key not in TRANSIENT_RESULT_FIELDS
        }
    if isinstance(value, list):
        return [stable(child) for child in value]
    return value


def result_digest(tool_name: str, result: dict) -> str:
    return digest({"tool": tool_name, "result": stable(result)})
