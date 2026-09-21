"""Execute one versioned action. Every external call occurs outside a DB transaction."""

import hashlib
import logging
import threading
from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy.exc import SQLAlchemyError

from short_drama.ai import GenerationError, capability_fingerprint, select_adapter
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
from short_drama.domain import AIGenerationRecord, AIModelConfig, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.generation_archive import GenerationArchive
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
        return KeyCipher(key.get_secret_value() if key else None).decrypt(record.credential_cipher)

    def _input(self, record):
        request = deepcopy(record.request_data)
        data = request.setdefault("input", {})
        storage = StorageService(self.storage, self.settings)
        with self.factory() as session:

            def media_url(identifier):
                media = session.get(MediaFile, int(identifier))
                if media is None:
                    raise GenerationError("reference_missing", "参考素材不存在")
                return storage.download_url(media.storage_locator)

            if data.get("reference_media_ids"):
                data["reference_urls"] = [media_url(value) for value in data["reference_media_ids"]]
            for frame in ("first", "last"):
                if data.get(f"{frame}_frame_media_id"):
                    data[f"{frame}_frame_url"] = media_url(data[f"{frame}_frame_media_id"])
        return request

    def _generate(self, task, record, version, token):
        now = utcnow()
        budget = record.config_snapshot.get("budget_seconds", 180)
        elapsed = (now - task.started_at).total_seconds()
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
            current = owned_task(session, task.id, version, token)
            call = session.get(AIGenerationRecord, record.id)
            if task.next_action == "submit":
                if current.cancel_requested:
                    finish(current, "cancelled")
                    return
                config = session.get(AIModelConfig, call.config_id)
                if config is None or not config.enabled or config.is_deleted:
                    finish(
                        current,
                        "failed",
                        {"code": "configuration_disabled", "message": "模型配置已停用，未提交生成"},
                    )
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
            result = self.gateway.submit(snapshot, request, credential, adapter=adapter)
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
                if config:
                    current_snapshot = {
                        field: getattr(config, field)
                        for field in (
                            "base_url",
                            "model_key",
                            "service_type",
                        )
                    }
                    identity = hashlib.sha256((config.apikey or "").encode()).hexdigest()
                    captured_identity = call.config_snapshot.get("credential_identity")
                    if captured_identity and capability_fingerprint(
                        current_snapshot, identity
                    ) == capability_fingerprint(call.config_snapshot, captured_identity):
                        config.capability_cache = {
                            "adapter": result.adapter,
                            "fingerprint": capability_fingerprint(current_snapshot, identity),
                        }
            if result.provider_task_id:
                call.provider_task_id = result.provider_task_id
            data = {
                **(call.response_data or {}),
                "usage": result.usage or {},
                "finish_reason": result.finish_reason,
            }
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
                        schedule(current, "poll", self.settings.generation_poll_seconds)
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
                if expired:
                    finish(current, "failed", safe_error)
                else:
                    current.error = safe_error
                    schedule(current, "poll", 15)
            elif error.accepted_unknown:
                call.status = "unknown"
                finish(current, "failed", safe_error)
            elif (
                error.protocol_mismatch
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
        for entry in data.get("media_manifest", []):
            if entry.get("saved"):
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
            except Exception:
                transient = True
                with self.factory.begin() as session:
                    owned_task(session, task.id, version, token)
                    call = session.get(AIGenerationRecord, record.id)
                    response = deepcopy(call.response_data or {})
                    for item in response.get("media_manifest", []):
                        if item["output_index"] == entry["output_index"]:
                            item["save_error"] = {
                                "code": "archive_failed",
                                "message": "媒体保存失败，等待重试",
                            }
                    call.response_data = response
                    call.updated_at = utcnow()
        with self.factory.begin() as session:
            current = owned_task(session, task.id, version, token)
            call = latest_record(session, task.id)
            data = call.response_data or {}
            entries = data.get("media_manifest", [])
            saved = sum(bool(item.get("saved")) for item in entries)
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
                schedule(current, "save", 30)
            else:
                finish(
                    current,
                    "failed",
                    {
                        "code": "partial_result" if saved else "archive_failed",
                        "message": "结果未全部保存，已有资产仍可使用",
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
