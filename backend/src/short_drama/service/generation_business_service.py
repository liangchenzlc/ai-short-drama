"""Persist local business projections; caller owns task fencing and transaction."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    Episode,
    EpisodeNovel,
    EpisodeScript,
    NovelScriptRecord,
    ScriptShotRecord,
)
from short_drama.schemas.asset_extraction import parse_extraction_result
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.storyboard_apply import StoryboardApply
from short_drama.schemas.storyboard_result import parse_storyboard_result

from .base import BaseService, utcnow


class GenerationBusinessService(BaseService):
    model = AsyncTask

    def save_text_result(self, task_id, record_id):
        record = self.session.get(AIGenerationRecord, record_id)
        if record is None or record.task_id != task_id:
            raise WorkflowError("source_missing", "生成记录不存在", 404)
        response = deepcopy(record.response_data or {})
        existing = response.get("business_result")
        if existing:
            return existing
        content = record.text_content or ""
        if not content.strip():
            raise WorkflowError("empty_result", "模型未返回正文", 422)
        if response.get("finish_reason") in {"length", "max_output_tokens"}:
            raise WorkflowError("text_truncated", "模型输出被截断", 422)
        source = record.request_data.get("source") or {}
        if source.get("scene") not in {"novel_script", "script_shots", "script_assets"}:
            return None
        snapshot = record.request_data["source_snapshot"]
        if source["scene"] == "script_assets":
            try:
                result = parse_extraction_result(content, snapshot)
            except ValueError:
                raise WorkflowError(
                    "invalid_structured_output", "素材提取格式或原文依据不正确，原始文本已保留", 422
                ) from None
        elif source["scene"] == "script_shots":
            try:
                parsed = parse_storyboard_result(
                    content,
                    {int(a["id"]) for a in snapshot["assets"]},
                    snapshot["content"],
                )
            except ValueError as error:
                code = (
                    "unknown_asset_reference"
                    if str(error) == "unknown_asset_reference"
                    else "invalid_structured_output"
                )
                raise WorkflowError(code, "分镜结果格式不正确，原始文本已保留", 422) from None
            result = {"kind": "script_shots", "schema_version": 1, **parsed, "applied": None}
        else:
            if len(content.encode("utf-8")) > 1048576:
                raise WorkflowError("invalid_structured_output", "剧本正文超过1 MiB", 422)
            episode = self.session.scalar(
                select(Episode)
                .where(
                    Episode.id == int(source["episode_id"]),
                    Episode.project_id == int(source["project_id"]),
                )
                .with_for_update()
            )
            novel = self.session.get(EpisodeNovel, int(source["novel_id"]))
            if episode is None or novel is None or novel.episode_id != episode.id:
                raise WorkflowError("source_missing", "生成来源已不存在", 404)
            maximum = (
                self.session.scalar(
                    select(EpisodeScript.position)
                    .where(EpisodeScript.episode_id == episode.id)
                    .order_by(EpisodeScript.position.desc())
                    .limit(1)
                )
                or 0
            )
            if maximum >= 2**32 - 1:
                raise WorkflowError("position_exhausted", "剧本排序空间已用尽")
            now = utcnow()
            # This creates a candidate, not the current editing document.
            script = BaseDAO(self.session, EpisodeScript).create(
                {
                    "episode_id": episode.id,
                    "position": maximum + 1,
                    "content": content,
                    "state": "unconfirmed",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            BaseDAO(self.session, NovelScriptRecord).create(
                {
                    "novel_id": novel.id,
                    "script_id": script.id,
                    "batch_id": task_id,
                    "model_id": record.config_id,
                    "created_at": now,
                }
            )
            result = {"kind": "novel_script", "schema_version": 1, "script_id": str(script.id)}
        response["business_result"] = result
        record.response_data = response
        record.updated_at = utcnow()
        self.session.flush()
        return result

    def apply_storyboard(self, project_id, episode_id, generation_id, payload):
        from .episode_storyboard_service import EpisodeStoryboardService
        from .generation_context_service import content_hash

        parsed = StoryboardApply.model_validate(payload)
        project_id, episode_id, generation_id = map(
            parse_identifier, (project_id, episode_id, generation_id)
        )
        with self._transaction():
            task = self._require(AsyncTask, generation_id)
            record = self.session.scalar(
                select(AIGenerationRecord)
                .where(AIGenerationRecord.task_id == generation_id)
                .order_by(AIGenerationRecord.call_no.desc())
                .limit(1)
            )
            source = (record.request_data.get("source") or {}) if record else {}
            if (
                source.get("scene") != "script_shots"
                or str(source.get("project_id")) != str(project_id)
                or str(source.get("episode_id")) != str(episode_id)
            ):
                raise WorkflowError("not_found", "此生成结果不属于当前分集", 404)
            storyboard = EpisodeStoryboardService(self.session)
            episode = storyboard.lock_episode(project_id, episode_id)
            data = deepcopy(record.response_data or {})
            result = data.get("business_result") or {}
            if task.status != "succeeded" or result.get("kind") != "script_shots":
                raise WorkflowError("result_not_ready", "分镜结果尚不可采用")
            applied = result.get("applied")
            if applied:
                if applied["mode"] != parsed.mode:
                    raise WorkflowError(
                        "result_already_applied", "该结果已经采用，不能换模式重复应用"
                    )
                return {
                    "generation_id": str(generation_id),
                    "mode": applied["mode"],
                    "shot_ids": applied["shot_ids"],
                    "storyboard_version": str(episode.storyboard_version),
                    "already_applied": True,
                }
            if episode.content_version != parsed.content_version:
                raise WorkflowError("writing_version_conflict", "剧本版本已变化，请重新核对")
            storyboard.lock_episode(project_id, episode_id, parsed.storyboard_version)
            script = self.session.scalar(
                select(EpisodeScript)
                .where(
                    EpisodeScript.id == int(source["script_id"]),
                    EpisodeScript.episode_id == episode_id,
                )
                .with_for_update()
            )
            if (
                script is None
                or script.id != episode.editing_script_id
                or script.state != "confirmed"
                or content_hash(script.content)
                != record.request_data["source_snapshot"]["content_hash"]
            ):
                raise WorkflowError("source_changed", "来源剧本已变化，请基于当前确认剧本重新生成")
            if parsed.mode == "replace":
                storyboard.archive_active_locked(storyboard.list_active_for_update(episode.id))
            rows = storyboard.create_generated_locked(episode, result["shots"])
            records = BaseDAO(self.session, ScriptShotRecord)
            now = utcnow()
            for row in rows:
                records.create(
                    {
                        "script_id": script.id,
                        "shot_id": row.id,
                        "batch_id": task.id,
                        "model_id": record.config_id,
                        "created_at": now,
                    }
                )
            storyboard.advance_storyboard_version(episode)
            result["applied"] = {
                "mode": parsed.mode,
                "shot_ids": [str(row.id) for row in rows],
                "applied_at": now.isoformat() + "Z",
                "storyboard_version": str(episode.storyboard_version),
            }
            data["business_result"] = result
            record.response_data = data
            self.session.flush()
            return {
                "generation_id": str(generation_id),
                "mode": parsed.mode,
                "shot_ids": result["applied"]["shot_ids"],
                "storyboard_version": str(episode.storyboard_version),
                "already_applied": False,
            }
