from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.dao.base import BaseDAO
from short_drama.domain import AIModelConfig
from short_drama.domain.identity.accounts.user import User


class AIModelConfigDAO(BaseDAO):
    def __init__(self, session: Session):
        super().__init__(session, AIModelConfig)

    def lock_owner(self, owner: int) -> int | None:
        return self.session.scalar(select(User.id).where(User.id == owner).with_for_update())

    def created_request(self, owner: int, key: str) -> AIModelConfig | None:
        return self.session.scalar(
            select(AIModelConfig).where(
                AIModelConfig.owner_user_id == owner, AIModelConfig.create_key == key
            )
        )

    def enabled_service_types(self, owner: int) -> set[str]:
        return set(
            self.session.scalars(
                select(AIModelConfig.service_type).where(
                    AIModelConfig.owner_user_id == owner,
                    AIModelConfig.enabled == 1,
                    AIModelConfig.is_deleted == 0,
                )
            )
        )
