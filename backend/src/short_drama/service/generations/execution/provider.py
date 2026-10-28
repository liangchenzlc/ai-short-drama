import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from short_drama.ai import GenerationError, select_adapter
from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO
from short_drama.ai.adapters.image.agnes_adapters import (
    agnes_has_stored_references,
    is_agnes_video,
    validate_agnes_reference_guard,
)
from short_drama.ai.adapters.image.image_references import (
    is_stored_image_edit_request,
    uses_canvas_inline_images,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
)
from short_drama.ai.gateway.canvas_credentials import decode_canvas_credentials
from short_drama.ai.model_identity import model_credential_identity
from short_drama.core.crypto import KeyCipher
from short_drama.dao.generations.runtime.lifecycle import LeaseLost, owned_task
from short_drama.domain import AIGenerationRecord, AIModelConfig, AsyncTask, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.generations.execution.base import ExecutionServiceBase
from short_drama.service.generations.execution.support import log
from short_drama.service.generations.references.references import (
    StoredAudioReferences,
    StoredImageReferences,
)
from short_drama.service.generations.runtime.lifecycle import finish, schedule
from short_drama.service.media.files.storage import StorageService
from short_drama.tasks.runtime.state import recovery_action


class ProviderExecution(ExecutionServiceBase):
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
                # 恢复归档只保存已有供应商结果，不能因模块拆分再次调用供应商生成。
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
                # 保留租约证据供恢复核对，不把结果未知的中断确认成普通失败。
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
        from short_drama.service.canvas.generation.video.video_admission import VIDEO_ADAPTERS

        if record.adapter in VIDEO_ADAPTERS or record.adapter in RUNNINGHUB_ADAPTERS:
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
                    and is_stored_image_edit_request(record.config_snapshot, request)
                ):
                    data["reference_urls"] = [
                        f"https://reference.invalid/{index}"
                        for index, _ in enumerate(data["reference_media_ids"])
                    ]
                else:
                    data["reference_urls"] = [
                        media_url(value) for value in data["reference_media_ids"]
                    ]
            if data.get("video_reference_media_ids"):
                data["video_reference_urls"] = [
                    media_url(value) for value in data["video_reference_media_ids"]
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

    def _persist_agnes_reference_guard(self, task, record, version, token, guard):
        validate_agnes_reference_guard(guard)
        try:
            with self.factory.begin() as session:
                current = owned_task(session, task.id, version, token)
                call = session.get(AIGenerationRecord, record.id)
                if (
                    current.next_action != "submit"
                    or call is None
                    or call.task_id != task.id
                    or call.status != "sent"
                    or (call.response_data or {}).get("agnes_reference_guard") is not None
                ):
                    raise LeaseLost()
                data = {**(call.response_data or {}), "agnes_reference_guard": deepcopy(guard)}
                call.response_data = data
                call.updated_at = utcnow()
            # Only update the detached record after the database commit succeeds.
            record.response_data = deepcopy(data)
        except SQLAlchemyError:
            raise GenerationError("reference_guard_unavailable") from None

    def _generate(self, task, record, version, token):
        now = utcnow()
        budget = record.config_snapshot.get("budget_seconds", 180)
        elapsed = (now - task.started_at).total_seconds()
        from short_drama.service.canvas.generation.video.video_admission import VIDEO_ADAPTERS

        if (
            task.next_action == "poll"
            and (
                record.adapter in VIDEO_ADAPTERS
                or record.adapter == ASYNC_AUDIO
                or record.adapter in RUNNINGHUB_ADAPTERS
            )
            and elapsed >= budget
        ):
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
                from short_drama.service.generations.runtime.access import (
                    lock_resource_project,
                    may_submit,
                    submission_error,
                )

                lock_resource_project(session, AsyncTask, task.id)
            current = owned_task(session, task.id, version, token)
            call = session.get(AIGenerationRecord, record.id)
            if task.next_action == "submit":
                if not may_submit(
                    session,
                    current,
                    agent_enabled=getattr(self.settings, "agent_enabled", False),
                    settings=self.settings,
                ):
                    finish(current, "cancelled", submission_error(session, current))
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
            from short_drama.service.canvas.generation.video.video_admission import VIDEO_ADAPTERS

            if adapter in VIDEO_ADAPTERS or adapter in RUNNINGHUB_ADAPTERS:
                from short_drama.service.canvas.generation.video.video_references import (
                    StoredCanvasVideoReferences,
                )

                snapshot["submission_key"] = str(
                    uuid.uuid5(uuid.NAMESPACE_OID, f"{task.id}:{record.id}")
                )
                if is_agnes_video(snapshot, adapter):
                    from short_drama.service.canvas.generation.video.video_references import (
                        StoredCanvasVideoReferenceURLs,
                    )

                    options["canvas_reference_url_resolver"] = StoredCanvasVideoReferenceURLs(
                        self.factory, self.storage, self.settings, request, task.project_id
                    )
                    options["agnes_reference_guard_writer"] = lambda guard: (
                        self._persist_agnes_reference_guard(task, record, version, token, guard)
                    )
                else:
                    options["canvas_reference_loader"] = StoredCanvasVideoReferences(
                        self.factory, self.storage, self.settings, request
                    )
                mask_ids = request.get("input", {}).get("mask_media_ids", [])
                if mask_ids and adapter in RUNNINGHUB_ADAPTERS:
                    options["mask_reference_loader"] = StoredImageReferences(
                        self.factory, self.storage, self.settings, mask_ids
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
            mask_id = (
                request.get("canvas_parameters") or request.get("image_edit_parameters") or {}
            ).get("mask_media_id")
            if (
                mask_id
                and adapter == "openai_images.v1"
                and is_stored_image_edit_request(snapshot, request)
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
                    from short_drama.service.canvas.generation.text.text_stream import (
                        CanvasTextStreamWriter,
                    )

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
            snapshot["budget_seconds"] = (
                min(60, budget - elapsed)
                if adapter == ASYNC_AUDIO or adapter in RUNNINGHUB_ADAPTERS
                else min(60, budget)
            )
            poll_options = {}
            if is_agnes_video(snapshot, adapter) and agnes_has_stored_references(
                record.request_data
            ):
                guard = (record.response_data or {}).get("agnes_reference_guard")
                validate_agnes_reference_guard(guard)
                poll_options = {
                    "agnes_reference_guard": guard,
                    "require_agnes_reference_guard": True,
                }
            result = self.gateway.poll(
                snapshot, record.provider_task_id, credential, adapter, **poll_options
            )
        self._store_result(task, record, version, token, result)
