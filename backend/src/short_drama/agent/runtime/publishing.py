from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged
from short_drama.agent.core.events import (
    TERMINAL,
)
from short_drama.agent.runtime.base import RuntimeStoreBase
from short_drama.agent.runtime.claims import preceding_execution
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.service.base import utcnow


class RuntimePublishing(RuntimeStoreBase):
    def claim_publish(self):
        if not self.settings.agent_enabled:
            return None
        now = utcnow()
        with self.factory.begin() as session:
            ids = session.scalars(
                select(AgentRun.id)
                .where(
                    AgentRun.status.in_(("queued", "running")),
                    AgentRun.message_status == "pending",
                    AgentRun.next_run_at <= now,
                )
                .order_by(AgentRun.next_run_at, AgentRun.id)
                .limit(50)
            ).all()
            for identifier in ids:
                rows = lock_run(session, identifier, skip_locked=True)
                if rows is None:
                    continue
                _project, _conversation, run = rows
                if (
                    run.message_status != "pending"
                    or run.status in TERMINAL
                    or not run.next_run_at
                    or run.next_run_at > now
                ):
                    continue
                if preceding_execution(session, run) is not None:
                    continue
                run.message_status = "publishing"
                run.lease_token, run.lease_until = (
                    uuid4().hex,
                    now + timedelta(seconds=self.settings.generation_publish_lease_seconds),
                )
                run.updated_at = now
                return {"run_id": run.id, "version": run.message_version, "token": run.lease_token}
        return None

    def publish_result(self, run_id, version, token, *, success):
        with self.factory.begin() as session:
            rows = lock_run(session, run_id)
            if rows is None:
                return False
            _project, _conversation, run = rows
            if (
                run.message_version != version
                or run.lease_token != token
                or run.message_status != "publishing"
            ):
                return False
            run.message_status = "published" if success else "pending"
            run.lease_token = run.lease_until = None
            run.updated_at = utcnow()
            if not success:
                run.next_run_at = run.updated_at + timedelta(seconds=2)
            return True
