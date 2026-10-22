import copy
import json

import pytest

from short_drama.ai.adapters import build_submission, parse_result, select_adapter, validate_request
from short_drama.ai.gateway import GenerationGateway
from short_drama.ai.types import GenerationError
from short_drama.core.config import Settings

BASE = "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
SNAPSHOT = {"service_type": "audio", "model_key": "cosyvoice-v3.5-flash", "base_url": BASE}
REQUEST = {"input": {"text": "你好，世界。"}, "parameters": {"voice": "enrolled-voice"}}
RESPONSE = {
    "request_id": "request-1",
    "output": {"finish_reason": "stop", "audio": {"url": "https://media.example/audio.wav"}},
    "usage": {"characters": 6},
}


@pytest.mark.parametrize("base", [BASE, BASE + "/services/audio/tts/SpeechSynthesizer"])
def test_native_speech_contract_and_frozen_parameters(base):
    snapshot = {**SNAPSHOT, "base_url": base}
    adapter = select_adapter(snapshot)
    assert adapter == "dashscope_speech.v1"
    url, headers, body = build_submission(snapshot, REQUEST, adapter)
    assert url == BASE + "/services/audio/tts/SpeechSynthesizer"
    assert "X-DashScope-SSE" not in headers
    assert body == {
        "model": "cosyvoice-v3.5-flash",
        "input": {
            "text": "你好，世界。",
            "voice": "enrolled-voice",
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    assert validate_request(snapshot, REQUEST)["resolved_parameters"] == {
        "voice": "enrolled-voice",
        "format": "wav",
        "sample_rate": 24000,
    }
    assert (
        select_adapter(
            {**snapshot, "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}
        )
        == "openai_speech.v1"
    )
    assert (
        select_adapter({**snapshot, "base_url": "https://speech.example/v1"}) == "openai_speech.v1"
    )


def test_native_json_audio_result_usage_and_credential_redaction():
    gateway = GenerationGateway(Settings(_env_file=None))
    sent = []

    def send(*args, **kwargs):
        sent.append(kwargs)
        return 200, {"Content-Type": "application/json"}, json.dumps(RESPONSE).encode()

    gateway.transport.request = send
    result = gateway.submit(SNAPSHOT, REQUEST, "test-secret")
    assert result.status == "succeeded" and result.provider_task_id == "request-1"
    assert result.outputs == [{"url": "https://media.example/audio.wav", "media_type": "audio"}]
    assert result.usage == {"characters": 6, "input_characters": 6}
    assert len(sent) == 1 and sent[0]["headers"]["Authorization"] == "Bearer test-secret"
    bad = copy.deepcopy(RESPONSE)
    bad["output"]["audio"]["url"] += "?token=test-secret"
    gateway.transport.request = lambda *a, **k: (200, {}, json.dumps(bad).encode())
    with pytest.raises(GenerationError) as error:
        gateway.submit(SNAPSHOT, REQUEST, "test-secret")
    assert error.value.accepted_unknown and "test-secret" not in str(error.value)


@pytest.mark.parametrize(
    "output",
    [
        None,
        {},
        {"finish_reason": "length"},
        {"finish_reason": "stop", "audio": {}},
        {"finish_reason": "stop", "audio": {"url": ""}},
    ],
)
def test_bad_response_cannot_trigger_a_second_paid_submission(output):
    with pytest.raises(GenerationError) as error:
        parse_result(
            {"request_id": "request-1", "output": output}, "dashscope_speech.v1", submitted=True
        )
    assert error.value.accepted_unknown and not error.value.retryable


def test_native_upstream_failure_is_not_retried():
    gateway = GenerationGateway(Settings(_env_file=None))
    calls = []

    def send(*args, **kwargs):
        calls.append(args)
        return 503, {}, b"private provider failure"

    gateway.transport.request = send
    with pytest.raises(GenerationError) as error:
        gateway.submit(SNAPSHOT, REQUEST, "test-secret")
    assert error.value.accepted_unknown and not error.value.retryable
    assert len(calls) == 1 and "private" not in str(error.value)


@pytest.mark.parametrize("request_id", [None, 123, [], ""])
def test_invalid_provider_identity_is_reported_as_unknown_acceptance(request_id):
    with pytest.raises(GenerationError) as error:
        parse_result({**RESPONSE, "request_id": request_id}, "dashscope_speech.v1", submitted=True)
    assert error.value.accepted_unknown and not error.value.retryable
