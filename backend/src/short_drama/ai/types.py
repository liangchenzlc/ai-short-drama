"""Small, provider-independent generation boundary; never retain raw responses."""

from dataclasses import dataclass, field
from typing import Literal


class GenerationError(Exception):
    def __init__(
        self,
        code: str,
        message=None,
        *,
        accepted_unknown=False,
        retryable=False,
        protocol_mismatch=False,
        http_status=None,
    ):
        self.code = code
        self.accepted_unknown = accepted_unknown
        self.retryable = retryable
        self.protocol_mismatch = protocol_mismatch
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        # Caller-controlled URLs, credentials, upstream messages and bodies are never included.
        super().__init__(f"Generation request failed: {code}")


@dataclass
class GenerationResult:
    status: Literal["submitted", "succeeded", "failed"]
    adapter: str
    provider_task_id: str | None = None
    text: str | None = None
    outputs: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None
    error: dict | None = None
    resolved_parameters: dict = field(default_factory=dict)
