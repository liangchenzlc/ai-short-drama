from fastapi import APIRouter

from short_drama.api.v1.ai_config.assistant.models import router as assistant_models_router
from short_drama.api.v1.ai_config.management.router import router as ai_model_configs_router
from short_drama.api.v1.assistant.operations.artifacts import router as agent_artifacts_router
from short_drama.api.v1.assistant.router import router as assistant_router
from short_drama.api.v1.assistant.status.status import router as assistant_status_router
from short_drama.api.v1.canvas.document.router import router as canvases_router
from short_drama.api.v1.canvas.drawing.drawings import router as canvas_drawings_router
from short_drama.api.v1.canvas.generation.providers.provider_tasks import (
    router as canvas_provider_tasks_router,
)
from short_drama.api.v1.canvas.generation.tasks.tasks import router as canvas_tasks_router
from short_drama.api.v1.canvas.library.library import folder_router as canvas_library_folders_router
from short_drama.api.v1.canvas.library.library import router as canvas_library_router
from short_drama.api.v1.canvas.models.catalog import router as canvas_catalog_router
from short_drama.api.v1.canvas.models.model_tests import router as canvas_model_tests_router
from short_drama.api.v1.canvas.resources.resources import router as canvas_resources_router
from short_drama.api.v1.canvas.styles.styles import router as canvas_styles_router
from short_drama.api.v1.canvas.timeline.timeline import router as canvas_timeline_router
from short_drama.api.v1.canvas.tools.depth.depth import router as canvas_depth_router
from short_drama.api.v1.canvas.tools.tool_runs import router as canvas_tool_runs_router
from short_drama.api.v1.canvas.workspace.folders.folders import router as canvas_folders_router
from short_drama.api.v1.creation.assembly.document.assembly import router as project_assembly_router
from short_drama.api.v1.creation.assets.management.assets import router as assets_router
from short_drama.api.v1.creation.drafts.drafts import router as user_creation_drafts_router
from short_drama.api.v1.creation.sound.tracks.sound import router as project_sound_router
from short_drama.api.v1.creation.sound.voices.voices import router as native_voice_router
from short_drama.api.v1.creation.storyboard.management.generation import (
    router as project_generation_router,
)
from short_drama.api.v1.creation.storyboard.management.storyboard import (
    router as project_storyboard_router,
)
from short_drama.api.v1.creation.writing.script.documents import router as project_writing_router
from short_drama.api.v1.generations.admission.router import router as ai_generations_router
from short_drama.api.v1.generations.batches.batches import router as generation_batches_router
from short_drama.api.v1.generations.references.references import (
    router as generation_references_router,
)
from short_drama.api.v1.identity.authentication.auth import router as auth_router
from short_drama.api.v1.media.image_groups.router import router as image_groups_router
from short_drama.api.v1.media.imports.router import router as personal_resources_router
from short_drama.api.v1.media.library.router import router as media_library_router
from short_drama.api.v1.projects.management.router import router as projects_router
from short_drama.api.v1.skills.catalog.router import router as skills_router
from short_drama.api.v1.system.health.router import router as test_router

router = APIRouter(prefix="/api/v1")
router.include_router(canvas_catalog_router)
router.include_router(canvases_router)
router.include_router(canvas_drawings_router)
router.include_router(canvas_folders_router)
router.include_router(canvas_resources_router)
router.include_router(canvas_tasks_router)
router.include_router(canvas_timeline_router)
router.include_router(canvas_styles_router)
router.include_router(canvas_depth_router)
router.include_router(canvas_tool_runs_router)
router.include_router(canvas_provider_tasks_router)
router.include_router(canvas_model_tests_router)
router.include_router(canvas_library_router)
router.include_router(canvas_library_folders_router)
router.include_router(assistant_models_router)
router.include_router(skills_router)
router.include_router(assistant_status_router)
router.include_router(assistant_router)
router.include_router(agent_artifacts_router)
router.include_router(project_assembly_router)
router.include_router(project_sound_router)
router.include_router(test_router)
router.include_router(ai_model_configs_router)
router.include_router(ai_generations_router)
router.include_router(media_library_router)
router.include_router(projects_router)
router.include_router(native_voice_router)
router.include_router(project_writing_router)
router.include_router(assets_router)
router.include_router(project_storyboard_router)
router.include_router(project_generation_router)

router.include_router(generation_references_router)
router.include_router(generation_batches_router)

router.include_router(auth_router)

router.include_router(user_creation_drafts_router)
router.include_router(image_groups_router)

router.include_router(personal_resources_router)
