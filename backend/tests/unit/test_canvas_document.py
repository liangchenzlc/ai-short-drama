from copy import deepcopy

import pytest
from pydantic import ValidationError

from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.canvas import CanvasDocument
from short_drama.service.canvas_document import project_document, split_document


def document():
    return {
        "id": "canvas-one",
        "title": "画布",
        "revision": "18446744073709551610",
        "nodes": [
            {
                "id": "a",
                "type": "text",
                "title": "正文",
                "position": {"x": -0.125, "y": 800.375},
                "width": 256.75,
                "height": 100,
                "metadata": {
                    "content": "共享的作品",
                    "prompt": "仅本人提示词",
                    "model": "private-model",
                    "taskId": "123",
                    "taskFailureDiagnostics": {"message": "仅本人错误"},
                    "futurePrivateExtension": {"a": 1},
                    "storyboard": {
                        "rows": [{"id": "row1", "plotDescription": "共享剧情", "taskId": "secret"}]
                    },
                },
            }
        ],
        "connections": [],
        "chatSessions": [{"id": "chat", "messages": []}],
        "activeChatId": "chat",
        "viewport": {"x": 20.5, "y": 1, "k": 0.7},
    }


def test_author_roundtrip_and_shared_projection_do_not_publish_private_extensions():
    original = document()
    parsed = CanvasDocument.model_validate(original)
    assert parsed.model_dump(mode="json", by_alias=True)["revision"] == original["revision"]
    shared, private = split_document(parsed)
    metadata = shared["nodes"][0]["metadata"]
    assert metadata["content"] == "共享的作品"
    assert metadata["storyboard"]["rows"][0] == {"id": "row1", "plotDescription": "共享剧情"}
    assert "prompt" not in metadata and "taskId" not in metadata
    assert "chatSessions" not in shared and "viewport" not in shared
    projected = project_document(shared, private)
    expected = {k: v for k, v in original.items() if k not in {"id", "revision"}}
    assert projected == expected
    assert original == document()


def test_media_projection_uses_stable_files_and_preserves_text_and_private_prose():
    source = document()
    source["nodes"][0]["metadata"]["content"] = "blob:literal-text"
    image = deepcopy(source["nodes"][0])
    image.update(id="image", type="image")
    image["metadata"] = {
        "content": "blob:page-preview",
        "storageKey": "resource:42",
        "previewContent": "blob:unfinished-preview",
        "prompt": "blob:literal-prompt",
    }
    source["nodes"].append(image)
    source["timeline"] = {
        "clips": [
            {
                "id": "clip",
                "directMedia": {
                    "kind": "video",
                    "storageKey": "resource:44",
                    "url": "https://old/api/resources/44/file?signature=expired&variant=playback#t=2",
                },
            }
        ]
    }
    shared, private = split_document(CanvasDocument.model_validate(source))
    projected = project_document(shared, private)
    assert projected["nodes"][1]["metadata"]["content"] == (
        "/api/v1/canvas-runtime/resources/42/file"
    )
    assert "previewContent" not in projected["nodes"][1]["metadata"]
    assert projected["nodes"][1]["metadata"]["prompt"] == "blob:literal-prompt"
    assert projected["nodes"][0]["metadata"]["content"] == "blob:literal-text"
    assert projected["timeline"]["clips"][0]["directMedia"]["url"] == (
        "/api/v1/canvas-runtime/resources/44/file?variant=playback#t=2"
    )
    assert source["nodes"][1]["metadata"]["content"] == "blob:page-preview"


def test_media_locations_cover_derived_images_and_do_not_reinterpret_text_or_unknown_types():
    from short_drama.service.canvas_media_locators import canonicalize_canvas_media

    source = {
        "nodes": [
            {
                "type": "text",
                "metadata": {
                    "content": "blob:literal",
                    "mimeType": "image/png",
                    "storageKey": "resource:42",
                },
            },
            {
                "type": "drawing",
                "metadata": {
                    "drawingPreviewUrl": "blob:preview",
                    "drawingPreviewStorageKey": "resource:18446744073709551615",
                },
            },
            {"type": "director", "metadata": {"directorCoverUrl": "data:image/png;base64,preview"}},
            {
                "type": "video",
                "metadata": {
                    "videoTrimSource": {"content": "blob:trim", "storageKey": "resource:44"}
                },
            },
        ],
        "directorScenes": [
            {
                "kind": "model",
                "storageKey": "resource:45",
                "url": "https://provider.example/signed?token=temporary",
            }
        ],
        "unknown": {"kind": [], "type": {}, "content": "blob:unknown"},
    }
    result = canonicalize_canvas_media(source)
    assert result["nodes"][0] == source["nodes"][0]
    assert (
        result["nodes"][1]["metadata"]["drawingPreviewUrl"]
        == "/api/v1/canvas-runtime/resources/18446744073709551615/file"
    )
    assert result["nodes"][2]["metadata"] == {}
    assert (
        result["nodes"][3]["metadata"]["videoTrimSource"]["content"]
        == "/api/v1/canvas-runtime/resources/44/file"
    )
    assert result["directorScenes"][0]["url"] == "/api/v1/canvas-runtime/resources/45/file"
    assert result["unknown"] == source["unknown"]
    assert canonicalize_canvas_media(result) == result


def test_media_without_persistent_identity_cannot_be_newly_published():
    from short_drama.service.canvas_media_locators import canonicalize_canvas_media

    source = {
        "nodes": [
            {"type": "image", "metadata": {"content": "blob:orphan", "storageKey": "local-browser"}}
        ]
    }
    with pytest.raises(WorkflowError) as error:
        canonicalize_canvas_media(source)
    assert error.value.code == "canvas_media_not_persisted"
    assert canonicalize_canvas_media(source, strict=False) == source
    source["nodes"][0]["metadata"] = {"previewContent": "blob:pending"}
    assert canonicalize_canvas_media(source)["nodes"][0]["metadata"] == {}


def test_nested_private_arrays_follow_stable_identity_after_other_member_edits():
    source = document()
    rows = source["nodes"][0]["metadata"]["storyboard"]["rows"]
    rows.append({"id": "row2", "plotDescription": "第二镜", "prompt": "第二镜私密词"})
    source["directorScenes"] = [
        {"id": "scene1", "shots": [{"id": "shot1", "name": "镜头", "taskId": "job1"}]},
        {"id": "scene2", "name": "场景二", "prompt": "场景二私密词"},
    ]
    source["timeline"] = {
        "tracks": [
            {
                "id": "t1",
                "clips": [
                    {"id": "c1", "title": "片段一", "prompt": "片段一私密词"},
                    {"id": "c2", "title": "片段二", "taskId": "job2"},
                ],
            }
        ]
    }
    shared, private = split_document(CanvasDocument.model_validate(source))
    shared_rows = shared["nodes"][0]["metadata"]["storyboard"]["rows"]
    shared_rows.reverse()
    shared_rows[0]["plotDescription"] = "成员修改后的剧情"
    shared_rows.insert(0, {"id": "new", "plotDescription": "新插入"})
    shared["directorScenes"].reverse()
    shared["timeline"]["tracks"][0]["clips"].reverse()
    projected = project_document(shared, private)
    actual = projected["nodes"][0]["metadata"]["storyboard"]["rows"]
    assert actual[0] == shared_rows[0]
    assert actual[1]["prompt"] == "第二镜私密词" and "taskId" not in actual[1]
    assert actual[2]["taskId"] == "secret" and "prompt" not in actual[2]
    assert projected["directorScenes"][0]["prompt"] == "场景二私密词"
    assert projected["directorScenes"][1]["shots"][0]["taskId"] == "job1"
    assert projected["timeline"]["tracks"][0]["clips"][0]["taskId"] == "job2"
    assert project_document(shared, None) == shared


def test_deleted_parent_and_array_items_are_not_resurrected_by_private_projection():
    shared, private = split_document(CanvasDocument.model_validate(document()))
    shared["nodes"][0]["metadata"]["storyboard"]["rows"] = []
    assert project_document(shared, private)["nodes"][0]["metadata"]["storyboard"] == {"rows": []}
    del shared["nodes"][0]["metadata"]["storyboard"]
    assert "storyboard" not in project_document(shared, private)["nodes"][0]["metadata"]


def test_nested_library_ids_are_private_while_stable_media_and_order_stay_shared():
    source = document()
    media = {"kind": "image", "assetId": "private-library-key", "storageKey": "resource:42"}
    source["timeline"] = {"clips": [{"id": "clip1", "directMedia": media}]}
    source["directorScenes"] = [{"id": "scene1", "objects": [{"id": "object1", **media}]}]
    source["nodes"][0]["metadata"]["assetBindings"] = [{"id": "binding1", **media}]
    shared, private = split_document(CanvasDocument.model_validate(source))
    visible_media = {"kind": "image", "storageKey": "resource:42"}
    assert shared["timeline"]["clips"][0]["directMedia"] == visible_media
    assert shared["directorScenes"][0]["objects"][0] == {"id": "object1", **visible_media}
    assert shared["nodes"][0]["metadata"]["assetBindings"] == [{"id": "binding1", **visible_media}]
    own = project_document(shared, private)
    assert own["timeline"] == source["timeline"]
    assert own["directorScenes"] == source["directorScenes"]
    assert own["nodes"] == source["nodes"]


def test_explicit_null_private_values_roundtrip_without_restoring_previous_values():
    source = document()
    source["nodes"][0]["metadata"]["prompt"] = None
    source["nodes"][0]["metadata"]["storyboard"]["rows"][0]["taskId"] = None
    shared, private = split_document(CanvasDocument.model_validate(source))
    projected = project_document(shared, private)
    assert projected["nodes"] == source["nodes"]


def test_unkeyed_items_match_exact_public_content_and_ambiguous_identity_is_rejected():
    source = document()
    source["timeline"] = {
        "clips": [{"title": "一", "prompt": "private1"}, {"title": "二", "prompt": "private2"}]
    }
    shared, private = split_document(CanvasDocument.model_validate(source))
    shared["timeline"]["clips"].reverse()
    projected = project_document(shared, private)
    assert [item["prompt"] for item in projected["timeline"]["clips"]] == ["private2", "private1"]
    shared["timeline"]["clips"][0]["title"] = "没有稳定键的成员改动"
    assert "prompt" not in project_document(shared, private)["timeline"]["clips"][0]
    source["timeline"]["clips"][1]["title"] = "一"
    with pytest.raises(WorkflowError) as error:
        split_document(CanvasDocument.model_validate(source))
    assert error.value.code == "canvas_private_identity_required"


def test_legacy_positional_projection_requires_explicit_migration_and_reserved_keys_reject():
    shared, _ = split_document(CanvasDocument.model_validate(document()))
    legacy = {"nodes": {"a": {"storyboard": {"rows": [{"taskId": "secret"}]}}}}
    with pytest.raises(WorkflowError) as error:
        project_document(shared, legacy)
    assert error.value.code == "canvas_private_projection_upgrade_required"
    source = document()
    source["nodes"][0]["metadata"]["$canvas_projection"] = {"kind": "array"}
    with pytest.raises(ValidationError, match="reserved"):
        CanvasDocument.model_validate(source)


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, 0])
def test_invalid_dimensions_rejected(bad):
    source = document()
    source["nodes"][0]["width"] = bad
    with pytest.raises(ValidationError):
        CanvasDocument.model_validate(source)


def test_resource_remapping_keeps_urls_playback_options_and_unrelated_prose():
    from short_drama.service.canvas_document import remap_resource_references

    original = {
        "storageKey": "resource:1",
        "resourceIds": ["1", "resource:1"],
        "content": "https://old.example/api/v1/canvas-runtime/resources/1/file?token=old&variant=playback&proxy=1#t=2",
        "prompt": "https://old.example/api/resources/1/file",
        "taskId": "1",
    }
    mapped = remap_resource_references(original, {1: 9876543210987654321})
    assert mapped["resourceIds"] == ["9876543210987654321", "resource:9876543210987654321"]
    assert mapped["storageKey"] == "resource:9876543210987654321"
    assert (
        mapped["content"]
        == "/api/v1/canvas-runtime/resources/9876543210987654321/file?variant=playback&proxy=1#t=2"
    )
    assert mapped["prompt"] == original["prompt"] and mapped["taskId"] == "1"
    assert original["storageKey"] == "resource:1"


def test_edges_duplicate_keys_parent_cycles_and_credentials_are_rejected():
    source = document()
    second = deepcopy(source["nodes"][0])
    source["nodes"].append(second)
    with pytest.raises(ValidationError, match="duplicate node"):
        CanvasDocument.model_validate(source)
    second["id"] = "b"
    second["parentId"] = "a"
    source["nodes"][0]["parentId"] = "b"
    with pytest.raises(ValidationError, match="cyclic"):
        CanvasDocument.model_validate(source)
    del source["nodes"][0]["parentId"]
    source["connections"] = [{"id": "edge", "fromNodeId": "a", "toNodeId": "missing"}]
    with pytest.raises(ValidationError, match="endpoint"):
        CanvasDocument.model_validate(source)
    source["connections"] = []
    source["nodes"][0]["metadata"]["api_key"] = "test-value"
    with pytest.raises(ValidationError, match="credentials"):
        CanvasDocument.model_validate(source)
