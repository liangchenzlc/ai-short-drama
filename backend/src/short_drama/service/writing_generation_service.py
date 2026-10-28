"""Freeze and atomically apply the three single-document writing operations."""

import hashlib
import json

from short_drama.ai.business_prompts import text_messages
from short_drama.ai.prompts.registry import PROMPT_VERSIONS
from short_drama.core.exceptions import WorkflowError
from short_drama.dao.project_writing_dao import ProjectWritingDAO
from short_drama.domain import AsyncTask
from short_drama.schemas.project_writing import writing_text

from .project_writing_service import ProjectWritingService

WRITING_SCENES = frozenset({"story_outline", "script_generate", "script_rewrite"})


def document_fingerprint(document):
    if document is None:
        return {"id": None, "hash": None}
    encoded = json.dumps(
        [document.content, getattr(document, "editor_document", None)],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return {"id": str(document.id), "hash": hashlib.sha256(encoded).hexdigest()}


class WritingGenerationService:
    def __init__(self, session):
        self.session = session
        self.dao = ProjectWritingDAO(session)
        self.writing = ProjectWritingService(session)

    def lock_project(self, request):
        return self.dao.scoped_project(int(request["source"]["project_id"]))

    def _target(self, creation, scene):
        if scene == "story_outline":
            return self.dao.outline(creation.id)
        if creation.editing_script_id is None:
            return None
        document = self.dao.script(creation.id, creation.editing_script_id)
        if self.writing._requires_agent_adoption(document.id):
            raise WorkflowError(
                "agent_artifact_adoption_required", "请先在原会话采用此 Agent 剧本候选", 422
            )
        return document

    def prepare(self, request):
        source = request["source"]
        creation = self.lock_project(request)
        if creation.content_version != int(source["content_version"]):
            raise WorkflowError("writing_version_conflict", "正文已变化，请刷新后生成", 409)
        scene = source["scene"]
        target = "outline" if scene == "story_outline" else "script"
        self.dao.require_idle(creation.id, target)
        document = self._target(creation, scene)
        snapshot = {
            "target": document_fingerprint(document),
            "content_version": str(creation.content_version),
        }
        if scene == "script_rewrite":
            if document is None or str(document.id) != source["script_id"]:
                raise WorkflowError("writing_target_changed", "当前剧本已变化，请重新选择文字", 409)
            start, end = source["start"], source["end"]
            if (
                end > len(document.content)
                or document.content[start:end] != source["selected_text"]
            ):
                raise WorkflowError("writing_selection_changed", "选中文字已变化，请重新选择", 409)
            snapshot.update(
                selected_text=source["selected_text"],
                before=document.content[max(0, start - 2000) : start],
                after=document.content[end : end + 2000],
            )
        else:
            kind = source["input_kind"]
            origin = (
                self.dao.novel(creation.id)
                if kind == "novel"
                else self.dao.outline(creation.id)
                if kind == "outline"
                else None
            )
            content = source.get("idea", "") if kind == "idea" else origin.content if origin else ""
            if not content.strip():
                raise WorkflowError("writing_source_empty", "请先填写并保存所选来源", 422)
            snapshot.update(input_kind=kind, content=content)
        instructions = request.pop("instructions", "")
        request["source_snapshot"] = snapshot
        request["business_intent"] = {"instructions": instructions}
        request["template_version"] = PROMPT_VERSIONS[scene]
        request["input"] = {"messages": text_messages(scene, snapshot, instructions)}
        return request

    def validate_target(self, request, *, excluding_task=None):
        creation = self.lock_project(request)
        scene = request["source"]["scene"]
        self.dao.require_idle(
            creation.id,
            "outline" if scene == "story_outline" else "script",
            excluding_task=excluding_task,
        )
        document = self._target(creation, scene)
        if document_fingerprint(document) != request["source_snapshot"]["target"]:
            raise WorkflowError("writing_target_changed", "目标正文已变化，生成结果未覆盖原文", 409)
        return creation, document

    def apply(self, record, content):
        creation, document = self.validate_target(
            record.request_data, excluding_task=record.task_id
        )
        task = self.session.get(AsyncTask, record.task_id)
        if task is None or task.cancel_requested or task.status not in {"queued", "running"}:
            raise WorkflowError("generation_cancelled", "任务已停止，正文未被覆盖", 409)
        if task.initiated_by is not None and creation.owner_user_id != task.initiated_by:
            raise WorkflowError("writing_source_inaccessible", "作品归属已变化，正文未被覆盖", 409)
        source = record.request_data["source"]
        if source["scene"] == "script_rewrite":
            start, end = source["start"], source["end"]
            if document is None or document.content[start:end] != source["selected_text"]:
                raise WorkflowError("writing_selection_changed", "原文选区已变化，结果未覆盖", 409)
            content = document.content[:start] + content + document.content[end:]
        try:
            writing_text(content)
        except ValueError as error:
            raise WorkflowError("writing_result_too_large", str(error), 422) from error
        if source["scene"] == "story_outline":
            document = self.writing.write_outline(creation, content)
        else:
            document = self.writing.write_script(
                creation, document, content, source=source["scene"]
            )
        self.writing._changed(creation)
        return {
            "kind": source["scene"],
            "schema_version": 1,
            "document_id": str(document.id),
            "content_version": str(creation.content_version),
        }
