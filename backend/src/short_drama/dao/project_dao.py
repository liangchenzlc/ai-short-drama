from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.domain import MediaFile, Project, ProjectCanvas, ShotImage, ShotScript

from .base import BaseDAO


class ProjectDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Project)

    def lock_all_canvases(self, project_id: int) -> list[ProjectCanvas]:
        """删除整个项目时也锁定已归档画布，阻止旧回收站收据恢复项目。"""
        return list(
            self.session.scalars(
                select(ProjectCanvas)
                .where(ProjectCanvas.project_id == project_id)
                .order_by(ProjectCanvas.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def covers(self, project_ids: list[int]) -> dict[int, MediaFile]:
        """批量读取每个作品第一张有效分镜当前采用的图片。"""
        if not project_ids:
            return {}
        first_shots = (
            select(ShotScript.project_id, func.min(ShotScript.position).label("position"))
            .where(ShotScript.project_id.in_(project_ids), ShotScript.deleted_at.is_(None))
            .group_by(ShotScript.project_id)
            .subquery()
        )
        rows = self.session.execute(
            select(ShotScript.project_id, MediaFile)
            .join(
                first_shots,
                (ShotScript.project_id == first_shots.c.project_id)
                & (ShotScript.position == first_shots.c.position),
            )
            .join(ShotImage, ShotImage.shot_id == ShotScript.id)
            .join(MediaFile, MediaFile.id == ShotImage.media_id)
            .where(
                ShotScript.deleted_at.is_(None),
                ShotImage.project_id == ShotScript.project_id,
                ShotImage.state == "confirmed",
                MediaFile.project_id == ShotScript.project_id,
            )
        )
        return {project_id: media for project_id, media in rows}

    def list_recent(self, offset, limit, query):
        self.validate_pagination(offset, limit)
        conditions = [Project.name.contains(query, autoescape=True)] if query else []
        total = self.session.scalar(select(func.count()).select_from(Project).where(*conditions))
        rows = list(
            self.session.scalars(
                select(Project)
                .where(*conditions)
                .order_by(Project.last_opened_at.desc(), Project.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return rows, total
