from fastapi import APIRouter

from short_drama.api.dependencies.assistant import Tasks
from short_drama.schemas.assistant.operations.script import (
    ScriptProposalApplied,
    ScriptProposalRead,
)
from short_drama.schemas.base import Identifier
from short_drama.schemas.creation.writing.script.documents import WritingVersion

router = APIRouter()


@router.get("/operations/{operation_id}/script-proposal", response_model=ScriptProposalRead)
def script_proposal(operation_id: Identifier, service: Tasks):
    return service.script_proposal(operation_id)


@router.post("/operations/{operation_id}/apply-script", response_model=ScriptProposalApplied)
def apply_script_proposal(operation_id: Identifier, payload: WritingVersion, service: Tasks):
    return service.apply_script_proposal(operation_id, payload)
