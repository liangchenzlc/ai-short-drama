from fastapi import APIRouter

from short_drama.api.dependencies.assistant import Key, Questions
from short_drama.schemas.assistant.operations.questions import AskUserAnswer, QuestionAnswerReceipt
from short_drama.schemas.base import Identifier

router = APIRouter()


@router.post("/operations/{operation_id}/answer", response_model=QuestionAnswerReceipt)
def answer_question(
    operation_id: Identifier, payload: AskUserAnswer, service: Questions, idempotency_key: Key
):
    return service.answer(operation_id, payload, idempotency_key=idempotency_key)
