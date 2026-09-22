"""Build immutable model input from validated, saved business objects."""

import hashlib

from sqlalchemy import select

from short_drama.ai.business_prompts import image_prompt, text_messages
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    Asset,
    Episode,
    EpisodeAsset,
    EpisodeNovel,
    EpisodeScript,
    MediaAsset,
    ShotAsset,
    ShotScript,
)


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def asset_snapshot(asset):
    return {
        "id": str(asset.id),
        "kind": asset.kind,
        "name": asset.name,
        "description": asset.description,
        "prompt": asset.prompt,
        "label": asset.label,
        "tags": getattr(asset, "tags", []) or [],
        "scene_time": getattr(asset, "scene_time", "") or "",
        "state": getattr(asset, "state", "unconfirmed"),
        "media_id": str(asset.media_id) if asset.media_id else None,
    }


class GenerationContextService:
    def __init__(self, session, settings=None):
        self.session = session
        self.settings = settings

    def prepare_text(self, request):
        source = request["source"]
        episode = self.session.scalar(
            select(Episode)
            .where(
                Episode.id == int(source["episode_id"]),
                Episode.project_id == int(source["project_id"]),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if episode is None:
            raise NotFound("分集不存在")
        if episode.content_version != int(source["content_version"]):
            raise WorkflowError("writing_version_conflict", "正文已变化，请刷新后生成")
        scene = source["scene"]
        if scene == "novel_script":
            document = self.session.scalar(
                select(EpisodeNovel).where(EpisodeNovel.episode_id == episode.id).with_for_update()
            )
            if document is None or not document.content.strip():
                raise WorkflowError("novel_empty", "请先保存小说正文", 422)
        elif scene in {"script_shots", "script_assets"}:
            document = self.session.scalar(
                select(EpisodeScript)
                .where(
                    EpisodeScript.id == int(source["script_id"]),
                    EpisodeScript.episode_id == episode.id,
                )
                .with_for_update()
            )
            if document is None:
                raise NotFound("剧本不存在于当前分集")
            if document.id != episode.editing_script_id or document.state != "confirmed":
                raise WorkflowError("script_not_confirmed", "请先确认当前剧本")
            if not document.content.strip():
                raise WorkflowError("script_empty", "请先填写并确认剧本", 422)
        else:
            raise WorkflowError("invalid_source", "不支持的文本业务场景", 422)
        snapshot = {
            "content": document.content,
            "content_hash": content_hash(document.content),
            "content_version": str(episode.content_version),
            "aspect": episode.aspect,
            "style": episode.style,
        }
        if scene == "script_shots":
            assets = self.session.scalars(
                select(Asset)
                .join(EpisodeAsset, EpisodeAsset.asset_id == Asset.id)
                .where(EpisodeAsset.episode_id == episode.id)
                .order_by(Asset.id)
                .with_for_update()
            )
            snapshot["assets"] = [asset_snapshot(asset) for asset in assets]
            snapshot["storyboard"] = request["storyboard"]
        if scene == "script_assets":
            max_chars = getattr(self.settings, "extraction_max_script_chars", 30000)
            if len(document.content) > max_chars:
                raise WorkflowError(
                    "script_too_long", f"素材提取支持最多 {max_chars} 字，请拆分分集后重试", 422
                )
            snapshot["extraction"] = request["extraction"]
            snapshot["max_candidates"] = getattr(self.settings, "extraction_max_candidates", 100)
            token_limit = getattr(self.settings, "extraction_max_output_tokens", 8192)
            request["parameters"]["max_output_tokens"] = min(
                request["parameters"].get("max_output_tokens") or token_limit, token_limit
            )
        source["source_id"] = str(document.id)
        source["novel_id" if scene == "novel_script" else "script_id"] = str(document.id)
        instructions = request.pop("instructions", "")
        request["source_snapshot"] = snapshot
        request["business_intent"] = {"instructions": instructions}
        request["template_version"] = {
            "novel_script": "novel-script-v1",
            "script_shots": "script-shots-v1-r2",
            "script_assets": "script-assets-v1-r2",
        }[scene]
        request["input"] = {"messages": text_messages(scene, snapshot, instructions)}
        return request

    def locked_shot_context(self, shot_id):
        from .shot_context import compute_shot_context_hash, normalize_shot_context

        episode_id = self.session.scalar(
            select(ShotScript.episode_id).where(ShotScript.id == int(shot_id))
        )
        if episode_id is None:
            raise NotFound("分镜不存在")
        episode = self.session.scalar(
            select(Episode)
            .where(Episode.id == episode_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        shot = self.session.scalar(
            select(ShotScript)
            .where(ShotScript.id == int(shot_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if episode is None or shot is None:
            raise NotFound("分镜不存在")
        if shot.deleted_at is not None:
            raise WorkflowError("shot_archived", "此分镜已归档")
        assets = list(
            self.session.scalars(
                select(Asset)
                .join(ShotAsset, ShotAsset.asset_id == Asset.id)
                .where(ShotAsset.shot_id == shot.id)
                .order_by(Asset.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        values = {
            "shot_id": shot.id,
            "script": shot.script,
            "duration_ms": shot.duration_ms,
            "episode_aspect": episode.aspect,
            "episode_style": episode.style,
            "assets": assets,
        }
        return (
            episode,
            shot,
            assets,
            normalize_shot_context(**values),
            compute_shot_context_hash(**values),
        )

    def prepare_shot_image(self, request):
        source = request["source"]
        episode, shot, assets, snapshot, digest = self.locked_shot_context(source["shot_id"])
        if shot.row_version != int(source["row_version"]) or source["context_hash"] != digest:
            raise WorkflowError("shot_version_conflict", "分镜内容已变化，请刷新后生成")
        if not shot.script.strip():
            raise WorkflowError("shot_empty", "请先保存分镜正文", 422)
        settings = shot.image_settings or {
            "resolution": "2K",
            "aspect": "inherit",
            "layout": "single",
        }
        aspect = episode.aspect if settings["aspect"] == "inherit" else settings["aspect"]
        if (
            source["layout"] != settings["layout"]
            or request["parameters"].get("aspect") != aspect
            or request["parameters"].get("resolution") != settings["resolution"]
        ):
            raise WorkflowError("generation_settings_changed", "请先保存图片设置")
        references = [str(a.media_id) for a in assets if a.media_id and a.state == "confirmed"]
        extra = request["input"].get("reference_media_ids", [])
        for identifier in extra:
            if str(identifier) not in references and not self.session.scalar(
                select(MediaAsset.id).where(
                    MediaAsset.media_id == int(identifier), MediaAsset.media_type == "image"
                )
            ):
                raise WorkflowError(
                    "invalid_reference_media", "额外参考图必须来自生成资产库或当前已确认素材", 422
                )
        references = list(dict.fromkeys([*references, *map(str, extra)]))
        if len(references) > 16:
            raise WorkflowError("reference_limit_exceeded", "参考图片不能超过16张", 422)
        supplement = request["input"].get("prompt", "")
        source.update(
            project_id=str(episode.project_id), episode_id=str(episode.id), source_id=str(shot.id)
        )
        request["source_snapshot"] = {
            **snapshot,
            "context_hash": digest,
            "row_version": str(shot.row_version),
        }
        request["business_intent"] = {"instructions": supplement}
        request["template_version"] = "shot-image-v1"
        request["input"] = {
            "prompt": image_prompt(snapshot, supplement, source["layout"]),
            "reference_media_ids": references,
        }
        return request
