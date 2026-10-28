"""Advance expected states only through committed inverse proofs of the same task and target."""

from copy import deepcopy
from typing import Any

from short_drama.domain.assistant.undo.undo import AgentBatchUndo
from short_drama.service.assistant.operations.dialogue import COMMON_VERSIONS


def _binding_matches(standard: dict, binding: dict) -> bool:
    if standard.get("row_version") != binding.get("row_version"):
        return False
    media = standard["media_state"]
    if "media_id" in media:
        return (
            media["media_id"] == binding.get("media_id")
            and standard["current_version_id"] == binding.get("current_version_id")
            and standard["state"] == binding.get("state")
        )
    for field, plural in (("image", "images"), ("video", "videos")):
        if field in binding:
            value = binding[field]
            projected = [{key: value[key] for key in ("id", "media_id")}] if value else []
            if media[plural] != projected:
                return False
    return "image" in binding


def _lift_snapshot(expected: dict, change: dict, inverse: dict) -> dict | None:
    """Bridge native views using exact row versions; retain every unrelated field and fence."""
    before, after = inverse["restored_from"], inverse["after"]
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    lifted = deepcopy(expected)
    if change["kind"] == inverse["kind"] == "sound_line":
        track_fields = {"audio_clips", "clip_positions"}
        if "audio_clips" in expected and "audio_clips" in before:
            return None
        if {key: value for key, value in expected.items() if key not in track_fields} == {
            key: value for key, value in before.items() if key not in track_fields
        }:
            lifted.update({key: value for key, value in after.items() if key in expected})
            return lifted
    elif "media_state" in expected:
        if inverse["kind"] == "character_voice":
            if expected["media_state"].get("voice_version") == before["row_version"]:
                lifted["media_state"]["voice_version"] = after["row_version"]
                return lifted
        elif inverse.get("media_role") in {"image", "video"} and _binding_matches(expected, before):
            lifted["row_version"] = after["row_version"]
            if "media_id" in expected["media_state"]:
                lifted["current_version_id"] = after["current_version_id"]
            else:
                for field, plural in (("image", "images"), ("video", "videos")):
                    if field in after:
                        value = after[field]
                        lifted["media_state"][plural] = (
                            [{key: value[key] for key in ("id", "media_id")}] if value else []
                        )
            return lifted
    elif "media_state" in before and _binding_matches(before, expected):
        lifted["row_version"] = after["row_version"]
        if "current_version_id" in expected:
            lifted["current_version_id"] = after["current_version_id"]
        return lifted
    elif (
        change["kind"] == inverse["kind"] == "shot"
        and change.get("media_role") in {"image", "video"}
        and inverse.get("media_role") in {"image", "video"} - {change["media_role"]}
        and all(expected.get(key) == before.get(key) for key in ("row_version", "image"))
    ):
        lifted["row_version"] = after["row_version"]
        return lifted
    return None


def _advance_group_versions(expected: dict, inverse: dict, receipts: list[AgentBatchUndo]) -> dict:
    """Bridge group counters only through the exact applied structural state and inverse."""
    applied = inverse.get("applied_state")
    if not applied or "media_state" not in applied or "image_groups" not in expected:
        return expected
    candidate = deepcopy(expected)
    candidate["row_version"] = applied["row_version"]
    if "current_version_id" in candidate:
        candidate["current_version_id"] = applied["current_version_id"]
        candidate["state"] = applied["state"]
    if not _binding_matches(applied, candidate):
        return expected
    for receipt in receipts:
        for earlier in receipt.changes:
            if (
                (earlier["kind"], earlier["id"]) == (inverse["kind"], inverse["id"])
                and earlier.get("media_role") == "image"
                and earlier.get("restored_from") == candidate
            ):
                candidate = deepcopy(earlier["after"])
    result = deepcopy(expected)
    result["image_groups"] = deepcopy(candidate["image_groups"])
    return result


def advance_snapshot(
    change: dict[str, Any], receipts: list[AgentBatchUndo], operation_id: int
) -> dict[str, Any] | None:
    expected = deepcopy(change["after"])
    earlier_receipts = []
    for receipt in receipts:
        for inverse in receipt.changes:
            if inverse["id"] != change["id"] or not (
                inverse["kind"] == change["kind"]
                or change["kind"] == "asset"
                and inverse["kind"] == "character_voice"
            ):
                continue
            if receipt.operation_id == operation_id:
                matches = inverse["before"] == expected
            else:
                matches = "restored_from" in inverse and inverse["restored_from"] == expected
            if matches:
                expected = deepcopy(inverse["after"])
            elif (
                receipt.operation_id != operation_id
                and isinstance(expected, dict)
                and "restored_from" in inverse
            ):
                lifted = _lift_snapshot(expected, change, inverse)
                if lifted is not None:
                    groups = _advance_group_versions(expected, inverse, earlier_receipts)
                    if "image_groups" in groups:
                        lifted["image_groups"] = groups["image_groups"]
                    expected = lifted
        earlier_receipts.append(receipt)
    return expected


def advance_target_version(version: str, change: dict, receipts: list[AgentBatchUndo]) -> str:
    for receipt in receipts:
        for inverse in receipt.changes:
            before = inverse.get("restored_from")
            if (
                inverse["kind"] == "asset"
                and inverse["id"] == change["id"]
                and before is not None
                and before.get("row_version") == version
                and (
                    "media_state" not in before
                    or before["media_state"].get("voice_version") == change["after"]["row_version"]
                )
            ):
                version = inverse["after"]["row_version"]
    return version


def advance_document(
    expected: dict[str, Any],
    kind: str,
    identifier: str,
    receipts: list[AgentBatchUndo],
    operation_id: int,
) -> dict[str, Any]:
    current = deepcopy(expected)
    for receipt in receipts:
        for inverse in receipt.changes:
            if (inverse["kind"], inverse["id"]) != (kind, identifier):
                continue
            before = inverse.get(
                "before" if receipt.operation_id == operation_id else "restored_from"
            )
            if before == current:
                current = deepcopy(inverse["after"])
    return current


def record_restorations(operation, changes, selected, receipts, inverse):
    """Prove selected objects; shared versions require the entire domain restored."""
    completed = selected | {key for receipt in receipts for key in receipt.selected_keys}
    for item in inverse:
        key = f"{item['kind']}:{item['id']}"
        if key in selected:
            item["restored_from"] = deepcopy(changes[key]["before"])
            if operation.tool_name == "apply_standard_patch":
                item["applied_state"] = deepcopy(changes[key]["after"])
        elif item["kind"] == "canvas_document":
            keys = {key for key in changes if key.startswith(("canvas_node:", "canvas_edge:"))}
            revision = (
                (operation.arguments.get("patch") or {}).get("expected_revision")
                if operation.tool_name == "apply_canvas_patch"
                else (operation.result or {}).get("before_canvas_revision")
            )
            if revision is not None and keys.issubset(completed):
                item["restored_from"] = {"row_version": str(revision)}
        elif item["kind"] == "sound_document":
            keys = {key for key in changes if key.startswith("sound_line:")}
            sources = [
                change["before"]
                for change in operation.changes
                if change["kind"] in {"sound_line", "sound_document"}
                and change.get("before") is not None
            ]
            if sources and keys.issubset(completed):
                before = min(sources, key=lambda state: int(state["row_version"]))
                item["restored_from"] = {key: before[key] for key in COMMON_VERSIONS}
        elif item["kind"] == "storyboard_document":
            keys = {key for key in changes if key.startswith("shot:")}
            revision = (
                (operation.arguments.get("patch") or {}).get("storyboard_version")
                if operation.tool_name == "apply_standard_patch"
                else (operation.result or {}).get("before_storyboard_version")
            )
            expected = advance_document(
                {"row_version": str((operation.result or {}).get("storyboard_version"))},
                "storyboard_document",
                item["id"],
                receipts,
                operation.id,
            )
            if revision is not None and keys.issubset(completed) and item["before"] == expected:
                item["restored_from"] = {"row_version": str(revision)}
