from fastapi import APIRouter

from short_drama.api.v1.agent import router as agent_router
from short_drama.api.v1.agent_artifacts import router as agent_artifacts_router
from short_drama.api.v1.ai_generations import router as ai_generations_router
from short_drama.api.v1.ai_model_configs import router as ai_model_configs_router
from short_drama.api.v1.assets import router as assets_router
from short_drama.api.v1.auth import router as auth_router
from short_drama.api.v1.collaboration import router as collaboration_router
from short_drama.api.v1.episode_assembly import router as episode_assembly_router
from short_drama.api.v1.episode_generation import router as episode_generation_router
from short_drama.api.v1.episode_sound import router as episode_sound_router
from short_drama.api.v1.episode_storyboard import router as episode_storyboard_router
from short_drama.api.v1.episode_writing import router as episode_writing_router
from short_drama.api.v1.generation_batches import router as generation_batches_router
from short_drama.api.v1.generation_references import router as generation_references_router
from short_drama.api.v1.media_library import router as media_library_router
from short_drama.api.v1.native_voice import router as native_voice_router
from short_drama.api.v1.projects import router as projects_router
from short_drama.api.v1.test import router as test_router

router = APIRouter(prefix="/api/v1")
router.include_router(agent_router)
router.include_router(agent_artifacts_router)
router.include_router(episode_assembly_router)
router.include_router(episode_sound_router)
router.include_router(test_router)
router.include_router(ai_model_configs_router)
router.include_router(ai_generations_router)
router.include_router(media_library_router)
router.include_router(projects_router)
router.include_router(native_voice_router)
router.include_router(episode_writing_router)
router.include_router(assets_router)
router.include_router(episode_storyboard_router)
router.include_router(episode_generation_router)

router.include_router(generation_references_router)
router.include_router(generation_batches_router)

router.include_router(auth_router)

router.include_router(collaboration_router)
