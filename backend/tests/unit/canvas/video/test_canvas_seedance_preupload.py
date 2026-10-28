"""画布参考读取与内联助手：越界与超时在读取前拒绝。"""

import time

import pytest

from short_drama.ai.adapters.video.seedance_preupload import (
    JSON_LIMIT,
    JSON_OVERHEAD,
    inline_canvas_references,
    read_canvas_reference,
)
from short_drama.ai.types import GenerationError
from tests.unit.canvas.video.test_canvas_video_transport import video_request
from tests.unit.shared.generation.test_generation_adapters import gateway as gateway


def test_declared_limit_and_expired_budget_stop_before_loader(gateway):
    def forbidden(*_args):
        pytest.fail("Rejected metadata or expired budget must not read media")

    deadline = time.monotonic() + 10
    with pytest.raises(GenerationError, match="reference_media_too_large"):
        read_canvas_reference(
            gateway.transport,
            "image",
            0,
            {"storageKey": "resource:12", "bytes": 30 * 1024**2 + 1},
            forbidden,
            deadline,
        )
    with pytest.raises(GenerationError, match="reference_upload_timeout"):
        read_canvas_reference(
            gateway.transport,
            "image",
            0,
            {"storageKey": "resource:12"},
            forbidden,
            time.monotonic() - 1,
        )


def test_inline_fallback_accounts_for_base64_and_overhead_before_read(gateway):
    payload = video_request(
        videos=[{"storageKey": "resource:12", "bytes": (JSON_LIMIT - JSON_OVERHEAD) * 3 // 4 + 1}]
    )

    def forbidden(*_args):
        pytest.fail("Oversized fallback must not load the reference")

    with pytest.raises(GenerationError, match="reference_inline_too_large"):
        inline_canvas_references(gateway.transport, payload, forbidden, time.monotonic() + 10)
