"""Shared artifact reads and atomic adoption without access to private conversations."""

from sqlalchemy import func, select

from short_drama.agent.artifacts import asset_in_episode, candidate_patch
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.base import BaseDAO
from short_drama.dao.episode_writing_dao import EpisodeWritingDAO, bump_writing_version
from short_drama.db.access import require_project
from short_drama.domain import (
    AgentArtifact,
    EpisodeNovel,
    EpisodeScript,
    Project,
    ShotScript,
)
from short_drama.schemas.agent_artifacts import (
    ArtifactAdopt,
    ArtifactRead,
    ArtifactSource,
    ArtifactSummary,
)
from short_drama.schemas.base import UINT64_MAX, parse_identifier
from short_drama.service.asset_service import AssetService
from short_drama.service.base import BaseService, utcnow
from short_drama.service.shot_script_service import ShotScriptService


class AgentArtifactService(BaseService):
    def __init__(self, session, settings=None, storage=None):
        super().__init__(session)
        self.settings, self.storage = settings, storage

    model = AgentArtifact

    def _scope(self, project_id, episode_id):
        project_id, episode_id = parse_identifier(project_id), parse_identifier(episode_id)
        project = self.session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if project is None or project.archived_at is not None:
            raise NotFound("Project does not exist")
        require_project(self.session, project_id)
        return EpisodeWritingDAO(self.session).scoped_episode(project_id, episode_id)

    def _artifact(self, episode, artifact_id):
        artifact = self.session.scalar(
            select(AgentArtifact)
            .where(
                AgentArtifact.id == parse_identifier(artifact_id),
                AgentArtifact.project_id == episode.project_id,
                AgentArtifact.episode_id == episode.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if artifact is None:
            raise NotFound("Artifact does not exist in this episode")
        return artifact

    def _view(self, artifact, *, detail=True):
        # An explicit allowlist protects shared reads even if private-origin records
        # are extended. No query joins messages, conversations, runs or tool calls.
        source = {
            key: value
            for key, value in artifact.source_snapshot.items()
            if key in ArtifactSource.model_fields
        }
        script = (
            self.session.scalar(
                select(EpisodeScript).where(
                    EpisodeScript.id == artifact.script_id,
                    EpisodeScript.episode_id == artifact.episode_id,
                )
            )
            if artifact.script_id
            else None
        )
        content = script.content if script else artifact.source_content
        values = {
            key: getattr(artifact, key)
            for key in ArtifactSummary.model_fields
            if key not in {"source_snapshot", "preview"}
        }
        values.update(source_snapshot=source, preview=(content or "")[:200])
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

    def list(self, project_id, episode_id, offset=0, limit=20, *, kind=None, status=None):
        self.dao.validate_pagination(offset, limit)
        with self._transaction():
            episode = self._scope(project_id, episode_id)
            conditions = [
                AgentArtifact.project_id == episode.project_id,
                AgentArtifact.episode_id == episode.id,
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

    def get(self, project_id, episode_id, artifact_id):
        with self._transaction():
            episode = self._scope(project_id, episode_id)
            return self._view(self._artifact(episode, artifact_id))

    def _require_source(self, episode, artifact, values):
        source = artifact.source_snapshot
        if (
            episode.content_version != source["content_version"]
            or values["content_version"] != episode.content_version
        ):
            raise WorkflowError(
                "agent_source_changed",
                "正文已变化，请重新审查候选",
                409,
                details={"current_version": episode.content_version},
            )
        if artifact.kind == "shot_patch":
            if (
                episode.storyboard_version != source["storyboard_version"]
                or values.get("storyboard_version") != episode.storyboard_version
            ):
                raise WorkflowError(
                    "agent_source_changed",
                    "分镜已变化，请重新审查候选",
                    409,
                    details={"current_version": episode.storyboard_version},
                )

    def _apply_text(self, episode, artifact):
        dao = EpisodeWritingDAO(self.session)
        actor_id = getattr(self.session.info.get("actor"), "user_id", None)
        if artifact.kind == "novel_proposal":
            novel = dao.novel(episode.id)
            changed = novel is None or novel.content != artifact.source_content
            if novel is None:
                now = utcnow()
                novel = BaseDAO(self.session, EpisodeNovel).create(
                    {
                        "episode_id": episode.id,
                        "content": artifact.source_content,
                        "created_at": now,
                        "updated_at": now,
                        "created_by": actor_id,
                        "updated_by": actor_id,
                    }
                )
            elif changed:
                novel.content, novel.updated_at, novel.updated_by = (
                    artifact.source_content,
                    utcnow(),
                    actor_id,
                )
            if changed:
                bump_writing_version(episode)
            return {"novel_id": str(novel.id), "changed": changed, "action": "replace_novel"}
        script = dao.script(episode.id, artifact.script_id)
        changed = episode.editing_script_id != script.id
        if changed:
            episode.editing_script_id = script.id
            bump_writing_version(episode)
        return {"script_id": str(script.id), "changed": changed, "action": "select_script"}

    def _apply_patch(self, episode, artifact, values):
        expected = artifact.source_snapshot["target_row_version"]
        if values.get("target_row_version") != expected:
            raise WorkflowError("agent_source_changed", "候选对象版本未匹配，请刷新", 409)
        patch = candidate_patch(artifact.kind, artifact.proposed_patch, version=expected)
        if artifact.kind == "asset_patch":
            target = asset_in_episode(
                self.session, episode.project_id, episode.id, artifact.target_asset_id
            )
            before = target.row_version
            AssetService(self.session).apply_patch_locked(
                target, patch, expected, confirm_shared=values.get("confirm_shared", False)
            )
            reference = {"asset_id": str(target.id)}
        else:
            target = self.session.scalar(
                select(ShotScript)
                .where(
                    ShotScript.id == artifact.target_shot_id,
                    ShotScript.episode_id == episode.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if target is None:
                raise NotFound("Shot does not exist in this episode")
            before = target.row_version
            ShotScriptService(self.session).apply_patch_locked(episode, target, patch, expected)
            reference = {"shot_id": str(target.id)}
        return {
            **reference,
            "target_row_version": target.row_version,
            "changed": before != target.row_version,
            "action": "apply_patch",
        }

    def adopt(self, project_id, episode_id, artifact_id, payload):
        values = self._payload(ArtifactAdopt, payload)
        with self._transaction():
            episode = self._scope(project_id, episode_id)
            artifact = self._artifact(episode, artifact_id)
            # A committed receipt is stable even after later manual edits. It must
            # win before optimistic/source checks to make duplicate delivery safe.
            if artifact.status == "applied":
                return self._view(artifact)
            if artifact.status != "ready":
                raise WorkflowError("agent_artifact_not_ready", "候选已不可采用", 409)
            if artifact.row_version != values["row_version"]:
                raise WorkflowError(
                    "agent_artifact_version_conflict", "候选状态已变化，请刷新", 409
                )
            if artifact.kind not in {
                "novel_proposal",
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
            self._require_source(episode, artifact, values)
            if artifact.kind in {"novel_proposal", "script_candidate"}:
                receipt = self._apply_text(episode, artifact)
            elif artifact.kind in {"asset_patch", "shot_patch"}:
                receipt = self._apply_patch(episode, artifact, values)
            else:
                from short_drama.agent.native_adoption import adopt_native_locked

                receipt = adopt_native_locked(
                    self.session,
                    episode,
                    artifact,
                    values,
                    settings=self.settings,
                    storage=self.storage,
                )
            if artifact.row_version >= UINT64_MAX:
                raise Conflict("Artifact version exhausted")
            now = max(utcnow(), artifact.created_at)
            artifact.status, artifact.row_version = "applied", artifact.row_version + 1
            artifact.applied_by = getattr(
                self.session.info.get("actor"), "user_id", None
            ) or self.session.info.get("legacy_user_id")
            artifact.applied_at, artifact.updated_at = now, now
            artifact.apply_receipt = {
                **receipt,
                "artifact_id": str(artifact.id),
                "episode_id": str(episode.id),
                "content_version": episode.content_version,
                "storyboard_version": episode.storyboard_version,
                "episode_row_version": episode.row_version,
                "applied_at": now.isoformat(),
            }
            self.session.flush()
            return self._view(artifact)
