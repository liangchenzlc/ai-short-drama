from typing import Annotated, Literal

from fastapi import APIRouter, Query

from short_drama.api.dependencies.assistant import Key, Tasks
from short_drama.schemas.assistant.tasks.task import (
    OperationDetailRead,
    OperationPage,
    TaskAttachmentMetadata,
    TaskControlAccepted,
    TaskControlRequest,
    TaskSubjectInspect,
    TaskSubjectMetadata,
    TaskSubjectOption,
    TaskSubjectReference,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse

router = APIRouter()


@router.post("/tasks/{task_id}/control", response_model=TaskControlAccepted)
def control_task(
    task_id: Identifier, payload: TaskControlRequest, service: Tasks, idempotency_key: Key
):
    return service.control_task(task_id, payload, idempotency_key)


@router.get("/tasks/{task_id}/operations", response_model=OperationPage)
def task_operations(
    task_id: Identifier,
    service: Tasks,
    after_id: Identifier | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.operation_page(task_id, after_id=after_id, limit=limit)


@router.get("/tasks/{task_id}/subjects", response_model=TaskSubjectMetadata)
def task_subjects(task_id: Identifier, service: Tasks):
    return service.subject_metadata(task_id)


@router.get("/tasks/{task_id}/attachments", response_model=TaskAttachmentMetadata)
def task_attachments(task_id: Identifier, service: Tasks):
    return service.attachment_metadata(task_id)


@router.get("/subjects", response_model=PageResponse[TaskSubjectOption])
def subject_directory(
    service: Tasks,
    project_id: Identifier,
    kind: Literal["asset", "shot", "node", "image_group", "image_slot", "sound_line"],
    query: Annotated[str, Query(max_length=120)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return service.subject_directory(project_id, kind, query, offset, limit)


@router.post("/subjects/metadata", response_model=list[TaskSubjectReference])
def inspect_subjects(payload: TaskSubjectInspect, service: Tasks):
    return service.inspect_subjects(payload)


@router.get("/operations/{operation_id}", response_model=OperationDetailRead)
def operation_detail(operation_id: Identifier, service: Tasks):
    return service.operation_detail(operation_id)
