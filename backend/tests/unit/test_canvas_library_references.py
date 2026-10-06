import pytest

from short_drama.core.exceptions import WorkflowError
from short_drama.service.canvas_document import media_references, resource_identifier
from short_drama.service.canvas_library_references import canvas_media_bindings


@pytest.mark.parametrize(
    "value",
    [
        "resource:9007199254740993",
        "  resource:9007199254740993  ",
        "/api/resources/9007199254740993/file",
        "/api/v1/canvas-runtime/resources/9007199254740993/file?variant=playback",
        "https://canvas.example.test/api/resources/9007199254740993/file#preview",
    ],
)
def test_source_and_host_locators_resolve_the_same_lossless_identity(value):
    assert resource_identifier(value) == 9007199254740993


@pytest.mark.parametrize(
    "value",
    [
        "https://cdn.example.test/image.png",
        "blob:http://local/image",
        "data:image/png;base64,AA",
        "resource:0",
        "resource:-1",
        "resource:not-a-host-id",
        "9007199254740993",
        "http://[invalid/api/resources/42/file",
        "/other/api/resources/42/file",
    ],
)
def test_non_resource_locators_are_not_guessed_or_fetched(value):
    assert resource_identifier(value) is None


def test_only_resource_fields_interpret_urls_and_bare_ids():
    document = {
        "url": "/api/resources/42/file",
        "referenceUrls": ["/api/v1/canvas-runtime/resources/43/file"],
        "referenceResourceIds": ["44", "45"],
        "prompt": "https://canvas.example.test/api/resources/46/file",
        "title": "47",
        "taskId": "48",
        "referenceImages": ["resource:49"],
    }
    assert list(media_references(document)) == [
        (("url",), 42),
        (("referenceUrls", "0"), 43),
        (("referenceResourceIds", "0"), 44),
        (("referenceResourceIds", "1"), 45),
        (("referenceImages", "0"), 49),
    ]
    with pytest.raises(WorkflowError, match="资源 ID 超出有效范围"):
        list(media_references({"resourceId": "18446744073709551616"}))


def test_canvas_guard_matches_source_kind_and_locator_priority():
    document = {
        "nodes": [
            {
                "type": " IMAGE ",
                "metadata": {
                    "assetId": " own ",
                    "storageKey": "resource:41",
                    "content": "resource:42",
                },
            },
            {"type": "video", "metadata": {"assetId": "", "content": "/api/resources/43/file"}},
            {"type": "model", "metadata": {"assetId": "own", "storageKey": "resource:44"}},
            {"type": "text", "metadata": {"assetId": "own", "content": "resource:45"}},
        ],
        "timeline": {
            "clips": [
                {
                    "id": "a",
                    "directMedia": {
                        "kind": "audio",
                        "assetId": "voice",
                        "url": "/api/resources/46/file",
                        "dataUrl": "resource:47",
                    },
                },
                {"id": "b", "nodeId": "an-image"},
            ]
        },
    }
    assert list(canvas_media_bindings(document)) == [("own", 41), ("", 43), ("voice", 46)]


@pytest.mark.parametrize(
    "document",
    [
        {"timeline": {"clips": "not-a-list"}},
        {"nodes": [{"type": "image", "metadata": []}]},
        {"nodes": [{"type": "audio", "metadata": {"assetId": 4, "storageKey": "resource:42"}}]},
    ],
)
def test_unparseable_existing_media_fails_closed(document):
    with pytest.raises(WorkflowError) as error:
        list(canvas_media_bindings(document))
    assert error.value.code == "canvas_asset_reference_invalid"
