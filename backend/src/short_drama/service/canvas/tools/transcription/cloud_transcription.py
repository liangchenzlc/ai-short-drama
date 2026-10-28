"""手动 OpenAI 兼容转写渠道；只发一次请求，不存储或回显执行凭据。"""

import asyncio
from pathlib import Path
from typing import BinaryIO

import httpx

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas.tools.transcription.transcription import CanvasTranscriptionResult
from short_drama.service.ai_config.management.runtime import decrypt_runtime_credentials
from short_drama.service.canvas.tools.transcription.transcription import (
    build_transcription_srt,
    decode_whisper_verbose_json,
)
from short_drama.service.canvas.tools.transcription.transcription_execute import (
    WHISPER_RESPONSE_BYTES,
    WHISPER_TIMEOUT_SECONDS,
    CanvasTranscriptionExecutor,
)


def supports_cloud_transcription(config) -> bool:
    profile = config.runtime_profile or {}
    audio = (profile.get("capability_config") or {}).get("audio") or {}
    return (
        profile.get("protocol") == "openai-audio"
        and profile.get("api_format") == "openai"
        and isinstance(audio, dict)
        and audio.get("transcriptionSupported") is True
    )


def require_cloud_transcription(config, version: str | None = None):
    if config is None or not supports_cloud_transcription(config):
        raise WorkflowError(
            "canvas_transcription_model_unavailable", "转写模型不可用，请刷新模型列表", 422
        )
    if version is not None and str(config.row_version) != version:
        raise WorkflowError(
            "canvas_transcription_model_changed", "转写模型配置已变化，请重新选择后提交", 409
        )
    return config


class CanvasCloudTranscriptionExecutor(CanvasTranscriptionExecutor):
    timeout_message = "云端转写请求超时，提交结果可能未知，请到渠道核实"

    def __init__(self, settings, config, progress, *, transport=None, before_submit=lambda: None):
        super().__init__(settings, progress, transport=transport)
        self.base_url = config.base_url.strip().rstrip("/")
        self.model_key = config.model_key
        self.before_submit = before_submit
        key = settings.encryption_key
        try:
            cipher = KeyCipher(key.get_secret_value() if key else None)
            extended = decrypt_runtime_credentials(config, cipher)
            api_key = cipher.decrypt(config.apikey) if config.apikey else ""
        except ValueError:
            raise RuntimeError("转写渠道凭据不可用，请检查宿主模型配置") from None
        self.headers = {
            **extended["headers"],
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        }

    def transcribe(self, wav_path: Path, language: str = "") -> CanvasTranscriptionResult:
        if wav_path.stat().st_size > 25 * 1024**2:
            raise RuntimeError("转写音频超过兼容渠道 25 MiB 上限，请先缩短素材")
        with wav_path.open("rb") as audio:
            return asyncio.run(self._observe_request(self.base_url, audio, wav_path.name, language))

    async def _request(
        self, base_url: str, audio: BinaryIO, filename: str, language: str
    ) -> CanvasTranscriptionResult:
        fields = {"model": self.model_key, "response_format": "verbose_json"}
        if language.strip():
            fields["language"] = language.strip()
        try:
            async with httpx.AsyncClient(
                timeout=WHISPER_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                self.before_submit()
                async with client.stream(
                    "POST",
                    base_url + "/audio/transcriptions",
                    headers=self.headers,
                    data=fields,
                    files={"file": (filename, audio, "audio/wav")},
                ) as response:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"云端转写渠道返回 HTTP {response.status_code}，请检查模型和渠道配置"
                        )
                    payload = bytearray()
                    async for block in response.aiter_bytes(64 * 1024):
                        if len(payload) + len(block) > WHISPER_RESPONSE_BYTES:
                            raise RuntimeError("云端转写结果超过大小上限")
                        payload.extend(block)
        except httpx.InvalidURL:
            raise RuntimeError("云端转写地址无效，请检查宿主模型配置") from None
        except httpx.HTTPError:
            raise RuntimeError(
                "云端转写连接中断，提交结果可能未知，请到渠道核实，未自动重发"
            ) from None
        segments, detected = decode_whisper_verbose_json(bytes(payload))
        segments = [
            item for item in segments if item.text.strip() and item.end_ms > item.start_ms >= 0
        ]
        if not segments:
            raise RuntimeError("云端转写渠道未返回有效时间戳字幕")
        return CanvasTranscriptionResult(
            segments=segments, language=detected, srt=build_transcription_srt(segments)
        )
