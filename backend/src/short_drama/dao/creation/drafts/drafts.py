from sqlalchemy import select

from short_drama.dao.base import BaseDAO
from short_drama.domain.creation.drafts.drafts import UserCreationDraft


class UserCreationDraftDAO(BaseDAO):
    model = UserCreationDraft

    def __init__(self, session):
        super().__init__(session, self.model)

    def scoped(self, user_id, project_id, object_id, purpose, *, for_update=False):
        query = (
            select(self.model)
            .where(
                self.model.user_id == user_id,
                self.model.project_id == project_id,
                self.model.object_id == object_id,
                self.model.purpose == purpose,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def list_for_project(self, user_id, project_id):
        return list(
            self.session.scalars(
                select(self.model)
                .where(
                    self.model.user_id == user_id,
                    self.model.project_id == project_id,
                )
                .order_by(self.model.id)
            )
        )
