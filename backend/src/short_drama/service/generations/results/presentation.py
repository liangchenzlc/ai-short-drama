"""Human-readable, frozen task origin without request parameter dumps."""

from sqlalchemy import select

from short_drama.domain import Asset, Project, ShotScript


def generation_display_context(session, request, *, lookups=None):
    source = request.get("source") or {}
    scene = source.get("scene")
    snapshot = request.get("source_snapshot") or {}
    project_id = source.get("project_id")
    subject = {
        "character_voice_design": f"角色声音：{snapshot.get('name') or source.get('asset_id', '')}",
        "dialogue_audio": "台词配音",
        "dialogue_extract": "台词提取",
        "script_rewrite": "剧本局部修改",
        "script_assets": "素材提取",
        "script_shots": "分镜脚本",
    }.get(scene, "通用生成")
    if scene in {"shot_image", "shot_video"}:
        action = "生图" if scene == "shot_image" else "视频"
        shot = (
            lookups["shots"].get(int(source["shot_id"]))
            if lookups is not None
            else session.get(ShotScript, int(source["shot_id"]))
        )
        if shot is not None:
            project_id = str(shot.project_id)
            subject = f"分镜 {shot.position:02d} {action}"
        else:
            subject = f"分镜 {source['shot_id']} {action}"
    elif scene == "asset_image":
        asset = snapshot.get("asset") or {}
        if not asset:
            row = (
                lookups["assets"].get(int(source["asset_id"]))
                if lookups is not None
                else session.get(Asset, int(source["asset_id"]))
            )
            if row is not None:
                asset = {"kind": row.kind, "name": row.name}
        label = {"character": "角色", "scene": "场景", "prop": "道具"}.get(
            asset.get("kind"), "素材"
        )
        subject = f"{label}：{asset.get('name') or source['asset_id']}"
    project = (
        lookups["projects"].get(int(project_id))
        if project_id and lookups is not None
        else session.get(Project, int(project_id))
        if project_id
        else None
    )
    return {
        "project": project.name if project else None,
        "subject": subject,
        "scope": "素材生成" if scene == "asset_image" else "通用任务",
    }


def generation_display_contexts(session, requests):
    """Resolve historical origins in page-sized batches without changing frozen labels."""
    requests = list(requests)
    sources = [request.get("source") or {} for request in requests]
    shot_ids = {
        int(source["shot_id"])
        for source in sources
        if source.get("scene") in {"shot_image", "shot_video"}
    }
    asset_ids = {
        int(source["asset_id"])
        for source, request in zip(sources, requests, strict=True)
        if source.get("scene") == "asset_image"
        and not (request.get("source_snapshot") or {}).get("asset")
    }
    shots = (
        {
            row.id: row
            for row in session.scalars(select(ShotScript).where(ShotScript.id.in_(shot_ids)))
        }
        if shot_ids
        else {}
    )
    assets = (
        {row.id: row for row in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))}
        if asset_ids
        else {}
    )
    project_ids = {int(source["project_id"]) for source in sources if source.get("project_id")} | {
        shot.project_id for shot in shots.values()
    }
    projects = (
        {row.id: row for row in session.scalars(select(Project).where(Project.id.in_(project_ids)))}
        if project_ids
        else {}
    )
    lookups = {
        "shots": shots,
        "assets": assets,
        "projects": projects,
    }
    return [generation_display_context(session, request, lookups=lookups) for request in requests]
