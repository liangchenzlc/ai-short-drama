"""Business receipt and account locking; the caller owns the transaction."""

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from short_drama.dao.assistant.context.context import AssistantContextDAO
from short_drama.domain import Asset, ShotScript
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.identity.accounts.user import User


class BusinessInvocationDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def account(self, user_id: int, *, lock: bool) -> User | None:
        statement = select(User).where(User.id == user_id)
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement.execution_options(populate_existing=True))

    def reserve_receipt(self, candidate: BusinessToolInvocation) -> BusinessToolInvocation:
        # 先建立唯一键，再 current read；空键 FOR UPDATE 会让不同账号互锁间隙。
        self.session.execute(
            insert(BusinessToolInvocation)
            .values(
                **{
                    name: getattr(candidate, name)
                    for name in (
                        "id",
                        "owner_user_id",
                        "tool_name",
                        "request_id",
                        "arguments_hash",
                        "source",
                        "token_id",
                        "status",
                        "result_refs",
                        "changes",
                        "created_at",
                    )
                }
            )
            .on_duplicate_key_update(id=BusinessToolInvocation.id)
        )
        return self.receipt(candidate.owner_user_id, candidate.tool_name, candidate.request_id)

    def receipt(self, owner: int, tool_name: str, request_id: str) -> BusinessToolInvocation | None:
        return self.session.scalar(
            select(BusinessToolInvocation)
            .where(
                BusinessToolInvocation.owner_user_id == owner,
                BusinessToolInvocation.tool_name == tool_name,
                BusinessToolInvocation.request_id == request_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def media_receipt(self, identifier: int, owner: int) -> BusinessToolInvocation | None:
        return self.session.scalar(
            select(BusinessToolInvocation)
            .where(
                BusinessToolInvocation.id == identifier,
                BusinessToolInvocation.owner_user_id == owner,
                BusinessToolInvocation.tool_name == "workbench__submit_generation_batch",
                BusinessToolInvocation.status == "succeeded",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def media_operations(self, receipt_id: int):
        from short_drama.domain.assistant.tasks.task import AgentOperation

        return list(
            self.session.scalars(
                select(AgentOperation)
                .where(AgentOperation.business_invocation_id == receipt_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def image_origins(self, owner: int, project_id: int, identifiers: set[int], *, lock: bool):
        from short_drama.domain import AIGenerationRecord, AsyncTask, MediaAsset, MediaFile

        query = (
            select(
                MediaFile.id,
                AsyncTask.id,
                MediaAsset.output_index,
                AIGenerationRecord.config_snapshot,
            )
            .join(MediaAsset, MediaAsset.media_id == MediaFile.id)
            .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
            .join(AsyncTask, AsyncTask.id == AIGenerationRecord.task_id)
            .where(
                MediaFile.id.in_(identifiers),
                MediaFile.project_id == project_id,
                MediaFile.created_by == owner,
                MediaFile.format_code.startswith("image/"),
                MediaFile.storage_locator.startswith("minio://"),
                MediaAsset.media_type == "image",
                AIGenerationRecord.status == "succeeded",
                AsyncTask.initiated_by == owner,
                AsyncTask.project_id == project_id,
            )
            .order_by(AsyncTask.id, MediaAsset.output_index)
        )
        return self.session.execute(query.with_for_update() if lock else query).all()

    def subjects(self, project_id: int) -> dict[str, frozenset[str]]:
        result = {
            "asset": frozenset(
                str(value)
                for value in self.session.scalars(
                    select(Asset.id).where(
                        Asset.project_id == project_id, Asset.deleted_at.is_(None)
                    )
                )
            ),
            "shot": frozenset(
                str(value)
                for value in self.session.scalars(
                    select(ShotScript.id).where(
                        ShotScript.project_id == project_id, ShotScript.deleted_at.is_(None)
                    )
                )
            ),
        }
        scope = AssistantContextDAO(self.session).sound_scope(project_id, lock=True)
        result["sound_line"] = (
            frozenset(line["id"] for line in scope[1].document.get("dialogue", []))
            if scope
            else frozenset()
        )
        return result

    def canvas_versions(
        self, project_id: int, source_key: str, owner: int
    ) -> dict[str, dict[str, str]]:
        from short_drama.domain import ProjectCanvas
        from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState

        rows = self.session.execute(
            select(CanvasNode.node_key, CanvasNode.row_version, CanvasNodeUserState.row_version)
            .join(ProjectCanvas, ProjectCanvas.id == CanvasNode.canvas_id)
            .outerjoin(
                CanvasNodeUserState,
                (CanvasNodeUserState.canvas_id == CanvasNode.canvas_id)
                & (CanvasNodeUserState.node_key == CanvasNode.node_key)
                & (CanvasNodeUserState.user_id == owner),
            )
            .where(
                ProjectCanvas.project_id == project_id,
                ProjectCanvas.source_key == source_key,
                ProjectCanvas.archived_at.is_(None),
                CanvasNode.archived_at.is_(None),
            )
            .with_for_update()
        ).all()
        return {
            key: {"row_version": str(public), "private_revision": str(private or 0)}
            for key, public, private in rows
        }

    def generations(self, project_id: int, identifiers: list[int]):
        from short_drama.domain import AsyncTask

        return list(
            self.session.scalars(
                select(AsyncTask).where(
                    AsyncTask.project_id == project_id, AsyncTask.id.in_(identifiers)
                )
            )
        )

    def media(self, identifier: int):
        from short_drama.domain import MediaFile

        return self.session.scalar(select(MediaFile).where(MediaFile.id == identifier))

    def canvas_image_target(self, owner: int, project_id: int, canvas_key: str, node_key: str):
        from short_drama.domain import Project, ProjectCanvas
        from short_drama.domain.canvas.document.canvas import CanvasNode

        return self.session.execute(
            select(ProjectCanvas, CanvasNode)
            .join(Project, Project.id == ProjectCanvas.project_id)
            .join(CanvasNode, CanvasNode.canvas_id == ProjectCanvas.id)
            .where(
                Project.id == project_id,
                Project.owner_user_id == owner,
                Project.workspace_mode == "infinite_canvas",
                Project.archived_at.is_(None),
                ProjectCanvas.source_key == canvas_key,
                ProjectCanvas.archived_at.is_(None),
                CanvasNode.node_key == node_key,
                CanvasNode.archived_at.is_(None),
            )
        ).one_or_none()

    def canvas_node_images(self, owner: int, canvas_id: int, node_key: str):
        from short_drama.domain import AIGenerationRecord, CanvasTaskBinding, MediaAsset
        from short_drama.domain.canvas.document.canvas import (
            CanvasMediaReference,
            CanvasUserMediaReference,
        )

        identifiers = set(
            self.session.scalars(
                select(CanvasMediaReference.media_id).where(
                    CanvasMediaReference.canvas_id == canvas_id,
                    CanvasMediaReference.node_key == node_key,
                )
            )
        )
        identifiers.update(
            self.session.scalars(
                select(CanvasUserMediaReference.media_id).where(
                    CanvasUserMediaReference.canvas_id == canvas_id,
                    CanvasUserMediaReference.user_id == owner,
                    CanvasUserMediaReference.owner_kind == "node",
                    CanvasUserMediaReference.owner_key == node_key,
                    CanvasUserMediaReference.revision_id.is_(None),
                )
            )
        )
        identifiers.update(
            self.session.scalars(
                select(MediaAsset.media_id)
                .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
                .join(
                    CanvasTaskBinding, CanvasTaskBinding.async_task_id == AIGenerationRecord.task_id
                )
                .where(
                    CanvasTaskBinding.canvas_id == canvas_id,
                    CanvasTaskBinding.initiated_by == owner,
                    (CanvasTaskBinding.source_node_key == node_key)
                    | (CanvasTaskBinding.node_key == node_key),
                    MediaAsset.media_type == "image",
                )
            )
        )
        return identifiers

    def generation_outputs(self, identifiers: list[int]) -> dict[int, list[dict]]:
        from short_drama.domain import AIGenerationRecord, MediaAsset

        rows = self.session.execute(
            select(
                AIGenerationRecord.task_id,
                MediaAsset.id,
                MediaAsset.media_id,
                MediaAsset.media_type,
            )
            .join(MediaAsset, MediaAsset.record_id == AIGenerationRecord.id)
            .where(
                AIGenerationRecord.task_id.in_(identifiers),
            )
            .order_by(AIGenerationRecord.task_id, MediaAsset.output_index)
        ).all()
        result = {}
        for task_id, asset_id, media_id, kind in rows:
            result.setdefault(task_id, []).append(
                {
                    "kind": kind,
                    "id": str(media_id),
                    "media_id": str(media_id),
                    "media_asset_id": str(asset_id),
                }
            )
        return result

    def generation_sources(self, identifiers: list[int]) -> dict[int, dict]:
        from short_drama.domain import AIGenerationRecord, CanvasTaskBinding, ProjectCanvas

        rows = self.session.execute(
            select(
                AIGenerationRecord.task_id,
                AIGenerationRecord.request_data,
            )
            .where(AIGenerationRecord.task_id.in_(identifiers))
            .order_by(
                AIGenerationRecord.task_id,
                AIGenerationRecord.call_no,
            )
        ).all()
        result = {task_id: request.get("source") or {} for task_id, request in rows}
        for task_id, key, source_key, canvas_key in self.session.execute(
            select(
                CanvasTaskBinding.async_task_id,
                CanvasTaskBinding.node_key,
                CanvasTaskBinding.source_node_key,
                ProjectCanvas.source_key,
            )
            .join(ProjectCanvas, ProjectCanvas.id == CanvasTaskBinding.canvas_id)
            .where(CanvasTaskBinding.async_task_id.in_(identifiers))
        ):
            result[task_id] = {"canvas_node": source_key or key, "canvas_id": canvas_key}
        return result

    def project_images(self, project_id: int, identifiers: set[int]) -> frozenset[int]:
        from short_drama.domain import MediaFile

        if not identifiers:
            return frozenset()
        return frozenset(
            self.session.scalars(
                select(MediaFile.id).where(
                    MediaFile.id.in_(identifiers),
                    MediaFile.project_id == project_id,
                    MediaFile.format_code.startswith("image/"),
                    MediaFile.storage_locator.startswith("minio://"),
                )
            )
        )

    def generated_image_groups(self, owner: int, project_id: int, identifiers: set[int]):
        from short_drama.domain import AsyncTask, ImageGroup

        return list(
            self.session.scalars(
                select(ImageGroup)
                .join(
                    AsyncTask,
                    AsyncTask.id == ImageGroup.task_id,
                )
                .where(
                    ImageGroup.project_id == project_id,
                    AsyncTask.initiated_by == owner,
                    AsyncTask.id.in_(identifiers),
                )
                .order_by(ImageGroup.id)
            )
        )
