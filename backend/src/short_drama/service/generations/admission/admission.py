import copy

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
from short_drama.domain import (
    AIModelConfig,
    Project,
    ShotScript,
)
from short_drama.schemas.generations.admission.generation import GENERATION_SCHEMAS
from short_drama.service.base import utcnow
from short_drama.service.generations.admission.base import GenerationServiceBase
from short_drama.service.generations.admission.task_policy import (
    ERROR_MESSAGES as ERROR_MESSAGES,
)
from short_drama.service.generations.admission.task_policy import (
    can_reparse_storyboard as can_reparse_storyboard,
)
from short_drama.service.generations.admission.task_policy import (
    can_retry as can_retry,
)
from short_drama.service.generations.admission.task_policy import (
    resume_action as resume_action,
)
from short_drama.service.generations.admission.task_policy import (
    safe_error as safe_error,
)


class GenerationAdmission(GenerationServiceBase):
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
        from short_drama.db.access.identity import set_scope
        from short_drama.db.scope.resource_scope import require_project, scope_of
        from short_drama.domain import Asset, ProjectScript

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
            from short_drama.service.media.image_tools.operations import ImageOperationService

            payload = ImageOperationService(self.session).prepare_generation(kind, payload)
        elif source and source["scene"] == "character_voice_design":
            from short_drama.service.creation.sound.voices.voices import NativeVoiceService

            payload = NativeVoiceService(self.session, self.settings).prepare_design(payload)
        elif source and source["scene"] in {"dialogue_audio", "dialogue_extract"}:
            from short_drama.service.creation.sound.tracks.sound import ProjectSoundService

            sound = ProjectSoundService(self.session, self.settings)
            payload = (
                sound.prepare_speech(payload)
                if kind == "audio"
                else sound.prepare_extraction(payload)
            )
        elif source and kind == "text":
            from short_drama.service.generations.admission.context import GenerationContextService

            payload = GenerationContextService(self.session, self.settings).prepare_text(
                payload, use_current_saved_script=use_current_saved_script
            )
        elif source and kind == "video" and source["scene"] == "shot_video":
            from short_drama.service.generations.admission.context import GenerationContextService

            payload = GenerationContextService(self.session, self.settings).prepare_shot_video(
                payload
            )
        elif source:
            if kind != "image" or source["scene"] not in {"shot_image", "asset_image"}:
                raise BusinessError("Source scene does not support this generation type")
            if source["scene"] == "asset_image":
                from short_drama.service.generations.admission.context import (
                    GenerationContextService,
                )

                payload = GenerationContextService(self.session).prepare_asset_image(payload)
            elif source.get("context_mode") == "saved":
                from short_drama.service.generations.admission.context import (
                    GenerationContextService,
                )

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
        from short_drama.service.generations.results.presentation import generation_display_context

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

    def _existing(self, key, request_hash):
        from short_drama.db.access.identity import scoped_key

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
            from short_drama.service.canvas.models.model_catalog import CanvasModelCatalogService

            catalog = CanvasModelCatalogService(self.session, settings=self.settings)
            if catalog.refresh_runtime_model_locked(config):
                self.session.flush()
        from short_drama.db.access.identity import scoped_key, set_scope
        from short_drama.db.scope.resource_scope import require_project, scope_of

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
                from short_drama.ai.adapters.image.image_operations import validate_operation_vision
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
            from short_drama.service.canvas.generation.credential_freeze import (
                freeze_model_credentials,
            )
            from short_drama.service.canvas.models.model_catalog import CanvasModelCatalogService

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
                from short_drama.service.ai_config.management.runtime import (
                    decrypt_runtime_credentials,
                )

                secrets = {
                    "apiKey": key_value,
                    **decrypt_runtime_credentials(config, catalog.configs._key_cipher()),
                }
            freeze_model_credentials(record, secrets, catalog.configs._key_cipher())
        from short_drama.service.media.image_groups.groups import ImageGroupService

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
        """在已授权且持有项目锁的事务内受理生成。

        Agent 的预算预留、父工具关联和任务落库必须共享当前事务，拆文件不能各自提交。
        HTTP 入口仍由 create 管理事务和冲突重放；剧本来源策略只由可信调用方选择。
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
            from short_drama.service.creation.drafts.drafts import UserCreationDraftService

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

    def lookup_by_key(self, kind, key):
        from short_drama.core.identity import require_actor
        from short_drama.db.access.identity import scoped_key

        actor = require_actor(self.session)
        key = self._key(key)
        with self._transaction(read_only=True):
            task = self.dao.by_key(scoped_key(self.session, key))
            if task is None or task.service_type != kind or task.initiated_by != actor.user_id:
                return None
            return self._summary(task)
