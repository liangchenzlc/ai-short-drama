from short_drama.ai.adapters.audio.canvas_audio import (
    ASYNC_AUDIO,
)
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
)
from short_drama.ai.adapters.text.claude_adapters import (
    CLAUDE_MESSAGES,
)
from short_drama.ai.adapters.text.gemini_adapters import (
    GEMINI_GENERATE_CONTENT,
)
from short_drama.ai.adapters.video.video_adapters import (
    VIDEO_ADAPTERS,
)
from short_drama.ai.gateway.endpoints import endpoint as endpoint

ADAPTER_TYPES = {
    **RUNNINGHUB_ADAPTERS,
    **dict.fromkeys(VIDEO_ADAPTERS, "video"),
    "dashscope_voice_design.v1": "audio",
    "dashscope_speech.v1": "audio",
    "openai_speech.v1": "audio",
    ASYNC_AUDIO: "audio",
    "openai_chat.v1": "text",
    "openai_responses.v1": "text",
    CLAUDE_MESSAGES: "text",
    GEMINI_GENERATE_CONTENT: "text",
    "openai_images.v1": "image",
    "ark_images.v1": "image",
    "ark_video.v1": "video",
    "dashscope_images.v1": "image",
    "dashscope_video.v1": "video",
    "modelhub_video.v1": "video",
}


RESOLVER_VERSION = "2026-09-30.2"
