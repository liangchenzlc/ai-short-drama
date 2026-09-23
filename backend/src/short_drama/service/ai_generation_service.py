"""Transactional task admission and user actions; never performs model calls."""

import copy
import hashlib
import json

from sqlalchemy import select

from short_drama.ai import GenerationError, validate_request
from short_drama.core.exceptions import (
    BusinessError,
    Conflict,
    GenerationRequestError,
    WorkflowError,
)
from short_drama.dao.ai_generation_record_dao import AIGenerationRecordDAO
from short_drama.dao.async_task_dao import AsyncTaskDAO
from short_drama.domain import AIModelConfig, AsyncTask, Episode, MediaAsset, ShotScript
from short_drama.schemas.ai_generation import GENERATION_SCHEMAS, GenerationRetry
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.storyboard_result import parse_storyboard_result

from .base import BaseService, utcnow

ERROR_MESSAGES = {
    "timeout": "等待模型响应超时，任务已失败；生成请求未自动重发，请核对服务商调用记录。",
    "generation_timeout": "生成任务超过等待时限，任务已失败；已停止自动查询和生成。",
    "upstream_unavailable": (
        "模型服务或中转站返回了服务器错误（HTTP 5xx）。"
        "当前无法确认生成结果，生成请求未自动重发；请核对服务商调用记录。"
    ),
    "partial_result": "Some results could not be saved.",
    "text_truncated": "The model stopped at its output limit.",
    "message_delivery_unknown": "Message delivery needs verification.",
    "acceptance_unknown": "Model acceptance needs verification.",
    "archive_failed": "Generated results could not be saved.",
    "archive_timeout": "The result saving window expired.",
    "business_save_failed": "结果已保存，业务入库失败，可恢复本地保存。",
    "invalid_structured_output": "模型返回的结构不正确，原文已保留。请调整要求后重新生成。",
    "unknown_asset_reference": "模型引用了不存在于输入清单的素材，原文已保留。",
    "source_missing": "生成来源已不存在，原始结果已保留。",
    "empty_result": "模型未返回有效正文。",
}


def safe_error(error):
    if not error:
        return None
    code = error.get("code", "generation_failed")
    if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 80:
        code = "generation_failed"
    result = {
        "code": code,
        "message": ERROR_MESSAGES.get(code, "Generation could not be completed."),
    }
    http_status = error.get("http_status")
    if type(http_status) is int and 100 <= http_status <= 599:
        result["http_status"] = http_status
        result["message"] = result["message"].replace("HTTP 5xx", f"HTTP {http_status}")
    return result


def can_retry(task, record):
    return (
        task.status in {"failed", "cancelled"}
        and record.status not in {"sent", "unknown"}
        and (task.error or {}).get("code")
        not in {"message_delivery_unknown", "business_save_failed"}
    )


def can_reparse_storyboard(record):
    request = getattr(record, "request_data", None) or {}
    if (request.get("source") or {}).get("scene") != "script_shots":
        return False
    snapshot = request.get("source_snapshot") or {}
    try:
        parse_storyboard_result(
            record.text_content,
            {int(asset["id"]) for asset in snapshot["assets"]},
            snapshot["content"],
        )
    except (KeyError, TypeError, ValueError):
        return False
    return True


def resume_action(task, record):
    response = record.response_data or {}
    entries = response.get("media_manifest", [])
    recoverable_media = bool(entries) and (
        any(
            not item.get("saved") and (item.get("source_cipher") or item.get("locator"))
            for item in entries
        )
        or (
            all(item.get("saved") for item in entries)
            and len(entries) >= response.get("expected_count", 1)
        )
    )
    if task.locked_until and task.locked_until > utcnow():
        return None
    if (
        task.status == "failed"
        and record.status == "succeeded"
        and getattr(record, "text_content", None)
        and response.get("finish_reason") not in {"length", "max_output_tokens"}
        and (
            (task.error or {}).get("code")
            in {"business_save_failed", "archive_timeout", "message_delivery_unknown"}
            or (
                (task.error or {}).get("code") == "invalid_structured_output"
                and can_reparse_storyboard(record)
            )
        )
    ):
        return "save"
    if task.status == "failed" and record.status == "succeeded" and recoverable_media:
        return "save"
    if task.status != "failed" or (task.error or {}).get("code") != "message_delivery_unknown":
        return None
    if task.next_action == "submit" and record.status == "prepared" and not task.cancel_requested:
        return "submit"
    if task.next_action == "poll" and record.provider_task_id:
        return "poll"
    if task.next_action == "save" and record.status == "succeeded" and recoverable_media:
        return "save"
    return None


class AIGenerationService(BaseService):
    model = AsyncTask

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings
        self.dao = AsyncTaskDAO(session)
        self.record_dao = AIGenerationRecordDAO(session)

    @staticmethod
    def _hash(kind, payload, operation):
        encoded = json.dumps(
            {"kind": kind, "operation": operation, "request": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > 1048576:
            raise BusinessError("Generation request exceeds 1 MiB")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _key(key):
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 128
            or any(ord(c) < 32 for c in key)
        ):
            raise BusinessError("A nonempty Idempotency-Key of at most 128 characters is required")
        return key

    def _config(self, kind, identifier):
        if identifier:
            return self._validate_model(identifier, kind)
        model = self.session.scalar(
            select(AIModelConfig)
            .where(
                AIModelConfig.service_type == kind,
                AIModelConfig.is_default == 1,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
            .with_for_update()
        )
        if model is None:
            raise GenerationRequestError("default_config_missing")
        return model

    def _prepare(self, kind, payload):
        source = payload.get("source")
        if source and kind == "text":
            from .generation_context_service import GenerationContextService

            payload = GenerationContextService(self.session, self.settings).prepare_text(payload)
        elif source:
            if kind != "image" or source["scene"] not in {"shot_image", "asset_image"}:
                raise BusinessError("Source scene does not support this generation type")
            if source["scene"] == "asset_image":
                from .generation_context_service import GenerationContextService

                payload = GenerationContextService(self.session).prepare_asset_image(payload)
            elif source.get("context_mode") == "saved":
                from .generation_context_service import GenerationContextService

                payload = GenerationContextService(self.session).prepare_shot_image(payload)
            else:
                shot = self._require(ShotScript, source["shot_id"])
                episode = self._require(Episode, shot.episode_id)
                source.update(episode_id=str(episode.id), project_id=str(episode.project_id))
                payload["source_snapshot"] = {"script": shot.script, "position": shot.position}
        from .generation_presentation import generation_display_context

        payload["display_context"] = generation_display_context(self.session, payload)
        inputs = payload["input"]
        references = inputs.get("reference_media_ids", []) + [
            inputs[k] for k in ("first_frame_media_id", "last_frame_media_id") if inputs.get(k)
        ]
        if len(set(references)) > 16:
            raise WorkflowError("reference_limit_exceeded", "参考图片不能超过16张", 422)
        for identifier in references:
            media = self._validate_media(identifier, "image")
            if not media.storage_locator.startswith("minio://"):
                raise BusinessError("Reference media must be permanently stored")
        payload["resolved_parameters"] = copy.deepcopy(payload["parameters"])
        return payload

    def _summary(self, task, record=None):
        from .generation_presentation import generation_display_context

        records = self.record_dao.for_task(task.id, include_text=False)
        record = record or records[0]
        latest = records[-1]
        config = record.config_snapshot
        can_resume = bool(resume_action(task, latest))
        error = safe_error(task.error)
        if can_resume and error and error["code"] == "invalid_structured_output":
            error["message"] = "已保留的模型原文现可解析，请使用安全恢复保存结果，无需重新生成。"
        return {
            "generation_id": str(task.id),
            "service_type": task.service_type,
            "status": task.status,
            "next_action": task.next_action,
            "config": {
                "id": str(record.config_id),
                **{k: config.get(k) for k in ("name", "model_key", "provider")},
            },
            "source": record.request_data.get("source"),
            "display_context": record.request_data.get("display_context")
            or generation_display_context(self.session, record.request_data),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "error": error,
            "can_cancel": task.status in {"queued", "running"} and not task.cancel_requested,
            "can_retry": can_retry(task, latest),
            "can_resume": can_resume,
            "cancel_requested": bool(task.cancel_requested),
            "retry_of_id": str(task.retry_of_id) if task.retry_of_id else None,
        }

    def _existing(self, key, request_hash):
        task = self.dao.by_key(key)
        if task:
            if task.request_hash != request_hash:
                raise Conflict("Idempotency-Key was used for another request")
            return self._summary(task), False
        return None

    def _insert(self, kind, payload, key, digest, config, retry_of_id=None):
        now = utcnow()
        snapshot = {
            k: getattr(config, k)
            for k in ("base_url", "model_key", "provider", "service_type", "name")
        }
        snapshot.update(
            credential_identity=hashlib.sha256((config.apikey or "").encode()).hexdigest(),
            capability_cache=copy.deepcopy(config.capability_cache),
            row_version=str(config.row_version),
            budget_seconds=getattr(
                self.settings,
                f"generation_{kind}_budget_seconds",
                {"text": 3600, "image": 300, "video": 1800}[kind],
            ),
            archive_budget_seconds=getattr(
                self.settings, "generation_archive_budget_seconds", 86400
            ),
        )
        try:
            validated = validate_request(snapshot, payload)
        except GenerationError as error:
            raise GenerationRequestError(error.code) from None
        payload["resolved_parameters"] = validated["resolved_parameters"]
        task = self.dao.create(
            dict(
                service_type=kind,
                status="queued",
                idempotency_key=key,
                request_hash=digest,
                retry_of_id=retry_of_id,
                next_action="submit",
                next_run_at=now,
                message_status="pending",
                message_version=1,
                publish_count=0,
                cancel_requested=0,
                created_at=now,
                updated_at=now,
            )
        )
        record = self.record_dao.create(
            dict(
                task_id=task.id,
                call_no=1,
                config_id=config.id,
                config_snapshot=snapshot,
                request_data=payload,
                credential_cipher=config.apikey,
                adapter=validated["adapter"],
                status="prepared",
                created_at=now,
                updated_at=now,
            )
        )
        return self._summary(task, record), True

    def create(self, kind, payload, idempotency_key):
        if kind not in GENERATION_SCHEMAS:
            raise BusinessError("Unsupported generation type")
        parsed = GENERATION_SCHEMAS[kind].model_validate(payload)
        key = self._key(idempotency_key)
        digest = self._hash(kind, parsed.model_dump(mode="json", exclude_unset=True), "create")
        try:
            with self._transaction():
                existing = self._existing(key, digest)
                if existing:
                    return existing
                config = self._config(kind, parsed.config_id)
                request = self._prepare(kind, parsed.model_dump(mode="json", exclude_none=True))
                return self._insert(kind, request, key, digest, config)
        except Conflict:
            with self._transaction():
                existing = self._existing(key, digest)
                if existing:
                    return existing
            raise

    create_generation = create

    def list(self, offset=0, limit=20, filters=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise BusinessError("Invalid pagination")
        with self._transaction():
            rows, total = self.dao.history(filters or {}, offset, limit)
            return {
                "items": [self._summary(task, record) for task, record in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def detail(self, identifier):
        with self._transaction():
            task = self._require(AsyncTask, identifier, for_update=False)
            records = self.record_dao.for_task(task.id)
            latest = records[-1]
            output = self._summary(task, latest)
            output.update(
                input=records[0].request_data.get("input"),
                # Adoption and previews use the frozen business request, not
                # provider fields such as size/n or duration measured in seconds.
                parameters=records[0].request_data.get("parameters") or {},
                resolved_parameters=latest.request_data.get("resolved_parameters") or {},
            )
            assets = list(
                self.session.scalars(
                    select(MediaAsset)
                    .where(MediaAsset.record_id.in_([r.id for r in records]))
                    .order_by(MediaAsset.output_index)
                )
            )
            output["result"] = {
                "business": (latest.response_data or {}).get("business_result"),
                "text": {
                    "record_id": str(latest.id),
                    "content": latest.text_content,
                    "finish_reason": (latest.response_data or {}).get("finish_reason"),
                }
                if latest.text_content is not None
                else None,
                "assets": [
                    {
                        "asset_id": str(a.id),
                        "record_id": str(a.record_id),
                        "media_id": str(a.media_id),
                        "media_type": a.media_type,
                        "name": a.name,
                        "row_version": str(a.row_version),
                    }
                    for a in assets
                ],
                "partial": (task.error or {}).get("code") == "partial_result"
                or bool(assets and task.status == "failed"),
                "warnings": [
                    {
                        "code": "asset_source_missing",
                        "message": "原素材已不存在，图片已保存至媒体库",
                        "output_index": item["output_index"],
                    }
                    for item in (latest.response_data or {}).get("media_manifest", [])
                    if item.get("candidate_status") == "source_missing"
                ],
            }
            output["source_snapshot"] = records[0].request_data.get("source_snapshot")
            output["effective_prompt"] = records[0].request_data.get("input", {}).get("prompt")
            return output

    get = detail

    def records(self, identifier):
        with self._transaction():
            task = self._require(AsyncTask, identifier, for_update=False)
            return {
                "items": [
                    {
                        "record_id": str(r.id),
                        "call_no": r.call_no,
                        "status": r.status,
                        "adapter": r.adapter,
                        "provider_task_id": r.provider_task_id,
                        "created_at": r.created_at,
                        "started_at": r.started_at,
                        "finished_at": r.finished_at,
                        "error": safe_error(r.error),
                        "usage": {
                            k: v
                            for k, v in ((r.response_data or {}).get("usage") or {}).items()
                            if k
                            in {
                                "input_tokens",
                                "output_tokens",
                                "total_tokens",
                                "prompt_tokens",
                                "completion_tokens",
                            }
                            and isinstance(v, (int, float))
                        },
                        "finish_reason": (r.response_data or {}).get("finish_reason"),
                    }
                    for r in self.record_dao.for_task(task.id)
                ]
            }

    def cancel(self, identifier):
        with self._transaction():
            task = self._require(AsyncTask, identifier)
            record = self.record_dao.for_task(task.id)[-1]
            if task.status in {"queued", "running"}:
                task.cancel_requested = 1
                task.updated_at = utcnow()
                if task.status == "queued" and record.status == "prepared":
                    task.status = "cancelled"
                    task.finished_at = task.updated_at
                    task.message_version += 1
                    task.message_status = "idle"
                    task.next_action = task.next_run_at = task.lock_token = task.locked_until = None
                self.session.flush()
            return self._summary(task, record)

    def resume(self, identifier):
        with self._transaction():
            task = self._require(AsyncTask, identifier)
            record = self.record_dao.for_task(task.id)[-1]
            if task.status in {"queued", "running"}:
                return self._summary(task, record)
            action = resume_action(task, record)
            if action is None:
                raise Conflict("This task has no safe recovery action")
            task.status = "queued" if action == "submit" else "running"
            task.next_action = action
            task.message_version += 1
            task.message_status = "pending"
            task.publish_count = 0
            task.next_run_at = task.updated_at = utcnow()
            task.finished_at = task.lock_token = task.locked_until = task.error = None
            if action == "save":
                response = copy.deepcopy(record.response_data or {})
                response["archive_started_at"] = utcnow().isoformat()
                record.response_data = response
                record.updated_at = utcnow()
            self.session.flush()
            return self._summary(task, record)

    def retry(self, identifier, payload, idempotency_key):
        try:
            return self._retry(identifier, payload, idempotency_key)
        except Conflict:
            parsed = GenerationRetry.model_validate(payload or {})
            digest = self._hash(
                "retry",
                {
                    "generation_id": str(parse_identifier(identifier)),
                    **parsed.model_dump(mode="json", exclude_unset=True),
                },
                "retry",
            )
            with self._transaction():
                existing = self._existing(idempotency_key, digest)
                if existing:
                    return existing
            raise

    def _retry(self, identifier, payload, idempotency_key):
        parsed = GenerationRetry.model_validate(payload or {})
        identifier = parse_identifier(identifier)
        key = self._key(idempotency_key)
        digest = self._hash(
            "retry",
            {
                "generation_id": str(identifier),
                **parsed.model_dump(mode="json", exclude_unset=True),
            },
            "retry",
        )
        with self._transaction():
            existing = self._existing(key, digest)
            if existing:
                return existing
            task = self._require(AsyncTask, identifier)
            records = self.record_dao.for_task(task.id)
            if not can_retry(task, records[-1]):
                raise Conflict("This task cannot safely be regenerated")
            record = records[0]
            config = self._config(task.service_type, parsed.config_id or record.config_id)
            if parsed.config_id is None and str(config.row_version) != str(
                record.config_snapshot["row_version"]
            ):
                raise Conflict("Model configuration changed; choose it explicitly")
            request = copy.deepcopy(record.request_data)
            request["config_id"] = str(config.id)
            return self._insert(
                task.service_type, request, key, digest, config, retry_of_id=task.id
            )
