"""Pure image protocol recipes consumed by admission and the existing worker."""

from dataclasses import dataclass
from typing import Protocol

from short_drama.ai.types import GenerationResult


class ImageProtocolAdapter(Protocol):
    adapter_id: str

    def capabilities(self, snapshot: dict) -> dict: ...

    def build_submission(self, snapshot: dict, request: dict) -> tuple[str, dict, dict]: ...

    def parse_result(
        self, body: dict, *, submitted: bool, task_id: str | None = None
    ) -> GenerationResult: ...

    def poll_endpoint(self, snapshot: dict, task_id: str) -> str: ...


@dataclass(frozen=True)
class ResolvedImageProtocol:
    interface_type: str
    adapter_id: str
    protocol_revision: str
    capabilities: dict
