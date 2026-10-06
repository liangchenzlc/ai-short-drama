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

    def models(self, config_service) -> list[dict]:
        from short_drama.domain import CanvasChannelModel

        records = self.session.scalars(
            select(AIModelConfig)
            .where(
                AIModelConfig.is_deleted == 0,
            )
            .order_by(AIModelConfig.id)
        )
        bindings = {
            value.model_config_id: value
            for value in self.session.scalars(select(CanvasChannelModel))
        }
        from short_drama.service.model_runtime_config import model_runtime_public

        return [
            {
                "id": str(value.id),
                "name": value.name,
                "model_key": value.model_key,
                "provider": value.provider,
                "service_type": value.service_type,
                "enabled": bool(value.enabled),
                "has_api_key": bool(value.apikey),
                "is_default": bool(value.is_default),
                "selection_aliases": [
                    f"{bindings[value.id].channel_key}::{bindings[value.id].model_key}"
                ]
                if value.id in bindings
                else [],
                **model_runtime_public(
                    value,
                    config_service._key_cipher() if value.runtime_credentials_cipher else None,
                    bindings.get(value.id),
                ),
            }
            for value in records
        ]
