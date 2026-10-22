"""云端 multipart 与失败协议使用受控 HTTP，不调用真实付费模型。"""

import base64
from types import SimpleNamespace

import httpx
import pytest

from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.service.canvas_cloud_transcription import (
    CanvasCloudTranscriptionExecutor,
    require_cloud_transcription,
    supports_cloud_transcription,
)


def model():
    settings = Settings(_env_file=None, encryption_key=base64.b64encode(b"t" * 32).decode())
    return settings, SimpleNamespace(
        base_url="https://asr.example.test/v1",
        model_key="whisper-1",
        row_version=3,
        apikey=KeyCipher(settings.encryption_key.get_secret_value()).encrypt("private-test-key"),
        runtime_credentials_cipher=None,
        runtime_profile={
            "protocol": "openai-audio",
            "api_format": "openai",
            "capability_config": {"audio": {"transcriptionSupported": True}},
        },
    )


def test_requires_explicit_transcription_capability_and_frozen_version():
    _, config = model()
    assert supports_cloud_transcription(config)
    assert require_cloud_transcription(config, "3") is config
    with pytest.raises(WorkflowError):
        require_cloud_transcription(config, "2")
    config.runtime_profile["capability_config"]["audio"]["transcriptionSupported"] = False
    assert not supports_cloud_transcription(config)
    with pytest.raises(WorkflowError):
        require_cloud_transcription(config)


def test_submission_marker_precedes_single_multipart_request(tmp_path):
    markers, requests = [], []

    async def handler(request):
        assert markers == ["started"]
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer private-test-key"
        body = await request.aread()
        assert b'name="model"\r\n\r\nwhisper-1' in body
        assert b'name="response_format"\r\n\r\nverbose_json' in body
        assert b'name="language"\r\n\r\nzh' in body
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "language": "zh",
                "segments": [
                    {"start": 0, "end": 1.25, "text": "对白"},
                    {"start": 2, "end": 2, "text": "无效"},
                ],
            },
        )

    settings, config = model()
    path = tmp_path / "speech.wav"
    path.write_bytes(b"test-controlled-wav")
    executor = CanvasCloudTranscriptionExecutor(
        settings,
        config,
        lambda *_: None,
        transport=httpx.MockTransport(handler),
        before_submit=lambda: markers.append("started"),
    )
    result = executor.transcribe(path, "zh")
    assert len(requests) == 1 and len(result.segments) == 1
    assert "00:00:01,250" in result.srt


@pytest.mark.parametrize("mode", ["http", "disconnect", "malformed"])
def test_failure_is_not_retried_and_does_not_expose_credentials(tmp_path, mode):
    calls = []

    def handler(request):
        calls.append(request)
        if mode == "disconnect":
            raise httpx.ReadError("private-test-key secret response", request=request)
        return (
            httpx.Response(401, text="private-test-key secret response")
            if mode == "http"
            else httpx.Response(200, text="private-test-key")
        )

    settings, config = model()
    path = tmp_path / "speech.wav"
    path.write_bytes(b"controlled")
    executor = CanvasCloudTranscriptionExecutor(
        settings, config, lambda *_: None, transport=httpx.MockTransport(handler)
    )
    with pytest.raises((RuntimeError, WorkflowError)) as error:
        executor.transcribe(path)
    assert len(calls) == 1
    assert "private-test-key" not in str(error.value)
