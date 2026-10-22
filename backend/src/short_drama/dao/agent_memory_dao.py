"""All retrieval predicates include the trusted owner and explicitly permitted scopes."""

from dataclasses import dataclass

from sqlalchemy import and_, or_, select

from short_drama.domain.agent import AgentConversation
from short_drama.domain.agent_memory import AgentMemoryItem, AgentMemorySource


@dataclass(frozen=True)
class MemoryScopeContext:
    owner_user_id: int
    conversation_id: int | None = None
    project_ids: tuple[int, ...] = ()


class AgentMemoryDAO:
    def __init__(self, session):
        self.session = session

    def permitted(self, scope, *, active=True):
        clauses = [AgentMemoryItem.scope_type == "user"]
        if scope.conversation_id is not None:
            clauses.append(
                and_(
                    AgentMemoryItem.scope_type == "conversation",
                    AgentMemoryItem.conversation_id == scope.conversation_id,
                )
            )
        if scope.project_ids:
            clauses.append(
                and_(
                    AgentMemoryItem.scope_type == "project",
                    AgentMemoryItem.project_id.in_(scope.project_ids),
                )
            )
        return and_(
            AgentMemoryItem.owner_user_id == scope.owner_user_id,
            or_(*clauses),
            AgentMemoryItem.state == "active" if active else True,
        )

    def rows(self, scope, *, active=True):
        return select(AgentMemoryItem).where(self.permitted(scope, active=active))

    def item(self, identifier, owner, *, lock=False):
        query = select(AgentMemoryItem).where(
            AgentMemoryItem.id == identifier,
            AgentMemoryItem.owner_user_id == owner,
            or_(
                AgentMemoryItem.conversation_id.is_(None),
                select(AgentConversation.id)
                .where(
                    AgentConversation.id == AgentMemoryItem.conversation_id,
                    AgentConversation.owner_user_id == owner,
                    AgentConversation.status == "active",
                )
                .exists(),
            ),
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
            .order_by(AgentMemoryItem.pinned.desc(), AgentMemoryItem.id)
            .offset(offset)
            .limit(limit)
            .with_for_update(read=True)
        ).all()
