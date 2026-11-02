"""Account identity for tests using the disposable database."""

from short_drama.core.identity import SessionActorContext
from short_drama.db.session import session_factory as application_session_factory


def actor(user_id=1):
    return SessionActorContext(
        user_id=user_id,
        username="test",
        email="test@example.test",
        verified=True,
        session_id=1,
        csrf_hash="hash",
        request_id="test",
    )


def session_factory(engine):
    factory = application_session_factory(engine)
    factory.configure(info={"actor": actor()})
    return factory
