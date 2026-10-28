from copy import deepcopy

from sqlalchemy.exc import SQLAlchemyError

from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO
from short_drama.ai.adapters.image.agnes_adapters import (
    AgnesReferenceEcho,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
    RUNNINGHUB_VIDEO,
)
from short_drama.core.exceptions import BusinessError
from short_drama.dao.generations.runtime.lifecycle import LeaseLost, latest_record, owned_task
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.base import utcnow
from short_drama.service.generations.execution.base import ExecutionServiceBase
from short_drama.service.generations.runtime.lifecycle import finish, schedule
from short_drama.tasks.runtime.state import archive_due
from short_drama.utils.snowflake import next_id


class ExecutionSettlement(ExecutionServiceBase):
    def _call_error(self, task, record, version, token, error):
        with self.factory.begin() as session:
            current = owned_task(session, task.id, version, token)
            call = session.get(AIGenerationRecord, record.id)
            now = utcnow()
            safe_error = {
                "code": error.code,
                "message": "模型请求失败，请核对模型配置或稍后查看结果",
            }
            if error.http_status is not None:
                safe_error["http_status"] = error.http_status
            call.updated_at = now
            call.error = safe_error
            if isinstance(error, AgnesReferenceEcho) and error.provider_task_id:
                if call.provider_task_id in (None, error.provider_task_id):
                    call.provider_task_id = error.provider_task_id
            if task.next_action == "poll":
                expired = (now - current.started_at).total_seconds() >= call.config_snapshot.get(
                    "budget_seconds", 180
                )
                from short_drama.service.canvas.generation.video.video_admission import (
                    VIDEO_ADAPTERS,
                )
                from short_drama.service.canvas.generation.video.video_policy import (
                    poll_retry_delay,
                )

                delay = 15
                if (
                    call.adapter == ASYNC_AUDIO
                    or call.adapter in RUNNINGHUB_ADAPTERS
                    and call.adapter != RUNNINGHUB_VIDEO
                ):
                    # 源音频声明式policy关闭RetryTransient；保留原provider ID，停止自动查询。
                    delay = None
                elif call.adapter in VIDEO_ADAPTERS or call.adapter == RUNNINGHUB_VIDEO:
                    data = deepcopy(call.response_data or {})
                    delay = poll_retry_delay(error, data)
                    call.response_data = data
                if expired or delay is None:
                    finish(current, "failed", safe_error)
                else:
                    current.error = safe_error
                    if call.adapter in VIDEO_ADAPTERS or call.adapter == RUNNINGHUB_VIDEO:
                        remaining = (
                            call.config_snapshot.get("budget_seconds", 180)
                            - (now - current.started_at).total_seconds()
                        )
                        delay = min(delay, max(0, remaining))
                    schedule(current, "poll", delay)
            elif error.accepted_unknown:
                call.status = "unknown"
                finish(current, "failed", safe_error)

            elif (
                error.protocol_mismatch
                and not call.config_snapshot.get("agent_managed")
                and call.call_no == 1
                and call.adapter
                in {
                    "openai_chat.v1",
                    "openai_responses.v1",
                }
            ):
                call.status = "failed"
                call.finished_at = now
                candidate = (
                    "openai_responses.v1" if call.adapter == "openai_chat.v1" else "openai_chat.v1"
                )
                session.add(
                    AIGenerationRecord(
                        id=next_id(),
                        task_id=task.id,
                        call_no=2,
                        config_id=call.config_id,
                        config_snapshot=deepcopy(call.config_snapshot),
                        request_data=deepcopy(call.request_data),
                        credential_cipher=call.credential_cipher,
                        adapter=candidate,
                        status="prepared",
                        created_at=now,
                        updated_at=now,
                    )
                )
                schedule(current, "submit")
            else:
                call.status = "failed"
                call.finished_at = now
                finish(current, "failed", safe_error)

    def _save(self, task, record, version, token):
        if task.service_type == "text":
            self._save_text(task, record, version, token)
            return
        data = record.response_data or {}
        transient = False
        save_delay = 30
        request_source = (record.request_data.get("source") or {}).get("scene")
        image_group = (
            bool(record.request_data.get("image_group_id")) or request_source == "image_operation"
        )
        for entry in data.get("media_manifest", []):
            if entry.get("ignored"):
                continue
            if entry.get("saved") and not (
                image_group
                and not entry.get("image_group_saved")
                or not image_group
                and request_source == "asset_image"
                and not entry.get("candidate_status")
            ):
                continue
            if not archive_due(
                data,
                utcnow(),
                record.config_snapshot.get(
                    "archive_budget_seconds", self.settings.generation_archive_budget_seconds
                ),
            ):
                break
            if not self.store.heartbeat(task.id, version, token):
                raise LeaseLost()
            try:
                self.archive.save_one(task, record, entry, version, token)
            except LeaseLost:
                raise
            except Exception as error:
                transient = True
                with self.factory.begin() as session:
                    owned_task(session, task.id, version, token)
                    call = session.get(AIGenerationRecord, record.id)
                    response = deepcopy(call.response_data or {})
                    for item in response.get("media_manifest", []):
                        if item["output_index"] == entry["output_index"]:
                            from short_drama.service.canvas.generation.video.video_policy import (
                                CanvasVideoDownloadError,
                                retry_delay,
                            )

                            if isinstance(error, CanvasVideoDownloadError):
                                attempts = int(item.get("canvas_video_download_failures", 0)) + 1
                                item["canvas_video_download_failures"] = attempts
                                delay = retry_delay(error)
                                if delay is None or attempts >= 3:
                                    transient = False
                                else:
                                    save_delay = max(save_delay, delay)
                            item["save_error"] = {
                                "code": "download_failed"
                                if isinstance(error, CanvasVideoDownloadError)
                                else "archive_failed",
                                "message": "生成结果下载失败，保留原任务等待取回"
                                if isinstance(error, CanvasVideoDownloadError)
                                else "媒体保存失败，等待重试",
                            }
                    call.response_data = response
                    call.updated_at = utcnow()
        with self.factory.begin() as session:
            if request_source == "shot_video":
                from short_drama.service.generations.runtime.access import lock_resource_project

                lock_resource_project(session, AsyncTask, task.id)
            current = owned_task(session, task.id, version, token)
            call = latest_record(session, task.id)
            data = call.response_data or {}
            entries = data.get("media_manifest", [])
            source_scene = (call.request_data.get("source") or {}).get("scene")

            def complete(item):
                if image_group:
                    return bool(item.get("saved") and item.get("image_group_saved"))
                return bool(item.get("saved")) and (
                    source_scene != "asset_image"
                    or item.get("candidate_status") in {"linked", "source_missing"}
                )

            saved = sum(complete(item) for item in entries)
            ignored = (
                sum(bool(item.get("ignored")) for item in entries)
                if call.adapter in RUNNINGHUB_ADAPTERS
                else 0
            )
            if (
                saved > 0
                and saved + ignored >= data.get("expected_count", 1)
                and saved + ignored == len(entries)
            ):
                from short_drama.service.media.library.assets import MediaAssetService

                receipt = MediaAssetService(
                    session, self.settings, self.storage
                ).confirm_generated_shot_video_locked(current, call)
                warning = (
                    {"code": "shot_video_auto_apply_skipped", "reason": receipt["code"]}
                    if receipt and receipt["status"] == "skipped"
                    else None
                )
                finish(current, "succeeded", warning)
            elif entries and ignored == len(entries):
                finish(
                    current,
                    "failed",
                    {
                        "code": "workflow_output_type_unsupported",
                        "message": "RunningHub 没有返回非空且支持的媒体产物",
                    },
                )
            elif transient and archive_due(
                data,
                utcnow(),
                call.config_snapshot.get(
                    "archive_budget_seconds", self.settings.generation_archive_budget_seconds
                ),
            ):
                current.error = {"code": "archive_retrying", "message": "模型已生成，正在重试保存"}
                schedule(current, "save", save_delay)
            else:
                download_failed = any(
                    not item.get("saved")
                    and (item.get("save_error") or {}).get("code") == "download_failed"
                    for item in entries
                )
                expected = int(data.get("expected_count", len(entries) or 1))
                failed_indexes = {
                    item["output_index"]
                    for item in entries
                    if not complete(item) and not item.get("ignored")
                }
                failed_indexes.update(
                    set(range(1, expected + 1)) - {item["output_index"] for item in entries}
                )
                error = {
                    "code": "download_failed"
                    if download_failed
                    else "partial_result"
                    if saved
                    else "archive_failed",
                    "message": "生成结果下载失败，请取回原任务结果，不要重新提交"
                    if download_failed
                    else (
                        f"已保存 {saved}/{expected} 张图片，部分结果未能保存；"
                        "已保存结果仍可使用，请检查失败位置后重试。"
                    )
                    if saved
                    else "生成结果未能保存，请检查对象存储后重试。",
                }
                if saved:
                    error.update(
                        {
                            "saved_count": saved,
                            "expected_count": expected,
                            "returned_count": len(entries),
                            "failed_output_indexes": sorted(failed_indexes),
                        }
                    )
                    from short_drama.service.generations.admission.task_policy import safe_error

                    error["message"] = safe_error(error)["message"]
                finish(current, "failed", error)

    def _save_text(self, task, record, version, token):
        from short_drama.service.creation.writing.rewrite.generation import (
            WRITING_SCENES,
            WritingGenerationService,
        )
        from short_drama.service.generations.admission.business import GenerationBusinessService

        try:
            with self.factory.begin() as session:
                if (record.request_data.get("source") or {}).get("scene") in WRITING_SCENES:
                    WritingGenerationService(session).lock_project(record.request_data)
                current = owned_task(session, task.id, version, token)
                if current.cancel_requested:
                    finish(current, "cancelled")
                    return
                GenerationBusinessService(session).save_text_result(task.id, record.id)
                finish(current, "succeeded")
        except BusinessError as error:
            with self.factory.begin() as session:
                current = owned_task(session, task.id, version, token)
                finish(current, "failed", {"code": error.code, "message": error.message})
        except SQLAlchemyError:
            # Raw response was committed before entering this local-only transaction.
            with self.factory.begin() as session:
                current = owned_task(session, task.id, version, token)
                call = session.get(AIGenerationRecord, record.id)
                data = deepcopy(call.response_data or {})
                attempts = int(data.get("business_save_attempts", 0)) + 1
                data["business_save_attempts"] = attempts
                call.response_data = data
                if archive_due(
                    data,
                    utcnow(),
                    call.config_snapshot.get(
                        "archive_budget_seconds", self.settings.generation_archive_budget_seconds
                    ),
                ):
                    current.error = {"code": "business_save_failed", "message": "正在恢复结果保存"}
                    schedule(current, "save", min(60, 2 ** min(attempts, 6)))
                else:
                    finish(
                        current,
                        "failed",
                        {"code": "business_save_failed", "message": "结果保存失败"},
                    )
