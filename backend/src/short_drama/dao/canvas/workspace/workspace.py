from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig, CanvasChannelModel
from short_drama.domain.canvas.document.canvas import CanvasWorkspaceUserState
from short_drama.domain.identity.accounts.user import User


class CanvasWorkspaceDAO:
    def __init__(self, session: Session):
        self.session = session

    def lock_user(self, user_id: int) -> User | None:
        return self.session.scalar(select(User).where(User.id == user_id).with_for_update())

    def preferences(self, user_id: int, *, lock=False) -> CanvasWorkspaceUserState | None:
        query = select(CanvasWorkspaceUserState).where(CanvasWorkspaceUserState.user_id == user_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def models(self) -> tuple[list[AIModelConfig], dict[int, CanvasChannelModel]]:
        records = list(
            self.session.scalars(
                select(AIModelConfig)
                .where(
                    AIModelConfig.is_deleted == 0,
                )
                .order_by(AIModelConfig.id)
            )
        )
        bindings = {
            value.model_config_id: value
            for value in self.session.scalars(select(CanvasChannelModel))
        }
        return records, bindings
