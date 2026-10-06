import hashlib
import json

import pytest

from short_drama.core.exceptions import WorkflowError
from short_drama.service.canvas_plugin_catalog import (
    SOURCE_COMMIT,
    bundled_manifest_snapshot,
    provider_catalog,
)


def test_catalog_keeps_every_fixed_manifest_with_verifiable_source_bytes():
    snapshot = bundled_manifest_snapshot()
    assert snapshot["sourceCommit"] == SOURCE_COMMIT == "4ca2a65a7780a8dfcaaa86c33679f84fb04e055c"
    assert len(snapshot["packages"]) == 85
    for item in snapshot["packages"]:
        assert hashlib.sha256(item["manifestText"].encode()).hexdigest() == item["sourceSha256"]
        manifest = json.loads(item["manifestText"])
        assert manifest["description"]
        assert manifest["documentation"]


def test_catalog_distinguishes_metadata_from_execution_and_preserves_source_projection():
    providers = {item["id"]: item for item in provider_catalog("user.custom-channel")}
    assert len(providers) == 86
    for identifier in (
        "chat-completion",
        "openai-response",
        "openai-image",
        "openai-audio",
        "volcengine-ark-image",
        "volcengine-ark-video",
        "dashscope-qwen-image",
        "dashscope-wan-video",
    ):
        assert providers[identifier]["enabled"]
        assert providers[identifier]["executionSupported"]
    unsupported = providers["gemini-image"]
    assert unsupported["catalogAvailable"]
    assert not unsupported["enabled"] and not unsupported["executionSupported"]
    assert unsupported["unavailableReason"]
    image = providers["openai-image"]
    assert image["create"] == "POST /v1/images/generations"
    assert image["version"] == "2.0.0"
    assert image["vendor"] == "BeefTV Contributors"
    assert image["parameters"] and image["description"]
    assert image["sourceCommit"] == SOURCE_COMMIT
    assert len(image["sourceSha256"]) == 64


def test_catalog_filters_scope_and_capability_without_discarding_unavailable_metadata():
    providers = provider_catalog("canvas", "image")
    assert providers and all(item["categories"] == ["image"] for item in providers)
    assert any(not item["enabled"] for item in providers)
    assert all("canvas" in item["scopes"] for item in providers)


@pytest.mark.parametrize("scope,capability", [("bogus", None), ("canvas", "bogus")])
def test_catalog_rejects_unknown_scope_or_capability(scope, capability):
    with pytest.raises(WorkflowError) as caught:
        provider_catalog(scope, capability)
    assert caught.value.status_code == 422
