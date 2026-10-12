"""来源只读查询保留 Actor 过滤，锁定当前视图；不读取私人创作或提交事务。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import Episode, EpisodeNovel


class CanvasSourceProjectDAO:
    def __init__(self, session: Session):
        self.session = session

    def episodes(self, source_project_id: int) -> list[Episode]:
        return list(
            self.session.scalars(
                select(Episode)
                .where(Episode.project_id == source_project_id)
                .order_by(Episode.position, Episode.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )

    def episode(self, source_project_id: int, episode_id: int) -> Episode | None:
        return self.session.scalar(
            select(Episode)
            .where(Episode.project_id == source_project_id, Episode.id == episode_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

    def novel(self, source_project_id: int, episode_id: int) -> EpisodeNovel | None:
        return self.session.scalar(
            select(EpisodeNovel)
            .join(Episode, Episode.id == EpisodeNovel.episode_id)
            .where(Episode.project_id == source_project_id, Episode.id == episode_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
