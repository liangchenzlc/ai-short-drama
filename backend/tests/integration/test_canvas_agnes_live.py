"""显式授权才执行：原画布、真实 Agnes/MySQL/MinIO，线程驱动真实执行器。"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai import GenerationError, GenerationGateway
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.domain import (
    AIGenerationRecord,
    AIModelConfig,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    MediaFile,
)
from short_drama.service.base import utcnow
from short_drama.service.generation_execution_service import GenerationExecutionService
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_generation_runtime import TASKS, WORKSPACE
from tests.integration.test_canvas_resources import canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(
            os.environ.get(name) != "1"
            for name in (
                "RUN_AGNES_LIVE",
                "RUN_CANVAS_RESOURCE_MINIO",
                "RUN_CANVAS_BROWSER_INTEGRATION",
            )
        ),
        reason="Explicit live Agnes, isolated MinIO and browser opt-in are required",
    ),
]
MODELS = {
    "text": "agnes-3.0-flash",
    "image": "agnes-image-2.5-flash",
    "video": "agnes-video-2.5-flash",
}
PROTOCOLS = {"text": "chat-completion", "image": "openai-image", "video": "openai-videos"}
PROMPTS = {
    "text": "请为短剧道具写一句不超过30字的中文描述，必须提到红色礼盒。只返回这一句。",
    "image": "无文字的短剧道具参考照片：白色桌面上只有一个红色方形礼盒，柔和棚拍光，清晰完整构图。",
    "image-reference": "保留参考图片中的礼盒形状、位置和白色桌面，只把红色礼盒改成蓝色，无文字。",
    "video": (
        "固定镜头，纯白桌面上只有一个纯红色方形礼盒缓慢转动，柔和棚拍光。"
        "礼盒和丝带均为纯色，盒面空白，不带任何文字、字母、数字、标签、标志或logo，无人物。"
    ),
}


def live_key():
    path = Path(".runtime/agnes-provider-credential.encrypted")
    if not path.is_file():
        pytest.fail("The authorized encrypted Agnes credential is missing")
    settings = Settings()
    return KeyCipher(settings.encryption_key.get_secret_value()).decrypt(
        path.read_text(encoding="utf-8")
    )


def assert_no_secret(value, secret):
    if secret in json.dumps(value, ensure_ascii=False, default=str):
        raise AssertionError("A live credential appeared in non-secret state")


class ObservedTransport:
    """只记录身份、字段名、字节摘要和状态；每次调用都委托真实 SafeTransport。"""

    def __init__(self, delegate, report, directory, checkpoint):
        self.delegate, self.report, self.directory, self.checkpoint = (
            delegate,
            report,
            directory,
            checkpoint,
        )
        self.media = []

    def request(self, method, url, **kwargs):
        body = kwargs.get("body")
        parts = urlsplit(url)
        item = {"method": method, "host": parts.hostname, "path": parts.path}
        if isinstance(body, dict):
            item["fields"] = sorted(body)
            item["options"] = {
                key: body[key]
                for key in ("model", "mode", "stream", "size", "ratio", "seconds", "aspect_ratio")
                if key in body
            }
            refs = (body.get("extra_body") or {}).get("image", [])
            item["image_references"] = [
                hashlib.sha256(base64.b64decode(ref.split(",", 1)[-1])).hexdigest() for ref in refs
            ]
        self.report["requests"].append(item)
        self.checkpoint()
        status, headers, data = self.delegate.request(method, url, **kwargs)
        item["http_status"] = status
        item["response_bytes"] = len(data)
        if parts.path in {"/v1/videos", "/agnesapi"}:
            try:
                response = json.loads(data)
            except (ValueError, UnicodeError):
                response = {}
            if isinstance(response, dict):
                item["public_status"] = response.get("status")
                if parts.path == "/v1/videos":
                    private = (
                        kwargs.get("headers", {}).get("Authorization", "").removeprefix("Bearer ")
                    )
                    for field, source in (("video_id", "video_id"), ("task_id", "id")):
                        value = response.get(source)
                        item[field] = (
                            value.replace(private, "[redacted]")
                            if private and isinstance(value, str)
                            else value
                        )
                    item["queue_full"] = response.get("code") == "video_queue_full"
        self.checkpoint()
        return status, headers, data

    def download_media(self, url, max_bytes, **kwargs):
        data, mime = self.delegate.download_media(url, max_bytes, **kwargs)
        self.media.append(data)
        filename = f"supplier-media-{len(self.media)}.bin"
        (self.directory / filename).write_bytes(data)
        self.report["downloads"].append(
            {
                "bytes": len(data),
                "mime": mime,
                "sha256": hashlib.sha256(data).hexdigest(),
                "file": filename,
            }
        )
        self.checkpoint()
        return data, mime


class LiveGateway(GenerationGateway):
    """提交前仅等待原窗口关闭；之后的供应商响应、SSE及轮询完全真实。"""

    def __init__(self, settings, release, stopped, directory):
        super().__init__(settings)
        self.release, self.stopped, self.directory = release, stopped, directory
        self.report = {
            "scope": "real Agnes through application gateway",
            "submissions": 0,
            "requests": [],
            "downloads": [],
            "results": [],
        }
        self.transport = ObservedTransport(self.transport, self.report, directory, self.checkpoint)
        self.inline_outputs = []

    def checkpoint(self):
        (self.directory / "provider-report.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def submit(self, snapshot, request, credential, adapter=None, **kwargs):
        deadline = time.monotonic() + 90
        while not self.release.exists():
            if self.stopped.wait(0.05) or time.monotonic() >= deadline:
                raise GenerationError("live_test_not_released")
        self.report["submissions"] += 1
        self.report["model"] = snapshot["model_key"]
        self.report["adapter"] = adapter
        self.checkpoint()
        try:
            result = super().submit(snapshot, request, credential, adapter, **kwargs)
        except GenerationError as error:
            self.report["error"] = {
                "code": error.code,
                "http_status": error.http_status,
                "accepted_unknown": error.accepted_unknown,
            }
            self.checkpoint()
            raise
        for output in result.outputs:
            if "base64" in output:
                data = base64.b64decode(output["base64"].split(",", 1)[-1], validate=True)
                self.inline_outputs.append(data)
                (self.directory / f"supplier-inline-{len(self.inline_outputs)}.bin").write_bytes(
                    data
                )
        self.report["results"].append(
            {
                "status": result.status,
                "provider_task_id": result.provider_task_id,
                "text": result.text,
                "finish_reason": result.finish_reason,
            }
        )
        self.checkpoint()
        return result


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("scenario", ["text", "image", "image-reference", "video"])
def test_original_canvas_real_agnes_generation_and_fresh_recovery(
    resource_app, tmp_path, scenario, width
):
    kind = "image" if scenario == "image-reference" else scenario
    owner, owner_user = account(resource_app, f"ag_o_{scenario.replace('-', '')}_{width}")
    member, member_user = account(resource_app, f"ag_m_{scenario.replace('-', '')}_{width}")
    outsider, _ = account(resource_app, f"ag_x_{scenario.replace('-', '')}_{width}")
    project, path, document = canvas(owner, "agnes-" + uuid4().hex)
    join(resource_app, owner, member, project["id"], member_user["id"])
    secret = live_key()
    profile = {"version": 1, "api_format": "openai", "protocol": PROTOCOLS[kind]}
    if kind == "video":
        profile["capability_config"] = {
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
                    "maxAudios": 3,
                    "maxAudioBytes": 50 * 1024**2,
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
                "operations": [
                    "text_to_video",
                    "image_to_video",
                    "reference_to_video",
                    "audio_to_video",
                ],
                "defaultOperation": "text_to_video",
            },
        }
    response = owner.post(
        "/api/v1/ai-model-configs",
        json={
            "name": "Agnes真实验收-" + kind,
            "service_type": kind,
            "provider": "Agnes",
            "model_key": MODELS[kind],
            "base_url": "https://api.agnes-ai.cn/v1",
            "apikey": secret,
            "runtime_profile": profile,
        },
    )
    if response.status_code != 201:
        pytest.fail("Saved real model configuration failed: HTTP " + str(response.status_code))
    model = response.json()
    assert_no_secret(model, secret)
    selection = f"host-{model['id']}::{model['model_key']}"
    workspace = owner.get(WORKSPACE).json()
    response = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "model": selection,
                f"{kind}Model": selection,
                "size": "16:9" if kind == "video" else "1:1",
                "quality": "auto",
                "count": "1",
                "videoSeconds": "4",
                "vquality": "720p",
                "videoGenerateAudio": "false",
            },
        },
    )
    assert response.status_code == 200
    assert_no_secret(response.json(), secret)
    document["nodes"] = [
        {
            "id": "runtime-node",
            "type": kind,
            "title": "Agnes真实验收",
            "position": {"x": 420, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"prompt": "验收前保存的来源", "content": "", "manualSize": True},
        }
    ]
    reference = None
    reference_body = None
    if kind == "video":
        document["nodes"][0]["metadata"].update(
            seconds="4", vquality="720p", size="16:9", generateAudio="false", watermark="false"
        )
    if scenario == "image-reference":
        reference_path = Path(".runtime/agnes-live-probes/image-a/generated-image.bin")
        if not reference_path.is_file():
            pytest.fail("The real supplier reference image is missing")
        reference_body = reference_path.read_bytes()
        response = upload(
            owner,
            reference_body,
            source=document["id"],
            key="agnes-real-reference",
            name="agnes-reference.png",
        )
        assert response.status_code == 200
        reference = response.json()["resource"]
        document["nodes"].insert(
            0,
            {
                "id": "agnes-reference",
                "type": "image",
                "title": "真实供应商原参考图",
                "position": {"x": 0, "y": 0},
                "width": 320,
                "height": 320,
                "metadata": {
                    "storageKey": "resource:" + reference["id"],
                    "mimeType": "image/png",
                    "content": f"/api/v1/canvas-runtime/resources/{reference['id']}/file",
                    "status": "success",
                    "naturalWidth": reference["width"],
                    "naturalHeight": reference["height"],
                    "bytes": reference["size"],
                    "nodeRole": "result",
                    "resultOrigin": "upload",
                    "manualSize": True,
                },
            },
        )
        document["connections"] = [
            {
                "id": "agnes-reference-edge",
                "fromNodeId": "agnes-reference",
                "toNodeId": "runtime-node",
            }
        ]
    response = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "agnes-source"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert response.status_code == 200
    document = owner.get(path + "/my-document").json()["source_document"]
    factory = resource_app[1]
    with factory() as session:
        saved_model = session.get(AIModelConfig, int(model["id"]))
        assert saved_model.apikey != secret
        if (
            KeyCipher(resource_app[2].encryption_key.get_secret_value()).decrypt(saved_model.apikey)
            != secret
        ):
            raise AssertionError("Saved model credential does not decrypt to the authorized key")
    settings = resource_app[2].model_copy(
        update={
            "public_origin": "http://127.0.0.1:4193",
            "generation_text_budget_seconds": 120,
            "generation_image_budget_seconds": 180,
            "generation_video_budget_seconds": 900,
        }
    )
    stopped = threading.Event()
    release = tmp_path / "real-provider-submit-allowed"
    gateway = LiveGateway(settings, release, stopped, tmp_path)
    executor = GenerationExecutionService(factory, settings, gateway, resource_app[0].state.storage)
    worker_errors = []

    def run_worker():
        try:
            while not stopped.wait(0.05):
                with factory() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version).where(
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                                AsyncTask.next_run_at <= utcnow(),
                            )
                        )
                    )
                for identifier, version in pending:
                    if not stopped.is_set():
                        executor.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(type(error).__name__ + ": " + str(error))

    config = {
        "username": owner_user["username"],
        "password": PASSWORD,
        "projectId": project["id"],
        "canvasId": project["primary_canvas_id"],
        "sourceKey": document["id"],
        "kind": kind,
        "scenario": scenario,
        "width": width,
        "prompt": PROMPTS[scenario],
        "model": MODELS[kind],
        "modelId": model["id"],
        "releasePath": str(release),
        "outputDirectory": str(tmp_path / "browser"),
    }
    if reference:
        config["reference"] = {
            "sha256": hashlib.sha256(reference_body).hexdigest(),
            "width": reference["width"],
            "height": reference["height"],
        }
    with isolated_api(factory, settings, resource_app[0].state.storage) as api_url:
        config["apiUrl"] = api_url
        worker = threading.Thread(target=run_worker, name="agnes-live-executor", daemon=True)
        worker.start()
        process = subprocess.Popen(
            [shutil.which("node"), "scripts/verify-agnes-live-python.mjs"],
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
            output, errors = process.communicate(json.dumps(config), timeout=1020)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            if kind == "video":
                pytest.exit(
                    "Video timed out; reconcile the original task before another supplier POST",
                    returncode=1,
                )
            pytest.fail(
                "Live browser timed out; reconcile saved provider identity before submitting again"
            )
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            release.touch(exist_ok=True)
            worker.join(10)
            gateway.checkpoint()
        assert not worker.is_alive(), "The live executor did not stop"
        if worker_errors:
            pytest.fail("Live executor failed: " + "; ".join(worker_errors))
        if process.returncode != 0:
            assert_no_secret(errors + output, secret)
            rejection = gateway.report.get("error") or {}
            if kind == "video" and not (
                rejection.get("code") == "provider_queue_full"
                and rejection.get("accepted_unknown") is False
            ):
                pytest.exit(
                    "Live video requires reconciliation before another supplier POST: "
                    + errors
                    + "\n"
                    + output,
                    returncode=1,
                )
            pytest.fail("Live Agnes browser failed: " + errors + "\n" + output)
    browser = json.loads(output)
    assert browser["step"] == "complete"
    assert browser["task_posts"] == gateway.report["submissions"] == 1
    assert not browser["page_errors"] and not browser["http_errors"]
    task_id = int(browser["task_id"])
    with factory() as session:
        task = session.get(AsyncTask, task_id)
        call = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        binding = session.scalar(
            select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task_id)
        )
        results = list(
            session.scalars(select(CanvasResult).where(CanvasResult.task_binding_id == binding.id))
        )
        assert task.status == call.status == "succeeded"
        assert task.initiated_by == int(owner_user["id"])
        assert call.config_snapshot["model_key"] == MODELS[kind]
        assert call.credential_cipher and secret not in call.credential_cipher
        assert_no_secret(call.config_snapshot, secret)
        assert_no_secret(call.request_data, secret)
        assert_no_secret(call.response_data, secret)
        assert len(results) == 1 and results[0].attachment_status == "attached"
        assert call.call_no == 1
        if kind == "text":
            assert "红色礼盒" in call.text_content and call.text_content == browser["text"]
            assert len(call.text_content.strip()) <= 30
            assert browser["text"] == gateway.report["results"][0]["text"]
        else:
            media = session.get(MediaFile, results[0].media_id)
            location = ObjectLocation.parse(
                media.storage_locator, {getattr(settings, f"minio_{kind}_bucket")}
            )
            with resource_app[0].state.storage.open(
                location.bucket, location.object_name
            ) as stream:
                actual = stream.read()
            originals = gateway.inline_outputs or gateway.transport.media
            assert actual in originals
            assert media.checksum_sha256 == hashlib.sha256(actual).hexdigest() == browser["sha256"]
            assert media.width > 0 and media.height > 0
            (tmp_path / ("archived.png" if kind == "image" else "archived.mp4")).write_bytes(actual)
            if kind == "video":
                created = next(
                    item
                    for item in gateway.report["requests"]
                    if item["method"] == "POST" and item["path"] == "/v1/videos"
                )
                assert call.provider_task_id == created["video_id"]
                assert any(item["path"] == "/agnesapi" for item in gateway.report["requests"])
                assert 3800 <= media.duration_ms <= 4500
    own = owner.get(TASKS + "/" + str(task_id))
    assert own.status_code == 200
    assert member.get(TASKS + "/" + str(task_id)).status_code == 404
    assert outsider.get(TASKS + "/" + str(task_id)).status_code == 404
    for other in (member, outsider):
        assert other.get("/api/v1/ai-model-configs/" + model["id"]).status_code == 404
    shared = member.get(path + "/my-document").json()["source_document"]
    assert_no_secret(shared, secret)
    attached = next(node for node in shared["nodes"] if node["id"] == browser["node_id"])
    assert "taskId" not in attached["metadata"] and "prompt" not in attached["metadata"]
    if kind == "text":
        assert attached["metadata"]["content"] == browser["text"]
    else:
        resource_path = "/api/v1/canvas-runtime/resources/" + str(results[0].media_id) + "/file"
        assert member.get(resource_path).content == actual
        assert outsider.get(resource_path).status_code == 404
    if reference:
        assert len(browser["reference_views"]) == 3
        assert all(item == config["reference"] for item in browser["reference_views"])
        original = next(node for node in shared["nodes"] if node["id"] == "agnes-reference")
        assert original["metadata"]["storageKey"] == "resource:" + reference["id"]
        assert (
            owner.get(f"/api/v1/canvas-runtime/resources/{reference['id']}/file").content
            == reference_body
        )
        supplier = next(
            item for item in gateway.report["requests"] if item["path"] == "/v1/images/generations"
        )
        assert supplier["image_references"] == [hashlib.sha256(reference_body).hexdigest()]
    assert_no_secret(browser, secret)
    assert_no_secret(gateway.report, secret)
    (tmp_path / "acceptance.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "width": width,
                "model": MODELS[kind],
                "browser": browser,
                "provider": gateway.report,
                "python_postchecks": "passed",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
