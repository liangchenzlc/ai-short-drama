"""Current ownership and versions shared by image and video admission."""

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.domain import Project
from short_drama.schemas.base import UINT64_MAX


def lock_media_project(session, project_id, owner):
    project = session.scalar(
        select(Project)
        .where(
            Project.id == int(project_id),
            Project.owner_user_id == owner,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None or project.archived_at or project.workspace_mode != "standard":
        raise NotFound("媒体生成目标作品不存在或不再可访问")
    if project.storyboard_version == UINT64_MAX:
        raise Conflict("作品版本已达到上限，无法回填媒体")
    return project
