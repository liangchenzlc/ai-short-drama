"""画布原版任务合同：服务端模型身份、输入一致性及无静默参数降级。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
from short_drama.service.canvas_generation_inputs import generation_payload


def request(kind="text", config=None):
    return {
        "projectId": "original-canvas",
        "type": f"canvas_{kind}",
        "operation": "text_to_video" if kind == "video" else kind,
        "prompt": "原版有效提示词",
        "model": "host-9007199254740995::fixture",
        "logicalModelId": "9007199254740995",
        "input": {
            "mode": kind,
            "prompt": "原版有效提示词",
            "config": config or {},
            "metadata": {
                "nodeId": "target",
                "sourceNodeId": "source",
                "clientOperationId": "original-operation-1",
            },
        },
    }


def test_runtime_identifiers_and_private_metadata_round_trip_losslessly():
    body = request()
    body["input"]["metadata"]["skillVersions"] = {"skill-one": "9007199254740997"}
    parsed = CanvasRuntimeTaskCreate.model_validate(body)
    encoded = parsed.model_dump(mode="json", by_alias=True)
    assert encoded["logicalModelId"] == "9007199254740995"
    assert encoded["input"]["metadata"]["skillVersions"] == {"skill-one": "9007199254740997"}
    assert encoded["projectId"] == "original-canvas"


@pytest.mark.parametrize("change", ["mode", "prompt", "model", "operation_key", "nested_config"])
def test_runtime_rejects_incoherent_or_untyped_requests(change):
    body = request()
    if change == "mode":
        body["input"]["mode"] = "video"
    elif change == "prompt":
        body["input"]["prompt"] = "另一个提示词"
    elif change == "model":
        body.pop("logicalModelId")
    elif change == "operation_key":
        body["input"]["metadata"].pop("clientOperationId")
    else:
        body["input"]["config"]["size"] = {"untyped": "object"}
    with pytest.raises(ValidationError):
        CanvasRuntimeTaskCreate.model_validate(body)


@pytest.mark.parametrize("kind", ["text", "image", "video", "audio"])
def test_basic_runtime_payloads_use_standard_validated_inputs(kind):
    parsed = CanvasRuntimeTaskCreate.model_validate(request(kind))
    payload = generation_payload(parsed, 9007199254740999)
    assert payload["project_id"] == "9007199254740999"
    assert payload["config_id"] == "9007199254740995"
    assert "apikey" not in payload and "source" not in payload
    if kind == "text":
        assert payload["input"]["messages"] == [{"role": "user", "content": parsed.prompt}]
    elif kind == "audio":
        assert payload["input"]["text"] == parsed.prompt
        assert payload["parameters"]["voice"] == "alloy"
    else:
        assert payload["input"]["prompt"] == parsed.prompt


def test_text_history_and_system_prompt_preserve_order_and_request_body():
    body = request(config={"systemPrompt": "系统设置"})
    body["input"]["textHistory"] = [{"role": "assistant", "content": "之前的回答"}]
    before = deepcopy(body)
    payload = generation_payload(CanvasRuntimeTaskCreate.model_validate(body), 1)
    assert payload["input"]["messages"] == [
        {"role": "system", "content": "系统设置"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": body["prompt"]},
    ]
    assert body == before


@pytest.mark.parametrize(
    "option,value",
    [
        ("apiKey", "secret"),
        ("baseUrl", "https://evil.test"),
        ("customProviderOption", "unsupported"),
    ],
)
def test_client_transport_credentials_and_unknown_options_are_not_accepted(option, value):
    parsed = CanvasRuntimeTaskCreate.model_validate(request(config={option: value}))
    with pytest.raises(WorkflowError) as error:
        generation_payload(parsed, 1)
    assert error.value.code == "canvas_generation_option_unsupported"
    assert value not in error.value.message


@pytest.mark.parametrize(
    "kind,option,value",
    [
        ("image", "transparentBackground", "true"),
        ("audio", "audioSpeed", "1.5"),
        ("audio", "audioFormat", "wav"),
        ("video", "videoGenerateAudio", "true"),
    ],
)
def test_unimplemented_options_fail_before_admission_instead_of_using_other_defaults(
    kind, option, value
):
    parsed = CanvasRuntimeTaskCreate.model_validate(request(kind, {option: value}))
    with pytest.raises(WorkflowError, match=option):
        generation_payload(parsed, 1)


def test_image_references_use_stable_server_ids_and_keep_order():
    body = request("image")
    body["input"]["referenceImages"] = [
        {"storageKey": "resource:9007199254740997", "dataUrl": ""},
        {"storageKey": "resource:9007199254740999", "dataUrl": ""},
    ]
    payload = generation_payload(CanvasRuntimeTaskCreate.model_validate(body), 1)
    assert payload["input"]["reference_media_ids"] == ["9007199254740997", "9007199254740999"]
    body["input"]["referenceImages"][0] = {"url": "https://example.test/unarchived.png"}
    with pytest.raises(WorkflowError) as error:
        generation_payload(CanvasRuntimeTaskCreate.model_validate(body), 1)
    assert error.value.code == "canvas_reference_not_saved"


@pytest.mark.parametrize("location", ["metadata", "capabilityOptions"])
@pytest.mark.parametrize(
    "key",
    [
        "apiKey",
        "api_key",
        "api-key",
        "API.KEY",
        "baseUrl",
        "base-url",
        "credentialRef",
        "credential-ref",
        "authorization",
        "Authorization",
        "secretKey",
        "secret_key",
        "secret-key",
        "SECRET.KEY",
        "headers",
        "HEADERS",
    ],
)
def test_private_metadata_cannot_smuggle_transport_credentials_into_frozen_records(location, key):
    body = request()
    body["input"].setdefault(location, {})["privateSkill"] = {
        "nested": [{key: "controlled-secret"}]
    }
    with pytest.raises(ValidationError, match="client credentials"):
        CanvasRuntimeTaskCreate.model_validate(body)


def test_credential_key_words_in_prompt_and_reference_labels_remain_valid_content():
    body = request("image")
    body["prompt"] = body["input"]["prompt"] = "解释 secretKey、headers 和 Authorization 的用法"
    body["input"]["metadata"]["description"] = "apiKey baseUrl credentialRef secret_key headers"
    body["input"]["capabilityOptions"] = {"description": "api-key secret-key headers"}
    body["input"]["referenceImages"] = [
        {
            "id": "secretKey",
            "name": "Authorization headers apiKey",
            "type": "secret_key",
            "storageKey": "resource:9007199254740997",
        }
    ]
    parsed = CanvasRuntimeTaskCreate.model_validate(body)
    encoded = parsed.model_dump(mode="json", by_alias=True)
    assert encoded["prompt"] == body["prompt"]
    assert encoded["input"]["metadata"]["description"] == body["input"]["metadata"]["description"]
    assert encoded["input"]["capabilityOptions"] == body["input"]["capabilityOptions"]
    assert encoded["input"]["referenceImages"][0]["id"] == "secretKey"
    assert encoded["input"]["referenceImages"][0]["name"] == "Authorization headers apiKey"
    assert encoded["input"]["referenceImages"][0]["type"] == "secret_key"


@pytest.mark.parametrize("location", ["config", "metadata"])
def test_nonfinite_values_are_rejected_before_mysql_json_write(location):
    body = request()
    body["input"][location]["value"] = float("nan")
    with pytest.raises(ValidationError, match="finite"):
        CanvasRuntimeTaskCreate.model_validate(body)
