from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged
from short_drama.agent.core.events import (
    TERMINAL,
)
from short_drama.service.base import utcnow


class RuntimeStoreBase:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings

    def _owned(self, session, run_id, version, token):
        rows = lock_run(session, run_id)
        if rows is None:
            raise LeaseLost()
        project, conversation, run = rows
        if (
            run.status in TERMINAL
            or run.message_version != version
            or run.lease_token != token
            or run.lease_until is None
            or run.lease_until <= utcnow()
        ):
            raise LeaseLost()
        return project, conversation, run
