from short_drama.ai import GenerationError, capability_fingerprint
from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO, ASYNC_AUDIO_POLL_SECONDS
from short_drama.ai.adapters.image.agnes_adapters import (
    agnes_has_stored_references,
    check_agnes_reference_echo,
    is_agnes_video,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
    RUNNINGHUB_POLL_SECONDS,
    RUNNINGHUB_VIDEO,
)
from short_drama.ai.model_identity import model_credential_identity
from short_drama.dao.generations.runtime.lifecycle import owned_task
from short_drama.domain import AIGenerationRecord, AIModelConfig
from short_drama.service.base import utcnow
from short_drama.service.generations.execution.base import ExecutionServiceBase
from short_drama.service.generations.runtime.lifecycle import finish, schedule


class ResultArchiving(ExecutionServiceBase):
    def _store_result(self, task, record, version, token, result):
        if is_agnes_video(record.config_snapshot, record.adapter or result.adapter) and (
            agnes_has_stored_references(record.request_data)
        ):
            guard = (record.response_data or {}).get("agnes_reference_guard")
            check_agnes_reference_echo(result, guard, submitted=task.next_action == "submit")
        tool_reply = None
        if "canvas_tool_canonical" in record.request_data and result.status == "succeeded":
            from short_drama.ai.adapters.tools.adapters import validate_canvas_tool_reply

            if task.service_type != "text":
                raise GenerationError("invalid_response", accepted_unknown=True)
            tool_reply = validate_canvas_tool_reply(
                {
                    "content": result.text or "",
                    "reasoning": result.reasoning,
                    "toolCalls": result.tool_calls,
                },
                record.request_data["canvas_tool_canonical"],
                submitted=True,
            ).model_dump(mode="json", by_alias=True, exclude_none=True)
        manifest = None
        if result.status == "succeeded" and task.service_type != "text":
            manifest = self.archive.prepare(task, record, result.outputs)
        with self.factory.begin() as session:
            current = owned_task(session, task.id, version, token)
            call = session.get(AIGenerationRecord, record.id)
            now = utcnow()
            call.adapter = result.adapter
            call.updated_at = now
            if result.status in {"submitted", "succeeded"}:
                config = session.get(AIModelConfig, call.config_id)
                if config and result.adapter != "dashscope_voice_design.v1":
                    current_snapshot = {
                        field: getattr(config, field)
                        for field in (
                            "base_url",
                            "model_key",
                            "service_type",
                        )
                    }
                    identity = model_credential_identity(config)
                    captured_identity = call.config_snapshot.get("credential_identity")
                    current_canvas_version = (call.request_data.get("source") or {}).get(
                        "scene"
                    ) not in {"canvas_node", "canvas_model_test", "canvas_tool"} or str(
                        config.row_version
                    ) == str(call.config_snapshot.get("row_version"))
                    if (
                        current_canvas_version
                        and captured_identity
                        and capability_fingerprint(current_snapshot, identity)
                        == capability_fingerprint(call.config_snapshot, captured_identity)
                    ):
                        config.capability_cache = {
                            **(config.capability_cache or {}),
                            "adapter": result.adapter,
                            "fingerprint": capability_fingerprint(current_snapshot, identity),
                        }
            if result.provider_task_id:
                call.provider_task_id = result.provider_task_id
            data = {
                **(call.response_data or {}),
                "usage": result.usage or {},
                "finish_reason": result.finish_reason,
                **(
                    {"canvas_reasoning": result.reasoning}
                    if result.reasoning
                    and call.adapter in {"claude_messages.v1", "gemini_generate_content.v1"}
                    and (call.request_data.get("source") or {}).get("scene")
                    in {"canvas_node", "canvas_model_test"}
                    else {}
                ),
                **({"voice": result.voice} if result.voice else {}),
                **(
                    {"image_request_errors": result.error["output_errors"]}
                    if task.service_type == "image"
                    and result.error
                    and result.error.get("output_errors")
                    else {}
                ),
                **({"canvas_tool_reply": tool_reply} if tool_reply is not None else {}),
            }
            from short_drama.service.canvas.generation.video.video_admission import VIDEO_ADAPTERS
            from short_drama.service.canvas.generation.video.video_policy import (
                POLL_SECONDS,
                reset_poll_state,
            )

            if call.adapter in VIDEO_ADAPTERS or call.adapter == RUNNINGHUB_VIDEO:
                reset_poll_state(data)
            if result.status == "submitted":
                if not call.provider_task_id:
                    call.status = "unknown"
                    finish(
                        current,
                        "failed",
                        {"code": "missing_provider_id", "message": "供应商已受理但未返回任务标识"},
                    )
                else:
                    call.status = "sent"
                    expired = (
                        now - current.started_at
                    ).total_seconds() >= call.config_snapshot.get("budget_seconds", 180)
                    if expired:
                        call.error = {
                            "code": "generation_timeout",
                            "message": "等待生成结果超时，已停止自动查询",
                        }
                        finish(current, "failed", call.error)
                    else:
                        delay = (
                            POLL_SECONDS
                            if call.adapter in VIDEO_ADAPTERS
                            else self.settings.generation_poll_seconds
                        )
                        if call.adapter == ASYNC_AUDIO:
                            delay = 0 if task.next_action == "submit" else ASYNC_AUDIO_POLL_SECONDS
                        if call.adapter in RUNNINGHUB_ADAPTERS:
                            delay = (
                                POLL_SECONDS
                                if call.adapter == RUNNINGHUB_VIDEO
                                else 0
                                if task.next_action == "submit"
                                else RUNNINGHUB_POLL_SECONDS
                            )
                        if (
                            call.adapter in VIDEO_ADAPTERS
                            or call.adapter == ASYNC_AUDIO
                            or call.adapter in RUNNINGHUB_ADAPTERS
                        ):
                            remaining = (
                                call.config_snapshot.get("budget_seconds", 180)
                                - (now - current.started_at).total_seconds()
                            )
                            delay = min(delay, max(0, remaining))
                        schedule(current, "poll", delay)
            elif result.status == "failed":
                call.status = "failed"
                call.finished_at = now
                call.error = result.error or {
                    "code": "provider_failed",
                    "message": "供应商生成失败",
                }
                finish(current, "failed", call.error)
            else:
                call.status = "succeeded"
                call.finished_at = now
                call.error = None
                current.error = None
                if task.service_type == "text":
                    call.text_content = result.text or ""
                    if tool_reply is not None:
                        finish(current, "succeeded")
                    elif not call.text_content.strip():
                        finish(
                            current, "failed", {"code": "empty_result", "message": "模型未返回文本"}
                        )
                    elif result.finish_reason in {"length", "max_output_tokens"}:
                        finish(
                            current,
                            "failed",
                            {"code": "text_truncated", "message": "文本未完整生成，已保留正文"},
                        )
                    elif (
                        call.request_data.get("canvas_request", {}).get("operation") == "storyboard"
                    ):
                        from short_drama.service.canvas.document.storyboard.storyboard import (
                            canvas_storyboard_result,
                        )

                        try:
                            data["canvas_storyboard"] = canvas_storyboard_result(
                                call.text_content, call.request_data["canvas_request"], str(task.id)
                            )
                        except GenerationError as error:
                            call.status = "failed"
                            call.error = {
                                "code": error.code,
                                "message": "分镜结构无效，已保留私人任务正文，未覆盖镜头",
                            }
                            finish(current, "failed", call.error)
                        else:
                            finish(current, "succeeded")
                    elif (call.request_data.get("source") or {}).get("scene") in {
                        "script_rewrite",
                        "script_shots",
                        "script_assets",
                    }:
                        data["archive_started_at"] = now.isoformat()
                        schedule(current, "save")
                    else:
                        finish(current, "succeeded")
                else:
                    data["media_manifest"] = manifest
                    data["archive_started_at"] = now.isoformat()
                    data["expected_count"] = record.request_data.get("parameters", {}).get(
                        "count", 1
                    )
                    if record.adapter in RUNNINGHUB_ADAPTERS:
                        data["expected_count"] = len(result.outputs)
                    # Keep the current execution lease until inline staging finishes.
                    # A crash now recovers as save from the persisted manifest.
            call.response_data = data
            if "canvas_request" in call.request_data:
                from short_drama.service.canvas.generation.text.text_stream import (
                    finalize_canvas_text,
                )

                finalize_canvas_text(session, current)
        if manifest is not None:
            self.archive.stage_inline(
                task,
                record,
                result.outputs,
                manifest,
                lambda: self.store.heartbeat(task.id, version, token),
            )
            with self.factory.begin() as session:
                schedule(owned_task(session, task.id, version, token), "save")
