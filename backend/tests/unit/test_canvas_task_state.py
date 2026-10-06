"""原画布以 submission_unknown 区分待核对提交，不能显示普通失败重试。"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from short_drama.service.ai_generation_service import can_retry
from short_drama.service.canvas_generation_service import CanvasGenerationService
from short_drama.service.canvas_model_test_service import CanvasModelTestService


def fixture(status="unknown", task_status="failed", action="submit"):
    now = datetime(2026, 10, 6)
    task = SimpleNamespace(
        id=11,
        status=task_status,
        next_action=action,
        service_type="video",
        error={"code": "provider_acceptance_unknown"} if status == "unknown" else None,
        retry_of_id=None,
        cancel_requested=False,
        locked_until=None,
        created_at=now,
        updated_at=now,
        started_at=now,
        finished_at=now,
    )
    record = SimpleNamespace(
        status=status,
        provider_task_id=None,
        response_data={},
        request_data={
            "canvas_request": {
                "type": "canvas_video",
                "operation": "text_to_video",
                "prompt": "source prompt",
                "input": {"metadata": {"clientOperationId": "same-operation"}},
            }
        },
        config_snapshot={"provider": "openai", "model_key": "video-model"},
    )
    return task, record


def project(kind, task, record):
    if kind == "canvas":
        service = object.__new__(CanvasGenerationService)
        service.runtime = SimpleNamespace(records=lambda identifier: [record])
        return service._project(
            SimpleNamespace(source_key="source-canvas"),
            task,
            SimpleNamespace(
                client_operation_id="same-operation", node_key="result", source_node_key="source"
            ),
        )
    service = object.__new__(CanvasModelTestService)
    service._require = lambda *args, **kwargs: task
    service._project_locked = lambda identifier: {
        "result": None,
        "error": "Submission requires verification",
        "errorCode": "provider_acceptance_unknown",
        "canCancel": False,
    }
    service.generation = SimpleNamespace(
        record_dao=SimpleNamespace(for_task=lambda identifier: [record])
    )
    return service.runtime_locked(task.id)


@pytest.mark.parametrize("kind", ["canvas", "model-test"])
def test_unknown_submission_projects_original_uncertain_stage_and_blocks_actions(kind):
    task, record = fixture()
    value = project(kind, task, record)
    assert value["status"] == "failed"
    assert value["stage"] == "submission_unknown"
    assert value["errorCode"] == "provider_acceptance_unknown"
    assert value["canRetry"] is False and value["canResume"] is False
    assert value["canCancel"] is False and value["providerRequestId"] is None


@pytest.mark.parametrize("kind", ["canvas", "model-test"])
@pytest.mark.parametrize(
    "task_status,record_status,action,expected",
    [
        ("failed", "failed", "submit", "failed"),
        ("queued", "prepared", "submit", "queued"),
        ("running", "sent", "poll", "generating"),
        ("running", "succeeded", "save", "saving"),
        ("cancelled", "failed", "submit", "cancelled"),
    ],
)
def test_known_failures_and_active_tasks_keep_existing_stage(
    kind, task_status, record_status, action, expected
):
    task, record = fixture(record_status, task_status, action)
    assert project(kind, task, record)["stage"] == expected


@pytest.mark.parametrize(
    "adapter",
    [
        "canvas_openai_videos.v1",
        "canvas_newapi_video_generations.v1",
        "canvas_beefapi_seedance_video.v1",
    ],
)
def test_generated_canvas_video_cannot_create_paid_retry_after_download_failure(adapter):
    task, record = fixture("succeeded", "failed", "save")
    task.error = {"code": "download_failed"}
    record.provider_task_id = "already-paid-original-video"
    record.adapter = adapter
    record.request_data["source"] = {"scene": "canvas_node"}
    assert can_retry(task, record) is False


@pytest.mark.parametrize("scene", ["standard", "canvas_model_test"])
def test_canvas_download_guard_does_not_change_other_scene_retry_policy(scene):
    task, record = fixture("succeeded", "failed", "save")
    record.provider_task_id = "original-video"
    record.adapter = "canvas_openai_videos.v1"
    record.request_data["source"] = {"scene": scene}
    assert can_retry(task, record) is True
