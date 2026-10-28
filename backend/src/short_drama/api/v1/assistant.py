"""Account task API and explicit compatibility endpoints for historical project chat."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.api.v1.agent import agent_status, conversation_events
from short_drama.api.v1.assistant_memory import router as memory_router
from short_drama.schemas.agent import AgentStatus, ConversationPatch, ConversationRead
from short_drama.schemas.agent_context import AttachmentRead, AttachmentReference
from short_drama.schemas.agent_runtime import (
    ConversationRuntimeState,
    MessageAccepted,
    MessageRead,
    RunRead,
)
from short_drama.schemas.assistant import AssistantConversationCreate, AssistantMessageCreate
from short_drama.schemas.assistant_history import (
    DiscussionEligibility,
    DiscussionRevisionRead,
    DiscussionRevisionRequest,
)
from short_drama.schemas.assistant_policy import GenerationPolicyPatch, GenerationPolicyRead
from short_drama.schemas.assistant_script import ScriptProposalApplied, ScriptProposalRead
from short_drama.schemas.assistant_task import (
    AssistantTaskMessage,
    DeletionMemoryPreview,
    GlobalConversationCreate,
    GlobalConversationDelete,
    GlobalConversationPatch,
    OperationDetailRead,
    OperationPage,
    TaskAttachmentMetadata,
    TaskControlAccepted,
    TaskControlRequest,
    TaskMessageAccepted,
    TaskRuntimeState,
    TaskSubjectInspect,
    TaskSubjectMetadata,
    TaskSubjectOption,
    TaskSubjectReference,
)
from short_drama.schemas.assistant_undo import BatchUndoPreview, BatchUndoRead, BatchUndoRequest
from short_drama.schemas.base import Identifier
from short_drama.schemas.common import PageResponse
from short_drama.schemas.project_writing import WritingVersion
from short_drama.service.agent_attachment_service import AgentAttachmentService
from short_drama.service.assistant_policy_service import AssistantPolicyService
from short_drama.service.assistant_service import AssistantService
from short_drama.service.assistant_task_service import AssistantTaskService

router = APIRouter(prefix="/assistant", tags=["AI creative assistant"])
router.include_router(memory_router)
Key = Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:-]+$")]


def service(request: Request, session: Session = Depends(get_session)):
    return AssistantService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


def attachment_service(request: Request, session: Session = Depends(get_session)):
    return AgentAttachmentService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


Assistants = Annotated[AssistantService, Depends(service)]
Attachments = Annotated[AgentAttachmentService, Depends(attachment_service)]


def task_service(request: Request, session: Session = Depends(get_session)):
    return AssistantTaskService(
        session, request.app.state.settings, getattr(request.app.state, "storage", None)
    )


Tasks = Annotated[AssistantTaskService, Depends(task_service)]


@router.get("/operations/{operation_id}/script-proposal", response_model=ScriptProposalRead)
def script_proposal(operation_id: Identifier, service: Tasks):
    return service.script_proposal(operation_id)


@router.post("/operations/{operation_id}/apply-script", response_model=ScriptProposalApplied)
def apply_script_proposal(operation_id: Identifier, payload: WritingVersion, service: Tasks):
    return service.apply_script_proposal(operation_id, payload)


@router.get("/operations/{operation_id}/undo-preview", response_model=BatchUndoPreview)
def undo_preview(operation_id: Identifier, service: Tasks):
    return service.undo_preview(operation_id)


@router.post("/operations/{operation_id}/undo", response_model=BatchUndoRead, status_code=201)
def undo_batch(
    operation_id: Identifier, payload: BatchUndoRequest, service: Tasks, idempotency_key: Key
):
    return service.undo_batch(operation_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/messages/{message_id}/revision-eligibility",
    response_model=DiscussionEligibility,
)
def discussion_eligibility(conversation_id: Identifier, message_id: Identifier, service: Tasks):
    return service.discussion_eligibility(conversation_id, message_id)


@router.post(
    "/global/conversations/{conversation_id}/messages/{message_id}/revisions",
    response_model=TaskMessageAccepted,
    status_code=201,
)
def revise_discussion(
    conversation_id: Identifier,
    message_id: Identifier,
    payload: DiscussionRevisionRequest,
    service: Tasks,
    idempotency_key: Key,
):
    return service.revise_discussion(conversation_id, message_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/messages/{message_id}/revisions",
    response_model=PageResponse[DiscussionRevisionRead],
)
def discussion_revisions(
    conversation_id: Identifier,
    message_id: Identifier,
    service: Tasks,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    return service.discussion_revisions(conversation_id, message_id, offset, limit)


def policy_service(session: Session = Depends(get_session)):
    return AssistantPolicyService(session)


Policies = Annotated[AssistantPolicyService, Depends(policy_service)]


@router.get("/generation-policy", response_model=GenerationPolicyRead)
def generation_policy(service: Policies):
    return service.get_generation_policy()


@router.patch("/generation-policy", response_model=GenerationPolicyRead)
def update_generation_policy(payload: GenerationPolicyPatch, service: Policies):
    return service.update_generation_policy(payload)


@router.get("/global/conversations", response_model=PageResponse[ConversationRead])
def global_conversations(
    service: Tasks,
    project_id: Identifier | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.list_global_conversations(offset, limit, search=query, project_id=project_id)


@router.post("/global/conversations", response_model=ConversationRead, status_code=201)
def create_global_conversation(
    payload: GlobalConversationCreate, service: Tasks, idempotency_key: Key
):
    return service.create_global_conversation(payload, idempotency_key)


@router.get("/global/history", response_model=PageResponse[ConversationRead])
def global_history(
    service: Tasks,
    project_id: Identifier | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.historical_conversations(offset, limit, search=query, project_id=project_id)


@router.get("/global/conversations/{conversation_id}", response_model=ConversationRead)
def global_conversation(conversation_id: Identifier, service: Tasks):
    return service.get_conversation(conversation_id)


@router.patch("/global/conversations/{conversation_id}", response_model=ConversationRead)
def rename_global_conversation(
    conversation_id: Identifier, payload: GlobalConversationPatch, service: Tasks
):
    return service.rename_global_conversation(conversation_id, payload)


@router.delete("/global/conversations/{conversation_id}", response_model=ConversationRead)
def delete_global_conversation(
    conversation_id: Identifier,
    payload: GlobalConversationDelete,
    service: Tasks,
    idempotency_key: Key,
):
    return service.delete_global_conversation(conversation_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/deletion-memory",
    response_model=DeletionMemoryPreview,
)
def deletion_memory_preview(
    conversation_id: Identifier,
    service: Tasks,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    return service.deletion_memory_preview(conversation_id, offset, limit)


@router.post(
    "/global/conversations/{conversation_id}/messages",
    response_model=TaskMessageAccepted,
    status_code=201,
)
def send_task_message(
    conversation_id: Identifier, payload: AssistantTaskMessage, service: Tasks, idempotency_key: Key
):
    return service.send_task_message(conversation_id, payload, idempotency_key)


@router.get(
    "/global/conversations/{conversation_id}/messages", response_model=PageResponse[MessageRead]
)
def global_messages(
    conversation_id: Identifier,
    service: Tasks,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.list_messages(conversation_id, offset, limit)


@router.get("/global/conversations/{conversation_id}/state", response_model=TaskRuntimeState)
def global_state(
    conversation_id: Identifier,
    service: Tasks,
    before_task_id: Identifier | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.task_runtime_state(conversation_id, before_task_id=before_task_id, limit=limit)


@router.get(
    "/global/conversations/{conversation_id}/runtime", response_model=ConversationRuntimeState
)
def global_runtime(conversation_id: Identifier, service: Tasks):
    return service.runtime_state(conversation_id)


@router.get("/global/conversations/{conversation_id}/events")
async def global_events(
    conversation_id: Identifier,
    request: Request,
    service: Tasks,
    cursor: Annotated[int, Query(ge=0, le=2**64 - 1)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
):
    service.get_conversation(conversation_id)
    return await conversation_events(conversation_id, request, None, cursor, last_event_id)


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


@router.get("/status", response_model=AgentStatus)
def status(request: Request):
    return agent_status(request)


@router.get("/conversations", response_model=PageResponse[ConversationRead])
def conversations(
    service: Assistants,
    project_id: Identifier,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = False,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.list_project_conversations(
        project_id, offset, limit, include_archived=include_archived, search=query
    )


@router.get("/legacy-conversations", response_model=PageResponse[ConversationRead])
def legacy_conversations(
    service: Assistants,
    project_id: Identifier,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = True,
    query: Annotated[str | None, Query(max_length=120)] = None,
):
    return service.list_project_conversations(
        project_id, offset, limit, legacy=True, include_archived=include_archived, search=query
    )


@router.post("/conversations", response_model=ConversationRead, status_code=201)
def create_conversation(
    payload: AssistantConversationCreate, service: Assistants, idempotency_key: Key
):
    return service.create_project_conversation(payload, idempotency_key)


@router.post("/conversations/resolve", response_model=ConversationRead)
def resolve_conversation(payload: AssistantConversationCreate, service: Assistants):
    return service.create_project_conversation(payload, resolve=True)


@router.get("/conversations/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: Identifier, service: Assistants):
    return service.get_conversation(conversation_id)


@router.patch("/conversations/{conversation_id}", response_model=ConversationRead)
def patch_conversation(
    conversation_id: Identifier, payload: ConversationPatch, service: Assistants
):
    return service.patch_conversation(conversation_id, payload)


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageAccepted, status_code=201
)
def send_message(
    conversation_id: Identifier,
    payload: AssistantMessageCreate,
    service: Assistants,
    idempotency_key: Key,
):
    return service.send_message(conversation_id, payload, idempotency_key)


@router.get("/conversations/{conversation_id}/messages", response_model=PageResponse[MessageRead])
def messages(
    conversation_id: Identifier,
    service: Assistants,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.list_messages(conversation_id, offset, limit)


@router.get("/conversations/{conversation_id}/runs", response_model=PageResponse[RunRead])
def runs(
    conversation_id: Identifier,
    service: Assistants,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list_runs(conversation_id, offset, limit)


@router.get("/conversations/{conversation_id}/state", response_model=ConversationRuntimeState)
def state(conversation_id: Identifier, service: Assistants):
    return service.runtime_state(conversation_id)


@router.get("/conversations/{conversation_id}/events")
async def events(
    conversation_id: Identifier,
    request: Request,
    service: Assistants,
    cursor: Annotated[int, Query(ge=0, le=2**64 - 1)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=20)] = None,
):
    service.get_conversation(conversation_id)
    return await conversation_events(conversation_id, request, None, cursor, last_event_id)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: Identifier, service: Assistants):
    return service.get_run(run_id)


@router.post("/runs/{run_id}/stop", response_model=RunRead)
def stop(run_id: Identifier, service: Assistants):
    return service.stop(run_id)


@router.get(
    "/conversations/{conversation_id}/attachments", response_model=PageResponse[AttachmentRead]
)
def attachments(
    conversation_id: Identifier,
    service: Assistants,
    attachments: Attachments,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    pending: bool | None = None,
):
    service.validate_attachment_conversation(conversation_id)
    return attachments.list(conversation_id, offset, limit, pending)


@router.post(
    "/conversations/{conversation_id}/attachments/uploads",
    response_model=AttachmentRead,
    status_code=201,
)
def upload_attachment(
    conversation_id: Identifier,
    service: Assistants,
    attachments: Attachments,
    file: Annotated[UploadFile, File()],
    idempotency_key: Key,
):
    service.validate_attachment_conversation(conversation_id)
    return attachments.upload(conversation_id, file.file, file.filename or "", idempotency_key)


@router.post(
    "/conversations/{conversation_id}/attachments/references",
    response_model=AttachmentRead,
    status_code=201,
)
def reference_attachment(
    conversation_id: Identifier,
    payload: AttachmentReference,
    service: Assistants,
    attachments: Attachments,
    idempotency_key: Key,
):
    service.validate_attachment_conversation(conversation_id)
    return attachments.reference(conversation_id, payload, idempotency_key)


@router.delete("/conversations/{conversation_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    conversation_id: Identifier,
    attachment_id: Identifier,
    service: Assistants,
    attachments: Attachments,
):
    service.validate_attachment_conversation(conversation_id)
    attachments.delete(conversation_id, attachment_id)
