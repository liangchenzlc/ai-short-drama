"""Build a business identity from the current account without a browser session."""

from sqlalchemy.orm import Session

from short_drama.core.exceptions import WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.dao.identity.accounts.identity import IdentityDAO


def business_actor_locked(session: Session, user_id: int, request_id: str) -> ActorContext:
    user = IdentityDAO(session).user(user_id, refresh=True)
    if user is None or user.status != "active" or user.email_verified_at is None:
        raise WorkflowError("authentication_required", "Please sign in", 401)
    return ActorContext(
        user_id=user.id,
        username=user.username,
        email=user.email,
        verified=user.email_verified_at is not None,
        request_id=request_id,
    )
