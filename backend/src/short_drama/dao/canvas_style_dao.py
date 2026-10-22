"""风格持久化查询；权限沿用 ResourceScope，事务由服务管理。"""

from sqlalchemy import select

from short_drama.domain import CanvasStyleMediaReference, CanvasStylePreference, CanvasStyleProfile


class CanvasStyleDAO:
    def __init__(self, session):
        self.session = session

    def personal(self, user_id: int):
        return list(
            self.session.scalars(
                select(CanvasStyleProfile)
                .where(
                    CanvasStyleProfile.scope_user_id == user_id,
                    CanvasStyleProfile.deleted_at.is_(None),
                )
                .order_by(CanvasStyleProfile.updated_at.desc(), CanvasStyleProfile.id.desc())
            )
        )

    def get(self, identifier: int, *, lock=False):
        query = select(CanvasStyleProfile).where(
            CanvasStyleProfile.id == identifier, CanvasStyleProfile.deleted_at.is_(None)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def imported(self, user_id: int, key: str):
        return self.session.scalar(
            select(CanvasStyleProfile)
            .where(
                CanvasStyleProfile.scope_user_id == user_id, CanvasStyleProfile.import_key == key
            )
            .with_for_update()
        )

    def references(self, style_id: int):
        return list(
            self.session.scalars(
                select(CanvasStyleMediaReference).where(
                    CanvasStyleMediaReference.style_id == style_id
                )
            )
        )

    def preferences(self, user_id: int):
        return list(
            self.session.scalars(
                select(CanvasStylePreference).where(CanvasStylePreference.user_id == user_id)
            )
        )

    def preference(self, user_id: int, preset_id: str):
        return self.session.scalar(
            select(CanvasStylePreference)
            .where(
                CanvasStylePreference.user_id == user_id,
                CanvasStylePreference.preset_id == preset_id,
            )
            .with_for_update()
        )
