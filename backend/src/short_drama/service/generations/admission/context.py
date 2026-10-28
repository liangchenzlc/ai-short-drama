"""Build immutable model input from validated, saved business objects."""

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.ai.business_prompts import text_messages
from short_drama.ai.prompts.registry import PROMPT_VERSIONS
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Asset, MediaAsset, Project, ProjectScript, ShotAsset, ShotScript
from short_drama.service.creation.writing.script.documents import ProjectWritingService

CURRENT_SAVED_SCRIPT_SOURCE_POLICY = "current_saved_v1"


def content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def script_source_is_current(
    session: Session,
    creation: Project,
    script: ProjectScript | None,
    snapshot: dict[str, object],
) -> bool:
    """新标准任务按正文判断来源；无策略标记的历史任务保留确认合同。"""
    if (
        script is None
        or script.id != creation.editing_script_id
        or content_hash(script.content) != snapshot.get("content_hash")
    ):
        return False
    if snapshot.get("script_source_policy") == CURRENT_SAVED_SCRIPT_SOURCE_POLICY:
        return not ProjectWritingService(session)._requires_agent_adoption(script.id)
    return script.state == "confirmed"


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

    def prepare_text(self, request, *, use_current_saved_script=False):
        from short_drama.service.creation.writing.rewrite.generation import (
            WRITING_SCENES,
            WritingGenerationService,
        )

        if request["source"]["scene"] in WRITING_SCENES:
            return WritingGenerationService(self.session).prepare(request)
        source = request["source"]
        creation = self.session.scalar(
            select(Project)
            .where(
                Project.id == int(source["project_id"]),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if creation is None:
            raise NotFound("作品不存在")
        if creation.content_version != int(source["content_version"]):
            raise WorkflowError("writing_version_conflict", "正文已变化，请刷新后生成")
        scene = source["scene"]
        if scene in {"script_shots", "script_assets"}:
            ProjectWritingService(self.session).dao.require_idle(creation.id, "script")
            document = self.session.scalar(
                select(ProjectScript)
                .where(
                    ProjectScript.id == int(source["script_id"]),
                    ProjectScript.project_id == creation.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if document is None:
                raise NotFound("剧本不存在于当前作品")
            if use_current_saved_script:
                if document.id != creation.editing_script_id:
                    raise WorkflowError("script_not_current", "请基于当前编辑剧本生成")
                if ProjectWritingService(self.session)._requires_agent_adoption(document.id):
                    raise WorkflowError(
                        "agent_artifact_adoption_required",
                        "此剧本是尚未采用的 Agent 候选，请在原会话的创作候选中核对并采用。",
                        422,
                    )
            elif document.id != creation.editing_script_id or document.state != "confirmed":
                raise WorkflowError("script_not_confirmed", "请先确认当前剧本")
            if not document.content.strip():
                message = "请先保存剧本正文" if use_current_saved_script else "请先填写并确认剧本"
                raise WorkflowError("script_empty", message, 422)
        else:
            raise WorkflowError("invalid_source", "不支持的文本业务场景", 422)
        snapshot = {
            "content": document.content,
            "content_hash": content_hash(document.content),
            "content_version": str(creation.content_version),
        }
        if use_current_saved_script and scene in {"script_shots", "script_assets"}:
            snapshot["script_source_policy"] = CURRENT_SAVED_SCRIPT_SOURCE_POLICY
        if scene == "script_shots":
            assets = self.session.scalars(
                select(Asset)
                .where(Asset.project_id == creation.id, Asset.deleted_at.is_(None))
                .order_by(Asset.id)
                .with_for_update()
            )
            snapshot["assets"] = [asset_snapshot(asset) for asset in assets]
            snapshot["storyboard"] = request["storyboard"]
        if scene == "script_assets":
            max_chars = getattr(self.settings, "extraction_max_script_chars", 30000)
            if len(document.content) > max_chars:
                raise WorkflowError(
                    "script_too_long", f"素材提取支持最多 {max_chars} 字，请精简剧本后重试", 422
                )
            snapshot["extraction"] = request["extraction"]
            snapshot["max_candidates"] = getattr(self.settings, "extraction_max_candidates", 100)
            token_limit = getattr(self.settings, "extraction_max_output_tokens", 8192)
            request["parameters"]["max_output_tokens"] = min(
                request["parameters"].get("max_output_tokens") or token_limit, token_limit
            )
        source["source_id"] = str(document.id)
        source["script_id"] = str(document.id)
        instructions = request.pop("instructions", "")
        request["source_snapshot"] = snapshot
        if request.get("creation_draft_snapshot"):
            snapshot["creation_draft"] = request["creation_draft_snapshot"]
        request["business_intent"] = {"instructions": instructions}
        request["template_version"] = PROMPT_VERSIONS[scene]
        request["input"] = {"messages": text_messages(scene, snapshot, instructions)}
        return request

    def prepare_asset_image(self, request):
        from short_drama.service.creation.assets.images.image_context import (
            asset_image_content,
            asset_image_content_hash,
        )

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
        if str(asset.project_id) != str(source.get("project_id")) or asset.deleted_at is not None:
            raise WorkflowError("source_mismatch", "素材不属于当前作品", 422)
        content = asset_image_content(asset)
        if not content["name"].strip():
            raise WorkflowError(
                "asset_content_required",
                "Provide a name before generating",
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
        if request.get("creation_draft_snapshot"):
            request["source_snapshot"]["creation_draft"] = request["creation_draft_snapshot"]
        request["input"] = {
            "prompt": supplement,
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
        from short_drama.service.creation.storyboard.management.context import (
            compute_shot_context_hash,
            normalize_shot_context,
        )

        project_id = self.session.scalar(
            select(ShotScript.project_id).where(ShotScript.id == int(shot_id))
        )
        if project_id is None:
            raise NotFound("分镜不存在")
        creation = self.session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        shot = self.session.scalar(
            select(ShotScript)
            .where(ShotScript.id == int(shot_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if creation is None or shot is None:
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
            "assets": assets,
        }
        return (
            creation,
            shot,
            assets,
            normalize_shot_context(**values),
            compute_shot_context_hash(**values),
        )

    def prepare_shot_image(self, request, *, include_saved_references=True):
        source = request["source"]
        creation, shot, assets, snapshot, digest = self.locked_shot_context(source["shot_id"])
        if shot.row_version != int(source["row_version"]) or source["context_hash"] != digest:
            raise WorkflowError("shot_version_conflict", "分镜内容已变化，请刷新后生成")
        settings = shot.image_settings or {
            "resolution": "2K",
            "aspect": None,
            "layout": "single",
        }
        aspect = settings["aspect"]
        private_draft = request.get("creation_draft_snapshot")
        if source["layout"] != settings["layout"] or (
            not private_draft
            and (
                request["parameters"].get("aspect") != aspect
                or request["parameters"].get("resolution") != settings["resolution"]
            )
        ):
            raise WorkflowError("generation_settings_changed", "请先保存图片设置")
        if private_draft:
            personal = private_draft.get("image_settings") or {}
            request["parameters"].update(
                aspect=personal.get("aspect", aspect),
                resolution=personal.get("resolution") or settings["resolution"],
                count=personal.get("count", 1),
            )
        references = []
        if include_saved_references:
            references = [str(a.media_id) for a in assets if a.media_id and a.state == "confirmed"]
            references.extend(map(str, getattr(shot, "reference_media_ids", None) or []))
        extra = request["input"].get("reference_media_ids", [])
        for identifier in [] if private_draft or not include_saved_references else extra:
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
        source.update(project_id=str(creation.id), source_id=str(shot.id))
        request["source_snapshot"] = {
            **snapshot,
            "context_hash": digest,
            "row_version": str(shot.row_version),
        }
        if private_draft:
            request["source_snapshot"]["creation_draft"] = private_draft
        request["business_intent"] = {"instructions": supplement}
        request["template_version"] = PROMPT_VERSIONS["shot_image"]
        request["input"] = {
            "prompt": supplement,
            "reference_media_ids": references,
        }
        return request

    def prepare_shot_video(self, request):
        from short_drama.ai.business_prompts import video_prompt_parts
        from short_drama.domain import ShotImage
        from short_drama.service.creation.storyboard.videos.video_context import (
            DEFAULT_VIDEO_SETTINGS,
            video_context_hash,
        )

        source = request["source"]
        creation, shot, _assets, snapshot, image_hash = self.locked_shot_context(source["shot_id"])
        image = self.session.scalar(
            select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update()
        )
        if image is None:
            raise WorkflowError("video_reference_required", "请先采用一张分镜参考图", 422)
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
        duration = settings.get("duration_ms") or shot.duration_ms
        expected = {"duration_ms": duration, "resolution": settings["resolution"]}
        supplied = {
            k: v for k, v in request["parameters"].items() if k in expected and v is not None
        }
        private_draft = request.get("creation_draft_snapshot")
        if not private_draft and supplied and supplied != expected:
            raise WorkflowError("generation_settings_changed", "请先保存视频时长和清晰度", 422)
        prompt = shot.video_prompt
        if private_draft:
            personal = private_draft.get("video_settings") or {}
            duration = personal.get("duration_ms") or duration
            expected = {
                "duration_ms": duration,
                "resolution": personal.get("resolution") or settings["resolution"],
            }
            prompt = private_draft["input_text"]
        snapshot = {**snapshot, "video_duration_ms": duration}
        effective_aspect = request["parameters"].get("aspect")
        if private_draft:
            effective_aspect = (private_draft.get("video_settings") or {}).get(
                "aspect", effective_aspect
            )
            actual_prompt = (
                (
                    "已保存分镜脚本（创作依据）：\n"
                    + snapshot["shot"]["script"]
                    + "\n本次视频提示词：\n"
                    + prompt
                )
                if prompt.strip()
                else prompt
            )
            parts = video_prompt_parts(
                {**snapshot, "output": {"aspect": effective_aspect}},
                actual_prompt,
            )
        else:
            parts = video_prompt_parts(snapshot, prompt)
        source.update(project_id=str(creation.id), source_id=str(shot.id))
        request["source_snapshot"] = {
            **snapshot,
            "context_hash": digest,
            "shot_context_hash": image_hash,
            "reference_media_id": str(image.media_id),
            "reference_layout": image.layout,
            "input_mode": "omni_reference",
            "row_version": str(shot.row_version),
            "video_prompt": prompt,
            "video_settings": expected if private_draft else settings,
            "system_prompt": parts["system"],
            "user_prompt": parts["user"],
        }
        if private_draft:
            request["source_snapshot"]["creation_draft"] = private_draft
        request["template_version"] = PROMPT_VERSIONS["shot_video"]
        request["parameters"] = {**expected, "aspect": effective_aspect}
        request["input"] = {"prompt": parts["prompt"], "reference_media_ids": [str(image.media_id)]}
        from short_drama.service.creation.sound.voices.context import native_context, native_prompt

        native = native_context(self.session, shot, strict=True, settings=self.settings)
        if native is not None:
            request["source_snapshot"]["native_speech"] = native
            request["input"]["audio_reference_media_ids"] = [
                v["media_id"] for v in native["voices"]
            ]
            request["input"]["prompt"] += native_prompt(native)
            request["parameters"]["generate_audio"] = True
        return request
