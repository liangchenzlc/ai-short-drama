"""Atomic editor operations; no model requests or browser workflow persistence."""

from sqlalchemy import func, inspect, select

from short_drama.core.exceptions import BusinessError, Conflict, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.project_writing_dao import ProjectWritingDAO, bump_writing_version
from short_drama.domain import AsyncTask, NovelScriptRecord, Project, ProjectNovel, ProjectScript
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.project_writing import (
    NovelSave,
    NovelSaved,
    ScriptSave,
    ScriptSaved,
    ScriptSelect,
    WritingDocument,
    WritingRead,
    WritingScript,
    WritingVersion,
)

from .base import BaseService, utcnow
from .publication import publish


class EmptyScript(BusinessError):
    status_code = 422
    code = "empty_script"


class ProjectWritingService(BaseService):
    model = Project

    def __init__(self, session):
        super().__init__(session)
        self.dao = ProjectWritingDAO(session)
        self._has_agent_artifacts: bool | None = None

    def _unadopted_agent_script(self, script_id):
        # Prompt-only installations can legitimately lack Agent tables. Detect
        # the table on the transaction's connection; disabling execution must
        # not disable adoption protection when existing candidates are present.
        from short_drama.domain.agent import AgentArtifact

        if self._has_agent_artifacts is None:
            self._has_agent_artifacts = inspect(self.session.connection()).has_table(
                AgentArtifact.__tablename__
            )
        if not self._has_agent_artifacts:
            return None
        # This provenance predicate must see other authors' artifacts without
        # loading their private data. The enclosing ProjectScript ORM query
        # still enforces actor/project visibility.
        artifacts = AgentArtifact.__table__.alias("agent_script_provenance")
        return (
            select(artifacts.c.id)
            .where(artifacts.c.script_id == script_id, artifacts.c.status != "applied")
            .exists()
        )

    def _requires_agent_adoption(self, script_id: int) -> bool:
        unadopted = self._unadopted_agent_script(ProjectScript.id)
        if unadopted is None:
            return False
        return bool(
            self.session.scalar(
                select(unadopted).select_from(ProjectScript).where(ProjectScript.id == script_id)
            )
        )

    def _scope(self, project_id, version=None, *, for_update=True):
        creation = self.dao.scoped_project(parse_identifier(project_id), for_update=for_update)
        if version is not None and creation.content_version != version:
            raise Conflict("Content changed elsewhere; reload before saving")
        return creation

    def _view(self, creation, *, for_update=True):
        novel = self.dao.novel(creation.id, for_update=for_update)
        script = (
            self.dao.script(creation.id, creation.editing_script_id, for_update=for_update)
            if creation.editing_script_id
            else None
        )
        confirmed = self.dao.confirmed(creation.id, for_update=for_update)
        return WritingRead(
            project_id=creation.id,
            content_version=creation.content_version,
            novel=WritingDocument.model_validate(novel) if novel else None,
            editing_script=WritingScript.model_validate(script) if script else None,
            confirmed_script_id=confirmed.id if confirmed else None,
        ).model_dump(mode="json")

    def get(self, project_id):
        with self._transaction(read_only=True):
            return self._view(self._scope(project_id, for_update=False), for_update=False)

    def candidates(self, project_id, offset=0, limit=20, script_id=None):
        with self._transaction(read_only=True):
            creation = self._scope(project_id, for_update=False)
            statement = (
                select(ProjectScript, AsyncTask.id)
                .outerjoin(NovelScriptRecord, NovelScriptRecord.script_id == ProjectScript.id)
                .outerjoin(AsyncTask, AsyncTask.id == NovelScriptRecord.batch_id)
                .where(ProjectScript.project_id == creation.id)
            )
            actor = self.session.info.get("actor")
            if script_id is None:
                unadopted = self._unadopted_agent_script(ProjectScript.id)
                if unadopted is not None:
                    statement = statement.where(~unadopted)
            if actor and script_id is None:
                statement = statement.where(ProjectScript.created_by == actor.user_id)
            if script_id is not None:
                statement = statement.where(ProjectScript.id == parse_identifier(script_id))
            total = self.session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = self.session.execute(
                statement.order_by(ProjectScript.position.desc()).offset(offset).limit(limit)
            )
            items = [
                {
                    "id": str(row.id),
                    "position": row.position,
                    "state": row.state,
                    "preview": row.content[:200],
                    "is_editing": row.id == creation.editing_script_id,
                    "is_confirmed": row.state == "confirmed",
                    "generation_id": str(task_id) if task_id else None,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    **({"content": row.content} if script_id is not None else {}),
                }
                for row, task_id in rows
            ]
            if script_id is not None:
                if not items:
                    from short_drama.core.exceptions import NotFound

                    raise NotFound("剧本不存在于当前作品")
                return items[0]
            return {"items": items, "total": total, "offset": offset, "limit": limit}

    def _create_document(self, model, project_id, content, **extra):
        now = utcnow()
        document = BaseDAO(self.session, model).create(
            {
                "project_id": project_id,
                "content": content,
                "created_at": now,
                "updated_at": now,
                "created_by": None,
                "updated_by": None,
                **extra,
            }
        )
        return publish(document) if model is ProjectScript else document

    def _changed(self, creation):
        bump_writing_version(creation)
        self.session.flush()

    def save_novel(self, project_id, payload):
        data = self._payload(NovelSave, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            novel = self.dao.novel(creation.id)
            if novel is None:
                novel = self._create_document(ProjectNovel, creation.id, data["content"])
            elif novel.content != data["content"]:
                novel.content, novel.updated_at, novel.updated_by = data["content"], utcnow(), None
            # An accepted save also fences older in-flight writes when content is unchanged.
            self._changed(creation)
            return NovelSaved(content_version=creation.content_version, novel=novel).model_dump(
                mode="json"
            )

    def save_script(self, project_id, payload):
        data = self._payload(ScriptSave, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            script = self.dao.script(creation.id, data["script_id"]) if data["script_id"] else None
            if creation.editing_script_id != data["script_id"]:
                raise Conflict("The current editing script has changed")
            if script is not None:
                if self._requires_agent_adoption(script.id):
                    raise WorkflowError(
                        "agent_artifact_adoption_required",
                        "此剧本是尚未采用的 Agent 候选，请在原会话的创作候选中核对并采用。",
                        422,
                    )
            if script is None:
                script = self._create_document(
                    ProjectScript,
                    creation.id,
                    data["content"],
                    position=self.dao.next_position(creation.id),
                    state="unconfirmed",
                    editor_document=data.get("editor_document"),
                )
                creation.editing_script_id = script.id
            elif script.content != data["content"] or script.editor_document != data.get(
                "editor_document"
            ):
                if script.content != data["content"]:
                    script.content, script.state = data["content"], "unconfirmed"
                script.editor_document = data.get("editor_document")
                script.updated_at, script.updated_by = utcnow(), None
            self._changed(creation)
            return ScriptSaved(content_version=creation.content_version, script=script).model_dump(
                mode="json"
            )

    def select_script(self, project_id, payload):
        data = self._payload(ScriptSelect, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            target = self.dao.script(creation.id, data["script_id"])
            if self._requires_agent_adoption(target.id):
                raise WorkflowError(
                    "agent_artifact_adoption_required",
                    "此剧本是尚未采用的 Agent 候选，请在原会话的创作候选中核对并采用。",
                    422,
                )
            publish(target)
            if creation.editing_script_id != target.id:
                creation.editing_script_id = target.id
                self._changed(creation)
            return self._view(creation)

    def confirm(self, project_id, script_id, payload):
        data = self._payload(WritingVersion, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            target = self.dao.script(creation.id, parse_identifier(script_id))
            if creation.editing_script_id != target.id:
                raise Conflict("Only the current editing script can be confirmed")
            if self._requires_agent_adoption(target.id):
                raise WorkflowError(
                    "agent_artifact_adoption_required",
                    "此剧本是尚未采用的 Agent 候选，请在原会话的创作候选中核对并采用。",
                    422,
                )
            if not target.content.strip():
                raise EmptyScript("请先填写剧本正文再确认")
            if target.state != "confirmed":
                previous = self.dao.confirmed(creation.id)
                if previous is not None:
                    previous.state, previous.updated_at, previous.updated_by = (
                        "unconfirmed",
                        utcnow(),
                        None,
                    )
                    self.session.flush()  # Release the unique confirmed_project_id first.
                target.state, target.updated_at, target.updated_by = "confirmed", utcnow(), None
                self._changed(creation)
            return self._view(creation)
