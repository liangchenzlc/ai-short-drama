"""Token persistence; transaction ownership belongs to the service."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain.identity.accounts.mcp_token import McpAccessToken
from short_drama.domain.identity.accounts.user import User
from short_drama.domain.projects.management.project import Project


class McpTokenDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, owner: int, create_key: str | None = None) -> list[McpAccessToken]:
        filters = [McpAccessToken.owner_user_id == owner]
        filters.append(
            McpAccessToken.create_key == create_key
            if create_key is not None
            else McpAccessToken.deleted_at.is_(None)
        )
        return list(
            self.session.scalars(
                select(McpAccessToken).where(*filters).order_by(McpAccessToken.id.desc())
            )
        )

    def lock_owner(self, owner: int) -> int | None:
        return self.session.scalar(select(User.id).where(User.id == owner).with_for_update())

    def by_create_key(self, owner: int, create_key: str) -> McpAccessToken | None:
        return self.session.scalar(
            select(McpAccessToken).where(
                McpAccessToken.owner_user_id == owner, McpAccessToken.create_key == create_key
            )
        )

    def owned(self, owner: int, identifier: int) -> McpAccessToken | None:
        return self.session.scalar(
            select(McpAccessToken)
            .where(
                McpAccessToken.id == identifier,
                McpAccessToken.owner_user_id == owner,
            )
            .with_for_update()
        )

    def by_hash(self, digest: str) -> tuple[McpAccessToken, User] | None:
        return self.session.execute(
            select(McpAccessToken, User)
            .join(User, User.id == McpAccessToken.owner_user_id)
            .where(McpAccessToken.token_hash == digest)
        ).first()

    def projects(self, owner: int) -> list[Project]:
        return list(
            self.session.scalars(
                select(Project)
                .where(Project.owner_user_id == owner, Project.archived_at.is_(None))
                .order_by(Project.id)
            )
        )
