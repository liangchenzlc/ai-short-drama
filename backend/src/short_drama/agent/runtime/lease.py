"""A delivery lease outlives every graph checkpoint write."""

import asyncio
import logging
from dataclasses import dataclass

from short_drama.agent.core.access import LeaseLost

logger = logging.getLogger(__name__)


@dataclass(frozen=True, repr=False)
class ExecutionClaim:
    run_id: int
    task_id: int
    owner_id: int
    scope_id: int
    version: int
    token: str
    snapshot: dict
    budget: dict
    tool_names: tuple[str, ...]
    discussion_revision: bool = False
    pending_input: bool = False

    @property
    def fence(self):
        return self.version, self.token


async def run_with_lease(coroutine, *, renew, interval, sandbox=None):
    graph_task = asyncio.create_task(coroutine)

    async def heartbeat():
        while True:
            await asyncio.sleep(interval)
            try:
                alive = await asyncio.to_thread(renew)
            except Exception:
                logger.exception("agent_lease_renewal_failed")
                alive = False
            if not alive:
                graph_task.cancel()
                if sandbox is not None:
                    try:
                        await sandbox.cancel()
                    except Exception:
                        logger.exception("agent_sandbox_cancel_failed")
                return

    watch = asyncio.create_task(heartbeat())
    try:
        try:
            return await graph_task
        except asyncio.CancelledError:
            raise LeaseLost() from None
    finally:
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
