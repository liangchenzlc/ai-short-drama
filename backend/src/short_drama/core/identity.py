"""Request identity; workers use separate, explicitly unscoped database sessions."""

import hashlib
from dataclasses import dataclass

from short_drama.core.exceptions import WorkflowError


@dataclass(frozen=True)
class ActorContext:
    user_id: int
    username: str
    email: str
    verified: bool
    request_id: str


@dataclass(frozen=True)
class SessionActorContext(ActorContext):
    session_id: int
    csrf_hash: str


def require_session_actor(actor: ActorContext) -> SessionActorContext:
    if not isinstance(actor, SessionActorContext):
        raise WorkflowError("authentication_required", "Please sign in", 401)
    return actor


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def require_actor(session):
    actor = session.info.get("actor")
    if actor is None:
        raise WorkflowError("authentication_required", "Please sign in", 401)
    return actor
