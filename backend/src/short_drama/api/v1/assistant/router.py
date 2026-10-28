from fastapi import APIRouter

from short_drama.api.v1.assistant.context.attachments import router as router_3
from short_drama.api.v1.assistant.conversations.management import router as router_0
from short_drama.api.v1.assistant.conversations.messages import router as router_1
from short_drama.api.v1.assistant.conversations.revisions import router as router_2
from short_drama.api.v1.assistant.memory.memory import router as router_8
from short_drama.api.v1.assistant.operations.script import router as router_6
from short_drama.api.v1.assistant.tasks.operations import router as router_4
from short_drama.api.v1.assistant.tasks.policy import router as router_5
from short_drama.api.v1.assistant.undo.undo import router as router_7

router = APIRouter(prefix="/assistant", tags=["AI creative assistant"])
router.include_router(router_0)
router.include_router(router_1)
router.include_router(router_2)
router.include_router(router_3)
router.include_router(router_4)
router.include_router(router_5)
router.include_router(router_6)
router.include_router(router_7)
router.include_router(router_8)
