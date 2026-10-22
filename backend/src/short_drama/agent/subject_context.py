"""Stable subject identities and a bounded projection, including legacy task snapshots."""

from copy import deepcopy

from short_drama.utils.json_digest import digest


def subject_key(project_id, source_kind, source_id, kind, identifier):
    return "subject:" + digest(
        [str(project_id), source_kind, str(source_id), kind, str(identifier)]
    )


def snapshot_subjects(snapshot):
    if not snapshot:
        return []
    if snapshot.get("schema_version") == 2:
        return snapshot["subjects"]
    result = []
    for item in snapshot.get("selected", []):
        project_id = snapshot["project"]["id"]
        fields = {k: deepcopy(v) for k, v in item.items() if k not in {"kind", "id", "revision"}}
        result.append(
            {
                "subject_key": subject_key(
                    project_id, snapshot["kind"], snapshot["id"], item["kind"], item["id"]
                ),
                "project_id": project_id,
                "project_name": snapshot["project"].get("name", ""),
                "source_kind": snapshot["kind"],
                "source_id": snapshot["id"],
                "kind": item["kind"],
                "id": item["id"],
                "revision": item["revision"],
                "name": item.get("name") or item.get("content", {}).get("title") or item["kind"],
                "fields": fields,
                "allowed_fields": sorted(fields),
                "checksum": digest(fields),
                "snapshot_id": digest(item),
                "legacy_truncated": bool(snapshot.get("truncated")),
            }
        )
    return result


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
