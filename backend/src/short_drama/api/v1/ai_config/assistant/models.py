from fastapi import APIRouter

from short_drama.api.dependencies.assistant import Models
from short_drama.schemas.assistant.context.context import (
    ModelInputsPatch,
)
from short_drama.schemas.assistant.operations.runtime import (
    AgentModelRead,
    AgentModelsRead,
    ModelVerify,
)
from short_drama.schemas.base import Identifier

router = APIRouter(prefix="/agent")


@router.get("/models", response_model=AgentModelsRead)
def list_models(service: Models):
    return service.list_models()


@router.post("/models/{model_id}/verify", response_model=AgentModelRead)
def verify_model(model_id: Identifier, payload: ModelVerify, service: Models):
    return service.verify(model_id, payload)


@router.patch("/models/{model_id}/inputs", response_model=AgentModelRead)
def patch_model_inputs(model_id: Identifier, payload: ModelInputsPatch, service: Models):
    return service.patch_inputs(model_id, payload)
