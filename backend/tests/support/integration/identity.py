"""Account identity for tests using the disposable database."""

from short_drama.core.identity import ActorContext
from short_drama.db.session import session_factory as application_session_factory


def actor(user_id=1):
    return ActorContext(user_id, "test", "test@example.test", True, 1, "hash", "test")


def session_factory(engine):
    factory = application_session_factory(engine)
    factory.configure(info={"actor": actor()})
    return factory
