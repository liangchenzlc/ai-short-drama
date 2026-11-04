"""Account ownership is the sole boundary of persistent memory."""

from dataclasses import dataclass

from sqlalchemy import and_, select

from short_drama.domain.assistant.memory.memory import AgentMemoryItem, AgentMemorySource


@dataclass(frozen=True)
class MemoryScopeContext:
    owner_user_id: int


class AgentMemoryDAO:
    def __init__(self, session):
        self.session = session

    def permitted(self, scope, *, active=True):
        return and_(
            AgentMemoryItem.owner_user_id == scope.owner_user_id,
            AgentMemoryItem.state == "active" if active else True,
        )

    def rows(self, scope, *, active=True):
        return select(AgentMemoryItem).where(self.permitted(scope, active=active))

    def item(self, identifier, owner, *, lock=False):
        query = select(AgentMemoryItem).where(
            AgentMemoryItem.id == identifier,
            AgentMemoryItem.owner_user_id == owner,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def sources(self, item):
        return self.session.scalars(
            select(AgentMemorySource)
            .where(
                AgentMemorySource.memory_id == item.id,
                AgentMemorySource.content_version == item.content_version,
            )
            .order_by(AgentMemorySource.id)
        ).all()

    def catalog_rows(self, scope, *, offset=0, limit=50, active=True):
        return self.session.scalars(
            self.rows(scope, active=active)
            .order_by(AgentMemoryItem.updated_at.desc(), AgentMemoryItem.id.desc())
            .offset(offset)
            .limit(limit)
            .with_for_update(read=True)
        ).all()
