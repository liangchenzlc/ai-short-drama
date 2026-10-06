from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.domain import CanvasLibraryAsset
from short_drama.schemas.canvas_generation import CanvasTaskBindOperation
from short_drama.service.canvas_task_outputs import durable_output, generation_metadata


def test_bind_rejects_client_results_and_keeps_large_task_id_as_string():
    body = {
        "opId": "attach-node:18446744073709551614:node:0",
        "params": {
            "canvasId": "canvas-key",
            "taskId": "18446744073709551614",
            "nodeId": "node",
        },
    }
    parsed = CanvasTaskBindOperation.model_validate(body)
    assert (
        parsed.model_dump(mode="json", by_alias=True)["params"]["taskId"] == "18446744073709551614"
    )
    for extra in (
        {"content": "forged"},
        {"resultJson": "{}"},
        {"storageKey": "resource:1"},
        {"outputIndex": True},
        {"outputIndex": -1},
    ):
        with pytest.raises(ValidationError):
            CanvasTaskBindOperation.model_validate({**body, "params": {**body["params"], **extra}})


def test_generation_overlay_matches_source_and_keeps_unrelated_metadata():
    original = {
        "content": "old",
        "prompt": "private",
        "errorDetails": "failed",
        "locked": True,
        "generationEffectKeys": ["previous"],
    }
    result = SimpleNamespace(
        kind="image",
        content_json={
            "content": "/api/v1/canvas-runtime/resources/12/file",
            "storageKey": "resource:12",
            "naturalWidth": 64,
            "naturalHeight": 32,
            "bytes": 100,
        },
    )
    actual = generation_metadata(original, result, "17", "attach-node:17:node:0")
    assert actual["prompt"] == "private" and actual["locked"] is True
    assert actual["taskProgress"] == 100 and actual["status"] == "success"
    assert actual["nodeRole"] == "result" and actual["resultOrigin"] == "generated"
    assert actual["errorDetails"] is None
    assert actual["generationEffectKeys"] == ["previous", "attach-node:17:node:0"]
    assert generation_metadata(actual, result, "17", "attach-node:17:node:0") == actual
    assert original["content"] == "old" and original["errorDetails"] == "failed"


def test_media_delivery_keeps_source_stable_identity_and_library_payload():
    rows = []
    service = SimpleNamespace(
        tasks=SimpleNamespace(
            output=lambda _record, _index: (
                SimpleNamespace(media_type="image", name="供应商文件名"),
                SimpleNamespace(
                    id=12,
                    project_id=2,
                    created_by=1,
                    storage_locator="minio://fixture/image.png",
                    format_code="image/png",
                    byte_size=100,
                    width=64,
                    height=32,
                    duration_ms=None,
                ),
            )
        ),
        canvases=SimpleNamespace(
            actor_id=1,
            audit=lambda: {
                "id": 7,
                "created_at": datetime(2026, 1, 1),
                "updated_at": datetime(2026, 1, 1),
            },
        ),
        session=SimpleNamespace(
            add=rows.append,
            flush=lambda: None,
            scalar=lambda _query: None,
            scalars=lambda _query: [],
        ),
    )
    kind, content, media_id = durable_output(
        service,
        SimpleNamespace(project_id=2, source_key="canvas-key"),
        SimpleNamespace(id=17, service_type="image"),
        SimpleNamespace(id=19),
        0,
    )
    assert kind == "image" and media_id == 12
    assert content["assetId"] == (
        "generation_07d13efaecff5608e6b21f410208c3798fc010b52a5318cee01c84ca996d0935"
    )
    asset = next(row for row in rows if isinstance(row, CanvasLibraryAsset))
    assert asset.title == "生成图片"
    assert asset.payload_json["tags"] == ["生成"]
    assert asset.payload_json["metadata"]["generationEffectKey"] == "materialize:17:0"
    assert asset.payload_json["metadata"]["source"] == "generation-task"
    assert asset.payload_json["data"]["width"] == 64
