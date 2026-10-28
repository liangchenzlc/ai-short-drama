"""当前剧本的原子编辑操作；正文、发布状态与版本在同一事务中更新。"""

from sqlalchemy import inspect, select

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.creation.writing.script.documents import (
    ProjectWritingDAO,
    bump_writing_version,
)
from short_drama.domain import (
    Project,
    ProjectScript,
    ProjectScriptRevision,
    ProjectStorySettings,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.schemas.creation.writing.script.documents import (
    ScriptRevisionDetail,
    ScriptRevisionRead,
    ScriptSave,
    ScriptSaved,
    StorySettingsRead,
    StorySettingsSave,
    WritingRead,
    WritingScript,
)
from short_drama.service.base import BaseService, utcnow
from short_drama.service.creation.writing.script.publication import publish
from short_drama.utils.snowflake import next_id


class ProjectWritingService(BaseService):
    model = Project

    def __init__(self, session):
        super().__init__(session)
        self.dao = ProjectWritingDAO(session)
        self._has_agent_artifacts: bool | None = None

    def _unadopted_agent_script(self, script_id):
        # 仅提示词部署可以没有 Agent 表；关闭执行功能不能绕过已有候选的明确采用校验。
        # 在当前事务连接上检查表是否存在，避免另开连接造成可见性不一致。
        from short_drama.domain.assistant.artifacts.models import AgentArtifact

        if self._has_agent_artifacts is None:
            self._has_agent_artifacts = inspect(self.session.connection()).has_table(
                AgentArtifact.__tablename__
            )
        if not self._has_agent_artifacts:
            return None
        # 只检查候选来源是否存在，不读取其他作者的私有正文；外层 ORM 查询仍执行账号与项目隔离。
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
        # 先锁项目再比较版本，保存与生成受理共用这把锁，防止检查后被另一请求改写。
        if version is not None and creation.content_version != version:
            raise Conflict("Content changed elsewhere; reload before saving")
        return creation

    def _view(self, creation, *, for_update=True):
        script = (
            self.dao.script(creation.id, creation.editing_script_id, for_update=for_update)
            if creation.editing_script_id
            else None
        )
        confirmed = self.dao.confirmed(creation.id, for_update=for_update)
        return WritingRead(
            project_id=creation.id,
            content_version=creation.content_version,
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

    def write_script(self, creation, script, content, *, source=None):
        """写入唯一当前剧本，并在调用方事务中同步下游可用状态。"""
        before_content = script.content if script else ""
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
            # 先释放旧剧本的唯一确认位，再确认新稿；同一事务内不能同时存在两份确认稿。
            self.session.flush()
        state = "confirmed" if content.strip() else "unconfirmed"
        if script is None:
            script = self._create_document(
                ProjectScript,
                creation.id,
                content,
                position=self.dao.next_position(creation.id),
                state=state,
            )
            creation.editing_script_id = script.id
        else:
            if (script.content, script.state) != (content, state):
                script.content, script.state = content, state
                script.updated_at, script.updated_by = utcnow(), None
            publish(script)
        # 快照与正文共用事务和前后版本，回滚时一起撤销，不能记录一份实际未生效的修改。
        if source and before_content != content:
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
