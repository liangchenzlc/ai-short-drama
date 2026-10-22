"""System-side check before a new paid submission. Poll/save preserve accepted work."""

from sqlalchemy import select

from short_drama.domain import Project
from short_drama.domain.collaboration import User


def lock_resource_project(session, model, identifier):
    """Project precedes task/job locks, matching revocation and API transactions."""
    project_id = session.scalar(select(model.project_id).where(model.id == identifier))
    if project_id:
        session.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if model.__tablename__ == "async_tasks":
        from short_drama.domain import AIGenerationRecord

        snapshot = (
            session.scalar(
                select(AIGenerationRecord.config_snapshot)
                .where(AIGenerationRecord.task_id == identifier)
                .order_by(AIGenerationRecord.call_no)
                .limit(1)
            )
            or {}
        )
        if snapshot.get("agent_managed") is True:
            from short_drama.domain.agent_media import AgentMediaRequest
            from short_drama.domain.agent_task import AgentOperation

            operation_id = session.scalar(
                select(AgentMediaRequest.operation_id).where(
                    AgentMediaRequest.generation_task_id == identifier,
                )
            )
            if operation_id is not None:
                from short_drama.agent.core.access import lock_run

                run_id = session.scalar(
                    select(AgentOperation.run_id).where(
                        AgentOperation.id == operation_id,
                    )
                )
                rows = lock_run(session, run_id)
                operation = session.scalar(
                    select(AgentOperation)
                    .where(
                        AgentOperation.id == operation_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                intent = session.scalar(
                    select(AgentMediaRequest)
                    .where(
                        AgentMediaRequest.generation_task_id == identifier,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                session.info.setdefault("assistant_media_parents", {})[identifier] = (
                    rows,
                    operation,
                    intent,
                )
                return
            # Private linkage is never copied into project-shared generation records.
            from short_drama.agent.core.access import lock_run
            from short_drama.domain.agent import AgentToolCall

            run_id = session.scalar(
                select(AgentToolCall.run_id).where(AgentToolCall.generation_task_id == identifier)
            )
            rows = lock_run(session, run_id) if run_id is not None else None
            if rows is not None:
                session.scalar(
                    select(AgentToolCall)
                    .where(AgentToolCall.generation_task_id == identifier)
                    .with_for_update()
                )
            session.info.setdefault("agent_task_parents", {})[identifier] = rows


def may_submit(session, task, *, agent_enabled=None, settings=None):
    media_parents = session.info.get("assistant_media_parents", {})
    media_owner = None
    if getattr(task, "id", None) in media_parents:
        from short_drama.agent.core.events import TERMINAL
        from short_drama.service.assistant_policy_service import policy_state_locked

        rows, operation, intent = media_parents[task.id]
        if rows is None or operation is None or intent is None or agent_enabled is False:
            return False
        _project, conversation, run = rows
        if (
            run.status in TERMINAL
            or run.cancel_requested
            or conversation.status not in {"active", "deleting"}
            or run.initiated_by != conversation.owner_user_id
            or intent.task_id != run.task_id
            or operation.task_id != run.task_id
            or operation.run_id != run.id
            or intent.operation_id != operation.id
            or intent.generation_task_id != task.id
            or intent.media_kind != task.service_type
            or intent.reservation_state != "reserved"
            or intent.status != "accepted"
            or operation.tool_name != "submit_generation_batch"
            or operation.status not in {"waiting_media", "reconciling"}
            or str(task.project_id) != intent.frozen_target.get("project_id")
            or task.initiated_by != conversation.owner_user_id
        ):
            return False
        media_owner = conversation.owner_user_id
        if intent.frozen_target.get("kind") == "node":
            from .assistant_canvas_media_service import canvas_media_may_submit

            if not canvas_media_may_submit(session, media_owner, intent):
                session.info.setdefault("assistant_submission_errors", {})[task.id] = {
                    "code": "assistant_canvas_source_changed",
                    "message": "画布或节点已变化，原请求尚未外发",
                }
                return False
        elif intent.frozen_target.get("media_role") == "character_voice":
            from .assistant_voice_service import voice_design_may_submit

            if not voice_design_may_submit(session, media_owner, intent, settings=settings):
                session.info.setdefault("assistant_submission_errors", {})[task.id] = {
                    "code": "assistant_voice_source_changed",
                    "message": "角色、音色或声音功能配置已变化，原请求尚未外发",
                }
                return False
        elif intent.frozen_target.get("media_role") == "dialogue_audio":
            from .assistant_dialogue_service import dialogue_audio_may_submit

            if not dialogue_audio_may_submit(session, media_owner, intent, settings=settings):
                session.info.setdefault("assistant_submission_errors", {})[task.id] = {
                    "code": "assistant_dialogue_source_changed",
                    "message": "台词、声音文档、时间线或配音功能已变化，原请求尚未外发",
                }
                return False
        elif intent.media_kind == "video":
            from .shot_video_admission import shot_video_may_submit

            if not shot_video_may_submit(session, media_owner, intent, settings=settings):
                session.info.setdefault("assistant_submission_errors", {})[task.id] = {
                    "code": "assistant_shot_video_source_changed",
                    "message": "分镜、参考图、对白、角色音色或当前视频已变化，原请求尚未外发",
                }
                return False
        reference_results = intent.frozen_target.get("reference_results", [])
        if reference_results:
            from .assistant_media_reference_service import generated_image_references

            current = generated_image_references(
                session,
                media_owner,
                intent.task_id,
                {int(source["media_id"]) for source in reference_results},
                lock=True,
            )
            if any(current.get(int(source["media_id"])) != source for source in reference_results):
                session.info.setdefault("assistant_submission_errors", {})[task.id] = {
                    "code": "assistant_generation_reference_changed",
                    "message": "本任务生成参考图的来源已失效，原请求尚未外发",
                }
                return False
    parents = session.info.get("agent_task_parents", {})
    task_id = getattr(task, "id", None)
    if task_id is not None and task_id in parents:
        from short_drama.agent.core.access import may_decide
        from short_drama.agent.core.events import TERMINAL

        rows = parents[task_id]
        if rows is None or agent_enabled is False:
            return False
        project, conversation, run = rows
        if (
            run.status in TERMINAL
            or run.cancel_requested
            or not may_decide(session, project, conversation, run)
        ):
            return False
    if task.initiated_by is None:
        # Unowned tasks never cross the paid submission boundary.
        return False
    user = session.scalar(
        select(User)
        .where(User.id == task.initiated_by)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.status != "active" or not user.email_verified_at:
        return False
    if media_owner is not None and not policy_state_locked(session, media_owner, lock=True)[0]:
        session.info.setdefault("assistant_submission_errors", {})[task.id] = {
            "code": "assistant_media_disabled",
            "message": "助手媒体生成已关闭，任务尚未外发",
        }
        return False
    if task.project_id:
        project = session.scalar(
            select(Project).where(Project.id == task.project_id).with_for_update()
        )
        if project is None or project.archived_at:
            return False
        return project.owner_user_id == user.id
    return task.scope_user_id == user.id


def submission_error(session, task):
    return session.info.get("assistant_submission_errors", {}).get(
        task.id,
        {"code": "access_revoked"},
    )
