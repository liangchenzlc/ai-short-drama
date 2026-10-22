"""Transactional task admission and user actions; never performs model calls."""

import copy
import hashlib
import json

from sqlalchemy import select

from short_drama.ai import GenerationError, validate_request
from short_drama.ai.model_identity import model_credential_identity
from short_drama.core.exceptions import (
    BusinessError,
    ConfigurationError,
    Conflict,
    GenerationRequestError,
    WorkflowError,
)
from short_drama.dao.ai_generation_record_dao import AIGenerationRecordDAO
from short_drama.dao.async_task_dao import AsyncTaskDAO
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    MediaAsset,
    Project,
    ShotScript,
)
from short_drama.schemas.ai_generation import GENERATION_SCHEMAS, GenerationRetry
from short_drama.schemas.base import parse_identifier

from .base import BaseService, utcnow
from .generation_task_policy import (
    ERROR_MESSAGES as ERROR_MESSAGES,
)
from .generation_task_policy import (
    can_reparse_storyboard as can_reparse_storyboard,
)
from .generation_task_policy import (
    can_retry as can_retry,
)
from .generation_task_policy import (
    resume_action as resume_action,
)
from .generation_task_policy import (
    safe_error as safe_error,
)
from .generation_task_policy import (
    validate_idempotency_key,
)


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

    _key = staticmethod(validate_idempotency_key)

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

    def _lock_payload_scope(self, payload):
        actor = self.session.info.get("actor")
        from short_drama.db.access import require_project, scope_of, set_scope
        from short_drama.domain import Asset, ProjectNovel, ProjectScript

        source = payload.get("source") or {}
        if source.get("scene") == "image_operation":
            from short_drama.domain import ImageOperation

            operation = self._require(ImageOperation, source["operation_id"], for_update=False)
            scope = scope_of(self.session, operation)
            if scope[1]:
                require_project(self.session, scope[1])
            set_scope(self.session, scope)
            return
        project_id = source.get("project_id") or payload.get("project_id")
        scope = (None, int(project_id)) if project_id else (actor.user_id, None) if actor else None
        for field, model in [
            ("asset_id", Asset),
            ("shot_id", ShotScript),
            ("novel_id", ProjectNovel),
            ("script_id", ProjectScript),
        ]:
            if source.get(field):
                actual = scope_of(
                    self.session, self._require(model, source[field], for_update=False)
                )
                if project_id and actual[1] != int(project_id):
                    raise WorkflowError("not_found", "Source does not belong to this project", 404)
                scope = actual
                break
        if scope is None:
            return
        if scope[1]:
            require_project(self.session, scope[1])
        set_scope(self.session, scope)

    def _prepare(
        self, kind, payload, *, use_current_saved_script=False, include_saved_references=True
    ):
        explicit_references = copy.deepcopy(
            (payload.get("input") or {}).get("reference_media_ids", [])
        )
        source = payload.get("source")
        if source and source["scene"] == "image_operation":
            from .image_operation_service import ImageOperationService

            payload = ImageOperationService(self.session).prepare_generation(kind, payload)
        elif source and source["scene"] == "character_voice_design":
            from .native_voice_service import NativeVoiceService

            payload = NativeVoiceService(self.session, self.settings).prepare_design(payload)
        elif source and source["scene"] in {"dialogue_audio", "dialogue_extract"}:
            from .project_sound_service import ProjectSoundService

            sound = ProjectSoundService(self.session, self.settings)
            payload = (
                sound.prepare_speech(payload)
                if kind == "audio"
                else sound.prepare_extraction(payload)
            )
        elif source and kind == "text":
            from .generation_context_service import GenerationContextService

            payload = GenerationContextService(self.session, self.settings).prepare_text(
                payload, use_current_saved_script=use_current_saved_script
            )
        elif source and kind == "video" and source["scene"] == "shot_video":
            from .generation_context_service import GenerationContextService

            payload = GenerationContextService(self.session, self.settings).prepare_shot_video(
                payload
            )
        elif source:
            if kind != "image" or source["scene"] not in {"shot_image", "asset_image"}:
                raise BusinessError("Source scene does not support this generation type")
            if source["scene"] == "asset_image":
                from .generation_context_service import GenerationContextService

                payload = GenerationContextService(self.session).prepare_asset_image(payload)
            elif source.get("context_mode") == "saved":
                from .generation_context_service import GenerationContextService

                payload = GenerationContextService(self.session).prepare_shot_image(
                    payload, include_saved_references=include_saved_references
                )
            else:
                shot = self._require(ShotScript, source["shot_id"])
                creation = self._require(Project, shot.project_id)
                source.update(project_id=str(creation.id))
                payload["source_snapshot"] = {"script": shot.script, "position": shot.position}
        if kind == "image" and not include_saved_references:
            payload["input"]["reference_media_ids"] = explicit_references
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
        for identifier in inputs.get("audio_reference_media_ids", []):
            self._validate_media(identifier, "audio")
        return payload

    def _summary(self, task, record=None, *, records=None, owner_ids=None, display_context=None):
        from .generation_presentation import generation_display_context

        if records is None:
            records = self.record_dao.for_task(task.id, include_text=False)
        record = record or records[0]
        latest = records[-1]
        config = record.config_snapshot
        actor = self.session.info.get("actor")
        own_task = actor is None or task.initiated_by == actor.user_id
        owner_can_cancel = False
        if actor and task.project_id:
            owner_id = (
                owner_ids.get(task.project_id)
                if owner_ids is not None
                else self.session.scalar(
                    select(Project.owner_user_id).where(Project.id == task.project_id)
                )
            )
            owner_can_cancel = owner_id == actor.user_id
        can_resume = own_task and bool(resume_action(task, latest))
        error = safe_error(task.error)
        if can_resume and error and error["code"] == "invalid_structured_output":
            error["message"] = "已保留的模型原文现可解析，请使用安全恢复保存结果，无需重新生成。"
        return {
            "generation_id": str(task.id),
            "image_group_id": record.request_data.get("image_group_id"),
            "service_type": task.service_type,
            "status": task.status,
            "next_action": task.next_action,
            "config": {
                "id": str(record.config_id),
                **{k: config.get(k) for k in ("name", "model_key", "provider")},
            },
            "source": record.request_data.get("source"),
            "display_context": display_context
            or record.request_data.get("display_context")
            or generation_display_context(self.session, record.request_data),
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "error": error,
            "can_cancel": (own_task or owner_can_cancel)
            and task.status in {"queued", "running"}
            and not task.cancel_requested,
            "initiated_by": str(task.initiated_by) if task.initiated_by else None,
            "can_retry": own_task
            and can_retry(task, latest)
            and not record.config_snapshot.get("agent_managed")
            and not record.request_data.get("batch_id")
            and (record.request_data.get("source") or {}).get("scene")
            not in {"dialogue_audio", "character_voice_design"},
            "batch_id": record.request_data.get("batch_id"),
            "can_resume": can_resume,
            "cancel_requested": bool(task.cancel_requested),
            "retry_of_id": str(task.retry_of_id) if task.retry_of_id else None,
        }

    def _existing(self, key, request_hash):
        from short_drama.db.access import scoped_key

        key = scoped_key(self.session, key)
        task = self.dao.by_key(key)
        if task:
            if task.request_hash != request_hash:
                raise Conflict("Idempotency-Key was used for another request")
            return self._summary(task), False
        return None

    def _insert(
        self,
        kind,
        payload,
        key,
        digest,
        config,
        retry_of_id=None,
        *,
        agent_managed=False,
        assistant_managed=False,
    ):
        if getattr(config, "runtime_profile", None) is not None:
            from .canvas_model_catalog_service import CanvasModelCatalogService

            catalog = CanvasModelCatalogService(self.session, settings=self.settings)
            if catalog.refresh_runtime_model_locked(config):
                self.session.flush()
        from short_drama.db.access import require_project, scope_of, scoped_key, set_scope

        key = scoped_key(self.session, key)
        actor = self.session.info.get("actor")
        if actor:
            source = payload.get("source") or {}
            project_id = source.get("project_id") or payload.get("project_id")
            if source.get("asset_id"):
                from short_drama.domain import Asset

                resource = self._require(Asset, source["asset_id"], for_update=False)
                scope = scope_of(self.session, resource)
                if project_id and scope[1] != int(project_id):
                    raise WorkflowError("not_found", "Source does not belong to this project", 404)
            else:
                scope = (None, int(project_id)) if project_id else (actor.user_id, None)
            if scope[1]:
                require_project(self.session, scope[1])
            set_scope(self.session, scope)
            from short_drama.domain import MediaFile

            inputs = payload.get("input") or {}
            references = list(inputs.get("reference_media_ids", [])) + list(
                inputs.get("audio_reference_media_ids", [])
            )
            if source.get("scene") == "canvas_node":
                references += list(inputs.get("video_reference_media_ids", []))
            references += [
                inputs[k] for k in ("first_frame_media_id", "last_frame_media_id") if inputs.get(k)
            ]
            if source.get("scene") in {"canvas_node", "image_operation"}:
                parameters = (
                    payload.get("canvas_parameters") or payload.get("image_edit_parameters") or {}
                )
                if parameters.get("mode") == "image" and parameters.get("mask_media_id"):
                    references.append(parameters["mask_media_id"])
            for identifier in references:
                media = self._require(MediaFile, identifier, for_update=False)
                allowed_scopes = {scope}
                private_ids = (payload.get("creation_draft_snapshot") or {}).get(
                    "reference_media_ids", []
                )
                if str(identifier) in private_ids:
                    allowed_scopes.add((actor.user_id, None))
                if scope_of(self.session, media) not in allowed_scopes:
                    raise WorkflowError(
                        "not_found", "Import reference media into this scope first", 404
                    )
        now = utcnow()
        snapshot = {
            k: getattr(config, k)
            for k in ("base_url", "model_key", "provider", "service_type", "name")
        }
        snapshot.update(
            credential_identity=model_credential_identity(config),
            capability_cache=copy.deepcopy(config.capability_cache),
            row_version=str(config.row_version),
            budget_seconds=getattr(
                self.settings,
                f"generation_{kind}_budget_seconds",
                {"text": 3600, "image": 300, "video": 1800, "audio": 300}[kind],
            ),
            archive_budget_seconds=getattr(
                self.settings, "generation_archive_budget_seconds", 86400
            ),
        )
        if (payload.get("source") or {}).get("scene") in {
            "canvas_node",
            "canvas_model_test",
            "canvas_tool",
            "image_operation",
        }:
            capability = config.capability_cache or {}
            for field in (
                "canvas_channel_key",
                "canvas_video_capability",
                "canvas_video_variants",
                "canvas_text_capability",
                "canvas_text_defaults",
                "canvas_text_protocol",
                "canvas_audio_defaults",
                "canvas_runninghub_workflow",
            ):
                if field in capability:
                    snapshot[field] = copy.deepcopy(capability[field])
        operation = None
        if (payload.get("source") or {}).get("scene") == "image_operation":
            from short_drama.ai import capabilities
            from short_drama.domain import ImageOperation

            operation = self._require(ImageOperation, payload["source"]["operation_id"])
            support = capabilities(snapshot)
            if kind == "text" and not support.get("vision"):
                raise WorkflowError(
                    "vision_not_supported",
                    "当前文字模型未声明图片理解能力，请选择支持看图的模型",
                    422,
                )
            if kind == "text":
                from short_drama.ai.image_operations import validate_operation_vision
                from short_drama.domain import MediaFile

                media = self._require(MediaFile, operation.source_media_id, for_update=False)
                try:
                    validate_operation_vision(
                        snapshot, media.byte_size, payload["input"]["messages"][-1]["content"]
                    )
                except GenerationError as error:
                    raise GenerationRequestError(error.code) from None
            if kind == "image" and operation.mask_media_id and support.get("mask"):
                payload["image_edit_parameters"]["mask_media_id"] = str(operation.mask_media_id)
            if operation.operation_type == "mask" and not support.get("mask"):
                raise WorkflowError(
                    "mask_not_supported",
                    "当前模型不支持蒙版局部重绘，请切换支持蒙版的图片模型",
                    422,
                )
        try:
            validated = validate_request(
                snapshot,
                payload,
                "dashscope_voice_design.v1"
                if (payload.get("source") or {}).get("scene") == "character_voice_design"
                else None,
            )
        except GenerationError as error:
            raise GenerationRequestError(error.code) from None
        payload["resolved_parameters"] = validated["resolved_parameters"]
        if agent_managed:
            snapshot["agent_managed"] = True
        if assistant_managed:
            snapshot["assistant_managed"] = True
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
        if getattr(config, "runtime_profile", None) is not None or getattr(
            config, "runtime_credentials_cipher", None
        ):
            from .canvas_credential_freeze import freeze_model_credentials
            from .canvas_model_catalog_service import CanvasModelCatalogService

            catalog = CanvasModelCatalogService(self.session, settings=self.settings)
            secrets = catalog.runtime_credentials_locked(config.id, record.adapter)
            if secrets is None:
                try:
                    key_value = (
                        catalog.configs._key_cipher().decrypt(config.apikey)
                        if config.apikey
                        else ""
                    )
                except ValueError:
                    raise ConfigurationError("模型凭据无法解密") from None
                from .model_runtime_config import decrypt_runtime_credentials

                secrets = {
                    "apiKey": key_value,
                    **decrypt_runtime_credentials(config, catalog.configs._key_cipher()),
                }
            freeze_model_credentials(record, secrets, catalog.configs._key_cipher())
        from .image_group_service import ImageGroupService

        ImageGroupService(self.session).admit(task, record, assistant_managed=assistant_managed)
        if operation is not None:
            operation.task_id, operation.status = task.id, "running"
            self.session.flush()
        return self._summary(task, record), True

    def create(self, kind, payload, idempotency_key):
        if kind not in GENERATION_SCHEMAS:
            raise BusinessError("Unsupported generation type")
        parsed = GENERATION_SCHEMAS[kind].model_validate(payload)
        key = self._key(idempotency_key)
        digest = self._hash(kind, parsed.model_dump(mode="json", exclude_unset=True), "create")
        try:
            with self._transaction():
                return self.create_locked(
                    kind, parsed, key, request_hash=digest, use_current_saved_script=True
                )
        except Conflict:
            with self._transaction():
                existing = self._existing(key, digest)
                if existing:
                    return existing
            raise

    def create_locked(
        self,
        kind,
        payload,
        idempotency_key,
        *,
        request_hash=None,
        prepare_transform=None,
        use_current_saved_script=False,
        agent_managed=False,
        assistant_managed=False,
    ):
        """Admission core for an already authorized caller holding the project lock.

        Agent callers reserve budget and link the parent Tool in this same transaction.
        The public create method retains its own transaction and conflict replay contract.
        Saved-script policy is selected by trusted callers, never by request fields.
        """
        if not self.session.in_transaction() or kind not in GENERATION_SCHEMAS:
            raise BusinessError("Generation admission requires an active transaction")
        if isinstance(payload, GENERATION_SCHEMAS[kind]):
            payload = payload.model_dump(mode="json", exclude_unset=True)
        parsed = GENERATION_SCHEMAS[kind].model_validate(payload)
        key = self._key(idempotency_key)
        request_hash = request_hash or self._hash(
            kind, parsed.model_dump(mode="json", exclude_unset=True), "create"
        )
        existing = self._existing(key, request_hash)
        if existing:
            return existing
        self._lock_payload_scope(parsed.model_dump(mode="json", exclude_none=True))
        request = parsed.model_dump(mode="json", exclude_none=True)
        if (request.get("source") or {}).get("draft_id"):
            from .user_creation_draft_service import UserCreationDraftService

            request = UserCreationDraftService(self.session).freeze_for_generation(request)
        config = self._config(kind, request.get("config_id"))
        request = self._prepare(
            kind,
            request,
            use_current_saved_script=use_current_saved_script,
            include_saved_references=not assistant_managed,
        )
        if prepare_transform is not None:
            request = prepare_transform(request)
        return self._insert(
            kind,
            request,
            key,
            request_hash,
            config,
            agent_managed=agent_managed,
            assistant_managed=assistant_managed,
        )

    create_generation = create

    def lookup_by_key(self, kind, key):
        from short_drama.core.identity import require_actor
        from short_drama.db.access import scoped_key

        actor = require_actor(self.session)
        key = self._key(key)
        with self._transaction(read_only=True):
            task = self.dao.by_key(scoped_key(self.session, key))
            if task is None or task.service_type != kind or task.initiated_by != actor.user_id:
                return None
            return self._summary(task)

    def list(self, offset=0, limit=20, filters=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise BusinessError("Invalid pagination")
        with self._transaction(read_only=True):
            rows, total = self.dao.history(filters or {}, offset, limit)
            records = self.record_dao.for_tasks([task.id for task, _record in rows])
            actor = self.session.info.get("actor")
            project_ids = {task.project_id for task, _record in rows if task.project_id}
            owners = (
                dict(
                    self.session.execute(
                        select(Project.id, Project.owner_user_id).where(Project.id.in_(project_ids))
                    ).all()
                )
                if actor and project_ids
                else {}
            )
            # Resume decisions need full text only for a failed task's latest successful call.
            text_ids = [
                records[task.id][-1].id
                for task, _record in rows
                if task.status == "failed"
                and records[task.id][-1].status == "succeeded"
                and (actor is None or task.initiated_by == actor.user_id)
            ]
            if text_ids:
                self.session.scalars(
                    select(AIGenerationRecord).where(AIGenerationRecord.id.in_(text_ids))
                ).all()
            from .generation_presentation import generation_display_contexts

            historical = [
                (task.id, record.request_data)
                for task, record in rows
                if not record.request_data.get("display_context")
            ]
            contexts = dict(
                zip(
                    [identifier for identifier, _request in historical],
                    generation_display_contexts(
                        self.session, [request for _id, request in historical]
                    ),
                    strict=True,
                )
            )
            return {
                "items": [
                    self._summary(
                        task,
                        record,
                        records=records[task.id],
                        owner_ids=owners,
                        display_context=contexts.get(task.id),
                    )
                    for task, record in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def detail(self, identifier):
        with self._transaction(read_only=True):
            task = self._require(AsyncTask, identifier, for_update=False)
            records = self.record_dao.for_task(task.id)
            latest = records[-1]
            output = self._summary(task, latest, records=records)
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
                                "input_characters",
                                "audio_duration_ms",
                            }
                            and isinstance(v, (int, float))
                        },
                        "finish_reason": (r.response_data or {}).get("finish_reason"),
                    }
                    for r in self.record_dao.for_task(task.id)
                ]
            }

    def _authorize_task_action(self, task, action):
        actor = self.session.info.get("actor")
        if not actor or task.initiated_by == actor.user_id:
            return
        if action == "cancel" and task.project_id:
            from short_drama.db.access import require_project

            require_project(self.session, task.project_id, owner=True)
            return
        raise WorkflowError(
            "task_actor_required", "Only the task initiator can perform this action", 403
        )

    def cancel(self, identifier):
        with self._transaction():
            task = self._require(AsyncTask, identifier)
            self._authorize_task_action(task, "cancel")
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
                    from .image_group_service import ImageGroupService

                    ImageGroupService(self.session).task_state(task)
                self.session.flush()
            return self._summary(task, record)

    def resume(self, identifier):
        with self._transaction():
            task = self._require(AsyncTask, identifier)
            self._authorize_task_action(task, "resume")
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
            from .image_group_service import ImageGroupService

            ImageGroupService(self.session).task_state(task)
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
            self._authorize_task_action(task, "retry")
            records = self.record_dao.for_task(task.id)
            if not can_retry(task, records[-1]):
                raise Conflict("This task cannot safely be regenerated")
            record = records[0]
            if record.config_snapshot.get("agent_managed"):
                raise WorkflowError("agent_retry_requires_plan", "请在创作对话中重新授权生成", 409)
            if (record.request_data.get("source") or {}).get("scene") in {
                "dialogue_audio",
                "character_voice_design",
            }:
                raise WorkflowError(
                    "audio_retry_required", "请从角色声音或台词面板核对后重新生成", 409
                )
            if record.request_data.get("batch_id"):
                raise WorkflowError(
                    "batch_retry_required", "请从批次详情重新生成失败项，以保持并发限制", 409
                )
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
