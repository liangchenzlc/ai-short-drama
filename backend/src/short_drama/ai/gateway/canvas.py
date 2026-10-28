import json
import time
from copy import deepcopy

from short_drama.ai.adapters.capabilities import validate_request
from short_drama.ai.adapters.image.agnes_adapters import (
    agnes_has_stored_references,
    agnes_reference_guard,
    agnes_reference_url,
    check_agnes_reference_echo,
    is_agnes_video,
    parse_agnes_video_result,
)
from short_drama.ai.adapters.results import parse_result
from short_drama.ai.adapters.runninghub.workflow import RUNNINGHUB_WORKFLOW
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.adapters.text.claude_adapters import CLAUDE_MESSAGES
from short_drama.ai.adapters.text.gemini_adapters import GEMINI_GENERATE_CONTENT
from short_drama.ai.adapters.tools.adapters import (
    parse_canvas_tool_result,
    validate_canvas_tool_reply,
)
from short_drama.ai.adapters.tools.submission import (
    canvas_tool_resolved_parameters,
)
from short_drama.ai.adapters.video.seedance_preupload import (
    JSON_LIMIT,
    inline_canvas_references,
    read_canvas_reference,
)
from short_drama.ai.adapters.video.video_adapters import (
    OPENAI_VIDEOS,
    canvas_video_resolved_parameters,
    complete_video_urls,
)
from short_drama.ai.gateway.base import GatewayBase
from short_drama.ai.gateway.canvas_credentials import CanvasCredentials
from short_drama.ai.gateway.support import _authentication
from short_drama.ai.gateway.transport import (
    MultipartBody,
)
from short_drama.ai.types import GenerationError, GenerationResult


class CanvasGateway(GatewayBase):
    def _submit_canvas_tool(self, snapshot, request, credential, adapter, on_text_delta):
        url, headers, body = build_submission(snapshot, request, adapter)
        if not isinstance(credential, CanvasCredentials):
            raise GenerationError("invalid_credential")
        if body.get("stream"):
            headers["Accept"] = "text/event-stream, application/json"
        _, headers, secrets = _authentication(snapshot, credential, headers, adapter=adapter)
        try:
            budget = float(snapshot.get("budget_seconds", 3600))
            if not 0 < budget <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            raise GenerationError("invalid_budget") from None
        deadline = time.monotonic() + budget
        attempts = [body]
        if adapter == "openai_chat.v1":
            if body.get("tool_choice") != "auto":
                attempts.append({**body, "tool_choice": "auto"})
            attempts.append({key: value for key, value in body.items() if key != "tool_choice"})
        for index, attempted_body in enumerate(attempts):
            try:
                response = self._json_request(
                    "POST",
                    url,
                    snapshot,
                    headers,
                    attempted_body,
                    on_text_delta=None if adapter == GEMINI_GENERATE_CONTENT else on_text_delta,
                    secrets=secrets,
                    claude_text=adapter == CLAUDE_MESSAGES,
                    canvas_tool_adapter=adapter,
                    request_deadline=deadline,
                )
                body = attempted_body
                break
            except GenerationError as error:
                if error.code != "canvas_tool_choice_rejected":
                    raise
                if index == len(attempts) - 1:
                    raise GenerationError("provider_rejected") from None
        reply = parse_canvas_tool_result(
            response, adapter, request["canvas_tool_canonical"], submitted=True
        )
        result = GenerationResult(
            "succeeded",
            adapter,
            text=reply.content,
            reasoning=reply.reasoning,
            tool_calls=[
                call.model_dump(by_alias=True, exclude_none=True) for call in reply.tool_calls
            ],
            usage=response.get("usage") or response.get("usageMetadata") or {},
            finish_reason="stop",
            resolved_parameters=canvas_tool_resolved_parameters(body),
        )
        for secret in secrets:
            result = self._sanitize(result, secret, submitted=True)
        validate_canvas_tool_reply(
            {"content": result.text, "reasoning": result.reasoning, "toolCalls": result.tool_calls},
            request["canvas_tool_canonical"],
            submitted=True,
        )
        return result

    def _submit_canvas_video(
        self, snapshot, request, credential, adapter, loader, url_resolver, guard_writer
    ):
        # Admission stays pure. Only this explicitly frozen canvas branch reads media.
        agnes_video = is_agnes_video(snapshot, adapter)
        if agnes_video:
            validate_request(snapshot, request, adapter)
            if agnes_has_stored_references(request) and not callable(guard_writer):
                raise GenerationError("reference_guard_unavailable")
        else:
            build_submission(snapshot, request, adapter)
        if not isinstance(credential, CanvasCredentials):
            raise GenerationError("invalid_credential")
        _, headers, secrets = _authentication(snapshot, credential, {})
        try:
            budget = float(snapshot.get("budget_seconds", 1800))
            if not 0 < budget <= 86400:
                raise ValueError
        except (TypeError, ValueError):
            raise GenerationError("invalid_budget") from None
        deadline = time.monotonic() + budget
        prepared = deepcopy(request)
        reference_urls = []
        guard = None
        if agnes_video:
            refs = (snapshot.get("canvas_video_capability") or {}).get("references") or {}
            for kind, group, limit_name, default_limit in (
                ("image", "referenceImages", "maxImageBytes", 50 * 1024**2),
                ("audio", "referenceAudios", "maxAudioBytes", 15 * 1024**2),
            ):
                maximum = refs.get(limit_name) or default_limit
                for index, item in enumerate(prepared["canvas_request"]["input"].get(group, [])):
                    if item.get("storageKey"):
                        if url_resolver is None:
                            raise GenerationError("reference_media_requires_url")
                        url = agnes_reference_url(url_resolver(kind, index, maximum, deadline))
                        reference_urls.append(url)
                        item.pop("storageKey")
                        item["url"] = url
        elif adapter == RUNNINGHUB_WORKFLOW:
            inline_canvas_references(self.transport, prepared, loader, deadline)
        url, _, body = build_submission(snapshot, prepared, adapter)
        if reference_urls:
            guard = agnes_reference_guard(reference_urls)
            # The callback commits its short transaction before any provider request.
            guard_writer(deepcopy(guard))
        resolved = canvas_video_resolved_parameters(body)
        if adapter == OPENAI_VIDEOS and not agnes_video:
            reference = body.pop("input_reference", None)
            fields = [
                (name, str(value).lower() if isinstance(value, bool) else str(value))
                for name, value in body.items()
            ]
            if reference:
                data, mime = read_canvas_reference(
                    self.transport, "image", 0, reference, loader, deadline
                )
                fields.append(("input_reference", ("input-reference.png", data, mime)))
            body = MultipartBody(fields)
        if adapter == RUNNINGHUB_WORKFLOW:
            if len(json.dumps(body, ensure_ascii=False).encode()) > JSON_LIMIT:
                raise GenerationError("reference_inline_too_large")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GenerationError("timeout")
        raw = self._json_request(
            "POST",
            url,
            {**snapshot, "budget_seconds": remaining},
            headers,
            body,
            secrets=secrets,
            canvas_video=True,
            agnes_video=agnes_video,
        )
        if guard is not None:
            check_agnes_reference_echo(raw, guard, submitted=True, secrets=secrets)
        try:
            result = complete_video_urls(
                snapshot,
                (
                    parse_agnes_video_result(raw, adapter, submitted=True)
                    if agnes_video
                    else parse_result(raw, adapter, submitted=True)
                ),
            )
        except GenerationError as error:
            raise GenerationError(error.code, accepted_unknown=True) from None
        result.resolved_parameters = resolved
        for value in secrets:
            result = self._sanitize(result, value, submitted=True)
        if guard is not None:
            check_agnes_reference_echo(result, guard, submitted=True, secrets=secrets)
        return result
