"""本地渲染不依赖AI模型；请求闭合并保留稳定操作ID。"""

import pytest
from pydantic import ValidationError

from short_drama.domain import CANVAS_PRIVATE_TABLES, CANVAS_TABLES, CanvasRenderJob
from short_drama.schemas.canvas.timeline.timeline import CanvasTimelineRenderRequest


def body():
    return {
        "projectId": "canvas-render",
        "clientOperationId": "render-operation-1",
        "timeline": {"version": 2},
    }


def test_render_request_ignores_client_transport_and_facts():
    value = CanvasTimelineRenderRequest.model_validate(
        {
            **body(),
            "apiKey": "client-injected",
            "sources": [{"id": "1", "hasAudio": True}],
            "timeline": {
                "version": 2,
                "clips": [
                    {
                        "id": "v",
                        "directMedia": {
                            "storageKey": "resource:1",
                            "url": "https://untrusted.example",
                        },
                    }
                ],
            },
        }
    )
    serialized = value.model_dump_json(by_alias=True)
    assert "client-injected" not in serialized and "untrusted.example" not in serialized
    assert "hasAudio" not in serialized and value.client_operation_id == body()["clientOperationId"]


@pytest.mark.parametrize("key", ["", "short", "x" * 129, "bad/key-operation"])
def test_render_requires_stable_client_operation(key):
    with pytest.raises(ValidationError):
        CanvasTimelineRenderRequest.model_validate({**body(), "clientOperationId": key})


def test_render_jobs_are_private_non_ai_records_with_frozen_request():
    assert {"canvas_render_jobs", "canvas_render_media_references"} <= CANVAS_PRIVATE_TABLES
    assert {"canvas_render_jobs", "canvas_render_media_references"} <= CANVAS_TABLES
    assert "model_config_id" not in CanvasRenderJob.__table__.c
    assert "assembly_id" not in CanvasRenderJob.__table__.c
    assert {"request_json", "plan_json", "source_snapshot", "request_hash"} <= set(
        CanvasRenderJob.__table__.c.keys()
    )
