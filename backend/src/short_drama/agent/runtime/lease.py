"""A delivery lease outlives every graph checkpoint write."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from short_drama.agent.core.access import LeaseLost
from short_drama.core.time import utcnow

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
    lease_until: datetime
    title_input: str | None = None
    title_attempted: bool = False
    discussion_revision: bool = False
    pending_input: bool = False

    @property
    def fence(self):
        return self.version, self.token


class LeaseRenewalUnavailable(Exception):
    """Confirmed lease expired while renewal infrastructure was unavailable."""


async def run_with_lease(
    coroutine, *, renew, interval, lease_until, control=None, control_interval=1.0, sandbox=None
):
    deadline = monotonic() + max(0.0, (lease_until - utcnow()).total_seconds())
    if deadline <= monotonic():
        coroutine.close()
        raise LeaseRenewalUnavailable()
    graph_task = asyncio.create_task(coroutine)

    async def heartbeat():
        nonlocal deadline
        delay = interval
        while True:
            await asyncio.sleep(min(delay, max(0, deadline - monotonic())))
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise LeaseRenewalUnavailable()
            started = monotonic()
            try:
                valid_until = await asyncio.wait_for(renew(), timeout=remaining)
            except Exception:
                logger.exception(
                    "agent_lease_renewal_failed duration_ms=%s remaining_ms=%s",
                    int((monotonic() - started) * 1000),
                    max(0, int((deadline - monotonic()) * 1000)),
                )
                delay = min(1.0, interval)
                continue
            logger.info(
                "agent_lease_renewed duration_ms=%s",
                int((monotonic() - started) * 1000),
            )
            if valid_until is False:
                raise LeaseLost()
            deadline = monotonic() + max(0.0, (valid_until - utcnow()).total_seconds())
            delay = interval

    async def controls():
        while True:
            await asyncio.sleep(control_interval)
            try:
                alive = await control()
            except Exception:
                logger.exception("agent_control_read_failed")
                continue
            if not alive:
                raise LeaseLost()

    watchers = [asyncio.create_task(heartbeat())]
    if control is not None:
        watchers.append(asyncio.create_task(controls()))
    try:
        done, _ = await asyncio.wait([graph_task, *watchers], return_when=asyncio.FIRST_COMPLETED)
        stopped = next((watch for watch in watchers if watch in done), None)
        if stopped is not None:
            graph_task.cancel()
            if sandbox is not None:
                try:
                    await sandbox.cancel()
                except Exception:
                    logger.exception("agent_sandbox_cancel_failed")
            await asyncio.gather(graph_task, return_exceptions=True)
            return stopped.result()
        return graph_task.result()
    finally:
        for task in [graph_task, *watchers]:
            task.cancel()
        await asyncio.gather(graph_task, *watchers, return_exceptions=True)
