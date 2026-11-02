from short_drama.agent.context.business_scope import assistant_invocation_locked
from short_drama.domain.assistant.execution.models import AgentRun


def describe_scope(session, conversation, task, operation):
    """Expose the same frozen identities and versions used by the write guard."""
    scope = assistant_invocation_locked(
        session, conversation, task, session.get(AgentRun, operation.run_id), operation
    ).scope
    snapshots = scope.subject_snapshots
    if task.execution_target["mode"] == "standard":
        sound = next(
            (value["sound_binding"] for value in snapshots.values() if value.get("sound_binding")),
            None,
        )
        return {
            "allowed_assets": [
                {"id": key, "row_version": snapshots[f"asset:{key}"]["revision"]}
                for key in sorted(scope.allowed_subjects["asset"], key=int)
            ],
            "allowed_shots": [
                {"id": key, "row_version": snapshots[f"shot:{key}"]["revision"]}
                for key in sorted(scope.allowed_subjects["shot"], key=int)
            ],
            "allowed_sound_lines": sorted(scope.allowed_subjects["sound_line"]),
            "sound_revision": {
                field: sound[field]
                for field in ("row_version", "assembly_version", "timeline_hash")
            }
            if sound
            else None,
        }
    return {
        "allowed_nodes": [
            {"id": key, **snapshots[f"canvas_node:{key}"]["write_versions"]}
            for key in sorted(scope.allowed_subjects["canvas_node"])
        ]
    }
