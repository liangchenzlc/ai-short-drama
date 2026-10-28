"""固定源音频manifest与原任务恢复合同；不调用真实供应商。"""

import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai import GenerationError, GenerationGateway
from short_drama.ai.adapters.catalog import select_adapter
from short_drama.ai.adapters.results import parse_result, poll_endpoint
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.service.ai_config.management.runtime import refresh_runtime_model
from tests.unit.canvas.generation.test_canvas_generation_parameters import (
    frozen_request,
    request,
    snapshot,
)

ASYNC_AUDIO = "canvas_async_audio.v1"


def audio_snapshot():
    return {
        **snapshot("audio", "compatible-speech"),
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_node",
        "capability_cache": {"adapter": ASYNC_AUDIO},
    }


def test_saved_async_audio_profile_selects_its_own_adapter_without_provider_name_guessing():
    config = SimpleNamespace(
        runtime_profile={"version": 1, "api_format": "openai", "protocol": "async-audio"},
        service_type="audio",
        model_key="compatible-speech",
        base_url="https://provider.example/v1",
        apikey="encrypted-fixture",
        runtime_credentials_cipher=None,
        capability_cache=None,
    )
    assert refresh_runtime_model(config, "host-17")
    identity = vars(config).copy()
    from short_drama.ai.model_identity import model_credential_identity

    identity["credential_identity"] = model_credential_identity(config)
    assert select_adapter(identity) == ASYNC_AUDIO
    assert not refresh_runtime_model(config, "host-17")


@pytest.mark.parametrize("adapter,suffix", [("openai_speech.v1", "speech"), (ASYNC_AUDIO, "tasks")])
def test_canvas_audio_manifest_preserves_voice_format_speed_and_compatible_instructions(
    adapter, suffix
):
    payload = frozen_request(
        request(
            "audio",
            {
                "audioVoice": "nova",
                "audioFormat": "flac",
                "audioSpeed": "1.25",
                "audioInstructions": "温暖自然的旁白",
            },
        ),
        adapter,
    )
    before = deepcopy(payload)
    url, headers, body = build_submission(snapshot("audio", "compatible-speech"), payload, adapter)
    assert url == "https://example.test/v1/audio/" + suffix
    assert headers == {}
    assert body == {
        "model": "compatible-speech",
        "input": "原版提示词",
        "voice": "nova",
        "response_format": "flac",
        "speed": 1.25,
        "instructions": "温暖自然的旁白",
    }
    assert payload == before


@pytest.mark.parametrize("scene", [None, "dialogue_audio", "canvas_tool"])
def test_async_audio_requires_the_saved_canvas_envelope(scene):
    payload = {
        "input": {"text": "音频"},
        "parameters": {"voice": "alloy"},
        "canvas_parameters": {"mode": "audio", "format": "mp3", "speed": 1.0},
    }
    if scene:
        payload["source"] = {"scene": scene}
    with pytest.raises(GenerationError):
        build_submission(snapshot("audio", "compatible-speech"), payload, ASYNC_AUDIO)


@pytest.mark.parametrize("base", ["https://provider.example", "https://provider.example/v1"])
def test_async_audio_polls_the_original_provider_id_under_the_source_version_path(base):
    value = {**snapshot("audio", "compatible-speech"), "base_url": base}
    assert poll_endpoint(value, "original-audio", ASYNC_AUDIO) == (
        "https://provider.example/v1/audio/tasks/original-audio"
    )


@pytest.mark.parametrize("status", ["pending", "running", "processing", "unrecognized"])
def test_async_audio_pending_status_retains_original_identity_without_outputs(status):
    result = parse_result(
        {"data": {"id": "original-audio", "status": status}}, ASYNC_AUDIO, submitted=True
    )
    assert result.status == "submitted" and result.provider_task_id == "original-audio"
    assert result.outputs == []


@pytest.mark.parametrize(
    "body",
    [
        {"audio_url": "https://media.example/audio.mp3"},
        {"audioUrl": "https://media.example/audio.mp3"},
        {"result_url": "https://media.example/audio.mp3"},
        {"url": "https://media.example/audio.mp3"},
        {"data": {"audio_url": "https://media.example/audio.mp3"}},
        {"output": {"url": "https://media.example/audio.mp3"}},
    ],
)
def test_async_audio_source_result_paths_produce_audio_outputs(body):
    result = parse_result(body, ASYNC_AUDIO, submitted=False, task_id="original-audio")
    assert result.status == "succeeded" and result.provider_task_id == "original-audio"
    assert result.outputs == [{"url": "https://media.example/audio.mp3", "media_type": "audio"}]


@pytest.mark.parametrize("status", ["failed", "error", "expired", "cancelled"])
def test_async_audio_explicit_failure_never_synthesizes_an_output_or_echoes_provider_content(
    status,
):
    result = parse_result(
        {"id": "original-audio", "status": status, "message": "private-provider-message"},
        ASYNC_AUDIO,
        submitted=False,
    )
    assert result.status == "failed" and result.outputs == []
    assert "private-provider-message" not in str(result)


@pytest.mark.parametrize("submitted", [True, False])
def test_async_audio_missing_acceptance_identity_keeps_the_correct_unknown_or_retry_boundary(
    submitted,
):
    with pytest.raises(GenerationError) as caught:
        parse_result({"status": "running"}, ASYNC_AUDIO, submitted=submitted)
    assert caught.value.accepted_unknown is submitted
    assert caught.value.retryable is not submitted


def test_async_audio_completed_without_url_downloads_content_with_owned_authentication():
    calls = []
    gateway = GenerationGateway(SimpleNamespace())

    class Transport:
        def request(self, method, url, **options):
            calls.append((method, url, options))
            return 200, {"Content-Type": "application/json"}, b'{"status":"completed"}'

    gateway.transport = Transport()
    value = audio_snapshot()
    result = gateway.poll(
        value, "original-audio", CanvasCredentials(apiKey="fixture-key"), ASYNC_AUDIO
    )
    assert result.status == "succeeded"
    assert result.outputs == [
        {
            "url": "https://example.test/v1/audio/tasks/original-audio/content",
            "media_type": "audio",
        }
    ]
    assert [(method, url) for method, url, _ in calls] == [
        ("GET", "https://example.test/v1/audio/tasks/original-audio")
    ]
    assert calls[0][2]["headers"]["Authorization"] == "Bearer fixture-key"


def test_standard_speech_still_uses_wav_without_canvas_options():
    _, _, body = build_submission(
        snapshot("audio", "tts-1"),
        {"input": {"text": "标准配音"}, "parameters": {"voice": "alloy"}},
        "openai_speech.v1",
    )
    assert body == {
        "model": "tts-1",
        "input": "标准配音",
        "voice": "alloy",
        "response_format": "wav",
    }


@pytest.mark.parametrize("adapter", ["openai_speech.v1", ASYNC_AUDIO])
@pytest.mark.parametrize("code", ["archive_failed", "partial_result", "archive_timeout"])
def test_completed_canvas_audio_can_only_restore_its_archive_without_regeneration(adapter, code):
    from short_drama.service.generations.admission.task_policy import can_retry, resume_action

    task = SimpleNamespace(
        service_type="audio", status="failed", error={"code": code}, locked_until=None
    )
    record = SimpleNamespace(
        adapter=adapter,
        status="succeeded",
        provider_task_id="original-audio" if adapter == ASYNC_AUDIO else None,
        request_data={"source": {"scene": "canvas_node"}},
        text_content=None,
        response_data={"media_manifest": [{"source_cipher": "encrypted-original-result"}]},
    )
    assert can_retry(task, record) is False
    assert resume_action(task, record) == "save"


@pytest.mark.parametrize(
    "value",
    [
        "data:audio/mpeg;base64,SUQz",
        [{"dataUrl": "data:audio/mpeg;base64,SUQz"}],
        {"b64_json": "SUQz"},
    ],
)
def test_async_audio_source_inline_media_is_staged_as_private_bytes(value):
    result = parse_result(
        {"status": "completed", "audio_url": value},
        ASYNC_AUDIO,
        submitted=False,
        task_id="original-audio",
    )
    assert result.status == "succeeded"
    assert result.outputs == [{"base64": base64.b64encode(b"ID3").decode(), "media_type": "audio"}]


def test_async_audio_source_media_array_uses_the_first_audio_like_the_original_single_output():
    result = parse_result(
        {
            "status": "completed",
            "audio_url": [
                {"url": "https://media.example/first.mp3"},
                "https://media.example/second.mp3",
            ],
        },
        ASYNC_AUDIO,
        submitted=False,
        task_id="original-audio",
    )
    assert result.outputs == [{"url": "https://media.example/first.mp3", "media_type": "audio"}]


@pytest.mark.parametrize("adapter", ["openai_speech.v1", ASYNC_AUDIO])
@pytest.mark.parametrize(
    "model,voice,expected",
    [
        ("minimax-speech-2.8", "alloy", "male-qn-qingse"),
        ("minimax-speech-2.8", "中文", "male-qn-qingse"),
        ("minimax-speech-2.8", "female-tianmei", "female-tianmei"),
        ("minimax-music-2.5", "", "alloy"),
        ("compatible-speech", "", "alloy"),
    ],
)
def test_canvas_audio_source_voice_defaults_are_resolved_from_the_saved_model(
    adapter, model, voice, expected
):
    payload = frozen_request(request("audio", {"audioVoice": voice}), adapter)
    _, _, body = build_submission(snapshot("audio", model), payload, adapter)
    # 源ProtocolRequestFromInput先解析音色，manifest对音乐的空音色回落alloy。
    assert body["voice"] == expected


def test_standard_speech_does_not_apply_canvas_minimax_voice_defaults():
    _, _, body = build_submission(
        snapshot("audio", "minimax-speech-2.8"),
        {"input": {"text": "标准配音"}, "parameters": {"voice": "alloy"}},
        "openai_speech.v1",
    )
    assert body["voice"] == "alloy" and body["response_format"] == "wav"
