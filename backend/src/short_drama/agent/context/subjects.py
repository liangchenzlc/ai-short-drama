"""Stable subject identities and bounded projections for task snapshots."""

from short_drama.utils.json_digest import digest


def subject_key(project_id, source_kind, source_id, kind, identifier):
    return "subject:" + digest(
        [str(project_id), source_kind, str(source_id), kind, str(identifier)]
    )


def snapshot_subjects(snapshot):
    if not snapshot:
        return []
    return snapshot["subjects"]


def subject_catalog(snapshot):
    return [
        {
            k: v
            for k, v in item.items()
            if k
            not in {
                "fields",
                "media",
                "write_versions",
                "image_catalog",
                "voice_binding",
                "sound_binding",
            }
        }
        for item in snapshot_subjects(snapshot)
    ]


def subject_memory_keys(snapshot):
    return {
        f"{item['project_id']}:{item['source_id']}:{item['kind']}:{item['id']}"
        for item in snapshot_subjects(snapshot)
    }


def visible_result_reference(reference: dict, scope_keys: set[str]) -> bool:
    kind = "node" if reference.get("kind") == "canvas_node" else reference.get("kind")
    return any(key.endswith(f":{kind}:{reference.get('id')}") for key in scope_keys)
