"""Project-scoped reads and versioned internal document writes."""

from sqlalchemy import select
from sqlalchemy.orm import load_only

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    Project,
    ProjectNovel,
    ProjectScript,
    ProjectScriptRevision,
    ProjectStoryOutline,
    ProjectStorySettings,
)
from short_drama.schemas.base import UINT64_MAX

from .base import BaseDAO


def bump_writing_version(creation):
    if creation.content_version >= UINT64_MAX:
        raise Conflict("Writing version exhausted")
    creation.content_version += 1


class ProjectWritingDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, Project)

    def scoped_project(self, project_id, *, for_update=True):
        # All document writers share this mutex, including first insert/confirmation.
        statement = (
            select(Project)
            .where(
                Project.id == project_id,
                Project.workspace_mode == "standard",
                Project.archived_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise NotFound("Project does not exist in this project")
        return row

    def novel(self, project_id, *, for_update=True):
        statement = (
            select(ProjectNovel)
            .where(ProjectNovel.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def outline(self, project_id, *, for_update=True):
        statement = (
            select(ProjectStoryOutline)
            .where(ProjectStoryOutline.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement.with_for_update() if for_update else statement)

    def story_settings(self, project_id, *, for_update=False):
        statement = (
            select(ProjectStorySettings)
            .where(ProjectStorySettings.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement.with_for_update() if for_update else statement)

    def active_tasks(self, project_id, *, for_update=False):
        # Writers need a current read after waiting for the parent mutex. A plain
        # read could retain the pre-admission REPEATABLE READ snapshot in MySQL.
        scene = AIGenerationRecord.request_data["source"]["scene"].as_string()
        statement = (
            select(AsyncTask.id, AsyncTask.status, scene)
            .join(AIGenerationRecord, AIGenerationRecord.task_id == AsyncTask.id)
            .where(
                AsyncTask.project_id == project_id,
                AsyncTask.status.in_(("queued", "running")),
                scene.in_(("story_outline", "script_generate", "script_rewrite")),
            )
            .distinct()
        )
        rows = self.session.execute(statement.with_for_update() if for_update else statement)
        return [
            {
                "task_id": str(task_id),
                "status": status,
                "operation": operation,
                "target": "outline" if operation == "story_outline" else "script",
            }
            for task_id, status, operation in rows
        ]

    def require_idle(self, project_id, target, *, excluding_task=None):
        if any(
            row["target"] == target and row["task_id"] != str(excluding_task)
            for row in self.active_tasks(project_id, for_update=True)
        ):
            raise WorkflowError(
                "writing_target_busy", "此文档正在由 AI 修改，请等待完成或停止任务后再编辑", 409
            )

    def script(self, project_id, script_id, *, for_update=True):
        statement = (
            select(ProjectScript)
            .where(ProjectScript.id == script_id, ProjectScript.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise NotFound("Script does not exist in this creation")
        return row

    def confirmed(self, project_id, *, for_update=True):
        statement = (
            select(ProjectScript)
            .where(ProjectScript.project_id == project_id, ProjectScript.state == "confirmed")
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def next_position(self, project_id):
        maximum = (
            self.session.scalar(
                select(ProjectScript.position)
                .where(ProjectScript.project_id == project_id)
                .order_by(ProjectScript.position.desc())
                .limit(1)
                .with_for_update()
            )
            or 0
        )
        if maximum == 2**32 - 1:
            raise Conflict("Script ordering space exhausted")
        return maximum + 1

    def revisions(self, project_id, offset=0, limit=20):
        return self.session.scalars(
            select(ProjectScriptRevision)
            .options(
                load_only(
                    ProjectScriptRevision.id,
                    ProjectScriptRevision.script_id,
                    ProjectScriptRevision.before_version,
                    ProjectScriptRevision.after_version,
                    ProjectScriptRevision.source,
                    ProjectScriptRevision.created_at,
                )
            )
            .where(ProjectScriptRevision.project_id == project_id)
            .order_by(ProjectScriptRevision.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()

    def revision(self, project_id, identifier):
        row = self.session.scalar(
            select(ProjectScriptRevision).where(
                ProjectScriptRevision.project_id == project_id,
                ProjectScriptRevision.id == identifier,
            )
        )
        if row is None:
            raise NotFound("剧本变更记录不存在")
        return row


class VersionedDocumentDAO(BaseDAO):
    """Internal CRUD/generation services also invalidate open editor snapshots.

    Services own the enclosing transaction and lock creation before child records.
    Direct writing API uses BaseDAO and increments once for its aggregate mutation.
    """

    def _creation(self, project_id):
        creation = BaseDAO(self.session, Project).get(project_id, for_update=True)
        if creation is None:
            raise NotFound("Project does not exist")
        return creation

    def _guard_target(self, creation, entity=None):
        target = "outline" if self.model is ProjectStoryOutline else "script"
        if self.model is ProjectStoryOutline or (
            self.model is ProjectScript
            and (entity is None or creation.editing_script_id == entity.id)
        ):
            ProjectWritingDAO(self.session).require_idle(creation.id, target)

    def create(self, values):
        creation = self._creation(values["project_id"])
        self._guard_target(creation)
        row = super().create(values)
        if self.model is ProjectScript and creation.editing_script_id is None:
            creation.editing_script_id = row.id
        bump_writing_version(creation)
        self.session.flush()
        return row

    def update(self, entity, values):
        creation = self._creation(entity.project_id)
        self._guard_target(creation, entity)
        changed = any(getattr(entity, key) != value for key, value in values.items())
        row = super().update(entity, values)
        if changed:
            bump_writing_version(creation)
            self.session.flush()
        return row

    def delete(self, entity):
        creation = self._creation(entity.project_id)
        self._guard_target(creation, entity)
        if self.model is ProjectScript and creation.editing_script_id == entity.id:
            creation.editing_script_id = None
        bump_writing_version(creation)
        super().delete(entity)
