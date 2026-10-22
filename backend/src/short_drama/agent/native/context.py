"""Freeze reviewed task inputs independently of generation admission and collection."""

from copy import deepcopy

from pydantic import ValidationError
from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import (
    Asset,
    Project,
    ProjectScript,
    ShotImage,
)
from short_drama.schemas.ai_generation import (
    ExtractionOptions,
    ImageInput,
    ImageParameters,
    StoryboardOptions,
    VideoParameters,
)
from short_drama.service.generation_context_service import GenerationContextService
from short_drama.service.shot_video_context import DEFAULT_VIDEO_SETTINGS, video_context_hash
from short_drama.utils.json_digest import digest


def _parameters(schema, values):
    try:
        return schema.model_validate(values).model_dump(exclude_none=True)
    except ValidationError:
        raise WorkflowError("invalid_agent_parameters", "计划包含不支持的生成参数", 422) from None


def _image_references(step, inherited):
    extras = step["parameters"].get("reference_media_ids", [])
    if not isinstance(extras, list):
        raise WorkflowError("invalid_agent_parameters", "参考图参数必须为图片列表", 422)
    references = _parameters(
        ImageInput,
        {
            "prompt": step["instructions"],
            "reference_media_ids": list(dict.fromkeys(map(str, [*inherited, *extras]))),
        },
    )["reference_media_ids"]
    step["parameters"]["reference_media_ids"] = list(map(str, references))


def freeze_native_context(session, step):
    """No I/O or writes: the same object/hash/parameters are shown in plan review."""
    kind = step["kind"]
    source, options = step["source"], deepcopy(step["parameters"])
    creation = session.scalar(
        select(Project)
        .where(Project.id == int(source["project_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if kind in {"extract", "storyboard"}:
        schema = ExtractionOptions if kind == "extract" else StoryboardOptions
        defaults = {"kinds": ["character", "scene", "prop"]} if kind == "extract" else {}
        step["parameters"] = _parameters(schema, {**defaults, **options})
        script = session.scalar(
            select(ProjectScript)
            .where(ProjectScript.id == creation.editing_script_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if script is None or script.state != "confirmed" or not script.content.strip():
            raise WorkflowError("script_not_confirmed", "提取与分镜需要当前已确认的剧本", 422)
        source["script_id"] = str(script.id)
        source["script_hash"] = digest(script.content)
        return
    if len(step["instructions"]) > 4000:
        raise WorkflowError("agent_media_instructions_too_long", "媒体生成要求最多4000字", 422)
    if kind == "image" and step["target_kind"] == "asset":
        allowed = {"target_kind", "aspect", "resolution", "reference_media_ids"}
        params = _parameters(
            ImageParameters,
            {
                "aspect": None,
                "resolution": "2K",
                **{
                    k: v
                    for k, v in options.items()
                    if k in {"aspect", "resolution"} and v is not None
                },
            },
        )
        params.pop("count", None)
        step["parameters"] = {**options, **params, "aspect": params.get("aspect")}
        asset = session.scalar(
            select(Asset)
            .where(Asset.id == int(step["target_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        _image_references(step, asset.reference_media_ids or [])
    else:
        context = GenerationContextService(session)
        _, shot, assets, _, image_hash = context.locked_shot_context(step["target_id"])
        source["shot_context_hash"] = image_hash
        if kind == "image":
            allowed = {"target_kind", "aspect", "resolution", "layout", "reference_media_ids"}
            settings = shot.image_settings or {
                "resolution": "2K",
                "aspect": None,
                "layout": "single",
            }
            defaults = {
                "resolution": settings["resolution"],
                "aspect": settings["aspect"],
            }
            params = _parameters(
                ImageParameters,
                {
                    **defaults,
                    **{k: v for k, v in options.items() if k in defaults and v is not None},
                },
            )
            params.pop("count", None)
            layout = settings["layout"] if options.get("layout") is None else options["layout"]
            if layout not in {"single", "four", "five", "nine"}:
                raise WorkflowError("invalid_agent_layout", "不支持的图片布局", 422)
            step["parameters"] = {**options, **params, "layout": layout}
            _image_references(
                step,
                [
                    *(a.media_id for a in assets if a.media_id and a.state == "confirmed"),
                    *(shot.reference_media_ids or []),
                ],
            )
        else:
            allowed = {"target_kind", "aspect", "resolution", "duration_ms"}
            image = session.scalar(
                select(ShotImage).where(ShotImage.shot_id == shot.id).with_for_update()
            )
            if image is None or image.context_hash != image_hash:
                raise WorkflowError(
                    "video_reference_required", "请先核对并采用当前镜头的参考图", 422
                )
            settings = shot.video_settings or DEFAULT_VIDEO_SETTINGS
            params = _parameters(
                VideoParameters,
                {
                    "resolution": settings["resolution"],
                    "duration_ms": settings.get("duration_ms") or shot.duration_ms,
                    **{
                        k: v
                        for k, v in options.items()
                        if k in {"aspect", "resolution", "duration_ms"} and v is not None
                    },
                },
            )
            source.update(
                reference_media_id=str(image.media_id),
                context_hash=video_context_hash(
                    image_hash,
                    image.media_id,
                    shot.video_prompt,
                    settings,
                    session=session,
                    shot=shot,
                ),
            )
            step["parameters"] = {
                **options,
                **params,
                "aspect": params.get("aspect"),
                "reference_media_ids": [str(image.media_id)],
            }
    if set(options) - allowed:
        raise WorkflowError("invalid_agent_parameters", "计划包含不支持的媒体参数", 422)
