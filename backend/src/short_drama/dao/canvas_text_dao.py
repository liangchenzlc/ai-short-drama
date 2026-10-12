"""增量读取受通用作者/当前项目权限保护；内部 Worker 另有租约校验。"""

from sqlalchemy import func, select

from short_drama.domain import CanvasTaskTextDelta
from short_drama.service.base import utcnow


class CanvasTextDAO:
    def __init__(self, session):
        self.session = session

    def deltas(self, binding_id: int, after: int, *, lock: bool = False):
        query = (
            select(CanvasTaskTextDelta)
            .where(
                CanvasTaskTextDelta.task_binding_id == binding_id,
                CanvasTaskTextDelta.sequence > after,
                CanvasTaskTextDelta.expires_at > utcnow(),
            )
            .order_by(CanvasTaskTextDelta.sequence)
            .limit(1000)
        )
        return list(self.session.scalars(query.with_for_update() if lock else query))

    def task_usage(self, binding_id: int, *, lock: bool = False):
        query = select(
            func.coalesce(func.max(CanvasTaskTextDelta.sequence), 0),
            func.coalesce(func.sum(CanvasTaskTextDelta.byte_count), 0),
        ).where(CanvasTaskTextDelta.task_binding_id == binding_id)
        return self.session.execute(query.with_for_update() if lock else query).one()

    def user_bytes(self, actor_id: int) -> int:
        return self.session.scalar(
            select(func.coalesce(func.sum(CanvasTaskTextDelta.byte_count), 0)).where(
                CanvasTaskTextDelta.created_by == actor_id,
                CanvasTaskTextDelta.expires_at > utcnow(),
            )
        )
