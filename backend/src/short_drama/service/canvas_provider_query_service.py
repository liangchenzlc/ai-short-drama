"""查询原失败视频的供应商状态；专用租约不改变 failed 的源语义。"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from short_drama.ai import GenerationError, GenerationGateway, GenerationResult, select_adapter
from short_drama.ai.adapters import poll_endpoint
from short_drama.ai.canvas_credentials import decode_canvas_credentials
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.dao.task_runtime_dao import LeaseLost
from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding
from short_drama.storage.minio import MinioStorage

from .base import BaseService, utcnow
from .canvas_generation_service import CanvasGenerationService
from .generation_archive import GenerationArchive

log = logging.getLogger(__name__)


class ScopedFactory:
    def __init__(self, factory: sessionmaker[Session], information: dict) -> None:
        self.factory = factory
        self.information = information

    def __call__(self) -> Session:
        session = self.factory()
        session.info.update(self.information)
        return session

    @contextmanager
    def begin(self) -> Iterator[Session]:
        with self() as session, session.begin():
            yield session


@dataclass(frozen=True)
class QueryTask:
    id: int
    service_type: str
    project_id: int | None
    scope_user_id: int | None
    initiated_by: int


@dataclass(frozen=True)
class QueryRecord:
    id: int
    adapter: str
    provider_task_id: str
    config_snapshot: dict
    request_data: dict
    credential_cipher: str | None


@dataclass(frozen=True)
class QueryClaim:
    task: QueryTask
    record: QueryRecord
    version: int
    token: str
    manifest: list[dict]


class CanvasProviderQueryService(BaseService):
    model = CanvasTaskBinding

    def __init__(
        self,
        session: Session,
        factory: sessionmaker[Session],
        settings: Settings,
        gateway: GenerationGateway,
        storage: MinioStorage,
    ) -> None:
        super().__init__(session)
        actor = session.info.get("actor")
        if actor is None:
            raise WorkflowError("task_actor_required", "视频取回需要当前账号", 403)
        self.settings = settings
        self.generation = CanvasGenerationService(session, settings)
        self.factory = ScopedFactory(
            factory,
            {
                name: session.info[name]
                for name in ("actor", "resource_scope", "request_project")
                if name in session.info
            },
        )
        self.gateway = gateway
        self.archive = GenerationArchive(self.factory, settings, gateway, storage)

    def _claim(self, identifier: int) -> QueryClaim | dict:
        with self._transaction():
            canvas, task, binding = self.generation._owned(identifier)
            if task.service_type != "video":
                raise WorkflowError(
                    "canvas_provider_query_not_allowed", "只支持原失败视频任务", 409
                )
            if task.status == "succeeded":
                return {
                    "task": self.generation._project(canvas, task, binding),
                    "providerStatus": "succeeded",
                    "recovered": True,
                }
            if task.status != "failed":
                raise WorkflowError("canvas_provider_query_not_allowed", "原任务尚未失败", 409)
            records = self.generation.runtime.records(task.id)
            record = records[-1] if records else None
            if record is None or not record.provider_task_id or record.status == "prepared":
                raise WorkflowError(
                    "canvas_provider_query_missing_id", "原任务没有已确认的供应商任务标识", 409
                )
            try:
                adapter = record.adapter or select_adapter(record.config_snapshot)
                from .canvas_video_admission import VIDEO_ADAPTERS

                if (
                    adapter
                    not in {"ark_video.v1", "dashscope_video.v1", "modelhub_video.v1"}
                    | VIDEO_ADAPTERS
                ):
                    raise GenerationError("unsupported_poll")
                poll_endpoint(record.config_snapshot, record.provider_task_id, adapter)
            except GenerationError:
                raise WorkflowError(
                    "canvas_provider_query_not_supported", "原供应商不支持安全查询", 409
                ) from None
            manifest = deepcopy((record.response_data or {}).get("media_manifest", []))
            anonymous = (
                record.config_snapshot.get("credential_identity") == hashlib.sha256(b"").hexdigest()
            )
            if not manifest and not record.credential_cipher and not anonymous:
                raise WorkflowError(
                    "canvas_provider_query_credentials_expired",
                    "原任务凭据已不可用，不能安全查询",
                    409,
                )
            now = utcnow()
            if task.lock_token and task.locked_until and task.locked_until > now:
                raise WorkflowError(
                    "canvas_provider_query_busy", "原任务正在查询或取回，请稍后查看", 409
                )
            token = uuid4().hex
            task.lock_token = token
            task.locked_until = now + timedelta(seconds=self.settings.generation_lease_seconds)
            task.updated_at = now
            return QueryClaim(
                QueryTask(
                    task.id,
                    task.service_type,
                    task.project_id,
                    task.scope_user_id,
                    task.initiated_by,
                ),
                QueryRecord(
                    record.id,
                    adapter,
                    record.provider_task_id,
                    deepcopy(record.config_snapshot),
                    deepcopy(record.request_data),
                    record.credential_cipher,
                ),
                task.message_version,
                token,
                manifest,
            )

    def _owned(self, session: Session, claim: QueryClaim) -> AsyncTask:
        generation = CanvasGenerationService(session, self.settings)
        _, task, _ = generation._owned(claim.task.id)
        records = generation.runtime.records(task.id)
        record = records[-1] if records else None
        if (
            task.status != "failed"
            or task.message_version != claim.version
            or task.lock_token != claim.token
            or task.locked_until is None
            or task.locked_until <= utcnow()
            or record is None
            or record.id != claim.record.id
            or record.provider_task_id != claim.record.provider_task_id
        ):
            raise LeaseLost()
        return task

    def _renew(self, claim: QueryClaim) -> bool:
        try:
            with self.factory.begin() as session:
                task = self._owned(session, claim)
                task.locked_until = utcnow() + timedelta(
                    seconds=self.settings.generation_lease_seconds
                )
            return True
        except (LeaseLost, BusinessError):
            return False

    @contextmanager
    def _heartbeat(self, claim: QueryClaim) -> Iterator[None]:
        stopped = threading.Event()

        def renew() -> None:
            while not stopped.wait(max(5, self.settings.generation_lease_seconds // 3)):
                try:
                    if not self._renew(claim):
                        return
                except Exception:
                    log.warning(
                        "Canvas provider query lease renewal failed; task_id=%s", claim.task.id
                    )
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join(timeout=1)

    def _observation(self, claim: QueryClaim, result: GenerationResult) -> list[dict]:
        if result.provider_task_id and result.provider_task_id != claim.record.provider_task_id:
            raise GenerationError("provider_task_identity_changed")
        manifest = (
            self.archive.prepare(claim.task, claim.record, result.outputs)
            if result.status == "succeeded"
            else []
        )
        with self.factory.begin() as session:
            self._owned(session, claim)
            record = session.get(AIGenerationRecord, claim.record.id)
            now = utcnow()
            data = deepcopy(record.response_data or {})
            data["canvas_provider_query"] = {
                "status": result.status,
                "observed_at": now.isoformat(),
            }
            if result.status == "succeeded":
                if not manifest:
                    raise GenerationError("missing_output")
                data.update(
                    media_manifest=manifest,
                    expected_count=record.request_data.get("parameters", {}).get("count", 1),
                    archive_started_at=now.isoformat(),
                )
                record.status = "succeeded"
                record.error = None
                record.finished_at = now
            elif result.status == "failed":
                record.status = "failed"
                record.finished_at = now
                record.error = {"code": "provider_failed", "message": "供应商原视频任务失败"}
            record.response_data = data
            record.updated_at = now
        return manifest

    def _save(self, claim: QueryClaim, manifest: list[dict], deadline: float) -> bool:
        def ownership(session: Session, task_id: int, version: int, token: str) -> AsyncTask:
            if (task_id, version, token) != (claim.task.id, claim.version, claim.token):
                raise LeaseLost()
            return self._owned(session, claim)

        def wait(delay: float) -> None:
            if time.monotonic() + delay >= deadline:
                raise GenerationError("timeout")
            if not self._renew(claim):
                raise LeaseLost()
            time.sleep(delay)
            if not self._renew(claim):
                raise LeaseLost()

        for entry in manifest:

            def save(entry=entry):
                return self.archive.save_one(
                    claim.task,
                    claim.record,
                    entry,
                    claim.version,
                    claim.token,
                    ownership_check=ownership,
                )

            from .canvas_video_admission import VIDEO_ADAPTERS
            from .canvas_video_policy import run_download

            if claim.record.adapter in VIDEO_ADAPTERS:
                run_download(save, wait)
            else:
                save()
        with self.factory.begin() as session:
            task = self._owned(session, claim)
            record = session.get(AIGenerationRecord, claim.record.id)
            saved = (record.response_data or {}).get("media_manifest", [])
            expected = (record.response_data or {}).get("expected_count", 1)
            if not saved or len(saved) < expected or not all(entry.get("saved") for entry in saved):
                return False
            now = utcnow()
            task.status = "succeeded"
            task.error = None
            task.finished_at = task.updated_at = now
            task.next_action = task.next_run_at = None
            task.message_status = "idle"
            task.message_version += 1
            task.lock_token = task.locked_until = None
        return True

    def _query_error(self, claim: QueryClaim) -> None:
        with self.factory.begin() as session:
            task = self._owned(session, claim)
            task.error = {"code": "canvas_video_recovery_failed", "message": "原成片查询或取回失败"}
            task.updated_at = utcnow()

    def _release(self, claim: QueryClaim) -> None:
        with self.factory.begin() as session:
            task = session.scalar(
                select(AsyncTask).where(AsyncTask.id == claim.task.id).with_for_update()
            )
            if task and task.message_version == claim.version and task.lock_token == claim.token:
                task.lock_token = task.locked_until = None
                task.updated_at = utcnow()

    def query(self, identifier: int) -> dict:
        claim = self._claim(identifier)
        if isinstance(claim, dict):
            return claim
        recovered = False
        deadline = time.monotonic() + 600
        status = "succeeded" if claim.manifest else "processing"
        try:
            with self._heartbeat(claim):
                if not self._renew(claim):
                    raise LeaseLost()
                manifest = claim.manifest
                if not manifest:
                    key = self.settings.encryption_key
                    credential = (
                        KeyCipher(key.get_secret_value() if key else None).decrypt(
                            claim.record.credential_cipher
                        )
                        if claim.record.credential_cipher
                        else ""
                    )
                    credential = decode_canvas_credentials(
                        claim.record.config_snapshot, claim.record.request_data, credential
                    )
                    snapshot = {
                        **claim.record.config_snapshot,
                        "budget_seconds": min(
                            60, claim.record.config_snapshot.get("budget_seconds", 60)
                        ),
                    }
                    result = self.gateway.poll(
                        snapshot, claim.record.provider_task_id, credential, claim.record.adapter
                    )
                    manifest = self._observation(claim, result)
                    status = {
                        "submitted": "processing",
                        "failed": "failed",
                        "succeeded": "succeeded",
                    }[result.status]
                    if manifest:
                        self.archive.stage_inline(
                            claim.task,
                            claim.record,
                            result.outputs,
                            manifest,
                            lambda: self._renew(claim),
                        )
                if manifest:
                    recovered = self._save(claim, manifest, deadline)
        except LeaseLost:
            raise WorkflowError(
                "canvas_provider_query_changed", "原任务或权限已改变，请重新打开画布查看", 409
            ) from None
        except BusinessError:
            raise
        except Exception:
            log.warning("Canvas original video recovery failed; task_id=%s", claim.task.id)
            try:
                self._query_error(claim)
            except LeaseLost:
                raise WorkflowError(
                    "canvas_provider_query_changed", "原任务已改变，请重新打开画布查看", 409
                ) from None
            except BusinessError:
                raise
            except Exception:
                log.warning("Canvas recovery error observation failed; task_id=%s", claim.task.id)
            raise WorkflowError(
                "canvas_video_recovery_failed",
                "原成片查询或取回失败，请稍后再次取回，无需重新生成",
                503,
            ) from None
        finally:
            try:
                self._release(claim)
            except Exception:
                log.warning("Canvas provider query lease release failed; task_id=%s", claim.task.id)
        self.session.expire_all()
        return {
            "task": self.generation.detail(identifier),
            "providerStatus": status,
            "recovered": recovered,
        }
