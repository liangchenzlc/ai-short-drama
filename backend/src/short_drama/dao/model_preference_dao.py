from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig
from short_drama.domain.collaboration import User, UserModelPreference


class ModelPreferenceDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_user(self, user_id: int) -> list[UserModelPreference]:
        return list(
            self.session.scalars(
                select(UserModelPreference).where(UserModelPreference.user_id == user_id)
            )
        )

    def selected_config(self, user_id: int, context_key: str) -> int | None:
        return self.session.scalar(
            select(UserModelPreference.config_id)
            .join(AIModelConfig, AIModelConfig.id == UserModelPreference.config_id)
            .where(
                UserModelPreference.user_id == user_id,
                UserModelPreference.context_key == context_key,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
        )

    def lock_user(self, user_id: int) -> int | None:
        return self.session.scalar(select(User.id).where(User.id == user_id).with_for_update())

    def preference(self, user_id: int, context_key: str) -> UserModelPreference | None:
        return self.session.scalar(
            select(UserModelPreference)
            .where(
                UserModelPreference.user_id == user_id,
                UserModelPreference.context_key == context_key,
            )
            .with_for_update()
        )

    def enabled_config(self, config_id: int) -> AIModelConfig | None:
        return self.session.scalar(
            select(AIModelConfig).where(
                AIModelConfig.id == config_id,
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
        )
