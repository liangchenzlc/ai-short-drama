from fastapi import APIRouter

from short_drama.api.dependencies.assistant import Policies
from short_drama.schemas.assistant.tasks.policy import GenerationPolicyPatch, GenerationPolicyRead

router = APIRouter()


@router.get("/generation-policy", response_model=GenerationPolicyRead)
def generation_policy(service: Policies):
    return service.get_generation_policy()


@router.patch("/generation-policy", response_model=GenerationPolicyRead)
def update_generation_policy(payload: GenerationPolicyPatch, service: Policies):
    return service.update_generation_policy(payload)
