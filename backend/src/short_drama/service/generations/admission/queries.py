from sqlalchemy import select

from short_drama.core.exceptions import (
    BusinessError,
)
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    MediaAsset,
    Project,
)
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


class GenerationQueries(GenerationServiceBase):
    def _summary(self, task, record=None, *, records=None, owner_ids=None, display_context=None):
        from short_drama.service.generations.results.presentation import generation_display_context

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
            from short_drama.service.generations.results.presentation import (
                generation_display_contexts,
            )

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
