"""Private artifact reads, scoped origin filtering and explicit atomic adoption."""

from sqlalchemy import func, select

from short_drama.agent.native.patches import asset_in_creation, candidate_patch
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.creation.writing.script.documents import (
    ProjectWritingDAO,
    bump_writing_version,
)
from short_drama.db.scope.resource_scope import require_project
from short_drama.domain import AgentArtifact, Project, ShotScript
from short_drama.schemas.assistant.operations.artifacts import (
    ArtifactAdopt,
    ArtifactRead,
    ArtifactSource,
    ArtifactSummary,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.service.base import BaseService, utcnow
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.creation.storyboard.management.script import ShotScriptService
from short_drama.service.creation.writing.script.publication import publish


class AgentArtifactService(BaseService):
    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage

    model = AgentArtifact

    def _scope(self, project_id, *, for_update=True):
        project_id = parse_identifier(project_id)
        statement = (
            select(Project)
            .where(Project.id == project_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        project = self.session.scalar(statement)
        if project is None or project.archived_at is not None:
            raise NotFound("Project does not exist")
        require_project(self.session, project_id, lock=for_update)
        return ProjectWritingDAO(self.session).scoped_project(project_id, for_update=for_update)

    def _artifact(self, creation, artifact_id, *, for_update=True):
        statement = (
            select(AgentArtifact)
            .where(
                AgentArtifact.id == parse_identifier(artifact_id),
                AgentArtifact.project_id == creation.id,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            statement = statement.with_for_update()
        artifact = self.session.scalar(statement)
        if artifact is None:
            raise NotFound("Artifact does not exist in this creation")
        return artifact

    def _view(self, artifact, *, detail=True):
        # An explicit allowlist protects shared reads even if private-origin records
        # are extended. No query joins messages, conversations, runs or tool calls.
        source = {
            key: value
            for key, value in artifact.source_snapshot.items()
            if key in ArtifactSource.model_fields
        }
        content = artifact.source_content
        values = {
            key: getattr(artifact, key)
            for key in ArtifactSummary.model_fields
            if key not in {"source_snapshot", "preview"}
        }
        values.update(source_snapshot=source, preview=(content or "")[:200])
        if values["apply_receipt"] is not None:
            values["apply_receipt"] = {
                key: str(value) if key.endswith("_version") and value is not None else value
                for key, value in values["apply_receipt"].items()
            }
        if not detail:
            return ArtifactSummary.model_validate(values).model_dump(mode="json")
        values.update(
            content=content,
            patch=artifact.proposed_patch,
            diff=[
                {key: item[key] for key in ("field", "before", "after")}
                for item in artifact.metadata_json.get("diff", [])
                if isinstance(item, dict) and {"field", "before", "after"} <= item.keys()
            ],
        )
        return ArtifactRead.model_validate(values).model_dump(mode="json")

    def list(
        self,
        project_id,
        offset=0,
        limit=20,
        *,
        kind=None,
        status=None,
    ):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            creation = self._scope(project_id, for_update=False)
            conditions = [
                AgentArtifact.project_id == creation.id,
            ]
            if kind is not None:
                conditions.append(AgentArtifact.kind == kind)
            if status is not None:
                conditions.append(AgentArtifact.status == status)
            rows = self.session.scalars(
                select(AgentArtifact)
                .where(*conditions)
                .order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            total = self.session.scalar(
                select(func.count()).select_from(AgentArtifact).where(*conditions)
            )
            return {
                "items": [self._view(row, detail=False) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def get(self, project_id, artifact_id):
        with self._transaction(read_only=True):
            creation = self._scope(project_id, for_update=False)
            return self._view(self._artifact(creation, artifact_id, for_update=False))

    def _require_source(self, creation, artifact, values):
        source = artifact.source_snapshot
        if (
            creation.content_version != source["content_version"]
            or values["content_version"] != creation.content_version
        ):
            raise WorkflowError(
                "agent_source_changed",
                "正文已变化，请重新审查候选",
                409,
                details={"current_version": creation.content_version},
            )
        if artifact.kind == "shot_patch":
            if (
                creation.storyboard_version != source["storyboard_version"]
                or values.get("storyboard_version") != creation.storyboard_version
            ):
                raise WorkflowError(
                    "agent_source_changed",
                    "分镜已变化，请重新审查候选",
                    409,
                    details={"current_version": creation.storyboard_version},
                )

    def _apply_text(self, creation, artifact):
        dao = ProjectWritingDAO(self.session)
        script = dao.script(creation.id, artifact.script_id)
        publish(script)
        changed = creation.editing_script_id != script.id
        if changed:
            creation.editing_script_id = script.id
            bump_writing_version(creation)
        return {"script_id": str(script.id), "changed": changed, "action": "select_script"}

    def _apply_patch(self, creation, artifact, values):
        expected = artifact.source_snapshot["target_row_version"]
        if values.get("target_row_version") != expected:
            raise WorkflowError("agent_source_changed", "候选对象版本未匹配，请刷新", 409)
        patch = candidate_patch(artifact.kind, artifact.proposed_patch, version=expected)
        if artifact.kind == "asset_patch":
            target = asset_in_creation(self.session, creation.id, artifact.target_asset_id)
            before = target.row_version
            AssetService(self.session).apply_patch_locked(target, patch, expected)
            reference = {"asset_id": str(target.id)}
        else:
            target = self.session.scalar(
                select(ShotScript)
                .where(
                    ShotScript.id == artifact.target_shot_id,
                    ShotScript.project_id == creation.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if target is None:
                raise NotFound("Shot does not exist in this creation")
            before = target.row_version
            ShotScriptService(self.session).apply_patch_locked(creation, target, patch, expected)
            reference = {"shot_id": str(target.id)}
        return {
            **reference,
            "target_row_version": target.row_version,
            "changed": before != target.row_version,
            "action": "apply_patch",
        }

    def adopt(self, project_id, artifact_id, payload):
        values = self._payload(ArtifactAdopt, payload)
        with self._transaction():
            creation = self._scope(project_id)
            artifact = self._artifact(creation, artifact_id)
            # 已提交的采用回执先于版本校验返回；之后的人工编辑不应破坏重复请求的幂等性。
            if artifact.status == "applied":
                return self._view(artifact)
            if artifact.status != "ready":
                raise WorkflowError("agent_artifact_not_ready", "候选已不可采用", 409)
            if artifact.row_version != values["row_version"]:
                raise WorkflowError(
                    "agent_artifact_version_conflict", "候选状态已变化，请刷新", 409
                )
            if artifact.kind not in {
                "script_candidate",
                "asset_patch",
                "shot_patch",
                "extraction_candidate",
                "storyboard_candidate",
                "image_candidate",
                "video_candidate",
            }:
                raise WorkflowError(
                    "agent_native_review_required", "请通过原有候选审核流程采用", 409
                )
            self._require_source(creation, artifact, values)
            if artifact.kind == "script_candidate":
                receipt = self._apply_text(creation, artifact)
            elif artifact.kind in {"asset_patch", "shot_patch"}:
                receipt = self._apply_patch(creation, artifact, values)
            else:
                from short_drama.agent.native.adoption import adopt_native_locked

                receipt = adopt_native_locked(
                    self.session,
                    creation,
                    artifact,
                    values,
                    settings=self.settings,
                    storage=self.storage,
                )
            if artifact.row_version >= UINT64_MAX:
                raise Conflict("Artifact version exhausted")
            now = max(utcnow(), artifact.created_at)
            artifact.status, artifact.row_version = "applied", artifact.row_version + 1
            artifact.applied_by = getattr(self.session.info.get("actor"), "user_id", None)
            artifact.applied_at, artifact.updated_at = now, now
            artifact.apply_receipt = {
                **receipt,
                "artifact_id": str(artifact.id),
                "project_id": str(creation.id),
                "content_version": creation.content_version,
                "storyboard_version": creation.storyboard_version,
                "project_row_version": creation.row_version,
                "applied_at": now.isoformat(),
            }
            self.session.flush()
            return self._view(artifact)
