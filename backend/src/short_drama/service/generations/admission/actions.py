import copy

from short_drama.core.exceptions import (
    Conflict,
    WorkflowError,
)
from short_drama.domain import (
    AsyncTask,
)
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.generations.admission.generation import GenerationRetry
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


class GenerationActions(GenerationServiceBase):
    def _authorize_task_action(self, task, action):
        actor = self.session.info.get("actor")
        if not actor or task.initiated_by == actor.user_id:
            return
        if action == "cancel" and task.project_id:
            from short_drama.db.scope.resource_scope import require_project

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
                    from short_drama.service.media.image_groups.groups import ImageGroupService

                    ImageGroupService(self.session).task_state(task)
                self.session.flush()
            return self._summary(task, record)

    def resume(self, identifier):
        with self._transaction():
            # Writing completion always takes project -> task -> document locks.
            preview = self._require(AsyncTask, identifier, for_update=False)
            preview_record = self.record_dao.for_task(preview.id)[-1]
            from short_drama.service.creation.writing.rewrite.generation import (
                WRITING_SCENES,
                WritingGenerationService,
            )

            if (preview_record.request_data.get("source") or {}).get("scene") in WRITING_SCENES:
                WritingGenerationService(self.session).validate_target(
                    preview_record.request_data, excluding_task=preview.id
                )
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
            from short_drama.service.media.image_groups.groups import ImageGroupService

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
                "script_rewrite",
            }:
                raise WorkflowError(
                    "writing_retry_requires_source", "请回到剧本工作台基于当前正文重新生成", 409
                )
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
