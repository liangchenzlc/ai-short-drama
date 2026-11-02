from short_drama.utils.json_digest import digest as digest


def source_snapshot(step, run, now):
    """Build a frozen source record for a candidate creation step."""
    source = step["source"]
    return {
        "project_id": str(source["project_id"]),
        "content_version": source["content_version"],
        "storyboard_version": source["storyboard_version"],
        "project_row_version": source["project_row_version"],
        "target_kind": step["target_kind"],
        "target_id": str(step["target_id"]),
        "target_row_version": source.get("target_row_version"),
        "model_name": step.get("model_name") or run.config_snapshot.get("name"),
        "generated_at": now.isoformat(),
    }
