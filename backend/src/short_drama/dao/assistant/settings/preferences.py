"""Account preference reads and the account mutex used by both preference versions."""

from sqlalchemy import select

from short_drama.domain.assistant.operations.preference import AgentPreference
from short_drama.domain.identity.accounts.user import User


class AssistantSettingsDAO:
    def __init__(self, session):
        self.session = session

    def lock_owner(self, owner: int) -> int | None:
        return self.session.scalar(select(User.id).where(User.id == owner).with_for_update())

    def preference(self, owner: int, *, lock=False) -> AgentPreference | None:
        statement = select(AgentPreference).where(AgentPreference.owner_user_id == owner)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)
