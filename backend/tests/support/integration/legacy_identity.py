"""Explicit ownership for old service fixtures in the disposable test database."""

from short_drama.db.session import session_factory as application_session_factory


def session_factory(engine):
    factory = application_session_factory(engine)
    factory.configure(info={"legacy_user_id": 1})
    return factory
