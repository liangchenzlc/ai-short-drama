"""固定音频manifest的协议命名空间、默认选项与优先级。"""

from types import SimpleNamespace

import pytest

from short_drama.ai import GenerationError
from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO
from short_drama.ai.adapters.submission import build_submission
from short_drama.service.ai_config.management.runtime import refresh_runtime_model
from tests.unit.canvas.generation.test_canvas_generation_parameters import (
    frozen_request,
    request,
    snapshot,
)

PROTOCOLS = [("openai_speech.v1", "openai-audio"), (ASYNC_AUDIO, "async-audio")]


def audio_submission(adapter, namespace, config=None, options=None, *, model="compatible-speech"):
    source = request("audio", config, metadata={"providerOptions": {namespace: options or {}}})
    return snapshot("audio", model), frozen_request(source, adapter)


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_namespace_instructions_fill_the_blank_extra(adapter, namespace):
    current, payload = audio_submission(adapter, namespace, options={"instructions": "轻柔旁白"})
    _, _, body = build_submission(current, payload, adapter)
    assert body["instructions"] == "轻柔旁白"


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_manifest_defaults_override_namespace_voice_format_and_speed(adapter, namespace):
    current, payload = audio_submission(
        adapter,
        namespace,
        options={"voice": "custom-voice", "response_format": "flac", "speed": 2},
    )
    _, _, body = build_submission(current, payload, adapter)
    assert body["voice"] == "alloy" and body["response_format"] == "mp3" and body["speed"] == 1


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_extra_instructions_are_trimmed_and_override_namespace(adapter, namespace):
    current, payload = audio_submission(
        adapter,
        namespace,
        {"audioInstructions": "  明确的指令  "},
        {"instructions": "另一份指令"},
    )
    _, _, body = build_submission(current, payload, adapter)
    assert body["instructions"] == "明确的指令"


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_minimax_music_empty_resolved_voice_uses_namespace_fallback(adapter, namespace):
    current, payload = audio_submission(
        adapter, namespace, options={"voice": "music-voice"}, model="minimax-music-2.5"
    )
    _, _, body = build_submission(current, payload, adapter)
    assert body["voice"] == "music-voice"


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_saved_defaults_are_frozen_and_namespace_can_override_them(adapter, namespace):
    current, payload = audio_submission(adapter, namespace, options={"instructions": "本次指令"})
    current["canvas_audio_defaults"] = {"instructions": "保存的指令", "voice": "saved-voice"}
    _, _, body = build_submission(current, payload, adapter)
    assert body["instructions"] == "本次指令" and body["voice"] == "alloy"
    payload["canvas_request"]["input"]["metadata"]["providerOptions"] = {}
    _, _, body = build_submission(current, payload, adapter)
    assert body["instructions"] == "保存的指令"


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_blank_extra_and_empty_namespace_instructions_are_omitted(adapter, namespace):
    current, payload = audio_submission(
        adapter, namespace, {"audioInstructions": "   "}, {"instructions": "\t"}
    )
    _, _, body = build_submission(current, payload, adapter)
    assert "instructions" not in body


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
@pytest.mark.parametrize(
    "options",
    [
        {"instructions": True},
        {"voice": 7},
        {"speed": False},
        {"speed": 5},
        {"response_format": "unknown-format"},
        {"input": "替换正文"},
        {"unknown": "private-unrecognised-option"},
    ],
)
def test_audio_namespace_is_validated_before_a_provider_request(adapter, namespace, options):
    current, payload = audio_submission(adapter, namespace, options=options)
    with pytest.raises(GenerationError, match="unsupported_parameters") as error:
        build_submission(current, payload, adapter)
    assert "private-unrecognised-option" not in str(error.value)


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_different_protocol_namespace_is_not_forwarded(adapter, namespace):
    current, payload = audio_submission(adapter, "other-protocol", options={"instructions": "其他"})
    _, _, body = build_submission(current, payload, adapter)
    assert "instructions" not in body
    assert "providerOptions" not in body


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_saved_runtime_defaults_are_derived_without_changing_model_identity(
    adapter, namespace
):
    config = SimpleNamespace(
        runtime_profile={
            "version": 1,
            "api_format": "openai",
            "protocol": namespace,
            "default_options": {"instructions": "保存的旁白指令"},
        },
        model_key="compatible-speech",
        service_type="audio",
        base_url="https://provider.example/v1",
        apikey="encrypted-placeholder",
        capability_cache=None,
    )
    assert refresh_runtime_model(config)
    assert config.capability_cache["adapter"] == adapter
    assert config.capability_cache["canvas_audio_defaults"] == {"instructions": "保存的旁白指令"}
    assert refresh_runtime_model(config) is False


@pytest.mark.parametrize(("adapter", "namespace"), PROTOCOLS)
def test_audio_zero_extra_speed_uses_namespace_then_manifest_default(adapter, namespace):
    current, payload = audio_submission(adapter, namespace, {"audioSpeed": "0"}, {"speed": 1.5})
    _, _, body = build_submission(current, payload, adapter)
    assert body["speed"] == 1.5
    payload["canvas_request"]["input"]["metadata"]["providerOptions"] = {}
    _, _, body = build_submission(current, payload, adapter)
    assert body["speed"] == 1
