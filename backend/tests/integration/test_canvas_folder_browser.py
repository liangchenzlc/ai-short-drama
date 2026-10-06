"""原版文件夹界面和 ZIP 使用真实 Python/MySQL/MinIO。"""

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
        reason="Enable explicit isolated folder browser verification",
    ),
]


def test_original_folder_ui_with_real_storage(resource_app):
    for username in ("canvas_folder_owner", "canvas_folder_importer"):
        client, _ = account(resource_app, username)
        client.post("/api/v1/auth/logout")
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4189"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api_url:
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-folders-python.mjs"],
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
                        "username": "canvas_folder_owner",
                        "importer": "canvas_folder_importer",
                        "password": PASSWORD,
                        "cover": base64.b64encode(png()).decode(),
                    }
                ),
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Folder browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, f"Folder browser failed: {errors}\n{output}"
        result = json.loads(output)
        for key in (
            "original_create_and_rename",
            "original_cover_saved",
            "fresh_context_preserves_both_canvas_assignments",
            "original_move_out_and_back",
            "original_zip_includes_folder_cover_and_two_canvases",
            "cross_account_zip_restored_private_folder_and_cover",
            "original_delete_archives_source_without_affecting_import",
        ):
            assert result[key]
        assert result["page_errors"] == []
