"""源轮询有界恢复与停止策略，不发起付费请求。"""

import pytest

from short_drama.ai import GenerationError
from short_drama.service.canvas_video_policy import (
    POLL_STATE_KEY,
    CanvasVideoDownloadError,
    poll_retry_delay,
    reset_poll_state,
    retry_delay,
    run_download,
)


@pytest.mark.parametrize(
    "error",
    [GenerationError("provider_error", http_status=404), GenerationError("invalid_response")],
)
def test_three_consecutive_missing_or_malformed_results_stop_polling(error):
    data = {}
    assert poll_retry_delay(error, data) == 30
    persisted = {POLL_STATE_KEY: dict(data[POLL_STATE_KEY])}
    assert poll_retry_delay(error, persisted) == 30
    assert poll_retry_delay(error, persisted) is None


def test_different_transient_error_and_valid_response_reset_consecutive_misses():
    data = {}
    missing = GenerationError("provider_error", http_status=404)
    malformed = GenerationError("invalid_response")
    assert poll_retry_delay(missing, data) == 30
    assert poll_retry_delay(missing, data) == 30
    assert poll_retry_delay(malformed, data) == 30
    assert poll_retry_delay(missing, data) == 30
    assert poll_retry_delay(GenerationError("provider_error", http_status=503), data) == 30
    assert data[POLL_STATE_KEY] == {"not_found": 0, "malformed": 0}
    assert poll_retry_delay(malformed, data) == 30
    reset_poll_state(data)
    assert data[POLL_STATE_KEY] == {"not_found": 0, "malformed": 0}


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_non_transient_http_rejection_does_not_retry_even_with_generic_retryable_flag(status):
    assert (
        retry_delay(GenerationError("provider_error", http_status=status, retryable=True)) is None
    )


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 503])
def test_source_transient_http_statuses_keep_polling(status):
    assert retry_delay(GenerationError("provider_error", http_status=status)) == 30


def test_explicit_task_not_ready_http400_counts_as_missing_task():
    data = {}
    error = GenerationError("provider_task_not_ready", http_status=400)
    assert poll_retry_delay(error, data) == 30
    assert poll_retry_delay(error, data) == 30
    assert poll_retry_delay(error, data) is None


def test_provider_retry_after_cannot_shorten_source_interval():
    error = GenerationError("provider_error", http_status=429)
    error.retry_after = 65
    assert retry_delay(error) == 65
    error.retry_after = 1
    assert retry_delay(error) == 30
    error.retry_after = 65.75
    assert retry_delay(error) == 65.75


def test_download_stops_after_three_transient_attempts_and_waits_source_interval():
    attempts, waits = [], []

    def download():
        attempts.append(True)
        raise CanvasVideoDownloadError(GenerationError("provider_error", http_status=503))

    with pytest.raises(CanvasVideoDownloadError):
        run_download(download, waits.append)
    assert len(attempts) == 3 and waits == [30, 30]


def test_download_non_transient_error_does_not_repeat_and_success_returns_bytes():
    attempts, waits = [], []

    def rejected():
        attempts.append(True)
        raise CanvasVideoDownloadError(GenerationError("provider_error", http_status=403))

    with pytest.raises(CanvasVideoDownloadError):
        run_download(rejected, waits.append)
    assert len(attempts) == 1 and waits == []
    assert run_download(lambda: b"original-result", waits.append) == b"original-result"
