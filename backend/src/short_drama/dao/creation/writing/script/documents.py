"""Project-scoped reads and versioned internal document writes."""

from sqlalchemy import select
from sqlalchemy.orm import load_only

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    Project,
    ProjectScript,
    ProjectScriptRevision,
    ProjectStorySettings,
)
from short_drama.schemas.base import UINT64_MAX


def bump_writing_version(creation):
    if creation.content_version >= UINT64_MAX:
        raise Conflict("Writing version exhausted")
    creation.content_version += 1


class ProjectWritingDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, Project)

    def scoped_project(self, project_id, *, for_update=True):
        # 首次写入、后续编辑和确认共用项目锁，避免两个请求各自判断“当前还没有剧本”。
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

    def story_settings(self, project_id, *, for_update=False):
        statement = (
            select(ProjectStorySettings)
            .where(ProjectStorySettings.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        return self.session.scalar(statement.with_for_update() if for_update else statement)

    def active_tasks(self, project_id, *, for_update=False):
        # 写入方等待项目锁后必须读取最新任务；普通查询会沿用 MySQL 可重复读的旧快照，
        # 可能漏掉刚受理的生成任务，让手动保存覆盖正在生成的来源。
        # 生成记录直接通过已授权的任务连接；若再经任务子查询过滤，MySQL 可重复读
        # 会让子查询使用旧快照，即使外层 FOR UPDATE 已读取到刚提交的任务。
        records = AIGenerationRecord.__table__.alias("writing_generation_records")
        scene = records.c.request_data["source"]["scene"].as_string()
        statement = (
            select(AsyncTask.id, AsyncTask.status, scene)
            .join(records, records.c.task_id == AsyncTask.id)
            .where(
                AsyncTask.project_id == project_id,
                AsyncTask.status.in_(("queued", "running")),
                scene.in_(("script_rewrite",)),
            )
            .distinct()
        )
        rows = self.session.execute(statement.with_for_update() if for_update else statement)
        return [
            {
                "task_id": str(task_id),
                "status": status,
                "operation": operation,
                "target": "script",
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
