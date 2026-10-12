"""真实来源 UI/MySQL/TCP；独立浏览器显式运行，不做供应商或对象写验收。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from tests.integration.test_canvas_browser import stop_browser
from tests.integration.test_canvas_source_project_read_http import ORIGINAL
from tests.integration.test_canvas_source_project_read_http import bound_source as bound_source
from tests.integration.test_canvas_source_project_read_http import source_http as source_http
from tests.integration.test_identity_collaboration import PASSWORD

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_SOURCE_READ_BROWSER") != "1",
        reason="RUN_CANVAS_SOURCE_READ_BROWSER=1 显式启用来源真实浏览器/MySQL/TCP",
    ),
]
COMMON_CHECKS = (
    "toolbar_first_chapter_literal",
    "cookie_only_restore",
    "one_based_position",
    "no_generation_submissions",
    "no_resource_writes",
    "legacy_source_read_absent",
    "save_status_and_server_ack",
    "source_request_start_and_result_observed",
)
DESKTOP_CHECKS = (
    "sidebar_visible",
    "literal_preview",
    "empty_preview_and_insert",
    "missing_preview_and_no_insert",
    "plus_insert",
    "real_drag_insert",
    "stale409_preserves_graph_and_draft",
    "explicit_refresh_reselect",
    "inline_missing_error_visible",
    "inline_drag_error_visible",
    "inline_stale409_error_visible",
)


@pytest.mark.parametrize("source_http", [{"public_origin": "http://127.0.0.1:4192"}], indirect=True)
@pytest.mark.parametrize("width", [1440, 600])
def test_source_real_browser_original_actions_and_conflict_preserve_draft(
    source_http, bound_source, width, tmp_path, request
):
    root = Path(__file__).resolve().parents[3]
    driver_package = root / ".runtime/source-project-read-browser-candidate-v7"
    manifest_path = driver_package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    driver = driver_package / "verify-source-project-read-python.mjs"
    assert driver.is_file()
    assert sha256(driver.read_bytes()).hexdigest() == manifest["driver_sha256"]
    node = shutil.which("node")
    assert node, "此显式浏览器验收需要已安装 Node.js"
    frontend = root / "frontend/canvas"
    assert (frontend / "node_modules/playwright").is_dir(), "需要现有画布依赖"
    case = bound_source
    literal = ORIGINAL.replace("第一行", "第一行 & 标点")
    response = case["owner"].put(
        f"/api/v1/projects/{case['source']['id']}/episodes/{case['episodes'][0]['id']}/novel",
        json={"content_version": case["episodes"][0]["content_version"], "content": literal},
    )
    assert response.status_code == 200, response.text
    case["episodes"][0]["content_version"] = response.json()["content_version"]
    response = case["owner"].get(case["canvas_path"] + "/my-document")
    assert response.status_code == 200, response.text
    current = response.json()["source_document"]
    assert current["nodes"] == [], "首章实际按钮只在空画布出现，夹具不得预置节点"
    episodes = [
        {
            "id": episode["id"],
            "title": episode["title"],
            "position": episode["position"],
            "contentVersion": episode["content_version"],
            "expectedPlainText": content,
            "state": "missing" if content is None else "present",
        }
        for episode, content in zip(case["episodes"], (literal, "", None), strict=True)
    ]
    config = {
        "apiUrl": source_http.url,
        "browserPort": 4192,
        "width": width,
        "username": case["user"]["username"],
        "password": PASSWORD,
        "authenticatedUserId": case["user"]["id"],
        "owningProjectId": case["owning"]["id"],
        "sourceProjectId": case["source"]["id"],
        "canvasId": case["owning"]["primary_canvas_id"],
        "canvasKey": current["id"],
        "expectedCanvasRowVersion": str(current["revision"]),
        "outputDirectory": str(tmp_path / "browser"),
        "episodes": episodes,
    }
    process = subprocess.Popen(
        [node, str(driver)],
        cwd=frontend,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    output, errors, result = "", "", None
    try:
        try:
            output, errors = process.communicate(json.dumps(config), timeout=300)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            (tmp_path / "driver.stdout.json").write_text(output, encoding="utf-8")
            (tmp_path / "driver.stderr.log").write_text(errors, encoding="utf-8")
            pytest.fail("来源真实浏览器超时；本轮driver报告/截图与stderr见pytest工件")
        (tmp_path / "driver.stdout.json").write_text(output, encoding="utf-8")
        (tmp_path / "driver.stderr.log").write_text(errors, encoding="utf-8")
        assert process.returncode == 0, (
            f"来源真实浏览器 OS={process.returncode}，width={width}\n{errors}\n{output}"
        )
        result = json.loads(output)
        assert result["step"] == "complete" and result["width"] == width
        assert result["real_tcp"] is True
        assert result["authenticated_user_id"] == case["user"]["id"]
        assert not result["page_errors"] and not result["unexpected_http_errors"]
        assert not result["trace_errors"]
        assert result["generation_submission_posts"] == 0
        assert result["resource_write_requests"] == []
        assert result["cleanup"]["browser_created"] is True
        assert result["cleanup"]["browser_closed"] is True
        assert result["cleanup"]["server_created"] is True
        assert result["cleanup"]["server_closed"] is True
        for name in COMMON_CHECKS:
            assert result["checks"][name] is True, name
        if width == 1440:
            assert len(result["expected_409_request_ids"]) == 2
            for name in DESKTOP_CHECKS:
                assert result["checks"][name] is True, name
        else:
            assert len(result["expected_409_request_ids"]) == 1
            assert result["checks"]["first_chapter_409_inline_preserves_empty"] is True
            assert result["checks"]["first_chapter_explicit_refresh_new_version"] is True
            assert result["checks"]["sidebar_hidden"] is True
            assert result["desktop_only_actions_not_exercised"] == [
                "preview",
                "plus",
                "drag",
                "409_refresh",
            ]
        writes = result["fixture_novel_writes"]
        assert len(writes) == (1 if width == 1440 else 2)
        assert writes[0]["old_version"] == episodes[0]["contentVersion"]
        for write in writes:
            assert write["episode_id"] == episodes[0]["id"] and write["status"] == 200
            assert isinstance(write["old_version"], str) and write["old_version"].isdecimal()
            assert isinstance(write["new_version"], str) and write["new_version"].isdecimal()
            assert int(write["new_version"]) > int(write["old_version"])
        if width == 600:
            assert writes[1]["old_version"] == writes[0]["new_version"]
            assert writes[1]["exact_original_restored"] is True
            feedback = next(
                item
                for item in result["inline_feedback_evidence"]
                if item["scope"] == "first_chapter600"
            )
            assert feedback["source_request_id"] == result["expected_409_request_ids"][0]
            assert feedback["button_enabled_after_failure"] is True
            assert feedback["graph_remained_empty"] is True and feedback["error"]
        confirmations = result["save_confirmations"]
        assert confirmations and result["document_commit_acks"]
        for confirmation in confirmations:
            ack = next(
                item
                for item in result["document_commit_acks"]
                if item["id"] == confirmation["commit_id"]
            )
            assert ack["status"] == 200 and ack["operation_id"].strip()
            body = ack["response_body"]
            assert body["id"] == config["canvasId"] == confirmation["canvas_id"]
            assert body["project_id"] == config["owningProjectId"]
            assert body["source_key"] == config["canvasKey"] == confirmation["source_key"]
            assert body["row_version"] == confirmation["revision"]
            assert int(body["row_version"]) > int(ack["request_body"]["expected_row_version"])
            assert confirmation["visible_label"] == "画布保存状态：已保存"
            assert [
                node["id"] for node in ack["request_body"]["source_document"]["nodes"]
            ] == confirmation["node_ids"]
            assert ack["request_at"] <= ack["response_at"] <= confirmation["observed_at"]
        assert result["source_pending_observations"]
        for observation in result["source_pending_observations"]:
            assert any(
                item["id"] == observation["source_request_id"] and item["phase"] == "response"
                for item in result["source_read_trace"]
            )
        assert result["source_read_trace"], "没有正式来源 GET 证据，不能记为浏览器通过"
        for identifier in result["expected_409_request_ids"]:
            response = next(
                entry
                for entry in result["source_read_trace"]
                if entry["id"] == identifier and entry["phase"] == "response"
            )
            assert response["status"] == 409
            assert response["body"]["error"]["code"] == "canvas_source_content_conflict"
    finally:
        if process.poll() is None:
            stop_browser(process)
            process.communicate(timeout=10)
        request.node.user_properties.append(
            (
                "source_browser_observations",
                json.dumps(
                    {
                        "width": width,
                        "driver_os_exit_code": process.returncode,
                        "driver_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(),
                        "report": result,
                        "provider": "not_exercised",
                        "storage": "no_object_io",
                    },
                    ensure_ascii=False,
                ),
            )
        )
