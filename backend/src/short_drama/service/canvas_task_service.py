"""将已持久化的生成任务结果按 BeefTV 规则附着到原节点。"""

from copy import deepcopy

from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_task_dao import CanvasTaskDAO
from short_drama.domain import (
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskMediaReference,
    ProjectCanvas,
)
from short_drama.schemas.canvas import CanvasDocument
from short_drama.schemas.canvas_generation import CanvasTaskBindOperation, CanvasTaskRegistration

from .base import BaseService, utcnow
from .canvas_document import content_hash
from .canvas_service import CanvasService
from .canvas_task_outputs import durable_output, generation_metadata


class CanvasTaskService(BaseService):
    model = CanvasTaskBinding

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.tasks = CanvasTaskDAO(session)
        self.canvases = CanvasService(session)

    def register_locked(self, payload: CanvasTaskRegistration) -> CanvasTaskBinding:
        """任务准入事务内调用；与 async_task 一起提交，不能绑定任意历史任务。"""
        if not self.session.in_transaction():
            raise WorkflowError("canvas_task_transaction_required", "任务关联需要准入事务", 409)
        payload = CanvasTaskRegistration.model_validate(payload.model_dump())
        canvas = self.canvases.require_canvas(payload.project_id, payload.canvas_id, lock=True)
        task = self.tasks.task(payload.task_id)
        if (
            task is None
            or task.project_id != canvas.project_id
            or task.initiated_by != self.canvases.actor_id
        ):
            raise NotFound("生成任务不属于此作者和项目")
        existing = self.tasks.binding(task.id)
        if existing is not None:
            if (
                existing.canvas_id,
                existing.node_key,
                existing.source_node_key,
                existing.client_operation_id,
            ) != (
                canvas.id,
                payload.node_key,
                payload.source_node_key,
                payload.client_operation_id,
            ):
                raise WorkflowError("canvas_task_binding_conflict", "任务已关联到其他画布操作", 409)
            return existing
        if task.status != "queued" or task.next_action != "submit":
            raise WorkflowError(
                "canvas_task_admission_required", "只能在生成准入时建立画布关联", 409
            )
        records = self.tasks.records(task.id)
        if len(records) != 1 or records[0].status != "prepared":
            raise WorkflowError(
                "canvas_task_admission_required", "生成请求已发送或缺少冻结输入", 409
            )
        document = self.canvases._document(canvas, private=True, lock=True)
        source_node = next(
            (node for node in document["nodes"] if node["id"] == payload.source_node_key), None
        )
        if payload.source_node_key is not None and source_node is None:
            raise NotFound("生成来源节点尚未保存或已删除")
        snapshot = {
            "request": deepcopy(records[0].request_data),
            "source_node": deepcopy(source_node),
        }
        binding = CanvasTaskBinding(
            **self.canvases.child(canvas),
            initiated_by=self.canvases.actor_id,
            async_task_id=task.id,
            node_key=payload.node_key,
            source_node_key=payload.source_node_key,
            client_operation_id=payload.client_operation_id,
            request_hash=task.request_hash,
            source_snapshot=snapshot,
            context_hash=content_hash(snapshot),
        )
        self.session.add(binding)
        self.session.flush()
        inputs = records[0].request_data.get("input") or {}
        for role in (
            "reference_media_ids",
            "video_reference_media_ids",
            "audio_reference_media_ids",
            "first_frame_media_id",
            "last_frame_media_id",
        ):
            values = inputs.get(role) or []
            if not isinstance(values, list):
                values = [values]
            for ordinal, value in enumerate(values):
                media = self.tasks.media(int(value))
                if (
                    media is None
                    or media.project_id != canvas.project_id
                    or not media.storage_locator.startswith("minio://")
                ):
                    raise NotFound("任务参考媒体不属于此项目或尚未持久保存")
                self.session.add(
                    CanvasTaskMediaReference(
                        **self.canvases.child(canvas),
                        task_binding_id=binding.id,
                        media_id=media.id,
                        role=role,
                        ordinal=ordinal,
                    )
                )
        self.session.flush()
        return binding

    def _result(
        self, canvas: ProjectCanvas, binding: CanvasTaskBinding, task: AsyncTask, index: int
    ) -> CanvasResult:
        result = self.tasks.result(binding.id, index)
        if result is not None:
            return result
        records = self.tasks.records(task.id)
        if not records or records[-1].status != "succeeded":
            raise WorkflowError("output_not_ready", "任务结果尚未持久保存", 409)
        record = records[-1]
        kind, content, media_id = durable_output(self, canvas, task, record, index, binding=binding)
        result = CanvasResult(
            **self.canvases.child(canvas),
            task_binding_id=binding.id,
            result_index=index,
            kind=kind,
            content_json=content,
            media_id=media_id,
            attachment_status="detached",
            attachment_receipt_id=None,
            attached_at=None,
            row_version=1,
        )
        self.session.add(result)
        self.session.flush()
        return result

    def materialize_locked(
        self, canvas: ProjectCanvas, binding: CanvasTaskBinding, task: AsyncTask
    ) -> list[CanvasResult]:
        """已授权准入/读取事务内交付真实产物，不修改共享图或已交付的用户素材。"""
        if not self.session.in_transaction():
            raise WorkflowError("canvas_task_transaction_required", "产物交付需要活动事务", 409)
        if (
            binding.initiated_by != self.canvases.actor_id
            or task.initiated_by != self.canvases.actor_id
            or binding.async_task_id != task.id
            or (binding.project_id, binding.canvas_id) != (canvas.project_id, canvas.id)
            or task.project_id != canvas.project_id
        ):
            raise NotFound("画布生成任务不存在或无权访问")
        self.canvases.require_canvas(canvas.project_id, canvas.id, lock=True)
        if task.status != "succeeded":
            return []
        records = self.tasks.records(task.id)
        if not records or records[-1].status != "succeeded":
            return []
        record = records[-1]
        indices = (
            [0]
            if task.service_type == "text" and (record.text_content or "").strip()
            else []
            if task.service_type == "text"
            else [asset.output_index - 1 for asset, _media in self.tasks.outputs(record.id)]
        )
        return [self._result(canvas, binding, task, index) for index in indices]

    def bind(self, operation: CanvasTaskBindOperation) -> dict:
        operation = CanvasTaskBindOperation.model_validate(operation.model_dump())
        args = operation.params
        with self._transaction():
            key = "canvas-bind:" + content_hash(operation.op_id)
            digest, prior = self.canvases.begin_write(
                key, "canvas.task.bind", args.model_dump(mode="json", by_alias=True)
            )
            canvas = self.tasks.canvas(args.canvas_id)
            if canvas is None:
                if prior is not None:
                    # 归档后的回执查询仍校验当前成员权限；只返回原操作事实，不能复活图。
                    prior = self.canvases.canvas_dao.receipt(self.canvases.actor_id, key, lock=True)
                    if prior is not None:
                        return self._response(operation, None, prior.result_json, replayed=True)
                raise NotFound("Canvas does not exist")
            canvas = self.canvases.require_canvas(canvas.project_id, canvas.id, lock=True)
            # Admission/replay lookup can establish an older REPEATABLE READ snapshot.
            # This operation owns a fresh transaction, so no pending graph edits are discarded.
            self.session.expire_all()
            self.session.refresh(canvas, with_for_update=True)
            # 锁顺序为项目、画布、任务；读取回执也重新验证本人任务及当前成员权限。
            task = self.tasks.task(args.task_id)
            binding = self.tasks.binding(args.task_id)
            if task is None or binding is None:
                raise NotFound("画布生成任务不存在")
            if binding.canvas_id != canvas.id or task.project_id != canvas.project_id:
                raise WorkflowError("canvas_mismatch", "任务不属于这块画布", 409)
            if binding.node_key != args.node_id:
                raise WorkflowError("node_mismatch", "只能绑定到任务原先指定的节点", 409)
            if task.status != "succeeded":
                raise WorkflowError("task_not_succeeded", "只有成功的任务才能绑定到画布", 409)
            digest, receipt = self.canvases.begin_write(
                key, "canvas.task.bind", args.model_dump(mode="json", by_alias=True), lock=True
            )
            if receipt is not None:
                return self._response(operation, canvas, receipt.result_json, replayed=True)
            result = self.tasks.result(binding.id, args.output_index)
            if result is not None and result.attachment_receipt_id is not None:
                from short_drama.domain.canvas import CanvasWriteReceipt

                prior = self._require(CanvasWriteReceipt, result.attachment_receipt_id)
                historical = deepcopy(prior.result_json)
                historical["alreadyBound"] = True
            else:
                document = self.canvases._document(canvas, private=True, lock=True)
                node = next(
                    (node for node in document["nodes"] if node["id"] == args.node_id), None
                )
                if node is None:
                    raise WorkflowError("node_deleted", "原任务节点已删除，未重新创建节点", 404)
                if (node.get("metadata") or {}).get("taskId") != str(task.id):
                    raise WorkflowError(
                        "node_task_mismatch", "节点已绑定到其他任务，未覆盖该节点", 409
                    )
                result = self._result(canvas, binding, task, args.output_index)
                effect_key = f"attach-node:{task.id}:{args.node_id}:{args.output_index}"
                node["metadata"] = generation_metadata(
                    node.get("metadata") or {}, result, str(task.id), effect_key
                )
                self.canvases._apply_document(
                    canvas, CanvasDocument.model_validate(document), reason="generation_bind"
                )
                historical = {
                    "applied": True,
                    "canvasId": canvas.source_key,
                    "nodeId": args.node_id,
                    "taskId": str(task.id),
                    "outputIndex": args.output_index,
                    "effectKey": effect_key,
                    "mediaType": result.kind,
                    "alreadyBound": False,
                    "revision": str(canvas.row_version),
                    **{
                        key: deepcopy(value)
                        for key, value in result.content_json.items()
                        if key in {"content", "storageKey", "assetId", "resourceId"}
                    },
                }
            self.canvases.record_write(
                key=key,
                operation="canvas.task.bind",
                digest=digest,
                canvas=canvas,
                result=historical,
            )
            if result is not None and result.attachment_receipt_id is None:
                receipt = self.canvases.canvas_dao.receipt(self.canvases.actor_id, key, lock=True)
                result.attachment_status = "attached"
                result.attachment_receipt_id = receipt.id
                result.attached_at = result.updated_at = utcnow()
                result.row_version += 1
                self.session.flush()
            return self._response(operation, canvas, historical, replayed=False)

    def _response(
        self,
        operation: CanvasTaskBindOperation,
        canvas: ProjectCanvas | None,
        historical: dict,
        *,
        replayed: bool,
    ) -> dict:
        if canvas is None:
            return {
                "op": "canvas.task.bind",
                "opId": operation.op_id,
                "replayed": replayed,
                "result": {
                    **{
                        key: deepcopy(historical[key])
                        for key in (
                            "applied",
                            "canvasId",
                            "nodeId",
                            "taskId",
                            "outputIndex",
                            "effectKey",
                            "mediaType",
                            "alreadyBound",
                        )
                    },
                    "historical": deepcopy(historical),
                    "bindingStatus": "deleted",
                },
            }
        document = self.canvases._document(canvas, private=True, lock=True)
        node = next(
            (item for item in document["nodes"] if item["id"] == operation.params.node_id), None
        )
        metadata = (node or {}).get("metadata") or {}
        status = (
            "deleted"
            if node is None
            else "replaced"
            if metadata.get("taskId") and metadata["taskId"] != historical["taskId"]
            else "bound"
        )
        result = {
            **{
                key: deepcopy(historical[key])
                for key in (
                    "applied",
                    "canvasId",
                    "nodeId",
                    "taskId",
                    "outputIndex",
                    "effectKey",
                    "mediaType",
                    "alreadyBound",
                )
            },
            "historical": deepcopy(historical),
            "bindingStatus": status,
            "revision": str(canvas.row_version),
            "canvas": document,
        }
        if node is not None:
            result["node"] = node
        if status == "bound":
            result.update(
                {
                    key: metadata[key]
                    for key in ("content", "storageKey", "assetId")
                    if isinstance(metadata.get(key), str) and metadata[key]
                }
            )
        return {
            "op": "canvas.task.bind",
            "opId": operation.op_id,
            "replayed": replayed,
            "result": result,
        }
