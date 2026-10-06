"""分类写入先锁本人文件夹集合，再锁项目；不阻塞无关素材库事务。"""

from contextlib import contextmanager

from sqlalchemy import text

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.dao.canvas_folder_dao import CanvasFolderDAO
from short_drama.domain.canvas_folder import CanvasProjectFolderItem
from short_drama.utils.snowflake import next_id


@contextmanager
def canvas_folder_write_lock(session):
    actor = session.info.get("actor")
    if actor is None:
        raise WorkflowError("authentication_required", "Sign in to use canvas folders", 401)
    with session.get_bind().engine.connect() as connection:
        name = connection.scalar(
            text(
                "SELECT CONCAT('short_drama:canvas-folders:', "
                "LEFT(SHA2(CONCAT(DATABASE(), ':', :user_id), 256), 32))"
            ),
            {"user_id": str(actor.user_id)},
        )
        if connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": name}) != 1:
            raise Conflict("Canvas folders are busy; retry operation")
        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})


def require_canvas_folder(session, key):
    folder = CanvasFolderDAO(session).folder(key)
    if folder is None or folder.tombstoned_at is not None:
        raise WorkflowError("canvas_folder_missing", "画布文件夹不存在", 422)
    return folder


def assign_canvas_folder(session, canvas, key: str | None):
    key = (key or "").strip()
    if key:
        require_canvas_folder(session, key)
    item = CanvasFolderDAO(session).item(canvas.id, lock=True)
    if (item.folder_key if item else "") == key:
        return
    if not key:
        if item is not None:
            session.delete(item)
    elif item is not None:
        item.folder_key = key
    else:
        session.add(
            CanvasProjectFolderItem(
                id=next_id(),
                user_id=session.info["actor"].user_id,
                canvas_id=canvas.id,
                folder_key=key,
            )
        )
