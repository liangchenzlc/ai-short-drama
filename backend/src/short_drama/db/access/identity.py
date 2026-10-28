from short_drama.db.scope.resource_scope import (
    require_project as require_project,
)
from short_drama.db.scope.resource_scope import (
    scope_of as scope_of,
)


def set_scope(session, scope):
    session.info["resource_scope"] = scope


def scoped_key(session, raw):
    """Opaque fixed-size key preserves existing storage limits and old system tests."""
    import hashlib

    actor = session.info.get("actor")
    if not actor:
        return raw
    return hashlib.sha256(f"{actor.user_id}:{raw}".encode()).hexdigest()
