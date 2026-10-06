"""回收站原操作通过真实 Python/MySQL/MinIO 恢复已保存绘图和媒体。"""

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_resources import png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable explicit isolated recycle browser verification",
    ),
]


@pytest.mark.parametrize(
    "script", ["verify-recycle-python.mjs", "verify-delete-recovery-python.mjs"]
)
def test_original_recycle_restore_with_real_storage(resource_app, script):
    client, _ = account(resource_app, "canvas_recycle_browser")
    client.post("/api/v1/auth/logout")
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4189"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/" + script],
            cwd=Path(__file__).resolve().parents[3] / "frontend" / "canvas",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(
                json.dumps(
                    {
                        "apiUrl": api_url,
                        "username": "canvas_recycle_browser",
                        "password": PASSWORD,
                        "image": base64.b64encode(png()).decode(),
                    }
                ),
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Recycle browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Recycle browser failed: {errors}\n{output}"
        result = json.loads(output)
        keys = (
            (
                "original_folder_delete_and_two_recycle_cards",
                "fresh_device_lists_deleted_canvases_and_loads_preview",
                "lost_restore_ack_reconciles_without_duplicate_write",
                "both_canvases_restored_under_original_project",
                "drawing_and_media_bytes_retained",
                "fresh_browser_can_edit_restored_drawing",
                "permanent_delete_survives_unknown_responses_and_fresh_device",
                "failed_local_removal_preserves_card_and_archive_key",
            )
            if script == "verify-recycle-python.mjs"
            else (
                "lost_delete_ack_recovers_snapshot_without_write_replay",
                "recovered_card_survives_second_reload",
                "restored_same_work_and_private_parameters",
                "superseded_archive_keeps_current_work",
                "uncommitted_delete_requires_explicit_same_request_retry",
            )
        )
        for key in keys:
            assert result[key]
        assert result["page_errors"] == []
