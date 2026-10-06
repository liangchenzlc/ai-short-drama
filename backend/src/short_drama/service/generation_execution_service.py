"""Execute one versioned action. Every external call occurs outside a DB transaction."""

import logging
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from short_drama.ai import GenerationError, capability_fingerprint, select_adapter
from short_drama.ai.canvas_credentials import decode_canvas_credentials
from short_drama.ai.canvas_image_references import (
    is_canvas_image_request,
    uses_canvas_inline_images,
)
from short_drama.ai.model_identity import model_credential_identity
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.task_runtime_dao import (
    LeaseLost,
    TaskRuntimeDAO,
    finish,
    latest_record,
    owned_task,
    schedule,
)
from short_drama.domain import AIGenerationRecord, AIModelConfig, AsyncTask, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.generation_archive import GenerationArchive
from short_drama.service.generation_references import StoredAudioReferences, StoredImageReferences
from short_drama.service.storage_service import StorageService
from short_drama.tasks.state import archive_due, recovery_action
from short_drama.utils.snowflake import next_id

log = logging.getLogger(__name__)


class GenerationExecutionService:
    def __init__(self, factory, settings, gateway, storage):
        self.factory, self.settings = factory, settings
        self.gateway, self.storage = gateway, storage
        self.store = TaskRuntimeDAO(factory, settings)
        self.archive = GenerationArchive(factory, settings, gateway, storage)

    @contextmanager
    def _heartbeat(self, task_id, version, token):
        stopped = threading.Event()

        def renew():
            interval = max(5, self.settings.generation_lease_seconds // 3)
            while not stopped.wait(interval):
                try:
                    if not self.store.heartbeat(task_id, version, token):
                        return
                except Exception:
                    log.warning("Generation lease renewal failed; task_id=%s", task_id)
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    def execute(self, task_id, message_version):
        task_id, version = int(task_id), int(message_version)
        claim = self.store.claim_execution(task_id, version)
        if claim is None:
            return
        task, record, token = claim
        with self._heartbeat(task_id, version, token):
            try:
                if task.next_action == "save":
                    self._save(task, record, version, token)
                else:
                    self._generate(task, record, version, token)
            except LeaseLost:
                return
            except GenerationError as error:
                self._call_error(task, record, version, token, error)
            except Exception:
                # No raw exception/body/credentials enter the Celery result or log.
                log.warning(
                    "Generation action interrupted; task_id=%s action=%s", task_id, task.next_action
                )
                # Leave ownership for evidence-based recovery; don't ACK a guessed failure.
                raise RuntimeError("Generation action interrupted; recovery required") from None

    def _credential(self, record):
        if not record.credential_cipher:
            return ""
        key = self.settings.encryption_key
        plaintext = KeyCipher(key.get_secret_value() if key else None).decrypt(
            record.credential_cipher
        )
        return decode_canvas_credentials(record.config_snapshot, record.request_data, plaintext)

    def _input(self, record):
        request = deepcopy(record.request_data)
        from .canvas_video_admission import VIDEO_ADAPTERS

        if record.adapter in VIDEO_ADAPTERS:
            return request
        data = request.setdefault("input", {})
        storage = StorageService(self.storage, self.settings)
        with self.factory() as session:

            def media_url(identifier):
                media = session.get(MediaFile, int(identifier))
                if media is None:
                    raise GenerationError("reference_missing", "参考素材不存在")
                return storage.download_url(media.storage_locator)

            if data.get("reference_media_ids"):
                adapter = record.adapter or select_adapter(record.config_snapshot)
                if uses_canvas_inline_images(record.config_snapshot, request, adapter) or (
                    adapter == "openai_images.v1"
                    and is_canvas_image_request(record.config_snapshot, request)
                ):
                    data["reference_urls"] = [
                        f"https://reference.invalid/{index}"
                        for index, _ in enumerate(data["reference_media_ids"])
                    ]
                else:
                    data["reference_urls"] = [
                        media_url(value) for value in data["reference_media_ids"]
                    ]
            if data.get("audio_reference_media_ids"):
                data["audio_reference_urls"] = [
                    f"https://reference.invalid/audio/{i}"
                    for i, _ in enumerate(data["audio_reference_media_ids"])
                ]
            for frame in ("first", "last"):
                if data.get(f"{frame}_frame_media_id"):
                    data[f"{frame}_frame_url"] = media_url(data[f"{frame}_frame_media_id"])
        return request

    def _generate(self, task, record, version, token):
        now = utcnow()
        budget = record.config_snapshot.get("budget_seconds", 180)
        elapsed = (now - task.started_at).total_seconds()
        from .canvas_video_admission import VIDEO_ADAPTERS

        if task.next_action == "poll" and record.adapter in VIDEO_ADAPTERS and elapsed >= budget:
            with self.factory.begin() as session:
                current = owned_task(session, task.id, version, token)
                call = session.get(AIGenerationRecord, record.id)
                call.error = {
                    "code": "generation_timeout",
                    "message": "等待生成结果超时，已停止自动查询",
                }
                call.updated_at = utcnow()
                finish(current, "failed", call.error)
            return
        if task.next_action == "submit":
            if record.status != "prepared":
                with self.factory.begin() as session:
                    current = owned_task(session, task.id, version, token)
                    action = recovery_action(record)
                    if action and action != "submit":
                        schedule(current, action)
                    else:
                        finish(
                            current,
                            "failed",
                            {"code": "provider_acceptance_unknown", "message": "提交状态待核对"},
                        )
                return
            if elapsed >= budget:
                with self.factory.begin() as session:
                    finish(
                        owned_task(session, task.id, version, token),
                        "failed",
                        {"code": "generation_timeout", "message": "提交前任务预算已耗尽"},
                    )
                return
        elif elapsed >= budget + 86400:
            with self.factory.begin() as session:
                finish(
                    owned_task(session, task.id, version, token),
                    "failed",
                    {"code": "reconciliation_timeout", "message": "自动核对窗口已结束"},
                )
            return
        credential = self._credential(record)
        adapter = record.adapter or select_adapter(record.config_snapshot)
        request = self._input(record) if task.next_action == "submit" else record.request_data
        resolved = (
            self.gateway.validate(record.config_snapshot, request, adapter)
            if task.next_action == "submit"
            else None
        )
        with self.factory.begin() as session:
            if task.next_action == "submit":
                from .task_access import lock_resource_project, may_submit

                lock_resource_project(session, AsyncTask, task.id)
            current = owned_task(session, task.id, version, token)
            call = session.get(AIGenerationRecord, record.id)
            if task.next_action == "submit":
                if not may_submit(
                    session, current, agent_enabled=getattr(self.settings, "agent_enabled", False)
                ):
                    finish(current, "cancelled", {"code": "access_revoked"})
                    return
                if current.cancel_requested:
                    finish(current, "cancelled")
                    return
                config = session.scalar(
                    select(AIModelConfig)
                    .where(AIModelConfig.id == call.config_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                frozen_model_test = bool(
                    config is not None
                    and call.config_snapshot.get("canvas_model_test") is True
                    and (call.request_data.get("source") or {}).get("scene") == "canvas_model_test"
                    and current.project_id is None
                    and current.scope_user_id == current.initiated_by == config.owner_user_id
                    and config.is_deleted == 1
                    and config.enabled == 0
                    and str(config.row_version) == call.config_snapshot.get("row_version")
                    and model_credential_identity(config)
                    == call.config_snapshot.get("credential_identity")
                )
                if config is None or (
                    not frozen_model_test and (not config.enabled or config.is_deleted)
                ):
                    finish(
                        current,
                        "failed",
                        {"code": "configuration_disabled", "message": "模型配置已停用，未提交生成"},
                    )
                    return
                if call.config_snapshot.get("agent_managed") and (
                    config.owner_user_id != current.initiated_by
                    or str(config.row_version) != str(call.config_snapshot["row_version"])
                ):
                    finish(current, "failed", {"code": "agent_configuration_changed"})
                    return
                call.status = "sent"
                call.started_at = utcnow()
                call.request_data = {**call.request_data, "resolved_parameters": resolved}
            call.adapter = adapter
            call.updated_at = utcnow()
            if current.cancel_requested:
                call.response_data = {
                    **(call.response_data or {}),
                    "cancel_result": {"supported": False, "confirmed": False},
                }
        record.adapter = adapter
        # HTTP deadlines bound each action; poll reconciliation has its own window.
        snapshot = {**record.config_snapshot, "budget_seconds": max(1, int(budget - elapsed))}
        if task.next_action == "submit":
            options = {}
            from .canvas_video_admission import VIDEO_ADAPTERS

            if adapter in VIDEO_ADAPTERS:
                from .canvas_video_references import StoredCanvasVideoReferences

                snapshot["submission_key"] = str(
                    uuid.uuid5(uuid.NAMESPACE_OID, f"{task.id}:{record.id}")
                )
                options["canvas_reference_loader"] = StoredCanvasVideoReferences(
                    self.factory, self.storage, self.settings, request
                )
            voices = (request.get("source_snapshot", {}).get("native_speech") or {}).get("voices")
            if voices:
                options["audio_reference_loader"] = StoredAudioReferences(
                    self.factory, self.storage, self.settings, voices
                )
            media_ids = request.get("input", {}).get("reference_media_ids")
            if media_ids and (
                adapter in {"openai_images.v1", "modelhub_video.v1"}
                or uses_canvas_inline_images(snapshot, request, adapter)
            ):
                options["reference_loader"] = StoredImageReferences(
                    self.factory, self.storage, self.settings, media_ids
                )
            mask_id = (request.get("canvas_parameters") or {}).get("mask_media_id")
            if (
                mask_id
                and adapter == "openai_images.v1"
                and is_canvas_image_request(snapshot, request)
            ):
                options["mask_reference_loader"] = StoredImageReferences(
                    self.factory, self.storage, self.settings, [mask_id]
                )
            if adapter in {"ark_video.v1", "dashscope_video.v1", "modelhub_video.v1"}:
                frames = [
                    request["input"][f"{name}_frame_media_id"]
                    for name in ("first", "last")
                    if request["input"].get(f"{name}_frame_media_id")
                ]
                if frames:
                    options["reference_loader"] = StoredImageReferences(
                        self.factory, self.storage, self.settings, frames
                    )
            writer = None
            canvas_input = request.get("canvas_request", {}).get("input", {})
            if task.service_type == "text" and canvas_input.get("textOptions", {}).get(
                "stream", True
            ):
                if "canvas_request" in request:
                    from .canvas_text_stream import CanvasTextStreamWriter

                    writer = CanvasTextStreamWriter(self.factory, task, record, version, token)
                    options["on_text_delta"] = writer.append
            try:
                result = self.gateway.submit(
                    snapshot, request, credential, adapter=adapter, **options
                )
            except GenerationError:
                if writer is not None:
                    writer.flush()
                raise
            if writer is not None:
                writer.flush()
        else:
            snapshot["budget_seconds"] = min(60, budget)
            result = self.gateway.poll(snapshot, record.provider_task_id, credential, adapter)
        self._store_result(task, record, version, token, result)

    def _store_result(self, task, record, version, token, result):
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
                    ) not in {"canvas_node", "canvas_model_test"} or str(config.row_version) == str(
                        call.config_snapshot.get("row_version")
                    )
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
                **({"voice": result.voice} if result.voice else {}),
            }
            from .canvas_video_admission import VIDEO_ADAPTERS
            from .canvas_video_policy import POLL_SECONDS, reset_poll_state

            if call.adapter in VIDEO_ADAPTERS:
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
                        if call.adapter in VIDEO_ADAPTERS:
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
                    if not call.text_content.strip():
                        finish(
                            current, "failed", {"code": "empty_result", "message": "模型未返回文本"}
                        )
                    elif result.finish_reason in {"length", "max_output_tokens"}:
                        finish(
                            current,
                            "failed",
                            {"code": "text_truncated", "message": "文本未完整生成，已保留正文"},
                        )
                    elif (call.request_data.get("source") or {}).get("scene") in {
                        "novel_script",
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
                    # Keep the current execution lease until inline staging finishes.
                    # A crash now recovers as save from the persisted manifest.
            call.response_data = data
            if "canvas_request" in call.request_data:
                from .canvas_text_stream import finalize_canvas_text

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
            if task.next_action == "poll":
                expired = (now - current.started_at).total_seconds() >= call.config_snapshot.get(
                    "budget_seconds", 180
                )
                from .canvas_video_admission import VIDEO_ADAPTERS
                from .canvas_video_policy import poll_retry_delay

                delay = 15
                if call.adapter in VIDEO_ADAPTERS:
                    data = deepcopy(call.response_data or {})
                    delay = poll_retry_delay(error, data)
                    call.response_data = data
                if expired or delay is None:
                    finish(current, "failed", safe_error)
                else:
                    current.error = safe_error
                    if call.adapter in VIDEO_ADAPTERS:
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
        for entry in data.get("media_manifest", []):
            if entry.get("saved") and not (
                request_source == "asset_image" and not entry.get("candidate_status")
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
                            from .canvas_video_policy import CanvasVideoDownloadError, retry_delay

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
            current = owned_task(session, task.id, version, token)
            call = latest_record(session, task.id)
            data = call.response_data or {}
            entries = data.get("media_manifest", [])
            source_scene = (call.request_data.get("source") or {}).get("scene")

            def complete(item):
                return bool(item.get("saved")) and (
                    source_scene != "asset_image"
                    or item.get("candidate_status") in {"linked", "source_missing"}
                )

            saved = sum(complete(item) for item in entries)
            if saved >= data.get("expected_count", 1) and saved == len(entries):
                finish(current, "succeeded")
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
                finish(
                    current,
                    "failed",
                    {
                        "code": "download_failed"
                        if download_failed
                        else "partial_result"
                        if saved
                        else "archive_failed",
                        "message": "生成结果下载失败，请取回原任务结果，不要重新提交"
                        if download_failed
                        else "结果未全部保存，已有资产仍可使用",
                    },
                )

    def _save_text(self, task, record, version, token):
        from .generation_business_service import GenerationBusinessService

        try:
            with self.factory.begin() as session:
                current = owned_task(session, task.id, version, token)
                GenerationBusinessService(session).save_text_result(task.id, record.id)
                finish(current, "succeeded")
        except WorkflowError as error:
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
