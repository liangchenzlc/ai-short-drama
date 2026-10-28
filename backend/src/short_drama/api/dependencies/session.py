from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        actor = getattr(request.state, "actor", None)
        if actor is not None:
            session.info["actor"] = actor
            scope = getattr(request.state, "resource_scope", None)
            if scope is not None:
                session.info["resource_scope"] = scope
                session.info["request_project"] = scope[1]
        yield session
