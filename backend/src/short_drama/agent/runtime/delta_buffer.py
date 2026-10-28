import time

from short_drama.agent.core.access import LeaseLost as LeaseLost
from short_drama.agent.core.access import lock_run as lock_run
from short_drama.agent.core.access import may_decide as may_decide
from short_drama.agent.core.access import model_unchanged as model_unchanged


class _DeltaBuffer:
    """Batch gateway-sanitized display text; private protocol data never enters this path."""

    def __init__(self, store, claim):
        self.store, self.claim = store, claim
        self.pending, self.offset, self.last = "", 0, time.monotonic()
        self.disabled, self.cancelled = False, False
        self.text = ""

    def add(self, delta, final=False):
        if self.disabled:
            return
        self.pending += delta
        if delta:
            self.text = (self.text + delta)[-4000:]
        if self.pending and (
            final or len(self.pending.encode()) >= 8192 or time.monotonic() - self.last >= 0.2
        ):
            self.flush()

    def flush(self):
        if self.pending and not self.disabled:
            status = self.store.save_delta(self.claim, self.pending, self.offset)
            self.offset += len(self.pending)
            self.pending, self.last = "", time.monotonic()
            if status == "cancelled":
                self.cancelled, self.disabled = True, True
            elif status != "ok":
                self.disabled = True
