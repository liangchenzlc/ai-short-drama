"""固定VDA真实CPU推理经AMQP/原浏览器；包装层只暂停时序和记录调用。"""

import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.domain import CanvasLocalJob
from tests.integration.canvas.depth.test_canvas_depth_browser import (
    test_original_depth_menu_keeps_live_edits_and_observes_same_task as original_browser,
)
from tests.integration.canvas.depth.test_canvas_depth_execution import COMPONENT
from tests.integration.canvas.depth.test_canvas_depth_execution import (
    depth_runtime as depth_runtime,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
                "RUN_CANVAS_BROKER_INTEGRATION",
                "RUN_CANVAS_DEPTH_NATIVE",
                "RUN_CANVAS_DEPTH_MODEL",
            )
        ),
        reason="Explicitly enable actual fixed VDA/AMQP/browser/MySQL/MinIO verification",
    ),
]
CHECKPOINT_HASH = "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"
CLI_HASHES = {
    "__init__.py": "2218a007c5056444ce82cb7704f4a2794ddf94bab4a209077ba1328eee873fbd",
    "__main__.py": "e014c07c71c5daede3fe8520fd696eaaed07cb19d77c01c14147273d0f9ed71b",
    "cli.py": "cf30085eb0e0c27f80eaee4270ecce3953ae36bd77720bf35be64dfb948251ed",
    "device_failure.py": "7508b408369d9090bab24de916521b005afd9cf5a69380e548776e3b46d458fa",
    "model.py": "c581e150939f7461485bc73a3004114f37ce4016611d67f368e888f00474d53e",
    "pipeline.py": "98164143f49cf9f89f0be9fb25471ec631d9c6b7e302a8c18079640a5c3db016",
    "probe.py": "615302c242ffd7ec93d14861778b3d505d749854193c5a17270ca967c8f92202",
}


def configure_actual_model(runtime):
    configured = Settings(_env_file=None)
    assert all(
        (
            configured.beeftv_depth_python,
            configured.beeftv_depth_tool_dir,
            configured.beeftv_depth_runtime,
        )
    ), "Configure the independently verified fixed VDA runtime"
    python = Path(configured.beeftv_depth_python).resolve()
    tool = Path(configured.beeftv_depth_tool_dir).resolve()
    model = Path(configured.beeftv_depth_runtime).resolve()
    assert python.is_file()
    for name, expected in CLI_HASHES.items():
        assert hashlib.sha256((tool / "depth_capture" / name).read_bytes()).hexdigest() == expected
    checkpoint = model / "checkpoints/video_depth_anything_vits.pth"
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == CHECKPOINT_HASH

    # 固定探针和模型由独立Python执行；API测试进程不加载Torch、不渲染替代输出。
    launch = (
        "\nimport os\n"
        f"actual_python = {str(python)!r}\n"
        f"actual_tool = {str(tool)!r}\n"
        "environment = os.environ.copy()\n"
        f"environment['BEEFTV_VDA_SOURCE'] = {str(tool.parent / 'vda')!r}\n"
        "result = subprocess.run([actual_python, '-m', MODULE, *sys.argv[1:]], "
        "cwd=actual_tool, env=environment)\n"
        "if result.returncode:\n    raise SystemExit(result.returncode)\n"
    )
    main = COMPONENT[: COMPONENT.index("output = Path(args.output_dir)")].replace(
        "[2/3] controlled component", "[2/3] controlled release before verified VDA"
    )
    main += "\nMODULE = 'depth_capture'\n" + launch
    main += (
        "\n(root / 'actual-model.json').write_text(json.dumps({"
        f"'checkpoint_sha256': {CHECKPOINT_HASH!r}, "
        "'arguments': sys.argv[1:], 'returncode': result.returncode, "
        "'component_is_test_double': False}), encoding='utf-8')\n"
    )
    package = runtime.component / "worker/depth_capture"
    (package / "__main__.py").write_text(main, encoding="utf-8")
    (package / "probe.py").write_text(
        "import subprocess, sys\nMODULE = 'depth_capture.probe'\n" + launch,
        encoding="utf-8",
    )
    settings = runtime.executor.settings.model_copy(update={"beeftv_depth_runtime": str(model)})
    runtime.executor.settings = settings
    runtime.app[0].state.settings = settings
    runtime.component_is_test_double = False


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("reopen", [False, True], ids=["direct", "cookie-recovery"])
@pytest.mark.parametrize("drag_surface", ["body", "title"])
def test_actual_fixed_depth_model_keeps_live_edits_and_restores_same_task(
    depth_runtime, tmp_path, width, reopen, drag_surface
):
    runtime = depth_runtime
    configure_actual_model(runtime)
    original_browser(runtime, tmp_path, width, reopen, drag_surface)
    actual = json.loads((runtime.component / "actual-model.json").read_text(encoding="utf-8"))
    assert actual["checkpoint_sha256"] == CHECKPOINT_HASH
    assert actual["returncode"] == 0 and actual["component_is_test_double"] is False
    args = actual["arguments"]
    assert args[args.index("--device") + 1] == "cpu"
    with runtime.app[1]() as session:
        job = session.scalar(select(CanvasLocalJob))
        assert job.status == "succeeded" and job.attempts == 1
        assert job.result_json["device"] == "cpu" and job.result_json["runtimeVersion"] == "v1"
    broker = json.loads((tmp_path / "depth-broker.json").read_text(encoding="utf-8"))
    assert broker["component_is_test_double"] is False
    assert broker["worker_stopped"] and broker["queue_absent"] and broker["component_calls"] == 1
    assert list(Path(runtime.executor.settings.render_scratch_root).glob("beeftv-depth-*")) == []
