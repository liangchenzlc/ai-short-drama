"""供应商协议适配的公共入口。"""

from short_drama.ai.adapters.capabilities import capabilities, resolved_parameters, validate_request
from short_drama.ai.adapters.catalog import capability_fingerprint, select_adapter
from short_drama.ai.adapters.results import parse_result, poll_endpoint
from short_drama.ai.adapters.submission import build_submission

__all__ = [
    "build_submission",
    "capabilities",
    "capability_fingerprint",
    "parse_result",
    "poll_endpoint",
    "resolved_parameters",
    "select_adapter",
    "validate_request",
]
