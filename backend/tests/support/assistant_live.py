"""Read actual protected model receipts; the observer never sends or alters a model request."""

from sqlalchemy import select

from short_drama.ai.gateway.transport import SafeTransport
from short_drama.domain.assistant.execution.models import AgentTurn
from short_drama.domain.assistant.memory.memory import AgentMemoryJob


class RunLedgerObservation:
    def __init__(self, factory, settings):
        self.factory, self.transport = factory, SafeTransport(settings)

    @property
    def inputs(self):
        with self.factory() as session:
            return list(session.scalars(select(AgentTurn.request_messages)))

    @property
    def outputs(self):
        with self.factory() as session:
            turns = session.scalars(select(AgentTurn).order_by(AgentTurn.id)).all()
            jobs = session.scalars(select(AgentMemoryJob).where(AgentMemoryJob.attempts > 0)).all()
            return [
                {"source": turn.usage.get("source"), "usage": turn.usage, "status": turn.status}
                for turn in turns
            ] + [
                {"source": "memory_maintenance", "usage": job.usage, "status": job.status}
                for job in jobs
            ]

    @property
    def usage(self):
        return [item["usage"] for item in self.outputs]
