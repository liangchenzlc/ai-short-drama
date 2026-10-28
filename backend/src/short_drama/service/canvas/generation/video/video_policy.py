"""固定源视频轮询/下载策略；计数随执行记录持久化，重启不归零。"""

import math
from collections.abc import Callable

from short_drama.ai import GenerationError

POLL_SECONDS = 30
POLL_STATE_KEY = "canvas_video_poll_state"


def reset_poll_state(data: dict) -> None:
    data[POLL_STATE_KEY] = {"not_found": 0, "malformed": 0}


def retry_delay(error: GenerationError) -> float | None:
    if error.http_status is not None:
        transient = error.http_status in {404, 408, 409, 425, 429} or error.http_status >= 500
        transient |= error.code == "provider_task_not_ready"
    else:
        transient = error.retryable or error.code in {"timeout", "invalid_response"}
    if not transient:
        return None
    delay = getattr(error, "retry_after", None)
    return max(
        POLL_SECONDS,
        delay if type(delay) in {int, float} and math.isfinite(delay) and delay > 0 else 0,
    )


def poll_retry_delay(error: GenerationError, data: dict) -> float | None:
    state = data.get(POLL_STATE_KEY) or {}
    previous_not_found = state.get("not_found", 0)
    previous_malformed = state.get("malformed", 0)
    reset_poll_state(data)
    delay = retry_delay(error)
    if delay is None:
        return None
    category = None
    if error.code == "invalid_response":
        category, previous = "malformed", previous_malformed
    elif error.http_status == 404 or error.code == "provider_task_not_ready":
        category, previous = "not_found", previous_not_found
    if category is not None:
        count = (previous if type(previous) is int and previous >= 0 else 0) + 1
        data[POLL_STATE_KEY][category] = count
        if count >= 3:
            return None
    return delay


class CanvasVideoDownloadError(GenerationError):
    def __init__(self, error: GenerationError) -> None:
        super().__init__(error.code, retryable=error.retryable, http_status=error.http_status)
        self.retry_after = getattr(error, "retry_after", None)


def run_download[T](operation: Callable[[], T], wait: Callable[[float], None]) -> T:
    attempt = 0
    while True:
        try:
            return operation()
        except CanvasVideoDownloadError as error:
            attempt += 1
            delay = retry_delay(error)
            if delay is None or attempt == 3:
                raise
            wait(delay)
