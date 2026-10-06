from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig
from short_drama.domain.canvas import CanvasWorkspaceUserState
from short_drama.domain.collaboration import User


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

    def models(self) -> list[dict]:
        from short_drama.domain import CanvasChannelModel

        records = self.session.scalars(
            select(AIModelConfig)
            .where(
                AIModelConfig.is_deleted == 0,
                AIModelConfig.id.not_in(select(CanvasChannelModel.model_config_id)),
            )
            .order_by(AIModelConfig.id)
        )
        return [
            {
                "id": str(value.id),
                "name": value.name,
                "model_key": value.model_key,
                "provider": value.provider,
                "service_type": value.service_type,
                "enabled": bool(value.enabled),
                "has_api_key": bool(value.apikey),
            }
            for value in records
        ]
