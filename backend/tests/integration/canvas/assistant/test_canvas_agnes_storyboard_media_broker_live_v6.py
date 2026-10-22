"""J/v6 追加明确拒绝后的原生持久化观测；原活动标准与全部后置断言保留。"""

import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.core.crypto import KeyCipher
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaFile,
)
from short_drama.service.video_render import executable
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.publisher import RabbitSender
from tests.integration.canvas.assistant.test_canvas_agnes_live import (
    MODELS,
    PROTOCOLS,
    assert_no_secret,
    live_key,
)
from tests.integration.canvas.document.test_canvas_browser import stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import eventually
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    records,
)
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import ROOT, canvas
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.assistant.test_project_assistant_agnes_broker_live import (
    ROLE_IDS,
    official_stack,
    read_json,
)
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_BROKER_INTEGRATION",
            )
        ),
        reason="Explicit live Agnes, real AMQP, isolated MySQL/MinIO and browser opt-in required",
    ),
]
NODE_ID = "agnes-storyboard-media-source"
TITLE = "真实分镜批量媒体验收 J v6 几何块"
ROWS = [
    {
        "durationSeconds": 4,
        "plotDescription": "白色桌面上只有一个纯红色立方几何块，固定近景。",
        "imageGenerationPrompt": (
            "纯白桌面上只有一个纯红色立方几何块，完整构图，柔和棚拍光。"
            "几何块表面纯色且空白，无文字、字母、数字、标签、标志或logo，无人物，无其他物体。"
        ),
        "videoMotionPrompt": (
            "固定镜头，纯白桌面上只有一个纯红色立方几何块缓慢转动，柔和棚拍光。"
            "几何块表面纯色且空白，无文字、字母、数字、标签、标志或logo，无人物，无其他物体。"
        ),
    },
    {
        "durationSeconds": 6,
        "plotDescription": "未选择的对照镜头：黑色桌面上一把银色钥匙静止。",
        "imageGenerationPrompt": "黑色桌面上只有一把银色钥匙，无人物，无文字。",
        "videoMotionPrompt": "固定镜头，黑色桌面上只有一把银色钥匙静止，无人物，无文字。",
    },
    {
        "durationSeconds": 5,
        "plotDescription": "白色桌面上只有一个纯蓝色圆柱几何块，固定近景。",
        "imageGenerationPrompt": (
            "纯白桌面上只有一个纯蓝色圆柱几何块，完整构图，柔和棚拍光。"
            "几何块表面纯色且空白，无文字、字母、数字、标签、标志或logo，无人物，无其他物体。"
        ),
        "videoMotionPrompt": (
            "固定镜头，纯白桌面上只有一个纯蓝色圆柱几何块缓慢转动，柔和棚拍光。"
            "几何块表面纯色且空白，无文字、字母、数字、标签、标志或logo，无人物，无其他物体。"
        ),
    },
]


def video_profile():
    return {
        "version": 1,
        "api_format": "openai",
        "protocol": PROTOCOLS["video"],
        "capability_config": {
            "version": 1,
            "video": {
                "references": {
                    "promptMaxChars": 8000,
                    "minImages": 0,
                    "maxImages": 5,
                    "maxImageBytes": 30 * 1024**2,
                    "maxVideos": 0,
                    "maxVideoBytes": 0,
                    "maxVideoDurationSeconds": 0,
                    "maxAudios": 0,
                    "maxAudioBytes": 0,
                    "maxAudioDurationSeconds": 0,
                },
                "duration": {"selection": "range", "min": 4, "max": 12, "step": 1, "default": 4},
                "durationSupported": True,
                "ratios": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
                "defaultRatio": "16:9",
                "resolutions": ["720p"],
                "defaultResolution": "720p",
                "generateAudio": {"supported": False, "default": False},
                "watermark": {"supported": False, "default": False},
                "operations": ["text_to_video", "image_to_video", "reference_to_video"],
                "defaultOperation": "text_to_video",
            },
        },
    }


def write_report(path, value, secret):
    assert_no_secret(value, secret)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def provider_reports(directory, role):
    return {
        path.parent.name: read_json(path)
        for path in (directory / "provider" / role).glob("*/report.json")
    }


def all_stage_task_ids(stage: dict) -> list[str]:
    return list(dict.fromkeys([*stage["original_task_ids"], *stage["task_ids"]]))


def reconcile_original_and_retry_tasks(
    owner, project, directory, factory, settings, processes, secret, reason
):
    """浏览器失败后只读观察全部原任务与已创建重试任务，正式栈自然收口。"""
    budget = (
        max(settings.generation_image_budget_seconds, settings.generation_video_budget_seconds)
        + settings.generation_archive_budget_seconds
        + 60
    )
    started = time.monotonic()
    report = {
        "scope": "failed J/v6 browser; observe original and already created retry tasks only",
        "no_new_post_retry_resume_or_injection": True,
        "original_browser_failure": reason,
        "budgets": {
            "image_seconds": settings.generation_image_budget_seconds,
            "video_seconds": settings.generation_video_budget_seconds,
            "archive_seconds": settings.generation_archive_budget_seconds,
            "reconciliation_seconds": budget,
        },
        "tasks": [],
        "records": [],
        "results": [],
        "read_errors": [],
    }
    browser_report = read_json(directory / "browser" / "report.json") or {}
    expected_task_ids = set(browser_report.get("failure_all_task_ids", []))
    expected_task_ids.update(browser_report.get("all_task_ids", []))
    for stage in browser_report.get("stages", {}).values():
        for key in ("original_task_ids", "final_task_ids", "all_task_ids", "task_ids"):
            expected_task_ids.update(stage.get(key, []))
    expected_task_ids.update(
        entry["response"]["id"]
        for entry in browser_report.get("submissions", [])
        if isinstance(entry.get("response"), dict) and entry["response"].get("id")
    )
    report["expected_task_ids"] = sorted(expected_task_ids)
    report["failure_all_task_ids"] = browser_report.get("failure_all_task_ids", [])
    report["browser_task_lineage"] = {
        kind: {
            key: stage.get(key, [])
            for key in ("original_task_ids", "final_task_ids", "all_task_ids", "retry_lineage")
        }
        for kind, stage in browser_report.get("stages", {}).items()
    }
    while True:
        elapsed = time.monotonic() - started
        report["elapsed_seconds"] = round(elapsed, 3)
        report["runtime_alive"] = {
            role: process.poll() is None for role, process in processes.items()
        }
        observed = False
        try:
            response = owner.get(TASKS, params={"projectId": project["source_key"]})
            assert_no_secret(response.text, secret)
            if response.status_code != 200:
                raise RuntimeError("Original-task list returned HTTP " + str(response.status_code))
            report["tasks"] = response.json()
            report["observed_original_task_ids"] = [
                task["id"] for task in report["tasks"] if not task.get("retryOf")
            ]
            report["observed_retry_task_ids"] = [
                task["id"] for task in report["tasks"] if task.get("retryOf")
            ]
            with factory() as session:
                bindings = list(
                    session.scalars(
                        select(CanvasTaskBinding).where(
                            CanvasTaskBinding.canvas_id == int(project["canvas_id"])
                        )
                    )
                )
                task_ids = [binding.async_task_id for binding in bindings]
                calls = list(
                    session.scalars(
                        select(AIGenerationRecord).where(AIGenerationRecord.task_id.in_(task_ids))
                    )
                )
                report["records"] = [
                    {
                        "task_id": str(call.task_id),
                        "record_id": str(call.id),
                        "call_no": call.call_no,
                        "status": call.status,
                        "provider_task_id": call.provider_task_id,
                        "error": call.error,
                    }
                    for call in calls
                ]
                report["results"] = [
                    {
                        "result_id": str(result.id),
                        "binding_id": str(result.task_binding_id),
                        "kind": result.kind,
                        "attachment_status": result.attachment_status,
                        "media_id": str(result.media_id) if result.media_id else None,
                        "content": result.content_json,
                    }
                    for result in session.scalars(
                        select(CanvasResult).where(
                            CanvasResult.task_binding_id.in_([binding.id for binding in bindings])
                        )
                    )
                ]
                database_task_ids = {str(task_id) for task_id in task_ids}
                api_task_ids = {task["id"] for task in report["tasks"]}
                report["expected_ids_observed_in_database"] = expected_task_ids <= database_task_ids
                report["expected_ids_observed_in_api"] = expected_task_ids <= api_task_ids
                observed = (
                    database_task_ids == api_task_ids
                    and expected_task_ids <= database_task_ids
                    and expected_task_ids <= api_task_ids
                )
        except Exception as error:
            report["read_errors"].append(
                {"at_seconds": round(elapsed, 3), "type": type(error).__name__}
            )
        report["provider"] = {
            kind: provider_reports(directory, kind) for kind in ("image", "video")
        }
        active = [task["id"] for task in report["tasks"] if task["status"] in {"queued", "running"}]
        unknown = [call["task_id"] for call in report["records"] if call["status"] == "unknown"]
        report["active_task_ids"] = active
        report["unknown_task_ids"] = unknown
        report["application_terminal_observed"] = observed and not active
        report["natural_terminal_observed"] = (
            observed and not active and not unknown and not report["read_errors"]
        )
        if unknown:
            report["supplier_terminal_verdict"] = "not confirmed for unknown submissions"
        elif report["read_errors"]:
            report["supplier_terminal_verdict"] = "not confirmed after reconciliation read errors"
        elif not observed:
            report["supplier_terminal_verdict"] = (
                "not confirmed without all expected API/DB task IDs"
            )
        elif report["natural_terminal_observed"]:
            report["supplier_terminal_verdict"] = (
                "inspect original provider statuses; "
                "application terminal alone is not supplier proof"
            )
        else:
            report["supplier_terminal_verdict"] = "not confirmed while original tasks remain active"
        if report["natural_terminal_observed"]:
            report["stop_reason"] = "original application tasks reached natural terminal states"
        elif report["application_terminal_observed"] and unknown:
            report["stop_reason"] = (
                "application inactive; unknown supplier outcome prevents natural reconciliation"
            )
        elif report["application_terminal_observed"] and report["read_errors"]:
            report["stop_reason"] = (
                "application tasks inactive; read errors prevent confirmed natural reconciliation"
            )
        elif not all(report["runtime_alive"].values()):
            report["stop_reason"] = "formal runtime exited; original supplier outcome not confirmed"
        elif elapsed >= budget:
            report["stop_reason"] = "reconciliation budget expired; supplier outcome not confirmed"
        write_report(directory / "reconciliation.json", report, secret)
        if "stop_reason" in report:
            return report
        time.sleep(1)


def probe_video(path, binary):
    process = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.returncode == 0, "ffprobe could not inspect the actual archived supplier video"
    assert len(process.stdout) <= 1024 * 1024
    information = json.loads(process.stdout)
    stream = next(item for item in information["streams"] if item.get("codec_type") == "video")
    duration = float(stream.get("duration") or information.get("format", {}).get("duration") or 0)
    assert stream["width"] > 0 and stream["height"] > 0 and duration > 0
    return {
        "width": stream["width"],
        "height": stream["height"],
        "duration_ms": round(duration * 1000),
    }


def check_pre_close_persistence(browser, stage):
    """批准后实际 HTTP commit ACK 与持久图必须完整对齐，不能用批准前 ACK。"""

    def moment(value):
        parsed = datetime.fromisoformat(value)
        assert parsed.utcoffset().total_seconds() == 0
        return parsed

    def same_viewport(actual, expected):
        assert set(actual) == set(expected) == {"x", "y", "k"}
        for key in expected:
            value = expected[key]
            assert math.isfinite(actual[key]) and math.isfinite(value)
            assert actual[key] in (
                value,
                math.nextafter(value, -math.inf),
                math.nextafter(value, math.inf),
            )

    manual = stage["pre_submit_manual_save"]
    approved = moment(stage["approval_requested_at"])
    assert moment(manual["started_at"]) <= moment(manual["finished_at"]) <= approved
    persisted = stage["original_persisted_pre_close"]
    ack = persisted["native_commit_ack"]
    assert ack in browser["document_commits"]
    assert ack["stage"] == stage["kind"] and ack["method"] == "POST"
    assert ack["path"] == (
        f"/api/v1/projects/{browser['project_id']}/canvases/{browser['canvas_id']}/commits"
    )
    assert ack["http_status"] == 200 and ack["idempotency_key"]
    assert approved <= moment(ack["started_at"]) <= moment(ack["response_received_at"])
    assert moment(ack["response_received_at"]) <= moment(ack["finished_at"])
    assert moment(ack["finished_at"]) <= moment(persisted["observed_at"])
    assert ack["response"]["canvas_id"] == browser["canvas_id"]
    assert ack["response"]["project_id"] == browser["project_id"]
    assert ack["response"]["source_key"] == browser["source_key"]
    assert int(persisted["graph_revision"]) >= int(ack["response"]["row_version"])
    assert int(ack["response"]["row_version"]) > int(ack["request"]["expected_row_version"])
    assert persisted["native_saved_status_visible"] is True
    same_viewport(persisted["live"], manual["viewport"])
    same_viewport(persisted["stored"], persisted["live"])
    same_viewport(stage["original_pre_close_snapshot"]["viewport"], persisted["stored"])
    original_batch = stage["original_pre_close_snapshot"]["batch"]
    expected_items = [
        {key: item[key] for key in ("id", "rowId", "nodeId", "taskId")}
        for item in original_batch["items"]
    ]
    for document in (ack["request"]["document_identity"], persisted["document_identity"]):
        assert document["source_key"] == browser["source_key"]
        assert document["source_node_id"] == NODE_ID
        assert document["row_ids"] == browser["row_ids"]
        assert document["connections"] == stage["original_pre_close_snapshot"]["connections"]
        batch = next(batch for batch in document["batches"] if batch["id"] == stage["batch_id"])
        assert batch["mode"] == "storyboard_" + stage["kind"]
        assert batch["sourceNodeId"] == NODE_ID and batch["items"] == expected_items
        assert [item["taskId"] for item in batch["items"]] == stage["original_task_ids"]
        for node_id, task_id in zip(stage["node_ids"], stage["original_task_ids"], strict=True):
            assert {"id": node_id, "task_id": task_id} in document["node_tasks"]
    original_submissions = [
        entry
        for entry in stage["submissions"]
        if entry["response"]["id"] in stage["original_task_ids"]
    ]
    assert len(original_submissions) == 2
    for entry in original_submissions:
        assert moment(entry["response_received_at"]) <= moment(ack["started_at"])
    reads = stage["original_pre_close_task_reads"]
    assert len(reads) == 2
    assert [entry["task"] for entry in reads] == stage["original_pre_close_tasks"]
    assert [entry["task"]["id"] for entry in reads] == stage["original_task_ids"]
    for entry in reads:
        assert moment(persisted["observed_at"]) <= moment(entry["started_at"])
        assert moment(entry["started_at"]) <= moment(entry["finished_at"])
        assert moment(entry["finished_at"]) <= moment(stage["original_pre_close_observed_at"])
        if stage["kind"] == "image":
            assert entry["task"]["status"] in {"queued", "running"}
            assert entry["task"]["result_present"] is False and entry["task"]["output_count"] == 0


def check_graph(browser, final):
    assert browser["step"] == "complete"
    assert browser["page_errors"] == browser["http_errors"] == []
    assert browser["source_rows_authored_via_original_table"] is True
    row_ids = browser["row_ids"]
    assert len(row_ids) == len(set(row_ids)) == 3
    selected = [row_ids[index] for index in (0, 2)]
    assert browser["selected_row_ids"] == selected
    assert browser["unselected_row_id"] == row_ids[1]
    script = next(node for node in final["nodes"] if node["id"] == NODE_ID)
    final_rows = script["metadata"]["storyboard"]["rows"]
    assert [row["id"] for row in final_rows] == row_ids
    for row, expected in zip(final_rows, ROWS, strict=True):
        for key, value in expected.items():
            assert row[key] == value
        assert row["characters"] == row["assetBindings"] == []
    assert not final_rows[1].get("imageNodeId") and not final_rows[1].get("videoNodeId")
    assert script["metadata"].get("storyboardVideoInputMode", "direct") == "direct"
    batches = script["metadata"]["generationBatches"]
    assert len(batches) == 2 and {batch["mode"] for batch in batches} == {
        "storyboard_image",
        "storyboard_video",
    }
    all_targets, all_tasks = set(), set()
    for kind in ("image", "video"):
        stage = browser["stages"][kind]
        check_pre_close_persistence(browser, stage)
        assert stage["row_ids"] == selected
        assert stage["original_table_cancel_no_tasks_targets_batch"] is True
        assert stage["original_table_repeat_no_new_tasks_batch"] is True
        assert stage["running_cookie_only_recovery"] is True
        assert stage["final_cookie_only_recovery"] is True
        batch = next(batch for batch in batches if batch["id"] == stage["batch_id"])
        assert batch["status"] == "completed" and batch["sourceNodeId"] == NODE_ID
        assert len(batch["items"]) == 2
        assert [item["rowId"] for item in batch["items"]] == selected
        assert [item["nodeId"] for item in batch["items"]] == stage["node_ids"]
        assert [item["taskId"] for item in batch["items"]] == stage["task_ids"]
        assert all(item["status"] == "succeeded" for item in batch["items"])
        lineage = stage["retry_lineage"]
        assert len(lineage) <= (2 if kind == "video" else 0)
        assert len({entry["original_task_id"] for entry in lineage}) == len(lineage)
        assert len({entry["row_id"] for entry in lineage}) == len(lineage)
        assert stage["final_task_ids"] == stage["task_ids"]
        assert len(stage["original_task_ids"]) == len(set(stage["original_task_ids"])) == 2
        assert stage["all_task_ids"] == all_stage_task_ids(stage)
        assert len(stage["submissions"]) == len(all_stage_task_ids(stage)) == 2 + len(lineage)
        for item in batch["items"]:
            retried = next((entry for entry in lineage if entry["row_id"] == item["rowId"]), None)
            assert item["retryCount"] == (1 if retried else 0)
            if retried:
                assert stage["explicit_unaccepted_retry_once_per_original"] is True
                assert retried["ui_click_count"] == 1
                assert retried["accepted_original_tasks_succeeded_before_retry"] is True
                assert retried["prior_retries_succeeded_and_attached_before_retry"] is True
                assert retried["retry_terminal"]["status"] == "succeeded"
                assert retried["retry_terminal"]["id"] == retried["retry_task_id"]
                assert retried["item_id"] == item["id"] and retried["node_id"] == item["nodeId"]
                assert retried["retry_task_id"] == item["taskId"]
                assert retried["original_task_id"] in stage["original_task_ids"]
                assert retried["original_task_id"] not in stage["task_ids"]
            else:
                ordinal = stage["row_ids"].index(item["rowId"])
                assert item["taskId"] == stage["original_task_ids"][ordinal]
        deferred = stage["running_recovery_basis"] == "first_accepted_retry"
        prefix = "retry_" if deferred else ""
        recovery_id = stage["retry_recovery_task_id" if deferred else "running_recovery_task_id"]
        recovery_tasks = stage[prefix + "running_tasks"]
        recovery_snapshot = stage[prefix + "running_snapshot"]
        recovery_task = next(task for task in recovery_tasks if task["id"] == recovery_id)
        assert recovery_task["status"] in {"queued", "running"}
        assert recovery_task["result_present"] is False and recovery_task["output_count"] == 0
        assert recovery_id in {item["taskId"] for item in recovery_snapshot["batch"]["items"]}
        assert [
            item["taskId"]
            for item in stage[prefix + "running_recovered_snapshot"]["batch"]["items"]
        ] == [item["taskId"] for item in recovery_snapshot["batch"]["items"]]
        restored_batch = stage[prefix + "running_recovered_snapshot"]["batch"]
        assert restored_batch["id"] == recovery_snapshot["batch"]["id"] == stage["batch_id"]
        assert (
            restored_batch["sourceNodeId"] == recovery_snapshot["batch"]["sourceNodeId"] == NODE_ID
        )
        assert [
            [item[key] for key in ("id", "rowId", "nodeId", "taskId")]
            for item in restored_batch["items"]
        ] == [
            [item[key] for key in ("id", "rowId", "nodeId", "taskId")]
            for item in recovery_snapshot["batch"]["items"]
        ]
        if stage["running_recovery_basis"] == "first_accepted_retry":
            assert kind == "video" and len(lineage) >= 1
            assert recovery_id == lineage[0]["retry_task_id"]
            assert recovery_task["status"] == "running" and recovery_task["provider_request_id"]
            assert stage["original_no_active_snapshot_preserved"] is True
            assert stage["running_recovery_deferred_until_first_accepted_retry"] is True
            originals = stage["original_no_active_tasks"]
            assert stage["running_tasks"] == originals
            assert stage["running_snapshot"] == stage["original_no_active_snapshot"]
            assert len(originals) == 2
            assert {task["id"] for task in originals} == set(stage["original_task_ids"])
            for original in originals:
                assert original["status"] == original["stage"] == "failed"
                assert original["error_code"] in {"provider_rate_limit", "provider_queue_full"}
                assert original["can_retry"] is True and original["can_resume"] is False
                assert not original["provider_request_id"] and not original["result_present"]
                assert original["output_count"] == 0
            assert all(
                item["status"] == "failed"
                for item in stage["original_no_active_snapshot"]["batch"]["items"]
            )
            assert all(
                not node["metadata"].get("content")
                for node in stage["original_no_active_snapshot"]["nodes"]
            )
        else:
            assert stage["running_recovery_basis"] == "original"
            assert recovery_id in stage["original_task_ids"]
        for index, entry in enumerate(lineage):
            previous = lineage[:index]
            assert entry["prior_retry_task_ids"] == [row["retry_task_id"] for row in previous]
            assert all(row["retry_terminal"]["status"] == "succeeded" for row in previous)
            attached = entry["retry_attached_snapshot"]
            item = next(
                item
                for item in attached["batch"]["items"]
                if item["taskId"] == entry["retry_task_id"]
            )
            node = next(node for node in attached["nodes"] if node["id"] == entry["node_id"])
            assert item["status"] == "succeeded" and node["metadata"]["status"] == "success"
            assert node["metadata"]["taskId"] == entry["retry_task_id"]
            assert re.fullmatch(r"resource:[1-9][0-9]*", node["metadata"]["storageKey"])
            receipts = entry["retry_binding_receipts"]
            assert len(receipts) >= 1
            for receipt in receipts:
                assert receipt in browser["bindings"]
                assert receipt["request"]["params"]["taskId"] == entry["retry_task_id"]
                assert receipt["request"]["params"]["nodeId"] == entry["node_id"]
                assert receipt["response"]["task_id"] == entry["retry_task_id"]
                assert receipt["response"]["node_id"] == entry["node_id"]
                assert receipt["response"]["storage_key"] == node["metadata"]["storageKey"]
                assert receipt["http_status"] == 200
                assert receipt["response"]["binding_status"] == "bound"
            assert all(
                datetime.fromisoformat(row["retry_attached_observed_at"])
                <= datetime.fromisoformat(entry["clicked_at"])
                for row in previous
            )
            cooldown = entry["cooldown"]
            assert cooldown["minimum_elapsed_ms"] == 65000
            assert cooldown["elapsed_since_reference_ms"] >= 65000
            reference = datetime.fromisoformat(cooldown["reference_post_finished_at"])
            started = datetime.fromisoformat(cooldown["wait_started_at"])
            finished = datetime.fromisoformat(cooldown["wait_finished_at"])
            clicked = datetime.fromisoformat(entry["clicked_at"])
            assert all(
                value.utcoffset() == UTC.utcoffset(value)
                for value in (reference, started, finished, clicked)
            )
            assert round((finished - started).total_seconds() * 1000) == cooldown["actual_wait_ms"]
            reference_ms = int(reference.timestamp()) * 1000 + reference.microsecond // 1000
            finished_ms = int(finished.timestamp()) * 1000 + finished.microsecond // 1000
            assert finished_ms - reference_ms == cooldown["elapsed_since_reference_ms"]
            assert started <= finished <= clicked
            assert (clicked - reference).total_seconds() >= 65
            prior_ids = [*stage["original_task_ids"], *entry["prior_retry_task_ids"]]
            assert set(cooldown["observed_post_task_ids"]) == set(prior_ids)
            assert len(cooldown["observed_post_task_ids"]) == len(prior_ids)
        assert len(stage["node_ids"]) == len(set(stage["node_ids"])) == 2
        assert len(stage["task_ids"]) == len(set(stage["task_ids"])) == 2
        for row_id, node_id, task_id in zip(
            selected, stage["node_ids"], stage["task_ids"], strict=True
        ):
            row = next(row for row in final_rows if row["id"] == row_id)
            target = next(node for node in final["nodes"] if node["id"] == node_id)
            assert row[kind + "NodeId"] == target["id"] and target["type"] == kind
            metadata = target["metadata"]
            assert metadata["status"] == "success" and metadata["taskId"] == task_id
            assert metadata["taskStatus"] == "succeeded"
            assert metadata["workflowKind"] == "shot" and metadata["shotIndex"] == row["shotNumber"]
            assert re.fullmatch(r"resource:[1-9][0-9]*", metadata["storageKey"])
            effect = f"attach-node:{task_id}:{node_id}:0"
            assert effect in metadata["generationEffectKeys"]
            assert any(
                edge["fromNodeId"] == NODE_ID
                and edge["toNodeId"] == node_id
                and edge.get("fromHandleId") == "row:" + row_id
                and edge.get("relation") == "storyboard-output"
                and edge.get("storyboardRowId") == row_id
                for edge in final["connections"]
            )
            if kind == "video":
                assert metadata["seconds"] == str(row["durationSeconds"])
                assert metadata["videoEditOperation"] == "text_to_video"
                assert not metadata.get("videoStartFrameNodeId")
            all_targets.add(node_id)
            all_tasks.add(task_id)
    assert len(all_targets) == len(all_tasks) == 4
    assert {node["id"] for node in final["nodes"]} == {NODE_ID, *all_targets}
    return script, final_rows


def check_stage(
    resource_app,
    settings,
    directory,
    browser,
    final,
    shared,
    owner,
    foreign,
    outsider,
    owner_user,
    project,
    kind,
    model,
    secret,
    ffprobe,
):
    stage = browser["stages"][kind]
    provider = provider_reports(directory, kind)
    assert set(provider) == set(all_stage_task_ids(stage))
    failed_originals = {entry["original_task_id"]: entry for entry in stage["retry_lineage"]}
    attempts = [(task_id, ordinal) for ordinal, task_id in enumerate(stage["task_ids"])]
    attempts.extend(
        (task_id, stage["row_ids"].index(entry["row_id"]))
        for task_id, entry in failed_originals.items()
    )
    postchecks = []
    with resource_app[1]() as session:
        for task_id, ordinal in attempts:
            node_id, row_id = stage["node_ids"][ordinal], stage["row_ids"][ordinal]
            target = next(node for node in final["nodes"] if node["id"] == node_id)
            row_index = browser["row_ids"].index(row_id)
            expected = ROWS[row_index]
            prompt = expected["imageGenerationPrompt" if kind == "image" else "videoMotionPrompt"]
            assert task_id.isdecimal()
            task = session.get(AsyncTask, int(task_id))
            calls = list(
                session.scalars(
                    select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
                )
            )
            assert len(calls) == 1
            call = calls[0]
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
            )
            results = list(
                session.scalars(
                    select(CanvasResult).where(CanvasResult.task_binding_id == binding.id)
                )
            )
            expected_status = "failed" if task_id in failed_originals else "succeeded"
            assert task.status == call.status == expected_status and task.message_status == "idle"
            assert task.project_id == int(project["id"]) and task.scope_user_id is None
            assert task.initiated_by == binding.initiated_by == int(owner_user["id"])
            assert binding.canvas_id == int(project["primary_canvas_id"])
            assert binding.node_key == binding.source_node_key == node_id
            assert call.call_no == 1 and call.config_id == int(model["id"])
            assert call.config_snapshot["model_key"] == MODELS[kind]
            assert call.credential_cipher
            if secret in call.credential_cipher:
                raise AssertionError("The generation record contains a plaintext credential")
            assert call.request_data["input"]["prompt"] == prompt
            assert call.request_data["input"]["reference_media_ids"] == []
            frozen = call.request_data["canvas_request"]
            assert frozen["logicalModelId"] == model["id"]
            assert frozen["input"]["metadata"]["nodeId"] == node_id
            assert frozen["input"]["metadata"]["sourceNodeId"] == node_id
            assert frozen["input"]["metadata"]["clientOperationId"] == binding.client_operation_id
            assert frozen["input"].get("referenceImages", []) == []
            assert frozen["input"].get("referenceVideos", []) == []
            assert frozen["input"].get("referenceAudios", []) == []
            submission = next(
                entry for entry in stage["submissions"] if entry["response"]["id"] == task_id
            )
            assert submission["http_status"] == 202
            assert submission["idempotency_key"] == binding.client_operation_id
            assert submission["request"]["input"]["metadata"]["nodeId"] == node_id
            assert submission["request"]["input"]["metadata"]["sourceNodeId"] == node_id
            assert submission["request"]["logicalModelId"] == model["id"]
            assert call.request_data["source"]["node_key"] == node_id
            assert call.request_data["source"]["source_node_key"] == node_id
            assert call.request_data["source"]["project_id"] == project["id"]
            assert call.request_data["source"]["canvas_id"] == project["primary_canvas_id"]
            assert call.request_data["source_snapshot"]["source_node"]["id"] == node_id
            assert (
                call.request_data["source_snapshot"]["source_node"]["metadata"]["prompt"] == prompt
            )
            assert binding.source_snapshot["request"] == call.request_data
            assert binding.source_snapshot["source_node"]["id"] == node_id
            if task_id in failed_originals:
                lineage = failed_originals[task_id]
                assert kind == "video" and task.retry_of_id is None
                error_code = task.error["code"]
                error_status = task.error["http_status"]
                assert (error_code, error_status) in {
                    ("provider_rate_limit", 429),
                    ("provider_queue_full", 503),
                }
                assert call.error["code"] == error_code
                assert call.error["http_status"] == error_status
                assert call.provider_task_id is None and results == []
                observation = provider[task_id]
                assert observation["submissions"] == 1 and observation["role"] == kind
                assert observation["task_id"] == task_id and observation["model"] == MODELS[kind]
                assert observation["snowflake_worker_id"] == ROLE_IDS[kind]
                assert observation["results"] == observation["downloads"] == []
                assert observation["inline_outputs"] == []
                assert observation["errors"] == [
                    {"code": error_code, "http_status": error_status, "accepted_unknown": False}
                ]
                wire = observation["requests"]
                assert len(wire) == 1 and wire[0]["method"] == "POST"
                assert wire[0]["host"] == "api.agnes-ai.cn" and wire[0]["path"] == "/v1/videos"
                assert wire[0]["http_status"] == error_status and not wire[0].get("video_id")
                if error_status == 503:
                    assert wire[0]["queue_full"] is True
                assert lineage["original_provider_observation"] == observation
                cooldown = lineage["cooldown"]
                prior_ids = [*stage["original_task_ids"], *lineage["prior_retry_task_ids"]]
                prior_posts = [
                    (prior_id, request)
                    for prior_id in prior_ids
                    for request in provider[prior_id]["requests"]
                    if request["method"] == "POST"
                ]
                assert len(prior_posts) == len(prior_ids)
                assert all(
                    request["host"] == "api.agnes-ai.cn" and request["path"] == "/v1/videos"
                    for _, request in prior_posts
                )
                latest_id, latest_post = max(
                    prior_posts, key=lambda item: datetime.fromisoformat(item[1]["finished_at"])
                )
                assert cooldown["reference_task_id"] == latest_id
                assert cooldown["reference_post_finished_at"] == latest_post["finished_at"]
                assert wire[0]["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
                assert wire[0]["options"] == {
                    "model": MODELS[kind],
                    "mode": "text",
                    "seconds": str(expected["durationSeconds"]),
                    "size": "720P",
                    "aspect_ratio": "16:9",
                }
                current = owner.get(f"{TASKS}/{task_id}")
                assert current.status_code == 200
                failed = current.json()
                assert failed["status"] == failed["stage"] == "failed"
                assert failed["errorCode"] == error_code and failed["canRetry"] is True
                assert failed["canResume"] is False and not failed.get("providerRequestId")
                assert not failed.get("resultJson") and failed["outputs"] == []
                assert foreign.get(f"{TASKS}/{task_id}").status_code == 404
                assert outsider.get(f"{TASKS}/{task_id}").status_code == 404
                assert not any(
                    entry["request"]["params"]["taskId"] == task_id for entry in browser["bindings"]
                )
                retry = session.get(AsyncTask, int(lineage["retry_task_id"]))
                retry_binding = session.scalar(
                    select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == retry.id)
                )
                assert retry.retry_of_id == task.id and retry.status == "succeeded"
                assert retry.idempotency_key != task.idempotency_key
                assert retry_binding.node_key == binding.node_key == node_id
                assert retry_binding.source_node_key == binding.source_node_key == node_id
                assert retry_binding.canvas_id == binding.canvas_id
                assert retry_binding.client_operation_id != binding.client_operation_id
                assert lineage["original_idempotency_key"] == binding.client_operation_id
                assert lineage["retry_idempotency_key"] == retry_binding.client_operation_id
                retry_metadata = retry_binding.source_snapshot["request"]["canvas_request"][
                    "input"
                ]["metadata"]
                assert retry_metadata["retryOf"] == task_id
                assert retry_metadata["attemptGroupId"] == (
                    frozen["input"]["metadata"].get("attemptGroupId") or task_id
                )
                for value in (call.request_data, binding.source_snapshot, observation, failed):
                    assert_no_secret(value, secret)
                continue
            assert len(results) == 1 and results[0].kind == kind
            result = results[0]
            assert result.created_by == int(owner_user["id"])
            assert (
                result.attachment_status == "attached" and result.attachment_receipt_id is not None
            )
            media = session.get(MediaFile, result.media_id)
            assert media.project_id == int(project["id"]) and media.scope_user_id is None
            assert media.published_at is not None and media.byte_size > 0
            assert target["metadata"]["storageKey"] == "resource:" + str(media.id)
            assert target["metadata"]["content"] == result.content_json["content"]
            assert target["metadata"]["content"] == f"{ROOT}/{media.id}/file"
            assert target["metadata"]["assetId"] == result.content_json["assetId"]
            location = ObjectLocation.parse(
                media.storage_locator, {getattr(settings, f"minio_{kind}_bucket")}
            )
            with resource_app[0].state.storage.open(
                location.bucket, location.object_name
            ) as stream:
                actual = stream.read()
            checksum = hashlib.sha256(actual).hexdigest()
            observation = provider[task_id]
            assert observation["submissions"] == 1 and observation["role"] == kind
            assert observation["task_id"] == task_id and observation["model"] == MODELS[kind]
            assert observation["snowflake_worker_id"] == ROLE_IDS[kind]
            assert not observation.get("errors")
            wire = [item for item in observation["requests"] if item["method"] == "POST"]
            assert len(wire) == 1
            assert wire[0]["host"] == "api.agnes-ai.cn" and wire[0]["http_status"] == 200
            assert wire[0]["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
            supplier = observation["inline_outputs"] + observation["downloads"]
            assert any(item["sha256"] == checksum for item in supplier)
            source_files = [
                directory / "provider" / kind / task_id / item["file"]
                for item in supplier
                if item["sha256"] == checksum
            ]
            assert any(source.read_bytes() == actual for source in source_files)
            output = next(item for item in stage["outputs"] if item["node_id"] == node_id)
            assert output["task_id"] == task_id and output["row_id"] == row_id
            assert output["storage_key"] == target["metadata"]["storageKey"]
            assert output["asset_id"] == result.content_json["assetId"]
            assert media.checksum_sha256 == output["sha256"] == checksum
            assert media.byte_size == output["bytes"] == len(actual)
            consumed = [
                entry
                for entry in stage["bindings"]
                if entry["request"]["params"]["taskId"] == task_id
            ]
            assert consumed, "The original browser consumer must bind this exact task"
            for entry in consumed:
                assert entry["http_status"] == 200
                assert entry["request"]["opId"] == f"attach-node:{task_id}:{node_id}:0"
                assert entry["request"]["params"] == {
                    "canvasId": frozen["projectId"],
                    "taskId": task_id,
                    "nodeId": node_id,
                    "outputIndex": 0,
                }
                assert entry["response"]["binding_status"] == "bound"
                assert entry["response"]["storage_key"] == target["metadata"]["storageKey"]
            archived = directory / f"archived-{kind}-{task_id}.bin"
            archived.write_bytes(actual)
            if kind == "image":
                assert wire[0]["path"] == "/v1/images/generations"
                assert wire[0]["options"]["model"] == MODELS[kind]
                assert wire[0]["options"]["ratio"] == "1:1"
                assert wire[0]["image_references"] == []
                assert call.request_data["parameters"]["count"] == 1
                with Image.open(io.BytesIO(actual)) as image:
                    image.load()
                    dimensions = {"width": image.width, "height": image.height}
                assert (media.width, media.height) == (dimensions["width"], dimensions["height"])
                assert media.width == media.height
            else:
                assert wire[0]["path"] == "/v1/videos"
                assert wire[0]["options"] == {
                    "model": MODELS[kind],
                    "mode": "text",
                    "seconds": str(expected["durationSeconds"]),
                    "size": "720P",
                    "aspect_ratio": "16:9",
                }
                assert not {"first_frame", "last_frame", "images", "audios"}.intersection(
                    wire[0]["fields"]
                )
                assert call.provider_task_id == wire[0]["video_id"]
                assert any(item["path"] == "/agnesapi" for item in observation["requests"])
                dimensions = probe_video(archived, ffprobe)
                assert (media.width, media.height) == (dimensions["width"], dimensions["height"])
                assert abs(media.duration_ms - dimensions["duration_ms"]) <= 5
                assert -200 <= media.duration_ms - expected["durationSeconds"] * 1000 <= 500
                assert abs(media.width / media.height - 16 / 9) < 0.02
                assert abs(output["duration_ms"] - media.duration_ms) <= 500
            assert (output["width"], output["height"]) == (media.width, media.height)
            own_task_response = owner.get(f"{TASKS}/{task_id}")
            assert own_task_response.status_code == 200
            own_task = own_task_response.json()
            assert own_task["status"] == "succeeded" and own_task["resultState"] == "READY"
            assert own_task.get("providerRequestId") == call.provider_task_id
            task_result = json.loads(own_task["resultJson"])
            assert task_result["mode"] == kind
            result_output = task_result["images"][0] if kind == "image" else task_result["video"]
            assert result_output["storageKey"] == target["metadata"]["storageKey"]
            assert result_output["url"] == result.content_json["content"]
            assert own_task["outputs"] == [
                {
                    "outputIndex": 0,
                    "mediaType": kind,
                    "materializedAssetId": result.content_json["assetId"],
                }
            ]
            assert foreign.get(f"{TASKS}/{task_id}").status_code == 404
            assert outsider.get(f"{TASKS}/{task_id}").status_code == 404
            asset_path = "/api/v1/canvas-runtime/assets/" + result.content_json["assetId"]
            assert owner.get(asset_path).status_code == 200
            assert foreign.get(asset_path).status_code == 404
            assert outsider.get(asset_path).status_code == 404
            public = next(node for node in shared["nodes"] if node["id"] == node_id)
            assert public["metadata"]["storageKey"] == target["metadata"]["storageKey"]
            for private in (
                "prompt",
                "composerContent",
                "model",
                "taskId",
                "seconds",
                "taskStatus",
            ):
                assert private not in public["metadata"]
            file_path = f"{ROOT}/{media.id}/file"
            assert owner.get(file_path).content == actual
            assert foreign.get(file_path).status_code == 404
            assert outsider.get(file_path).status_code == 404
            for value in (
                call.config_snapshot,
                call.request_data,
                call.response_data,
                binding.source_snapshot,
                observation,
            ):
                assert_no_secret(value, secret)
            postchecks.append(
                {
                    "task_id": task_id,
                    "node_id": node_id,
                    "row_id": row_id,
                    "resource_id": str(media.id),
                    "sha256": checksum,
                    **dimensions,
                }
            )
    return postchecks


def check_deliveries(directory, kind, task_ids, settings, processes, factory):
    report = eventually(
        lambda: read_json(directory / (kind + "-worker-report.json")),
        lambda value: (
            value is not None
            and set(task_ids) == {row["task_id"] for row in value["deliveries"]}
            and all(row["completed"] for row in value["deliveries"])
        ),
        timeout=30,
    )
    assert report["role"] == kind and processes[kind].pid in {report["pid"], report["parent_pid"]}
    with factory() as session:
        for task_id in task_ids:
            rows = [row for row in report["deliveries"] if row["task_id"] == task_id]
            task = session.get(AsyncTask, int(task_id))
            minimum = 1 if task.status == "failed" else 2 if kind == "image" else 3
            assert len(rows) >= minimum
            assert sorted(int(row["message_version"]) for row in rows) == list(
                range(1, task.message_version)
            )
            for row in rows:
                assert row["state"] == "SUCCESS"
                assert row["delivery"]["exchange"] == settings.generation_queue_namespace + ".tasks"
                assert row["delivery"]["routing_key"] == kind
                assert row["delivery"]["redelivered"] is False
    return report


@pytest.mark.parametrize("width", [1440, 390])
def test_original_table_geometry_media_and_serial_known_unaccepted_retries(
    resource_app, database_errors, tmp_path, monkeypatch, width
):
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022"
    node = shutil.which("node")
    assert node, "Node.js is required for the real original storyboard media driver"
    ffprobe = executable(resource_app[2].render_ffprobe_path)
    suffix = uuid4().hex[:12]
    owner, owner_user = account(resource_app, "agsm_o_" + suffix)
    foreign, foreign_user = account(resource_app, "agsm_m_" + suffix)
    outsider, outside_user = account(resource_app, "agsm_x_" + suffix)
    project, path, document = canvas(owner, "agnes-storyboard-media-J-v6-" + uuid4().hex)

    document["nodes"] = [
        {
            "id": NODE_ID,
            "type": "script",
            "title": TITLE,
            "position": {"x": 420.25, "y": -20.5},
            "width": 920,
            "height": 420,
            "metadata": {
                "storyboard": {
                    "rows": [],
                    "visibleColumns": ["shotNumber", "durationSeconds"],
                    "referenceNodeIds": [],
                }
            },
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-storyboard-media-empty-" + suffix},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    document = owner.get(path + "/my-document").json()["source_document"]
    assert document["nodes"][0]["metadata"]["storyboard"]["rows"] == []
    secret, models = live_key(), {}
    for kind in ("image", "video"):
        response = owner.post(
            "/api/v1/ai-model-configs",
            json={
                "name": "Agnes真实分镜媒体-" + kind,
                "service_type": kind,
                "provider": "Agnes",
                "model_key": MODELS[kind],
                "base_url": "https://api.agnes-ai.cn/v1",
                "apikey": secret,
                "runtime_profile": video_profile()
                if kind == "video"
                else {
                    "version": 1,
                    "api_format": "openai",
                    "protocol": PROTOCOLS[kind],
                },
            },
        )
        assert_no_secret(response.text, secret)
        assert response.status_code == 201, "Could not save the authorized media model"
        models[kind] = response.json()
    workspace = owner.get(WORKSPACE).json()
    selected = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "imageModel": f"host-{models['image']['id']}::{MODELS['image']}",
                "videoModel": f"host-{models['video']['id']}::{MODELS['video']}",
                "size": "16:9",
                "quality": "auto",
                "count": "1",
                "canvasImageCount": "1",
                "videoSeconds": "4",
                "vquality": "720p",
                "videoGenerateAudio": "false",
                "videoWatermark": "false",
            },
        },
    )
    assert selected.status_code == 200, selected.text
    assert_no_secret(selected.json(), secret)
    with resource_app[1]() as session:
        for model in models.values():
            saved_model = session.get(AIModelConfig, int(model["id"]))
            if saved_model.apikey == secret:
                raise AssertionError("The saved model credential was not encrypted")
            cipher = KeyCipher(resource_app[2].encryption_key.get_secret_value())
            if cipher.decrypt(saved_model.apikey) != secret:
                raise AssertionError("The encrypted model credential does not match authorization")
    port = int(os.environ.get("AGNES_STORYBOARD_MEDIA_BROWSER_PORT", "4198"))
    settings = resource_app[2].model_copy(
        update={
            "public_origin": f"http://127.0.0.1:{port}",
            "generation_image_budget_seconds": 360,
            "generation_video_budget_seconds": 900,
            "generation_archive_budget_seconds": 360,
            "agent_enabled": True,
            "generation_queue_namespace": "assistant_broker_live_" + uuid4().hex,
            "smtp_host": "",
            "smtp_from": "",
            "render_scratch_root": str(tmp_path / "render-scratch"),
        }
    )
    write_report(
        tmp_path / "storage-isolation.json",
        {
            "scope": "actual random test buckets; no credentials",
            "buckets": {
                kind: getattr(settings, f"minio_{kind}_bucket")
                for kind in ("image", "video", "audio")
            },
        },
        secret,
    )
    monkeypatch.setenv("AGNES_STORYBOARD_MEDIA_BROKER_DIRECTORY", str(tmp_path))
    config = {
        "browserPort": port,
        "username": owner_user["username"],
        "foreignUsername": foreign_user["username"],
        "outsideUsername": outside_user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "canvasId": project["primary_canvas_id"],
        "sourceKey": document["id"],
        "nodeId": NODE_ID,
        "title": TITLE,
        "width": width,
        "imageModel": MODELS["image"],
        "imageModelId": models["image"]["id"],
        "videoModel": MODELS["video"],
        "videoModelId": models["video"]["id"],
        "rows": ROWS,
        "selectedRowIndexes": [0, 2],
        "providerDirectory": str(tmp_path),
        "outputDirectory": str(tmp_path / "browser"),
    }
    try:
        with official_stack(
            resource_app[1],
            settings,
            tmp_path,
            generation_observer_module="tests.support.integration.agnes_storyboard_media_broker_observer_v6",
        ) as (api_url, stack, processes):
            config["apiUrl"] = api_url
            process = subprocess.Popen(
                [node, "scripts/verify-agnes-storyboard-media-python-v6.mjs"],
                cwd=Path(__file__).resolve().parents[5] / "frontend" / "canvas",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
            )
            browser_timed_out = False
            try:
                output, errors = process.communicate(json.dumps(config), timeout=4200)
            except subprocess.TimeoutExpired:
                stop_browser(process)
                output, errors = process.communicate(timeout=10)
                browser_timed_out = True
            finally:
                if process.poll() is None:
                    stop_browser(process)
                    process.communicate(timeout=10)
            assert_no_secret(output + errors, secret)
            (tmp_path / "browser-stdout.json").write_text(output, encoding="utf-8")
            (tmp_path / "browser-stderr.log").write_text(errors, encoding="utf-8")
            if process.returncode or browser_timed_out:
                failure = {
                    "returncode": process.returncode,
                    "timed_out": browser_timed_out,
                    "stdout": "browser-stdout.json",
                    "stderr": "browser-stderr.log",
                }
                reconcile_original_and_retry_tasks(
                    owner,
                    {"source_key": document["id"], "canvas_id": project["primary_canvas_id"]},
                    tmp_path,
                    resource_app[1],
                    settings,
                    processes,
                    secret,
                    failure,
                )
                pytest.exit("Original media browser failed: " + errors + "\n" + output, 1)
            browser = json.loads(output)
            assert browser["width"] == width
            assert browser["canvas_id"] == project["primary_canvas_id"]
            assert browser["project_id"] == project["id"]
            assert browser["source_key"] == document["id"]
            final = owner.get(path + "/my-document").json()["source_document"]
            script, _ = check_graph(browser, final)
            assert foreign.get(path + "/my-document").status_code == 404
            shared = owner.get(path).json()["source_document"]
            shared_script = next(node for node in shared["nodes"] if node["id"] == NODE_ID)
            assert "generationBatches" not in shared_script["metadata"]
            assert (
                shared_script["metadata"]["storyboard"]["rows"]
                == script["metadata"]["storyboard"]["rows"]
            )
            assert outsider.get(path + "/my-document").status_code == 404
            retry_lineage = [
                entry for stage in browser["stages"].values() for entry in stage["retry_lineage"]
            ]
            assert len(retry_lineage) <= 2
            expected_count = 4 + len(retry_lineage)
            expected_counts = dict.fromkeys(("tasks", "records", "bindings"), expected_count)
            assert records(resource_app, owner_user) == expected_counts
            media_checks, delivery_reports, provider_before = {}, {}, {}
            for kind in ("image", "video"):
                for other in (foreign, outsider):
                    assert (
                        other.get("/api/v1/ai-model-configs/" + models[kind]["id"]).status_code
                        == 404
                    )
                media_checks[kind] = check_stage(
                    resource_app,
                    settings,
                    tmp_path,
                    browser,
                    final,
                    shared,
                    owner,
                    foreign,
                    outsider,
                    owner_user,
                    project,
                    kind,
                    models[kind],
                    secret,
                    ffprobe,
                )
                task_ids = all_stage_task_ids(browser["stages"][kind])
                delivery_reports[kind] = check_deliveries(
                    tmp_path, kind, task_ids, settings, processes, resource_app[1]
                )
                provider_before[kind] = deepcopy(provider_reports(tmp_path, kind))
                assert all(
                    report["worker_pid"] == delivery_reports[kind]["pid"]
                    for report in provider_before[kind].values()
                )
            sender = RabbitSender(settings)
            try:
                for kind in ("image", "video"):
                    for task_id in all_stage_task_ids(browser["stages"][kind]):
                        first = next(
                            row
                            for row in delivery_reports[kind]["deliveries"]
                            if row["task_id"] == task_id
                        )
                        sender(int(task_id), int(first["message_version"]), kind)
                        sender(int(task_id), int(first["message_version"]) - 1, kind)
            finally:
                sender.app.close()
            duplicates = {}
            for kind in ("image", "video"):
                count = len(delivery_reports[kind]["deliveries"])
                extra_count = 2 * len(all_stage_task_ids(browser["stages"][kind]))
                duplicates[kind] = eventually(
                    lambda kind=kind: read_json(tmp_path / (kind + "-worker-report.json")),
                    lambda value, count=count, extra_count=extra_count: (
                        value is not None
                        and len(value["deliveries"]) == count + extra_count
                        and all(
                            row["completed"] and row["state"] == "SUCCESS"
                            for row in value["deliveries"]
                        )
                    ),
                    timeout=30,
                )
                assert provider_reports(tmp_path, kind) == provider_before[kind]
                for task_id in all_stage_task_ids(browser["stages"][kind]):
                    normal = [
                        row
                        for row in delivery_reports[kind]["deliveries"]
                        if row["task_id"] == task_id
                    ]
                    replayed = [
                        row for row in duplicates[kind]["deliveries"] if row["task_id"] == task_id
                    ]
                    assert len(replayed) == len(normal) + 2
                    identities = {row["celery_task_id"] for row in normal}
                    extra = [row for row in replayed if row["celery_task_id"] not in identities]
                    assert sorted(int(row["message_version"]) for row in extra) == [
                        int(normal[0]["message_version"]) - 1,
                        int(normal[0]["message_version"]),
                    ]
                    assert all(row["delivery"]["routing_key"] == kind for row in extra)
            assert records(resource_app, owner_user) == expected_counts
            with resource_app[1]() as session:
                bindings = list(
                    session.scalars(
                        select(CanvasTaskBinding).where(
                            CanvasTaskBinding.canvas_id == int(project["primary_canvas_id"])
                        )
                    )
                )
                unchanged_results = list(
                    session.scalars(
                        select(CanvasResult).where(
                            CanvasResult.task_binding_id.in_([binding.id for binding in bindings])
                        )
                    )
                )
                assert len(bindings) == expected_count and len(unchanged_results) == 4
                assert all(result.attachment_status == "attached" for result in unchanged_results)
                tasks = [session.get(AsyncTask, binding.async_task_id) for binding in bindings]
                assert sum(task.status == "succeeded" for task in tasks) == 4
                assert sum(task.status == "failed" for task in tasks) == len(retry_lineage)
                assert all(task.status in {"succeeded", "failed"} for task in tasks)
                assert {str(task.id) for task in tasks} == set(browser["all_task_ids"])
                assert {str(task.id) for task in tasks if task.retry_of_id} == {
                    entry["retry_task_id"] for entry in retry_lineage
                }
                failed_binding_ids = {
                    binding.id
                    for binding in bindings
                    if str(binding.async_task_id)
                    in {entry["original_task_id"] for entry in retry_lineage}
                }
                assert not any(
                    result.task_binding_id in failed_binding_ids for result in unchanged_results
                )
                expected_resources = {
                    item["resource_id"] for checks in media_checks.values() for item in checks
                }
                assert {str(result.media_id) for result in unchanged_results} == expected_resources
            unchanged = owner.get(path + "/my-document").json()["source_document"]
            assert (
                unchanged["nodes"] == final["nodes"]
                and unchanged["connections"] == final["connections"]
            )
            assert all(worker.poll() is None for worker in processes.values())
    finally:
        write_report(tmp_path / "database-errors.json", database_errors, secret)
        for log in tmp_path.glob("*.log"):
            assert_no_secret(log.read_text(encoding="utf-8"), secret)
    assert all(stack["cleanup"].values()) and database_errors == []
    assert_no_secret(shared, secret)
    write_report(
        tmp_path / "acceptance.json",
        {
            "scope": (
                "J/v6 uses C geometry input; original Table and once per original serial UI retries"
            ),
            "previous_attempt_verdicts": "A/B/C failures unchanged; B content failed",
            "input_revision": "J/v6 reuses C geometry inputs; distinct execution from failed A-I",
            "authored_rows": ROWS,
            "expected_counts": expected_counts,
            "original_task_ids": browser["original_task_ids"],
            "final_task_ids": browser["final_task_ids"],
            "all_task_ids": browser["all_task_ids"],
            "retry_lineage": retry_lineage,
            "width": width,
            "models": {kind: MODELS[kind] for kind in ("image", "video")},
            "budgets": {
                "image_seconds": settings.generation_image_budget_seconds,
                "video_seconds": settings.generation_video_budget_seconds,
                "archive_seconds": settings.generation_archive_budget_seconds,
            },
            "browser": browser,
            "provider": provider_before,
            "stack": stack,
            "normal_deliveries": delivery_reports,
            "duplicate_deliveries": duplicates,
            "media_postchecks": media_checks,
            "python_postchecks": "passed",
            "content_verdict": "requires manual review of the two images and two videos",
            "keyframe": "not exercised; Agnes requires public HTTPS references",
        },
        secret,
    )
