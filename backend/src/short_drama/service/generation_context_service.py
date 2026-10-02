"""Build immutable model input from validated, saved business objects."""

import hashlib

from sqlalchemy import select

from short_drama.ai.business_prompts import asset_image_prompt, image_prompt, text_messages
from short_drama.ai.prompts.registry import PROMPT_VERSIONS
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
        request["template_version"] = PROMPT_VERSIONS[scene]
        request["input"] = {"messages": text_messages(scene, snapshot, instructions)}
        return request

    def prepare_asset_image(self, request):
        from .asset_image_context import asset_image_content, asset_image_content_hash

        source = request["source"]
        asset = self.session.scalar(
            select(Asset)
            .where(Asset.id == int(source["asset_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if asset is None:
            raise NotFound("Asset does not exist")
        if asset.row_version != int(source["row_version"]):
            raise WorkflowError(
                "asset_version_conflict", "Asset changed; refresh before generating"
            )
        if source.get("episode_id"):
            episode = self.session.get(Episode, int(source["episode_id"]))
            linked = self.session.scalar(
                select(EpisodeAsset.id).where(
                    EpisodeAsset.episode_id == int(source["episode_id"]),
                    EpisodeAsset.asset_id == asset.id,
                )
            )
            if (
                episode is None
                or str(episode.project_id) != str(source.get("project_id"))
                or not linked
            ):
                raise WorkflowError("source_mismatch", "素材不属于所选分集", 422)
        elif source.get("project_id"):
            from short_drama.domain import ProjectAsset

            if not self.session.scalar(
                select(ProjectAsset.id).where(
                    ProjectAsset.project_id == int(source["project_id"]),
                    ProjectAsset.asset_id == asset.id,
                )
            ):
                raise WorkflowError("source_mismatch", "素材不属于所选项目", 422)
        content = asset_image_content(asset)
        if not content["name"].strip() or not (
            content["description"].strip() or content["prompt"].strip()
        ):
            raise WorkflowError(
                "asset_content_required",
                "Provide a name and either description or prompt before generating",
                400,
            )
        supplement = request["input"].get("prompt", "")
        request["source_snapshot"] = {
            "asset": content,
            "asset_row_version": str(asset.row_version),
            "asset_content_hash": asset_image_content_hash(content),
            "template_version": PROMPT_VERSIONS["asset_image"],
            "instructions": supplement,
        }
        request["input"] = {
            "prompt": asset_image_prompt(content, supplement),
            "reference_media_ids": list(
                dict.fromkeys(
                    map(
                        str,
                        [
                            *(getattr(asset, "reference_media_ids", None) or []),
                            *request["input"].get("reference_media_ids", []),
                        ],
                    )
                )
            ),
        }
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
            "reference_media_ids": getattr(shot, "reference_media_ids", None) or [],
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
        references.extend(map(str, getattr(shot, "reference_media_ids", None) or []))
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
        request["template_version"] = PROMPT_VERSIONS["shot_image"]
        request["input"] = {
            "prompt": image_prompt(snapshot, supplement, source["layout"]),
            "reference_media_ids": references,
        }
        return request

    def prepare_shot_video(self, request):
        from short_drama.ai.business_prompts import video_prompt_parts
        from short_drama.domain import ShotImage

        from .shot_video_context import DEFAULT_VIDEO_SETTINGS, video_context_hash

        source = request["source"]
        episode, shot, _assets, snapshot, image_hash = self.locked_shot_context(source["shot_id"])
        image = self.session.scalar(
            select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update()
        )
        if image is None:
            raise WorkflowError("video_reference_required", "请先采用一张分镜参考图", 422)
        if image.context_hash != image_hash:
            raise WorkflowError(
                "video_reference_stale", "分镜图已过期，请先核对并采用当前内容的图片"
            )
        settings = shot.video_settings or DEFAULT_VIDEO_SETTINGS
        digest = video_context_hash(
            image_hash, image.media_id, shot.video_prompt, settings, session=self.session, shot=shot
        )
        if (
            shot.row_version != int(source["row_version"])
            or source["context_hash"] != digest
            or int(source["reference_media_id"]) != image.media_id
        ):
            raise WorkflowError("shot_version_conflict", "分镜、提示词或参考图已变化，请刷新后生成")
        if not shot.script.strip():
            raise WorkflowError("shot_empty", "请先保存分镜正文", 422)
        duration = settings.get("duration_ms") or shot.duration_ms
        expected = {"duration_ms": duration, "resolution": settings["resolution"]}
        supplied = {k: v for k, v in request["parameters"].items() if v is not None}
        if supplied and supplied != expected:
            raise WorkflowError("generation_settings_changed", "请先保存视频时长和清晰度", 422)
        snapshot = {**snapshot, "video_duration_ms": duration}
        parts = video_prompt_parts(snapshot, shot.video_prompt)
        source.update(
            project_id=str(episode.project_id), episode_id=str(episode.id), source_id=str(shot.id)
        )
        request["source_snapshot"] = {
            **snapshot,
            "context_hash": digest,
            "shot_context_hash": image_hash,
            "reference_media_id": str(image.media_id),
            "reference_layout": image.layout,
            "input_mode": "omni_reference",
            "row_version": str(shot.row_version),
            "video_prompt": shot.video_prompt,
            "video_settings": settings,
            "system_prompt": parts["system"],
            "user_prompt": parts["user"],
        }
        request["template_version"] = PROMPT_VERSIONS["shot_video"]
        request["parameters"] = {**expected, "aspect": episode.aspect}
        request["input"] = {"prompt": parts["prompt"], "reference_media_ids": [str(image.media_id)]}
        from .native_voice_service import native_context, native_prompt

        native = native_context(self.session, shot, strict=True, settings=self.settings)
        if native is not None:
            request["source_snapshot"]["native_speech"] = native
            request["input"]["audio_reference_media_ids"] = [
                v["media_id"] for v in native["voices"]
            ]
            request["input"]["prompt"] += native_prompt(native)
            request["parameters"]["generate_audio"] = True
        return request
