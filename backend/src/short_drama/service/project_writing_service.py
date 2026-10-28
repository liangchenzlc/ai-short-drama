"""Atomic editor operations; no model requests or browser workflow persistence."""

from sqlalchemy import func, inspect, select

from short_drama.core.exceptions import BusinessError, Conflict, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.project_writing_dao import ProjectWritingDAO, bump_writing_version
from short_drama.domain import (
    AsyncTask,
    NovelScriptRecord,
    Project,
    ProjectNovel,
    ProjectScript,
    ProjectScriptRevision,
    ProjectStoryOutline,
    ProjectStorySettings,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.project_writing import (
    NovelSave,
    NovelSaved,
    OutlineSave,
    OutlineSaved,
    ScriptRevisionDetail,
    ScriptRevisionRead,
    ScriptSave,
    ScriptSaved,
    ScriptSelect,
    StorySettingsRead,
    StorySettingsSave,
    WritingDocument,
    WritingRead,
    WritingScript,
    WritingVersion,
)
from short_drama.utils.snowflake import next_id

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
        outline = self.dao.outline(creation.id, for_update=for_update)
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
            outline=WritingDocument.model_validate(outline) if outline else None,
            editing_script=WritingScript.model_validate(script) if script else None,
            confirmed_script_id=confirmed.id if confirmed else None,
            active_writing_tasks=self.dao.active_tasks(creation.id),
        ).model_dump(mode="json")

    def get(self, project_id):
        with self._transaction(read_only=True):
            return self._view(self._scope(project_id, for_update=False), for_update=False)

    def story_settings(self, project_id):
        with self._transaction(read_only=True):
            creation = self._scope(project_id, for_update=False)
            row = self.dao.story_settings(creation.id)
            view = StorySettingsRead.model_validate(row) if row else StorySettingsRead()
            return view.model_dump(mode="json")

    def save_story_settings(self, project_id, payload):
        data = self._payload(StorySettingsSave, payload)
        version = data.pop("row_version")
        with self._transaction():
            creation = self._scope(project_id)
            row = self.dao.story_settings(creation.id, for_update=True)
            if version != (row.row_version if row else 0):
                raise WorkflowError(
                    "story_settings_version_conflict",
                    "故事设定已变化，本机输入已保留，请核对版本",
                    409,
                )
            if version >= UINT64_MAX:
                raise Conflict("Story settings version exhausted")
            now = utcnow()
            if row is None:
                row = ProjectStorySettings(
                    project_id=creation.id, row_version=1, created_at=now, updated_at=now, **data
                )
                self.session.add(row)
            else:
                for field, value in data.items():
                    setattr(row, field, value)
                row.row_version += 1
                row.updated_at = now
            self.session.flush()
            return StorySettingsRead.model_validate(row).model_dump(mode="json")

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

    def save_outline(self, project_id, payload):
        data = self._payload(OutlineSave, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            self.dao.require_idle(creation.id, "outline")
            outline = self.write_outline(creation, data["content"])
            self._changed(creation)
            return OutlineSaved(
                content_version=creation.content_version, outline=outline
            ).model_dump(mode="json")

    def write_outline(self, creation, content):
        """Caller holds the project mutex and owns version/task completion."""
        outline = self.dao.outline(creation.id)
        if outline is None:
            return self._create_document(ProjectStoryOutline, creation.id, content)
        if outline.content != content:
            outline.content, outline.updated_at, outline.updated_by = content, utcnow(), None
        return outline

    def write_script(self, creation, script, content, editor_document=None, *, source=None):
        """Write the single current document, including downstream availability."""
        before_content = script.content if script else ""
        before_document = script.editor_document if script else None
        if script is not None and self._requires_agent_adoption(script.id):
            raise WorkflowError(
                "agent_artifact_adoption_required",
                "此剧本是尚未采用的 Agent 候选，请在原会话的创作候选中核对并采用。",
                422,
            )
        previous = self.dao.confirmed(creation.id)
        if previous is not None and (
            script is None or previous.id != script.id or not content.strip()
        ):
            previous.state, previous.updated_at, previous.updated_by = "unconfirmed", utcnow(), None
            self.session.flush()  # Release the unique confirmation before moving it.
        state = "confirmed" if content.strip() else "unconfirmed"
        if script is None:
            script = self._create_document(
                ProjectScript,
                creation.id,
                content,
                position=self.dao.next_position(creation.id),
                state=state,
                editor_document=editor_document,
            )
            creation.editing_script_id = script.id
        else:
            if (script.content, script.editor_document, script.state) != (
                content,
                editor_document,
                state,
            ):
                script.content, script.editor_document, script.state = (
                    content,
                    editor_document,
                    state,
                )
                script.updated_at, script.updated_by = utcnow(), None
            publish(script)
        if source and (before_content, before_document) != (content, editor_document):
            actor = self.session.info.get("actor")
            self.session.add(
                ProjectScriptRevision(
                    id=next_id(),
                    project_id=creation.id,
                    script_id=script.id,
                    before_version=creation.content_version,
                    after_version=creation.content_version + 1,
                    source=source,
                    before_content=before_content,
                    after_content=content,
                    before_document=before_document,
                    after_document=editor_document,
                    created_at=utcnow(),
                    created_by=actor.user_id if actor else None,
                )
            )
        return script

    def save_script(self, project_id, payload):
        data = self._payload(ScriptSave, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            self.dao.require_idle(creation.id, "script")
            script = self.dao.script(creation.id, data["script_id"]) if data["script_id"] else None
            if creation.editing_script_id != data["script_id"]:
                raise Conflict("The current editing script has changed")
            script = self.write_script(
                creation,
                script,
                data["content"],
                data.get("editor_document"),
                source="import" if data.get("change_source") == "import" else None,
            )
            self._changed(creation)
            return ScriptSaved(
                content_version=creation.content_version,
                script=script,
                confirmed_script_id=script.id if script.state == "confirmed" else None,
            ).model_dump(mode="json")

    def script_revisions(self, project_id, offset=0, limit=20, revision_id=None):
        with self._transaction(read_only=True):
            creation = self._scope(project_id, for_update=False)
            if revision_id is not None:
                row = self.dao.revision(creation.id, parse_identifier(revision_id))
                return ScriptRevisionDetail.model_validate(row).model_dump(mode="json")
            return {
                "items": [
                    ScriptRevisionRead.model_validate(row).model_dump(mode="json")
                    for row in self.dao.revisions(creation.id, offset, limit)
                ]
            }

    def restore_script_revision(self, project_id, revision_id, payload):
        data = self._payload(WritingVersion, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            self.dao.require_idle(creation.id, "script")
            revision = self.dao.revision(creation.id, parse_identifier(revision_id))
            script = (
                self.dao.script(creation.id, creation.editing_script_id)
                if creation.editing_script_id
                else None
            )
            self.write_script(
                creation,
                script,
                revision.before_content,
                revision.before_document,
                source="restore",
            )
            self._changed(creation)
            return self._view(creation)

    def select_script(self, project_id, payload):
        data = self._payload(ScriptSelect, payload)
        with self._transaction():
            creation = self._scope(project_id, data["content_version"])
            self.dao.require_idle(creation.id, "script")
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
            self.dao.require_idle(creation.id, "script")
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
