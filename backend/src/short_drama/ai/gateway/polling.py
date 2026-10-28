from short_drama.ai.adapters.audio.canvas_audio import ASYNC_AUDIO, complete_audio_urls
from short_drama.ai.adapters.constants import ADAPTER_TYPES
from short_drama.ai.adapters.image.agnes_adapters import (
    check_agnes_reference_echo,
    is_agnes_video,
    parse_agnes_video_result,
    validate_agnes_reference_guard,
)
from short_drama.ai.adapters.results import parse_result, poll_endpoint
from short_drama.ai.adapters.runninghub.adapters import (
    RUNNINGHUB_ADAPTERS,
)
from short_drama.ai.adapters.video.video_adapters import (
    VIDEO_ADAPTERS,
    complete_video_urls,
)
from short_drama.ai.gateway.base import GatewayBase
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.ai.gateway.support import _authentication
from short_drama.ai.types import GenerationError


class GenerationPolling(GatewayBase):
    def poll(
        self,
        snapshot,
        provider_task_id,
        credential,
        adapter,
        *,
        agnes_reference_guard=None,
        require_agnes_reference_guard=False,
    ):
        if ADAPTER_TYPES.get(adapter) != snapshot.get("service_type"):
            raise GenerationError("unsupported_protocol")
        guard = None
        if is_agnes_video(snapshot, adapter) and (
            require_agnes_reference_guard or agnes_reference_guard is not None
        ):
            guard = validate_agnes_reference_guard(agnes_reference_guard)
            check_agnes_reference_echo({"video_id": provider_task_id}, guard, submitted=False)
        if adapter in RUNNINGHUB_ADAPTERS:
            return self._poll_runninghub(snapshot, provider_task_id, credential, adapter)
        if adapter in VIDEO_ADAPTERS:
            if not isinstance(credential, CanvasCredentials):
                raise GenerationError("invalid_credential")
        url = poll_endpoint(snapshot, provider_task_id, adapter)
        _, headers, secrets = _authentication(snapshot, credential, {})
        body = self._json_request(
            "GET",
            url,
            snapshot,
            headers,
            None,
            canvas_video=adapter in VIDEO_ADAPTERS,
            agnes_video=is_agnes_video(snapshot, adapter),
        )
        if guard is not None:
            check_agnes_reference_echo(body, guard, submitted=False, secrets=secrets)
        result = (
            parse_agnes_video_result(body, adapter, submitted=False, task_id=provider_task_id)
            if is_agnes_video(snapshot, adapter)
            else parse_result(body, adapter, submitted=False, task_id=provider_task_id)
        )
        if adapter == ASYNC_AUDIO:
            result = complete_audio_urls(snapshot, result)
        if adapter in VIDEO_ADAPTERS:
            result = complete_video_urls(snapshot, result)
        for value in secrets:
            result = self._sanitize(result, value, submitted=False)
        if guard is not None:
            check_agnes_reference_echo(result, guard, submitted=False, secrets=secrets)
        return result
