"""Only trusted task snapshots and committed scope effects grant business access."""

from copy import deepcopy

from short_drama.agent.context.delegation import delegated_inputs_locked
from short_drama.agent.context.delegation_results import visible_business_outputs_locked
from short_drama.agent.context.image_catalog import _updated_catalog
from short_drama.agent.context.subjects import eligible_snapshot, snapshot_subjects
from short_drama.agent.memory.sources import restricted_messages
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
from short_drama.domain.assistant.execution.models import AgentToolCall
from short_drama.service.assistant.operations.image import freeze_image_catalog
from short_drama.service.assistant.operations.invocation import (
    BusinessInvocation,
    InvocationScope,
    assistant_request_id,
)
from short_drama.service.identity.authentication.actor import business_actor_locked


def assistant_invocation_locked(session, conversation, task, run, operation):
    target = task.execution_target or {}
    if not target.get("project_id"):
        raise WorkflowError("assistant_business_scope", "当前任务没有业务作品范围", 403)
    project_id = str(target["project_id"])
    delegated = delegated_inputs_locked(session, run.id, operation.scope_id)
    prior = visible_business_outputs_locked(session, run, operation.scope_id)
    restricted, _ = restricted_messages(session, conversation.id)
    snapshot = delegated.get("context_snapshot") if delegated is not None else task.context_snapshot
    allowed, catalogs, snapshots = selected_scope_subjects(
        snapshot_subjects(eligible_snapshot(snapshot, restricted)), project_id, prior
    )
    generations = _generation_ids(prior, int(project_id)) | {
        int(identifier) for identifier in (delegated or {}).get("generation_ids", [])
    }
    include_generation_results_locked(
        session,
        conversation.owner_user_id,
        int(project_id),
        prior,
        allowed,
        catalogs,
        snapshots,
        generation_ids=generations,
    )
    call = session.get(AgentToolCall, operation.tool_call_id)
    return BusinessInvocation(
        actor=business_actor_locked(session, conversation.owner_user_id, call.provider_call_id),
        source="assistant",
        request_id=assistant_request_id(run.id, operation.scope_id, call.provider_call_id),
        scope=InvocationScope(
            project_ids=frozenset({int(project_id)}),
            tool_names=frozenset({operation.tool_name}),
            subject_policy="selected",
            allowed_subjects={key: frozenset(value) for key, value in allowed.items()},
            image_catalogs=catalogs,
            subject_snapshots=snapshots,
            script_reference=deepcopy(
                delegated.get("script_reference")
                if delegated is not None
                else task.effective_state.get("script_reference")
            ),
            generation_ids=frozenset(generations),
        ),
    )


def selected_scope_subjects(subjects, project_id, prior):
    allowed = {kind: set() for kind in ("asset", "shot", "sound_line", "canvas_node")}
    catalogs, snapshots = {}, {}
    for subject in subjects:
        if subject["project_id"] != project_id:
            continue
        kind = "canvas_node" if subject["kind"] == "node" else subject["kind"]
        if kind in allowed:
            allowed[kind].add(subject["id"])
            snapshots[f"{kind}:{subject['id']}"] = deepcopy(subject)
        if subject.get("image_catalog"):
            catalog = _updated_catalog(subject, prior)
            key = f"{catalog['target_kind']}:{catalog['target_id']}"
            if key not in catalogs:
                catalogs[key] = catalog
            else:
                _merge_image_catalog(catalogs[key], catalog)
    for saved in prior:
        if (saved.result or {}).get("project_id") != project_id:
            continue
        for change in saved.changes:
            kind = "canvas_node" if change["kind"] == "node" else change["kind"]
            if kind in allowed and change["before"] is None:
                allowed[kind].add(change["id"])
            key = f"{kind}:{change['id']}"
            if kind in allowed and change["id"] in allowed[kind]:
                snapshot = snapshots.setdefault(
                    key,
                    {
                        "fields": {},
                        "id": change["id"],
                        "project_id": project_id,
                        "source_id": saved.result.get("canvas_id", project_id),
                    },
                )
                after = change["after"]
                if int(after["row_version"]) > int(snapshot.get("revision", "0")) or (
                    kind == "canvas_node"
                    and int(after["private_revision"])
                    > int(snapshot.get("write_versions", {}).get("private_revision", "0"))
                ):
                    snapshot["revision"] = after["row_version"]
                    snapshot["fields"].update(deepcopy(after))
                    if kind == "canvas_node":
                        snapshot["fields"] = deepcopy(after["document"])
                        snapshot["write_versions"] = {
                            field: after[field] for field in ("row_version", "private_revision")
                        }
                    elif kind == "asset" and change["before"] is None:
                        snapshot["voice_binding"] = {
                            "row_version": "0",
                            "record_id": None,
                            "media_id": None,
                        }
                    elif kind == "sound_line":
                        snapshot["sound_binding"] = {
                            field: after[field]
                            for field in (
                                "assembly_id",
                                "assembly_version",
                                "row_version",
                                "timeline_hash",
                                "line_hash",
                                "line_checksum",
                            )
                        }
    from short_drama.service.assistant.operations.dialogue import advance_sound_binding

    for key, snapshot in snapshots.items():
        if key.startswith("sound_line:") and snapshot.get("sound_binding"):
            snapshot["sound_binding"] = advance_sound_binding(
                snapshot["sound_binding"],
                snapshot["id"],
                [change for saved in prior for change in saved.changes],
            )
    return allowed, catalogs, snapshots


def include_generation_results_locked(
    session, owner, project_id, prior, allowed, catalogs, snapshots, *, generation_ids=None
):
    generation_ids = (
        _generation_ids(prior, project_id) if generation_ids is None else generation_ids
    )
    if not generation_ids:
        return
    dao = BusinessInvocationDAO(session)
    outputs = dao.generation_outputs(list(generation_ids))
    for group in dao.generated_image_groups(owner, project_id, generation_ids):
        kind = "asset" if group.asset_id else "shot"
        target_id = str(group.asset_id or group.shot_id)
        if target_id not in allowed[kind]:
            continue
        catalog = freeze_image_catalog(session, owner, project_id, "image_group", group.id)
        produced = {
            item["media_id"] for item in outputs.get(group.task_id, []) if item["kind"] == "image"
        }
        for entry in catalog["groups"]:
            for item in entry["items"]:
                item["history_media_ids"] = [
                    identifier for identifier in item["history_media_ids"] if identifier in produced
                ]
                if item["media_id"] not in produced:
                    item["media_id"], item["status"] = None, "unavailable"
            entry["items"] = [
                item for item in entry["items"] if item["media_id"] or item["history_media_ids"]
            ]
        if not any(entry["items"] for entry in catalog["groups"]):
            continue
        adoption = (group.source_context or {}).get("assistant_adoption_snapshot")
        source_version = (group.source_context or {}).get("target_version")
        if adoption or source_version:
            catalog["target_version"] = adoption["row_version"] if adoption else source_version
        key = f"{kind}:{target_id}"
        snapshot = snapshots.get(key)
        if snapshot and adoption and snapshot["revision"] == source_version:
            snapshot["revision"] = adoption["row_version"]
        if key in catalogs:
            _merge_image_catalog(catalogs[key], catalog)
        else:
            catalogs[key] = catalog
    sources = dao.generation_sources(list(generation_ids))
    for generation_id, source in sources.items():
        node_id = source.get("canvas_node")
        snapshot = snapshots.get(f"canvas_node:{node_id}")
        if (
            node_id in allowed["canvas_node"]
            and snapshot is not None
            and (snapshot["source_id"] == source["canvas_id"])
        ):
            snapshot.setdefault("media", []).extend(
                {"kind": "image", "media_id": item["media_id"]}
                for item in outputs.get(generation_id, [])
                if item["kind"] == "image"
            )


def _generation_ids(prior, project_id):
    return {
        int(item["generation_id"])
        for operation in prior
        if operation.tool_name == "workbench__submit_generation_batch"
        and (operation.result or {}).get("project_id") == str(project_id)
        for item in operation.result.get("items", [])
    }


def _merge_image_catalog(current, added):
    current["target_version"] = str(
        max(int(current["target_version"]), int(added["target_version"]))
    )
    for candidate in added["groups"]:
        group = next((item for item in current["groups"] if item["id"] == candidate["id"]), None)
        if group is None:
            current["groups"].append(candidate)
            continue
        slots = {item["id"]: item for item in group["items"]}
        for item in candidate["items"]:
            if item["id"] not in slots or int(item["item_version"]) > int(
                slots[item["id"]]["item_version"]
            ):
                slots[item["id"]] = item
        group["items"] = list(slots.values())
        if int(candidate["primary_version"]) > int(group["primary_version"]):
            group.update({key: value for key, value in candidate.items() if key != "items"})
