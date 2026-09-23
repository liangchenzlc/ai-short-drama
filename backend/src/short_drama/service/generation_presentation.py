"""Human-readable, frozen task origin without request parameter dumps."""

from sqlalchemy import func, select

from short_drama.domain import Asset, Episode, Project, ShotScript


def generation_display_context(session, request):
    source = request.get("source") or {}
    scene = source.get("scene")
    snapshot = request.get("source_snapshot") or {}
    project_id, episode_id = source.get("project_id"), source.get("episode_id")
    subject = {
        "novel_script": "小说改编",
        "script_assets": "素材提取",
        "script_shots": "分镜脚本",
    }.get(scene, "通用生成")
    if scene == "shot_image":
        shot = session.get(ShotScript, int(source["shot_id"]))
        if shot is not None:
            episode_id = str(shot.episode_id)
            subject = f"分镜 {shot.position:02d} 生图"
        else:
            subject = f"分镜 {source['shot_id']} 生图"
    elif scene == "asset_image":
        asset = snapshot.get("asset") or {}
        if not asset:
            row = session.get(Asset, int(source["asset_id"]))
            if row is not None:
                asset = {"kind": row.kind, "name": row.name}
        label = {"character": "角色", "scene": "场景", "prop": "道具"}.get(
            asset.get("kind"), "素材"
        )
        subject = f"{label}：{asset.get('name') or source['asset_id']}"
    episode_label = None
    if episode_id:
        episode = session.get(Episode, int(episode_id))
        if episode is not None:
            project_id = str(episode.project_id)
            number = session.scalar(
                select(func.count())
                .select_from(Episode)
                .where(
                    Episode.project_id == episode.project_id, Episode.position <= episode.position
                )
            )
            episode_label = f"第 {number} 集：{episode.title}"
    project = session.get(Project, int(project_id)) if project_id else None
    return {
        "project": project.name if project else None,
        "episode": episode_label,
        "subject": subject,
        "scope": "素材库" if scene == "asset_image" else "通用任务",
    }
