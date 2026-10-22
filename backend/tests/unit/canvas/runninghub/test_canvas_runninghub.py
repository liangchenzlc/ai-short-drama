"""固定源内置 RunningHub 的映射、提交/轮询及独立上传鉴权。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from short_drama.ai import GenerationError, GenerationGateway
from short_drama.ai import canvas_runninghub_fields as workflow_fields
from short_drama.ai.adapters import build_submission, select_adapter, validate_request
from short_drama.ai.canvas_credentials import CanvasCredentials
from short_drama.ai.canvas_runninghub_adapters import (
    RUNNINGHUB_ADAPTERS,
    output_url,
    parse_runninghub_result,
    payload_code,
    runninghub_output_kind,
    runninghub_root,
    runninghub_submission,
)
from short_drama.ai.canvas_runninghub_fields import (
    apply_parameters,
    collect_fields,
    fields_for_mode,
    freeze_random_fields,
    node_info,
    validate_slots,
)
from short_drama.schemas.canvas_runninghub import RunningHubField
from short_drama.service.model_runtime_config import refresh_runtime_model

IMAGE = "canvas_runninghub_workflow_image.v1"
WORKFLOW = {
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "旧正向"}, "_meta": {"title": "正向"}},
    "10": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "旧负向"},
        "_meta": {"title": "negative"},
    },
    "3": {"class_type": "INT", "inputs": {"value": 8}},
    "4": {
        "class_type": "ImageResize+",
        "inputs": {"width": 1024, "height": 1024, "multiple_of": 16},
    },
    "5": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
    "6": {"class_type": "LoadImage", "inputs": {"image": "old-2.png"}},
    "7": {"class_type": "KSampler", "inputs": {"seed": 7, "cfg": 5, "model": ["3", 0]}},
}


def snapshot(kind="image"):
    return {
        "base_url": "https://runninghub.example/openapi/v2",
        "model_key": "workflow-17",
        "service_type": kind,
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_node",
        "budget_seconds": 30,
        "capability_cache": {"adapter": f"canvas_runninghub_workflow_{kind}.v1"},
    }


def request(kind="image", *, fields=None, workflow=None, references=None, app=False):
    return {
        "source": {"scene": "canvas_node"},
        "input": {"prompt": "新提示词"},
        "canvas_request": {
            "input": {
                "mode": kind,
                "prompt": "新提示词",
                "config": {"size": "9:16", "vquality": "720"},
                "referenceImages": references or [],
            }
        },
        "canvas_runninghub": {
            "kind": "app" if app else "workflow",
            "workflow_id": "workflow-17",
            "webapp_id": "app-17" if app else "",
            "workflow_json": workflow or {},
            "fields": [
                field.model_dump(by_alias=True, exclude_none=True) for field in fields or []
            ],
        },
    }


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_saved_builtin_profile_selects_an_independent_adapter(kind):
    model = SimpleNamespace(
        runtime_profile={
            "protocol": f"runninghub-workflow-{kind}",
            "runninghub_workflow": {"workflow_id": "workflow-17"},
        },
        base_url="https://runninghub.example/openapi/v2",
        service_type=kind,
        model_key="workflow-17",
        apikey="encrypted-fixture",
        runtime_credentials_cipher=None,
        capability_cache=None,
    )
    assert refresh_runtime_model(model)
    from short_drama.ai.model_identity import model_credential_identity

    assert (
        select_adapter({**vars(model), "credential_identity": model_credential_identity(model)})
        == f"canvas_runninghub_workflow_{kind}.v1"
    )
    assert not refresh_runtime_model(model)


def test_absent_source_infers_width_but_explicit_empty_and_upstream_false_remain_static():
    fields = fields_for_mode(
        [
            RunningHubField(nodeId="1", fieldName="width"),
            RunningHubField(nodeId="2", fieldName="width", source=""),
            RunningHubField(nodeId="3", fieldName="image", sourceFromUpstream=False),
        ],
        "image",
    )
    assert [field.source for field in fields] == ["width", "", None]
    media = fields_for_mode(
        [RunningHubField(nodeId="1", fieldName="image", fieldType="IMAGE")], "image"
    )[0]
    assert media.source == "referenceImage" and media.source_from_upstream


def test_inference_preserves_prompt_ranking_media_order_and_internal_policy():
    fields = collect_fields(WORKFLOW, "image")
    by_id = {field.id: field for field in fields}
    assert by_id["2::text"].source == "prompt" and by_id["2::text"].required
    assert by_id["10::text"].enabled is False
    assert [by_id[key].image_order for key in ("5::image", "6::image")] == [1, 2]
    assert by_id["5::image"].required and not by_id["6::image"].required
    assert not by_id["3::value"].safe_to_override
    assert not by_id["4::width"].enabled
    assert by_id["7::seed"].random_enabled
    assert "7::model" not in by_id


def test_server_filters_unsafe_and_connected_fields_even_when_config_claims_safe():
    fields = [
        RunningHubField(nodeId="3", fieldName="value", safeToOverride=True, fieldValue=999),
        RunningHubField(nodeId="4", fieldName="width", source="width", safeToOverride=True),
        RunningHubField(nodeId="7", fieldName="model", source="prompt"),
    ]
    assert (
        node_info(
            fields, {}, {"mode": "image", "prompt": "new", "config": {"size": "16:9"}}, WORKFLOW
        )
        == []
    )


def test_dynamic_fields_do_not_collapse_two_same_sources_and_seed_freezes_once():
    fields = [
        RunningHubField(nodeId="1", fieldName="width", source="width", fieldType="NUMBER"),
        RunningHubField(nodeId="2", fieldName="width", source="width", fieldType="NUMBER"),
        RunningHubField(nodeId="3", fieldName="seed", randomEnabled=True, max=2**53 - 1),
    ]
    changed = apply_parameters(fields, {}, {"field:1:width": 640, "field:2:width": 1280})
    changed = freeze_random_fields(changed)
    assert changed[0].field_value == 640 and changed[1].field_value == 1280
    assert changed[0].source == "" and changed[1].source == ""
    assert 0 <= changed[2].field_value <= 2**32 - 1
    assert changed[2].random_enabled is False
    payload = request(fields=changed)
    before = deepcopy(payload)
    first = validate_request(snapshot(), payload, IMAGE)
    second = validate_request(snapshot(), payload, IMAGE)
    assert first == second and payload == before
    assert [item["fieldValue"] for item in first["resolved_parameters"]["nodeInfoList"][:2]] == [
        "640",
        "1280",
    ]


@pytest.mark.parametrize(
    "values",
    [
        {"field:999:cfg": 7},
        {"random:1:cfg": "true"},
        {"field:1:cfg": {}},
        {"field:1:cfg": 12},
        {"field:1:cfg": 5.5},
    ],
)
def test_invalid_parameters_fail_before_paid_create(values):
    fields = [
        RunningHubField(
            nodeId="1", fieldName="cfg", fieldType="NUMBER", min=1, max=10, step=1, fieldValue=5
        )
    ]
    with pytest.raises(GenerationError):
        changed = apply_parameters(fields, {}, values)
        build_submission(snapshot(), request(fields=changed), IMAGE)


def test_media_slot_order_and_mask_are_enforced():
    fields = fields_for_mode(
        [
            RunningHubField(nodeId="1", fieldName="image", source="image", sourceIndex=1),
            RunningHubField(nodeId="2", fieldName="mask", source="mask"),
        ],
        "image",
    )
    inputs = {
        "mode": "image",
        "referenceImages": [{"id": "a"}, {"id": "b"}],
        "mask": {"id": "mask"},
    }
    validate_slots(fields, inputs)
    assert node_info(
        fields, {"a": "first.png", "b": "second.png", "mask": "mask.png"}, inputs, {}
    ) == [
        {"nodeId": "1", "fieldName": "image", "fieldValue": "second.png"},
        {"nodeId": "2", "fieldName": "mask", "fieldValue": "mask.png"},
    ]
    with pytest.raises(GenerationError, match="workflow_media_slots_exceeded"):
        validate_slots(fields, {**inputs, "referenceImages": [{}, {}, {}]})


@pytest.mark.parametrize(
    "body,code",
    [
        ({"code": 0, "data": {"status": "queued"}}, 813),
        ({"errorCode": 0, "data": {"state": "running"}}, 804),
        ({"statusCode": 200, "data": {"code": 805}}, 805),
        ({"code": 804, "data": {"code": 0}}, 804),
        ({"data": {"data": {"taskStatus": "completed"}}}, 0),
    ],
)
def test_nested_task_status_wins_over_success_envelope(body, code):
    assert payload_code(body) == code


@pytest.mark.parametrize(
    "body", [{"data": {"taskId": "original-17"}}, {"code": 0, "task_id": "original-17"}]
)
def test_create_can_omit_code_but_must_retain_task_id(body):
    assert parse_runninghub_result(body, IMAGE, submitted=True).provider_task_id == "original-17"


@pytest.mark.parametrize("body", [{}, {"code": 0}, {"data": {"taskId": "bad id"}}])
def test_missing_create_identity_is_unknown(body):
    with pytest.raises(GenerationError) as caught:
        parse_runninghub_result(body, IMAGE, submitted=True)
    assert caught.value.accepted_unknown


def test_output_container_traversal_and_source_result_precedence():
    result = parse_runninghub_result(
        {
            "code": 0,
            "data": {
                "files": [
                    "output/a.png",
                    "output/b.png",
                    "output/first.mp4",
                    "output/last.mp4",
                    "output/sound.mp3",
                ],
                "secret_debug_url": "https://untrusted.example/debug",
            },
        },
        IMAGE,
        submitted=False,
        task_id="original-17",
        root="https://runninghub.example",
    )
    assert [item["url"] for item in result.outputs] == [
        "https://runninghub.example/output/a.png",
        "https://runninghub.example/output/b.png",
        "https://runninghub.example/output/first.mp4",
        "https://runninghub.example/output/last.mp4",
        "https://runninghub.example/output/sound.mp3",
    ]
    assert (
        output_url(
            "https://runninghub.example",
            "https://rh-images-1252422369.cos.ap-beijing.myqcloud.com/a.png",
        )
        == "https://rh-images.xiaoyaoyou.com/a.png"
    )


@pytest.mark.parametrize(
    "path",
    [
        "output/../../secret.png",
        "/output/%2e%2e/secret.png",
        "https://runninghub.example/../secret.png",
    ],
)
def test_output_parent_paths_are_rejected(path):
    with pytest.raises(GenerationError):
        output_url("https://runninghub.example", path)


@pytest.mark.parametrize(
    "url,mime,expected",
    [
        ("https://runninghub.example/extensionless", "image/png; charset=binary", "image"),
        ("https://runninghub.example/output.mp4", "audio/mpeg", "audio"),
        ("https://runninghub.example/output.mp4?name=a.png", "application/octet-stream", "video"),
        ("https://runninghub.example/output.png", "text/html", None),
        ("https://runninghub.example/extensionless", "application/octet-stream", None),
        ("https://runninghub.example/output.avif", "application/octet-stream", None),
    ],
)
def test_download_mime_classification_skips_unsupported_types(url, mime, expected):
    assert runninghub_output_kind(url, mime) == expected


@pytest.mark.parametrize("ignored", ["data:text/plain;base64,aHRtbA==", "data:image/png;base64,"])
def test_inline_empty_or_unsupported_output_does_not_discard_valid_media(ignored):
    result = parse_runninghub_result(
        {"code": 0, "data": [ignored, "data:image/png;base64,aW1hZ2U="]},
        IMAGE,
        submitted=False,
        task_id="original-17",
    )
    assert result.outputs == [{"base64": "aW1hZ2U=", "media_type": "image"}]


def test_all_inline_empty_or_unsupported_outputs_fail_without_new_submission():
    with pytest.raises(GenerationError, match="workflow_output_type_unsupported"):
        parse_runninghub_result(
            {"code": 0, "data": ["data:text/plain;base64,aHRtbA==", "data:image/png;base64,"]},
            IMAGE,
            submitted=False,
            task_id="original-17",
        )


def test_archive_completion_can_resume_with_ignored_outputs_but_not_all_ignored():
    from short_drama.service.ai_generation_service import resume_action

    task = SimpleNamespace(
        status="failed", error={"code": "archive_timeout"}, locked_until=None, next_action=None
    )
    record = SimpleNamespace(
        adapter=IMAGE,
        status="succeeded",
        text_content=None,
        response_data={
            "expected_count": 2,
            "media_manifest": [{"ignored": True}, {"saved": True}],
        },
    )
    assert resume_action(task, record) == "save"
    record.response_data["media_manifest"] = [{"ignored": True}, {"ignored": True}]
    assert resume_action(task, record) is None


class Transport:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **options):
        self.calls.append((method, url, options))
        value = self.responses.pop(0)
        if isinstance(value, GenerationError):
            raise value
        status, body = value
        return status, {"Content-Type": "application/json"}, json.dumps(body).encode()

    def download_media(self, url, limit, **options):
        self.calls.append(("DOWNLOAD", url, options))
        return b"fixture-media", "image/png"


def test_gateway_uses_json_points_key_and_separate_multipart_upload_key():
    gateway = GenerationGateway(SimpleNamespace())
    gateway.transport = Transport(
        [
            (200, {"code": 0, "data": {"fileName": "input/reference.png"}}),
            (200, {"data": {"taskId": "original-17"}}),
        ]
    )
    fields = [RunningHubField(nodeId="1", fieldName="image", source="referenceImage")]
    payload = request(
        fields=fields,
        workflow={"1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}}},
        references=[{"id": "r", "storageKey": "resource:17", "type": "image/png"}],
    )
    credential = CanvasCredentials(
        apiKey="fixture-points",
        runningHubUploadApiKey="fixture-upload",
        headers=[{"name": "X-Fixture", "value": "custom"}],
    )
    result = gateway.submit(
        snapshot(),
        payload,
        credential,
        IMAGE,
        canvas_reference_loader=lambda *_args: b"fixture-image",
    )
    assert result.provider_task_id == "original-17"
    upload, create = gateway.transport.calls
    assert upload[0] == create[0] == "POST"
    assert upload[1] == "https://runninghub.example/task/openapi/upload"
    assert upload[2]["body"].fields[:2] == [("apiKey", "fixture-upload"), ("fileType", "input")]
    assert create[2]["body"] == {
        "apiKey": "fixture-points",
        "workflowId": "workflow-17",
        "nodeInfoList": [
            {"nodeId": "1", "fieldName": "image", "fieldValue": "input/reference.png"}
        ],
    }
    assert all("Authorization" not in call[2]["headers"] for call in (upload, create))
    assert "fixture-points" not in str(result.resolved_parameters)
    assert "fixture-upload" not in str(result.resolved_parameters)


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_poll_post_keeps_original_identity_without_create_and_download_has_no_credentials(kind):
    adapter = next(key for key, value in RUNNINGHUB_ADAPTERS.items() if value == kind)
    gateway = GenerationGateway(SimpleNamespace())
    gateway.transport = Transport([(200, {"code": 0, "data": {"status": "queued"}})])
    credential = CanvasCredentials(
        apiKey="fixture-points", headers=[{"name": "X-Fixture", "value": "private"}]
    )
    result = gateway.poll(snapshot(kind), "original-17", credential, adapter)
    assert result.provider_task_id == "original-17" and result.status == "submitted"
    call = gateway.transport.calls[0]
    assert call[0] == "POST" and call[1].endswith("/task/openapi/outputs")
    assert call[2]["body"] == {"apiKey": "fixture-points", "taskId": "original-17"}
    gateway.download_media(
        "https://runninghub.example/output/image.png",
        100,
        snapshot=snapshot(kind),
        credential=credential,
    )
    assert "headers" not in gateway.transport.calls[1][2]


@pytest.mark.parametrize(
    "failure", [(503, {}), GenerationError("transport_error", accepted_unknown=True), (200, {})]
)
def test_poll_failure_is_retryable_query_failure_and_never_paid_unknown(failure):
    gateway = GenerationGateway(SimpleNamespace())
    gateway.transport = Transport([failure])
    with pytest.raises(GenerationError) as caught:
        gateway.poll(snapshot(), "original-17", CanvasCredentials(apiKey="fixture-points"), IMAGE)
    assert not caught.value.accepted_unknown and caught.value.retryable
    assert len(gateway.transport.calls) == 1


def test_workflow_and_app_use_distinct_create_endpoints():
    for app, suffix, identity in (
        (False, "create", "workflowId"),
        (True, "ai-app/run", "webappId"),
    ):
        url, headers, body = build_submission(snapshot(), request(app=app), IMAGE)
        assert url.endswith("/task/openapi/" + suffix) and identity in body and headers == {}
    assert runninghub_root("https://runninghub.example/OPENAPI/v2/") == "https://runninghub.example"


def test_app_public_fields_infer_prompt_and_order_media_without_vendor_metadata():
    fields = workflow_fields.collect_app_fields(
        [
            {"nodeId": "9", "fieldName": "image", "fieldValue": "b.png", "description": "vendor"},
            {"nodeId": "2", "fieldName": "image", "fieldValue": "a.png"},
            {"nodeId": "1", "fieldName": "prompt", "fieldValue": "旧提示词"},
            {"nodeId": "4", "fieldName": "cfg", "fieldValue": 5, "min": 1, "max": 10},
            {"fieldName": "invalid"},
        ],
        "image",
    )
    assert [field.node_id for field in fields] == ["1", "2", "4", "9"]
    assert fields[0].source == "prompt" and fields[0].required
    images = [field for field in fields if field.source == "referenceImage"]
    assert [field.image_order for field in images] == [1, 2]
    assert [field.required for field in images] == [True, False]
    assert fields[2].role == "business" and fields[2].enabled


@pytest.mark.parametrize(
    "key", ["source:aspect_ratio", "source:ratio", "field:1:aspectRatio", "FIELD:1:视频比例"]
)
def test_saved_legacy_parameter_keys_match_only_a_trusted_field(key):
    fields = [
        RunningHubField(
            nodeId="1", fieldName="aspect_ratio", label="视频比例", source="aspectRatio"
        )
    ]
    changed = apply_parameters(fields, {}, {key: "16:9"})
    assert changed[0].field_value == "16:9" and changed[0].source == ""
    with pytest.raises(GenerationError):
        apply_parameters(fields, {}, {"field:2:aspectRatio": "16:9"})


def test_missing_schema_defers_media_slots_until_worker_schema_fetch():
    payload = request(references=[{"id": "r", "storageKey": "resource:17"}])
    assert validate_request(snapshot(), payload, IMAGE)["resolved_parameters"] == {
        "workflowId": "workflow-17"
    }
    with pytest.raises(GenerationError, match="workflow_media_slots_exceeded"):
        runninghub_submission(snapshot(), payload, IMAGE, files={})


def test_failed_query_can_resume_only_a_known_runninghub_task_within_budget():
    from datetime import timedelta

    from short_drama.service.ai_generation_service import can_retry, resume_action
    from short_drama.service.base import utcnow

    task = SimpleNamespace(
        status="failed",
        error={"code": "invalid_response"},
        locked_until=None,
        next_action=None,
        cancel_requested=False,
        started_at=utcnow(),
    )
    record = SimpleNamespace(
        adapter=IMAGE,
        status="sent",
        provider_task_id="original-17",
        response_data={},
        request_data={"source": {"scene": "canvas_node"}},
        config_snapshot={"budget_seconds": 30},
    )
    assert resume_action(task, record) == "poll" and not can_retry(task, record)
    for error in (
        {"code": "provider_endpoint", "http_status": 404},
        {"code": "provider_rate_limit"},
        {"code": "provider_task_not_ready"},
    ):
        task.error = error
        assert resume_action(task, record) == "poll" and not can_retry(task, record)
    for error in (
        {"code": "provider_auth", "http_status": 401},
        {"code": "provider_endpoint", "http_status": 405},
    ):
        task.error = error
        assert resume_action(task, record) is None
    task.error = {"code": "invalid_response"}
    for status in ("unknown", "failed"):
        record.status = status
        assert resume_action(task, record) is None
    record.status = "sent"
    record.provider_task_id = None
    assert resume_action(task, record) is None
    record.provider_task_id = "original-17"
    task.started_at = utcnow() - timedelta(seconds=31)
    assert resume_action(task, record) is None
