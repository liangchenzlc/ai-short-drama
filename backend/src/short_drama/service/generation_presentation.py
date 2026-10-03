"""Human-readable, frozen task origin without request parameter dumps."""

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from short_drama.domain import Asset, Episode, Project, ShotScript


def generation_display_context(session, request, *, lookups=None):
    source = request.get("source") or {}
    scene = source.get("scene")
    snapshot = request.get("source_snapshot") or {}
    project_id, episode_id = source.get("project_id"), source.get("episode_id")
    subject = {
        "character_voice_design": f"角色声音：{snapshot.get('name') or source.get('asset_id', '')}",
        "dialogue_audio": "台词配音",
        "dialogue_extract": "台词提取",
        "novel_script": "小说改编",
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
            episode_id = str(shot.episode_id)
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
    episode_label = None
    if episode_id:
        episode = (
            lookups["episodes"].get(int(episode_id))
            if lookups is not None
            else session.get(Episode, int(episode_id))
        )
        if episode is not None:
            project_id = str(episode.project_id)
            number = (
                lookups["episode_numbers"][episode.id]
                if lookups is not None
                else session.scalar(
                    select(func.count())
                    .select_from(Episode)
                    .where(
                        Episode.project_id == episode.project_id,
                        Episode.position <= episode.position,
                    )
                )
            )
            episode_label = f"第 {number} 集：{episode.title}"
    project = (
        lookups["projects"].get(int(project_id))
        if project_id and lookups is not None
        else session.get(Project, int(project_id))
        if project_id
        else None
    )
    return {
        "project": project.name if project else None,
        "episode": episode_label,
        "subject": subject,
        "scope": "素材库" if scene == "asset_image" else "通用任务",
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
    episode_ids = {int(source["episode_id"]) for source in sources if source.get("episode_id")} | {
        shot.episode_id for shot in shots.values()
    }
    episodes, numbers = {}, {}
    if episode_ids:
        earlier = aliased(Episode)
        number = (
            select(func.count())
            .select_from(earlier)
            .where(earlier.project_id == Episode.project_id, earlier.position <= Episode.position)
            .correlate(Episode)
            .scalar_subquery()
        )
        for episode, count in session.execute(
            select(Episode, number).where(Episode.id.in_(episode_ids))
        ):
            episodes[episode.id], numbers[episode.id] = episode, count
    project_ids = {int(source["project_id"]) for source in sources if source.get("project_id")} | {
        episode.project_id for episode in episodes.values()
    }
    projects = (
        {row.id: row for row in session.scalars(select(Project).where(Project.id.in_(project_ids)))}
        if project_ids
        else {}
    )
    lookups = {
        "shots": shots,
        "assets": assets,
        "episodes": episodes,
        "episode_numbers": numbers,
        "projects": projects,
    }
    return [generation_display_context(session, request, lookups=lookups) for request in requests]
