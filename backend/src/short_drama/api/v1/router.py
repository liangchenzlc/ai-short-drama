from fastapi import APIRouter

from short_drama.api.v1.ai_generations import router as ai_generations_router
from short_drama.api.v1.ai_model_configs import router as ai_model_configs_router
from short_drama.api.v1.media_library import router as media_library_router
from short_drama.api.v1.projects import router as projects_router
from short_drama.api.v1.test import router as test_router

router = APIRouter(prefix="/api/v1")
router.include_router(test_router)
router.include_router(ai_model_configs_router)
router.include_router(ai_generations_router)
router.include_router(media_library_router)
router.include_router(projects_router)
