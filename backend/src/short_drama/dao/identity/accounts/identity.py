"""Account-session persistence and resources recognized by identity middleware."""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from short_drama.domain import Asset, AsyncTask, Base, GenerationBatchJob, Project, ShotScript
from short_drama.domain.identity.accounts.user import User, UserSession

RESOURCE_MODELS = {
    "project": Project,
    "asset": Asset,
    "generation": AsyncTask,
    "generation_batch": GenerationBatchJob,
    "shot": ShotScript,
}


class IdentityDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def user(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def revoke_session(self, session_id: int, revoked_at: datetime) -> None:
        self.session.execute(
            update(UserSession).where(UserSession.id == session_id).values(revoked_at=revoked_at)
        )

    def resource(self, kind: str, identifier: int) -> Base | None:
        model = RESOURCE_MODELS[kind]
        return self.session.scalar(select(model).where(model.id == identifier))
