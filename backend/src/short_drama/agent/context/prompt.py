"""Stable system instructions, per-turn state as a user message.

Prompt caching matches a byte-identical prefix. Appending task state, receipts or
skills to the system prompt therefore invalidates the whole cache on every turn, so
the stable policy text is sent as ``instructions`` and the changing state travels as
a user message — re-sent only when its digest changes.
"""

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SegmentPrompt:
    instructions: str
    user_prompt: str | None
    digest: str


def json_block(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_segment_prompt(*, stable, dynamic, first_prompt, has_history, previous_digest=None):
    """Return the stable instructions plus the user message carrying current state.

    First segment: state block and the user's own text arrive together.
    Continuation: the block is re-sent only when it changed, so an unchanged run
    keeps appending to the same cached prefix.
    """
    block = json_block(dynamic)
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()[:32]
    if not has_history:
        prompt = f"{block}\n\n{first_prompt}" if first_prompt else block
        return SegmentPrompt(stable, prompt, digest)
    if previous_digest != digest:
        return SegmentPrompt(stable, block, digest)
    return SegmentPrompt(stable, None, digest)


def remember_digest(run, digest):
    """Persist the digest on the run so the next segment can skip a repeat block."""
    checkpoint = dict(run.checkpoint or {})
    if checkpoint.get("prompt_digest") == digest:
        return
    checkpoint["prompt_digest"] = digest
    run.checkpoint = checkpoint
