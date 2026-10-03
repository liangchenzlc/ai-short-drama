"""Atomic admission and result collection for existing text/image/video tasks."""

from copy import deepcopy

from pydantic import ValidationError
from sqlalchemy import select

from short_drama.agent.authorization import check_source, digest
from short_drama.agent.runtime import lock_run, may_decide
from short_drama.agent.state import (
    TERMINAL,
    append_event,
    finish_locked,
    mark_scheduled,
    wait_locked,
)
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    Asset,
    Episode,
    EpisodeScript,
    MediaAsset,
    Project,
    ShotImage,
    User,
)
from short_drama.domain.agent import AgentArtifact, AgentToolCall
from short_drama.schemas.ai_generation import (
    ExtractionOptions,
    ImageInput,
    ImageParameters,
    StoryboardOptions,
    VideoParameters,
)
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.base import utcnow
from short_drama.service.generation_context_service import GenerationContextService
from short_drama.service.shot_video_context import DEFAULT_VIDEO_SETTINGS, video_context_hash
from short_drama.utils.snowflake import next_id

NATIVE_KINDS = {"extract", "storyboard", "image", "video"}


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
    episode = session.scalar(
        select(Episode)
        .where(Episode.id == int(source["episode_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if kind in {"extract", "storyboard"}:
        schema = ExtractionOptions if kind == "extract" else StoryboardOptions
        defaults = {"kinds": ["character", "scene", "prop"]} if kind == "extract" else {}
        step["parameters"] = _parameters(schema, {**defaults, **options})
        script = session.scalar(
            select(EpisodeScript)
            .where(EpisodeScript.id == episode.editing_script_id)
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
                "aspect": episode.aspect,
                "resolution": "2K",
                **{
                    k: v
                    for k, v in options.items()
                    if k in {"aspect", "resolution"} and v is not None
                },
            },
        )
        params.pop("count", None)
        step["parameters"] = {**options, **params, "aspect": params.get("aspect", episode.aspect)}
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
                "aspect": "inherit",
                "layout": "single",
            }
            defaults = {
                "resolution": settings["resolution"],
                "aspect": episode.aspect if settings["aspect"] == "inherit" else settings["aspect"],
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
                "aspect": params.get("aspect", episode.aspect),
                "reference_media_ids": [str(image.media_id)],
            }
    if set(options) - allowed:
        raise WorkflowError("invalid_agent_parameters", "计划包含不支持的媒体参数", 422)


def _payload(session, conversation, run, step, quantity):
    kind, source, options = step["kind"], step["source"], step["parameters"]
    scope = {"project_id": str(conversation.project_id), "episode_id": str(conversation.episode_id)}
    if kind in {"extract", "storyboard"}:
        payload = {
            "project_id": scope["project_id"],
            "config_id": str(run.model_config_id),
            "source": {
                **scope,
                "scene": "script_assets" if kind == "extract" else "script_shots",
                "script_id": source["script_id"],
                "content_version": str(source["content_version"]),
            },
            "instructions": step["instructions"],
        }
        if kind == "extract":
            payload["extraction"] = {"kinds": options.get("kinds", ["character", "scene", "prop"])}
        else:
            payload["storyboard"] = {
                "average_shot_duration_ms": options.get("average_shot_duration_ms", 3000)
            }
        return "text", payload, None
    parameters = {k: v for k, v in options.items() if k in {"aspect", "resolution", "duration_ms"}}
    payload = {"project_id": scope["project_id"], "config_id": step["model_config_id"]}
    if kind == "image":
        parameters["count"] = quantity
        if step["target_kind"] == "asset":
            payload.update(
                source={
                    **scope,
                    "scene": "asset_image",
                    "asset_id": step["target_id"],
                    "row_version": str(source["target_row_version"]),
                },
                parameters=parameters,
                input={
                    "prompt": step["instructions"],
                    "reference_media_ids": options.get("reference_media_ids", []),
                },
            )
            return kind, payload, None
        context = GenerationContextService(session)
        _, shot, _, _, current_hash = context.locked_shot_context(step["target_id"])
        if current_hash != source["shot_context_hash"]:
            raise Conflict("Image context changed after approval")
        settings = shot.image_settings or {
            "resolution": "2K",
            "aspect": "inherit",
            "layout": "single",
        }
        episode = session.get(Episode, conversation.episode_id)
        payload.update(
            source={
                "scene": "shot_image",
                "shot_id": step["target_id"],
                "layout": settings["layout"],
                "context_mode": "saved",
                "row_version": str(source["target_row_version"]),
                "context_hash": current_hash,
            },
            parameters={
                "aspect": episode.aspect if settings["aspect"] == "inherit" else settings["aspect"],
                "resolution": settings["resolution"],
                "count": quantity,
            },
            input={
                "prompt": step["instructions"],
                "reference_media_ids": options.get("reference_media_ids", []),
            },
        )
    else:
        payload.update(
            source={
                "scene": "shot_video",
                "shot_id": step["target_id"],
                "row_version": str(source["target_row_version"]),
                "context_hash": source["context_hash"],
                "reference_media_id": source["reference_media_id"],
            },
            parameters={},
        )

    def transform(prepared):
        # Approved overrides affect this candidate only; editor settings are untouched.
        prepared["parameters"] = {**prepared["parameters"], **parameters}
        if kind == "video":
            prepared["input"]["prompt"] += "\n创作要求：" + step["instructions"]
            prepared["business_intent"] = {"instructions": step["instructions"]}
        else:
            from short_drama.ai.business_prompts import image_prompt

            prepared["source"]["layout"] = options["layout"]
            prepared["input"]["prompt"] = image_prompt(
                prepared["source_snapshot"], step["instructions"], options["layout"]
            )
        return prepared

    return kind, payload, transform


def admit_native_locked(session, conversation, run, tool, step, *, settings):
    """Caller holds Project -> Conversation/Run -> Tool, and must retain this transaction."""
    project = session.scalar(
        select(Project)
        .where(Project.id == conversation.project_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not settings.agent_enabled
        or run.status in TERMINAL
        or run.cancel_requested
        or tool.run_id != run.id
        or tool.status not in {"prepared", "ready"}
        or project is None
        or not may_decide(session, project, conversation, run)
    ):
        raise WorkflowError("agent_authorization_ended", "本轮任务授权已结束", 409)
    check_source(session, step)
    checkpoint = deepcopy(run.checkpoint)
    authorization = checkpoint["authorization"]
    admitted = authorization.setdefault("admitted_quantities", {})
    remaining = step["count"] - admitted.get(step["id"], 0)
    if remaining <= 0 or step["id"] in authorization.get("consumed_steps", []):
        raise WorkflowError("agent_step_consumed", "此任务的批准数量已经用完", 409)
    quantity = min(remaining, 2 if step["kind"] == "image" else 1)
    if step["kind"] in {"image", "video"}:
        model = session.scalar(
            select(AIModelConfig)
            .where(AIModelConfig.id == int(step["model_config_id"]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            model is None
            or not model.enabled
            or model.is_deleted
            or model.owner_user_id != run.initiated_by
            or model.row_version != step["model_snapshot"]["row_version"]
        ):
            raise Conflict("Approved media model changed")
        meter = "images" if step["kind"] == "image" else "videos"
        usage = deepcopy(run.usage)
        if usage[meter] + quantity > run.budget[meter]:
            raise WorkflowError("agent_media_budget_exhausted", "已达到本轮批准的媒体数量", 409)
        usage[meter] += quantity
        run.usage = usage
    user = session.scalar(select(User).where(User.id == run.initiated_by).with_for_update())
    session.info["actor"] = ActorContext(
        user.id, user.username, user.email, True, 0, "", "agent-worker"
    )
    kind, payload, transform = _payload(session, conversation, run, step, quantity)
    summary, _created = AIGenerationService(session, settings).create_locked(
        kind,
        payload,
        f"agent:{tool.idempotency_key}",
        request_hash=digest(
            {
                "payload": payload,
                "instructions": step["instructions"],
                "quantity": quantity,
                "parameters": step["parameters"],
            }
        ),
        prepare_transform=transform,
    )
    task_id = int(summary["generation_id"])
    record = session.scalar(
        select(AIGenerationRecord)
        .where(AIGenerationRecord.task_id == task_id)
        .order_by(AIGenerationRecord.call_no)
        .limit(1)
    )
    record.config_snapshot = {**record.config_snapshot, "agent_managed": True}
    tool.generation_task_id, tool.status = task_id, "waiting_generation"
    tool.result = {
        "generation_id": str(task_id),
        "step_id": step["id"],
        "kind": step["kind"],
        "quantity": quantity,
        "settled": False,
    }
    admitted[step["id"]] = admitted.get(step["id"], 0) + quantity
    if admitted[step["id"]] == step["count"]:
        authorization.setdefault("consumed_steps", []).append(step["id"])
    run.checkpoint = checkpoint
    wait_locked(session, conversation, run, "waiting_generation", {"generation_id": str(task_id)})
    session.flush()
    return None


def collect_native_results(factory, settings, limit=100):
    """Waiting costs no worker slot. Accepted results may archive after cancellation."""
    with factory() as session:
        ids = session.scalars(
            select(AgentToolCall.id)
            .where(
                AgentToolCall.generation_task_id.is_not(None),
                AgentToolCall.status.in_(("waiting_generation", "cancelled")),
                AgentToolCall.result["settled"].as_boolean().is_(False),
            )
            .order_by(AgentToolCall.id)
            .limit(limit)
        ).all()
    collected = 0
    for identifier in ids:
        with factory.begin() as session:
            run_id = session.scalar(
                select(AgentToolCall.run_id).where(AgentToolCall.id == identifier)
            )
            rows = lock_run(session, run_id, skip_locked=True)
            if rows is None:
                continue
            project, conversation, run = rows
            tool = session.scalar(
                select(AgentToolCall)
                .where(AgentToolCall.id == identifier)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            result = deepcopy(tool.result or {})
            if result.get("settled") is not False:
                continue
            from short_drama.domain import AsyncTask

            task = session.scalar(
                select(AsyncTask).where(AsyncTask.id == tool.generation_task_id).with_for_update()
            )
            if task.status not in TERMINAL:
                continue
            record = session.scalar(
                select(AIGenerationRecord)
                .where(AIGenerationRecord.task_id == task.id)
                .order_by(AIGenerationRecord.call_no.desc())
                .limit(1)
                .with_for_update()
            )
            step = next(
                item
                for item in run.checkpoint["authorization"]["steps"]
                if item["id"] == result["step_id"]
            )
            artifacts = []
            media = session.scalars(
                select(MediaAsset)
                .where(MediaAsset.record_id == record.id)
                .order_by(MediaAsset.output_index)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            native_text_ready = task.status == "succeeded" and result["kind"] in {
                "extract",
                "storyboard",
            }
            entries = media if media else [None] if native_text_ready else []
            for index, item in enumerate(entries, 1):
                existing = session.scalar(
                    select(AgentArtifact)
                    .where(
                        AgentArtifact.tool_call_id == tool.id, AgentArtifact.result_index == index
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if existing:
                    artifacts.append(str(existing.id))
                    continue
                now = utcnow()
                kind = {
                    "extract": "extraction_candidate",
                    "storyboard": "storyboard_candidate",
                    "image": "image_candidate",
                    "video": "video_candidate",
                }[result["kind"]]
                from short_drama.agent.artifacts import source_snapshot

                source = source_snapshot(step, run, now)
                source["model_name"] = record.config_snapshot["name"]
                artifact = AgentArtifact(
                    id=next_id(),
                    project_id=conversation.project_id,
                    episode_id=conversation.episode_id,
                    tool_call_id=tool.id,
                    result_index=index,
                    kind=kind,
                    status="ready",
                    row_version=1,
                    source_snapshot=source,
                    source_content=None,
                    metadata_json={
                        "label": {
                            "extract": "资产提取候选",
                            "storyboard": "分镜候选",
                            "image": "图片候选",
                            "video": "视频候选",
                        }[result["kind"]],
                        "native_parameters": deepcopy(
                            record.request_data.get("resolved_parameters", {})
                        ),
                    },
                    generation_task_id=task.id,
                    media_asset_id=item.id if item else None,
                    media_id=item.media_id if item else None,
                    target_asset_id=int(step["target_id"])
                    if step["target_kind"] == "asset"
                    else None,
                    target_shot_id=int(step["target_id"])
                    if step["target_kind"] == "shot"
                    else None,
                    created_by=run.initiated_by,
                    created_at=now,
                    updated_at=now,
                )
                session.add(artifact)
                artifacts.append(str(artifact.id))
            active = (
                run.status not in TERMINAL
                and not run.cancel_requested
                and settings.agent_enabled
                and may_decide(session, project, conversation, run)
            )
            result.update(
                settled=True,
                status=task.status,
                artifact_ids=artifacts,
                error={"code": (task.error or {}).get("code", "generation_failed")}
                if task.status != "succeeded"
                else None,
            )
            tool.result, tool.updated_at = result, utcnow()
            if tool.status != "cancelled":
                tool.status = "succeeded" if task.status == "succeeded" else "failed"
            if active:
                append_event(
                    session,
                    conversation,
                    "artifacts.created",
                    {"run_id": str(run.id), "artifact_ids": artifacts},
                    run_id=run.id,
                )
                if artifacts and run.checkpoint["authorization"].get("approved_plan"):
                    checkpoint = deepcopy(run.checkpoint)
                    checkpoint.update(awaiting_artifacts=artifacts, awaiting_step_id=step["id"])
                    run.checkpoint = checkpoint
                    wait_locked(
                        session, conversation, run, "waiting_review", {"artifact_ids": artifacts}
                    )
                else:
                    mark_scheduled(run, phase="tools")
            elif run.status not in TERMINAL:
                finish_locked(session, conversation, run, "cancelled", {"code": "access_revoked"})
            session.flush()
            collected += 1
    return collected
