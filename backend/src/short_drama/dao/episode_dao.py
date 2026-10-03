from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.domain import Episode, MediaFile, ShotImage, ShotScript

from .base import BaseDAO


class EpisodeDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, Episode)

    def covers(self, episode_ids: list[int]) -> dict[int, MediaFile]:
        """Read adopted media from the first active shot for a whole episode page."""
        if not episode_ids:
            return {}
        first_shots = (
            select(
                ShotScript.episode_id,
                func.min(ShotScript.position).label("position"),
            )
            .where(ShotScript.episode_id.in_(episode_ids), ShotScript.deleted_at.is_(None))
            .group_by(ShotScript.episode_id)
            .subquery()
        )
        rows = self.session.execute(
            select(ShotScript.episode_id, MediaFile)
            .join(
                first_shots,
                (ShotScript.episode_id == first_shots.c.episode_id)
                & (ShotScript.position == first_shots.c.position),
            )
            .join(ShotImage, ShotImage.shot_id == ShotScript.id)
            .join(MediaFile, MediaFile.id == ShotImage.media_id)
            .join(Episode, Episode.id == ShotScript.episode_id)
            .where(
                ShotScript.deleted_at.is_(None),
                ShotImage.episode_id == ShotScript.episode_id,
                ShotImage.state == "confirmed",
                MediaFile.project_id == Episode.project_id,
            )
        )
        return {episode_id: media for episode_id, media in rows}

    def last_position_for_update(self, project_id: int) -> int:
        """Current read, called only after acquiring the parent project's row lock."""
        return (
            self.session.scalar(
                select(Episode.position)
                .where(Episode.project_id == project_id)
                .order_by(Episode.position.desc())
                .limit(1)
                .with_for_update()
            )
            or 0
        )
