"""Tool classes and user-quote validation shared without importing the contract."""

import json

SOURCE_BYTES = 12000

READ_TOOLS = frozenset(
    {
        "runtime__read_resource",
        "workbench__read_current_script",
        "runtime__read_selected_subjects",
        "runtime__read_task_results",
        "runtime__read_skill_resource",
        "workbench__read_image_group",
        "workbench__read_image_content",
        "runtime__read_memory",
        "runtime__read_conversation_history",
        "runtime__describe_creation_target",
    }
)

WRITE_TOOLS = frozenset(
    {
        "workbench__apply_standard_patch",
        "runtime__propose_script_patch",
        "workbench__apply_canvas_patch",
        "workbench__submit_generation_batch",
        "workbench__set_image_group_primary",
    }
)


def source_valid(item, messages, restricted) -> bool:
    """The cited user text must exist, be applied and not be forgotten."""
    source = messages.get(str(item.source_message_id))
    return bool(
        source
        and int(item.source_message_id) not in restricted
        and item.source_quote.strip()
        and item.source_quote in source.content
    )


def result_source_ids(result: dict) -> list[str]:
    sources = {str(value) for value in result.get("source_message_ids", [])}
    if result.get("answer_message_id"):
        sources.add(str(result["answer_message_id"]))
    return sorted(sources)


def source_projection(sources: list[dict], restricted: set[int]) -> dict:
    """Keep the original goal and newest corrections verbatim within 12 KiB."""
    eligible = [item for item in sources if int(item["id"]) not in restricted]
    selected, seen, used = [], set(), 0
    for source in [*eligible[:1], *reversed(eligible[-31:])]:
        if source["id"] in seen:
            continue
        raw = source["content"].encode("utf-8")
        content = raw[: min(8000, SOURCE_BYTES - used)].decode("utf-8", errors="ignore")
        item = {"id": source["id"], "seq": source["seq"], "content": content}
        item["truncated"] = content != source["content"]
        cost = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        while content and used + cost > SOURCE_BYTES:
            content = content[: len(content) // 2]
            item.update(content=content, truncated=True)
            cost = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if not content:
            break
        selected.append(item)
        seen.add(source["id"])
        used += cost
    return {
        "items": sorted(selected, key=lambda item: item["seq"]),
        "omitted_messages": len(eligible) - len(selected),
        "estimated_tokens": (used + 2) // 3,
        "quota_tokens": SOURCE_BYTES // 3,
    }
