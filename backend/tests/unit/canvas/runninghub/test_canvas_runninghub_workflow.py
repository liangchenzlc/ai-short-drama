"""固定源声明式 RunningHub 视频合同，独立于内置 JSON/POST 工作流。"""

import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai.adapters.capabilities import validate_request
from short_drama.ai.adapters.results import parse_result, poll_endpoint
from short_drama.ai.adapters.submission import build_submission
from short_drama.ai.types import GenerationError
from short_drama.service.ai_config.management.runtime import refresh_runtime_model
from tests.unit.canvas.video.test_canvas_video_transport import frozen_video, video_request

ADAPTER = "canvas_runninghub_workflow.v1"
BASE = "https://workflow.example/proxy/v1"


def request(options=None):
    value = video_request()
    if options is not None:
        value["canvas_request"]["input"]["metadata"]["providerOptions"] = {
            "runninghub-workflow": options
        }
    return value


def test_saved_protocol_selects_declarative_video_and_catalog_support():
    model = SimpleNamespace(
        runtime_profile={"protocol": "runninghub-workflow", "api_format": "openai"},
        base_url=BASE,
        service_type="video",
        model_key="workflow-17",
        apikey="encrypted-fixture",
        runtime_credentials_cipher=None,
        capability_cache=None,
    )
    assert refresh_runtime_model(model)
    assert model.capability_cache["adapter"] == ADAPTER
    from short_drama.service.canvas.tools.plugin_catalog import provider_catalog

    provider = next(
        item for item in provider_catalog("canvas", "video") if item["id"] == "runninghub-workflow"
    )
    assert provider["executionSupported"] is True
    assert provider["poll"] == "GET /task/openapi/status"


def test_default_create_body_uses_only_manifest_fields_and_keeps_prompt():
    payload = request()
    before = deepcopy(payload)
    url, headers, body = build_submission(frozen_video(BASE), payload, ADAPTER)
    assert url == BASE + "/task/openapi/create"
    assert headers == {}
    assert body == {
        "model": "sora",
        "input": {"prompt": "源提示词", "images": [], "videos": [], "audios": []},
    }
    assert payload == before
    assert validate_request(frozen_video(BASE), payload, ADAPTER)["adapter"] == ADAPTER


def test_full_open_input_workflow_and_extension_fields_are_preserved():
    options = {
        "version": "version-2",
        "model": "workflow-model",
        "input": {"dynamic": {"nodes": [0, False, None, {"extra": "完整输入"}]}},
        "workflow": {"4": {"class_type": "Sampler", "inputs": {"seed": 0}}},
        "client_id": "client-17",
        "webhook": "https://callback.example/finished",
        "webhook_events_filter": ["completed", "failed"],
    }
    _, _, body = build_submission(frozen_video(BASE), request(options), ADAPTER)
    assert body == {
        **{key: value for key, value in options.items() if key != "workflow"},
        "prompt": options["workflow"],
    }


@pytest.mark.parametrize("empty", [None, "", " \t", [], {}])
def test_manifest_empty_coalesces_input_model_and_omits_optional_fields(empty):
    _, _, body = build_submission(
        frozen_video(BASE),
        request(
            {
                key: empty
                for key in (
                    "version",
                    "model",
                    "input",
                    "workflow",
                    "client_id",
                    "webhook",
                    "webhook_events_filter",
                )
            }
        ),
        ADAPTER,
    )
    assert set(body) == {"model", "input"}
    assert body["model"] == "sora" and body["input"]["prompt"] == "源提示词"


@pytest.mark.parametrize("nonempty", [0, False])
def test_manifest_zero_and_false_are_not_empty(nonempty):
    _, _, body = build_submission(
        frozen_video(BASE),
        request({"version": nonempty, "input": nonempty, "workflow": nonempty}),
        ADAPTER,
    )
    assert body["version"] is nonempty and body["input"] is nonempty and body["prompt"] is nonempty


@pytest.mark.parametrize(
    "options",
    [
        {"apiKey": "untrusted"},
        {"input": {"authorization": "untrusted"}},
        {"input": {"x": float("nan")}},
        {"unmapped": 7},
        [1],
    ],
)
def test_unmapped_or_nonpublic_provider_options_fail_before_submission(options):
    with pytest.raises(GenerationError) as caught:
        build_submission(frozen_video(BASE), request(options), ADAPTER)
    assert caught.value.code == "unsupported_parameters"


def test_poll_keeps_no_task_id_query_body_or_path_and_uses_source_prefix():
    assert (
        poll_endpoint(frozen_video(BASE), "original/id-17", ADAPTER)
        == BASE + "/task/openapi/status"
    )
    assert (
        poll_endpoint(frozen_video("https://workflow.example"), "id-17", ADAPTER)
        == "https://workflow.example/v1/task/openapi/status"
    )


@pytest.mark.parametrize("path", ["id", "request_id", "prompt_id"])
def test_create_identity_paths_and_pending_are_retained(path):
    result = parse_result({path: "original-17", "status": "in_queue"}, ADAPTER, submitted=True)
    assert result.status == "submitted" and result.provider_task_id == "original-17"


@pytest.mark.parametrize(
    "status", ["running", "processing", "in_progress", "executing", "task_status_running"]
)
def test_processing_does_not_complete_even_when_data_has_media(status):
    result = parse_result(
        {"status": status, "data": [{"url": "/movie.mp4"}]},
        ADAPTER,
        submitted=False,
        task_id="original-17",
    )
    assert result.status == "submitted" and result.outputs == []


@pytest.mark.parametrize(
    "status", [None, "queued", "pending", "unrecognized", "task_status_queued", "done"]
)
def test_pending_with_data_media_completes_and_only_data_maps_media(status):
    body = {"data": [{"url": "/movie.mp4"}], "url": "https://ignored.example/movie.mp4"}
    if status is not None:
        body["state"] = status
    result = parse_result(body, ADAPTER, submitted=False, task_id="original-17")
    assert result.status == "succeeded" and result.outputs == [
        {"url": "/movie.mp4", "media_type": "video"}
    ]


@pytest.mark.parametrize("code", [None, 0, "", "0", "ok", "success", "succeeded", "true"])
def test_manifest_success_error_codes_do_not_fail(code):
    result = parse_result(
        {"error": {"code": code}, "data": {"video_url": "/movie.mp4"}},
        ADAPTER,
        submitted=False,
        task_id="original-17",
    )
    assert result.status == "succeeded"


@pytest.mark.parametrize("code", [False, True, 1, "failed", {}])
def test_manifest_error_codes_and_message_fail(code):
    result = parse_result(
        {"error": {"code": code, "message": "工作流执行失败"}},
        ADAPTER,
        submitted=False,
        task_id="original-17",
    )
    assert result.status == "failed" and result.error["message"] == "工作流执行失败"


@pytest.mark.parametrize("status", ["failed", "cancelled", "aborted", "expired"])
def test_terminal_failure_has_no_media_delivery(status):
    result = parse_result(
        {"status": status, "data": "/movie.mp4"}, ADAPTER, submitted=False, task_id="original-17"
    )
    assert result.status == "failed" and not result.outputs


@pytest.mark.parametrize(
    "alias",
    [
        "url",
        "file_url",
        "fileUrl",
        "image_url",
        "imageUrl",
        "video_url",
        "videoUrl",
        "audio_url",
        "audioUrl",
        "uri",
    ],
)
def test_source_media_url_aliases(alias):
    result = parse_result(
        {"data": [{alias: "/movie.mp4"}]}, ADAPTER, submitted=False, task_id="original-17"
    )
    assert result.outputs == [{"url": "/movie.mp4", "media_type": "video"}]


@pytest.mark.parametrize("alias", ["dataUrl", "data_url", "b64_json"])
def test_source_inline_media_aliases_take_precedence_over_url(alias):
    encoded = base64.b64encode(b"video fixture").decode()
    result = parse_result(
        {"data": [{alias: encoded, "url": "https://unused.example/movie.mp4"}]},
        ADAPTER,
        submitted=False,
        task_id="original-17",
    )
    assert result.outputs == [{"base64": encoded, "media_type": "video"}]


def test_complete_without_media_fails_without_inventing_content_download():
    with pytest.raises(GenerationError) as caught:
        parse_result(
            {"status": "completed", "url": "https://ignored.example/movie.mp4"},
            ADAPTER,
            submitted=False,
            task_id="original-17",
        )
    assert caught.value.code == "missing_output"


def test_invalid_create_is_unknown_while_poll_can_retry_original_task():
    for submitted in (True, False):
        with pytest.raises(GenerationError) as caught:
            parse_result(
                {"data": [{"b64_json": "invalid%%%"}]},
                ADAPTER,
                submitted=submitted,
                task_id="original-17" if not submitted else None,
            )
        assert caught.value.accepted_unknown is submitted


def test_default_video_capability_rejects_video_audio_references_until_saved_override():
    payload = video_request(videos=[{"url": "https://reference.example/video.mp4"}])
    with pytest.raises(GenerationError) as caught:
        build_submission(frozen_video(BASE), payload, ADAPTER)
    assert caught.value.code == "reference_limit"
    snapshot = frozen_video(BASE)
    snapshot["canvas_video_capability"] = {
        "references": {"maxImages": 9, "maxVideos": 1, "maxAudios": 1}
    }
    _, _, body = build_submission(snapshot, payload, ADAPTER)
    assert body["input"]["videos"] == ["https://reference.example/video.mp4"]
