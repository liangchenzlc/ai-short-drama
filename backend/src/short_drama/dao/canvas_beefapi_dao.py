"""设备授权状态与到期租约；事务归 Service 管理。"""

from sqlalchemy import or_, select

from short_drama.domain.canvas_beefapi_connection import CanvasBeefAPIConnection
from short_drama.domain.collaboration import User


class CanvasBeefAPIDAO:
    def __init__(self, session):
        self.session = session

    def lock_user(self, user_id):
        return self.session.scalar(select(User).where(User.id == user_id).with_for_update())

    def connection(self, user_id, *, lock=False):
        query = select(CanvasBeefAPIConnection).where(CanvasBeefAPIConnection.user_id == user_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def due_users(self, now, limit):
        model = CanvasBeefAPIConnection
        return list(
            self.session.scalars(
                select(model.user_id)
                .where(
                    model.next_poll_at <= now,
                    or_(model.lease_until.is_(None), model.lease_until <= now),
                )
                .order_by(model.next_poll_at, model.id)
                .limit(limit)
            )
        )
