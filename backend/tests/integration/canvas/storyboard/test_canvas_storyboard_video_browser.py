"""原分镜直接/首帧视频批次、独立 Worker 和稳定媒体回填；使用受控 HTTP。"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from amqp.exceptions import NotFound as BrokerObjectMissing
from kombu import Queue
from PIL import Image
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, CanvasTaskBinding, MediaFile
from short_drama.service.video_render import executable
from short_drama.storage.models import ObjectLocation
from short_drama.tasks.celery_app import make_celery, topology
from short_drama.tasks.publisher import Publisher
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.generation.test_canvas_generation_broker import (
    eventually,
    wait_for_consumers_to_detach,
    worker_environment,
)
from tests.integration.canvas.generation.test_canvas_generation_runtime import (
    TASKS,
    WORKSPACE,
    records,
    seed,
)
from tests.integration.canvas.image.test_canvas_image_tools_browser import minio_bytes
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_library_deletion import commit
from tests.integration.canvas.resources.test_canvas_resources import ROOT, png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable isolated original storyboard video AMQP/HTTP/MySQL/MinIO/browser",
    ),
]
KEY = "storyboard-video-http-fixture-not-a-real-key"
PROMPTS = [
    "已有视频提示词。",
    "镜头缓慢推进，人物在检票口挥手。",
    "固定远景，列车驶离，人物留在站台。",
]


def video_fixture(directory, seconds):
    target = directory / f"storyboard-{seconds}.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:size=160x90:rate=24",
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return target.read_bytes()


@pytest.fixture
def storyboard_video_broker(resource_app, tmp_path):
    assert os.environ.get("SNOWFLAKE_WORKER_ID") == "1022", "Reserve distinct test process IDs"
    app, factory, current = resource_app
    media = {seconds: video_fixture(tmp_path, seconds) for seconds in (4, 8)}
    release_path = tmp_path / "storyboard-video-released"
    released, stopped = Event(), Event()
    creates, observations, errors = [], [], []

    class Supplier(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                assert (
                    self.path == "/v1/videos"
                    and self.headers.get("Authorization") == "Bearer " + KEY
                )
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                multipart = BytesParser(policy=policy.default).parsebytes(
                    ("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + raw
                )
                assert multipart.is_multipart()
                body = {}
                for part in multipart.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    assert name not in body
                    body[name] = (
                        part.get_payload(decode=True)
                        if part.get_filename()
                        else part.get_payload(decode=True).decode()
                    )
                assert body["model"] == "sora" and int(body["seconds"]) in media
                identifier = "storyboard-video-" + str(len(creates) + 1)
                creates.append({"id": identifier, **body})
                self.respond({"id": identifier, "status": "queued"})
            except BaseException as error:
                errors.append(type(error).__name__)
                self.close_connection = True

        def do_GET(self):
            try:
                assert self.headers.get("Authorization") == "Bearer " + KEY
                match = re.fullmatch(r"/v1/videos/(storyboard-video-[12])(/content)?", self.path)
                assert match is not None
                identifier = match[1]
                created = next(item for item in creates if item["id"] == identifier)
                observations.append(self.path)
                ready = released.is_set() or release_path.exists()
                if match[2]:
                    assert ready
                    self.respond(media[int(created["seconds"])], mime="video/mp4")
                else:
                    self.respond(
                        {"id": identifier, "status": "completed" if ready else "in_progress"}
                    )
            except BaseException as error:
                errors.append(type(error).__name__)
                self.close_connection = True

        def respond(self, value, mime="application/json"):
            content = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    supplier = ThreadingHTTPServer(("127.0.0.1", 0), Supplier)
    supplier_thread = Thread(target=supplier.serve_forever, daemon=True)
    supplier_thread.start()
    namespace = "canvas_storyboard_video_test_" + uuid4().hex
    settings = current.model_copy(
        update={
            "generation_queue_namespace": namespace,
            "generation_allowed_hosts": ["127.0.0.1"],
            "model_discovery_allowed_hosts": ["127.0.0.1"],
            "snowflake_worker_id": 1022,
        }
    )
    app.state.settings = settings
    celery = make_celery(settings)
    exchange, dead_exchange, queues = topology(settings)
    queue = queues["video"]
    dead_queue = Queue(
        queue.name + ".dead", exchange=dead_exchange, routing_key=queue.routing_key, durable=True
    )
    publisher = Publisher(factory, settings)
    worker = publisher_thread = None
    cleanup = {"namespace": namespace, "worker_stopped": False, "queues_absent": False}

    def publish():
        try:
            while not stopped.wait(0.02):
                publisher.tick()
        except BaseException as error:
            errors.append(type(error).__name__)

    def queue_state():
        assert worker.poll() is None, "Task-owned storyboard video worker exited"
        with celery.connection_for_read() as connection:
            return connection.channel().queue_declare(queue.name, passive=True)

    try:
        with celery.connection_for_write() as connection:
            channel = connection.channel()
            for item in (queue, dead_queue):
                item(channel).declare()
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "short_drama.tasks.celery_app:app",
                "worker",
                "--pool=threads",
                "--concurrency=1",
                "--queues=" + queue.name,
                "--hostname=" + namespace + "@%h",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
                "--loglevel=CRITICAL",
            ],
            cwd=Path(__file__).resolve().parents[4],
            env=worker_environment(settings, factory),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        eventually(queue_state, lambda state: state.consumer_count == 1, timeout=25)
        publisher_thread = Thread(target=publish, name="storyboard-video-publisher", daemon=True)
        publisher_thread.start()
        yield SimpleNamespace(
            app=(app, factory, settings),
            url=f"http://127.0.0.1:{supplier.server_port}/v1",
            media=media,
            creates=creates,
            observations=observations,
            release_path=release_path,
            released=released,
        )
        assert errors == [], errors
        eventually(queue_state, lambda state: state.message_count == 0, timeout=10)
    finally:
        released.set()
        stopped.set()
        if publisher_thread is not None:
            publisher_thread.join(10)
            assert not publisher_thread.is_alive()
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(10)
        cleanup["worker_stopped"] = worker is None or worker.poll() is not None
        try:
            assert cleanup["worker_stopped"]
            cleanup["consumer_detach_wait_ms"] = wait_for_consumers_to_detach(celery, [queue])
            with celery.connection_for_write() as connection:
                channel = connection.channel()
                for item in (queue, dead_queue):
                    assert item.name.startswith(namespace + ".")
                    item(channel).delete(if_unused=True, if_empty=False)
                assert (
                    exchange.name == namespace + ".tasks"
                    and dead_exchange.name == namespace + ".dead"
                )
                exchange(channel).delete(if_unused=True)
                dead_exchange(channel).delete(if_unused=True)
                for item in (queue, dead_queue):
                    check = connection.channel()
                    try:
                        with pytest.raises(BrokerObjectMissing):
                            check.queue_declare(item.name, passive=True)
                    finally:
                        check.close()
            cleanup["queues_absent"] = True
        finally:
            supplier.shutdown()
            supplier.server_close()
            supplier_thread.join(5)
            assert not supplier_thread.is_alive()
            cleanup["http_stopped"] = True
            (tmp_path / "storyboard-video-broker-cleanup.json").write_text(
                json.dumps(cleanup, indent=2) + "\n", encoding="utf-8"
            )


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize("video_input", ["direct", "keyframe"])
def test_original_storyboard_video_batch_restores_without_repeating_create(
    storyboard_video_broker, database_errors, tmp_path, width, video_input
):
    runtime = storyboard_video_broker
    node = shutil.which("node")
    assert node, "Node.js is required for explicit storyboard video browser verification"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    owner, user, project, path, request = seed(runtime.app, "storyboard_video_owner", kind="video")
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    model = owner.get(model_path).json()
    changed = owner.patch(
        model_path,
        json={
            "row_version": model["row_version"],
            "model_key": "sora",
            "provider": "OpenAI",
            "base_url": runtime.url,
            "apikey": KEY,
            "runtime_profile": {"version": 1, "api_format": "openai", "protocol": "openai-videos"},
        },
    )
    assert changed.status_code == 200, changed.text
    workspace = owner.get(WORKSPACE).json()
    saved = owner.put(
        WORKSPACE,
        json={
            "expected_row_version": workspace["row_version"],
            "preferences": {
                **workspace["preferences"],
                "videoModel": f"host-{model['id']}::sora",
                "videoSeconds": "4",
                "vquality": "720p",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    document = owner.get(path + "/my-document").json()["source_document"]
    response = upload(
        owner,
        runtime.media[4],
        source=document["id"],
        key="existing-video",
        kind="video",
        name="existing.mp4",
        mime="video/mp4",
    )
    assert response.status_code == 200, response.text
    resource = response.json()["resource"]
    existing = {
        "id": "completed-image",
        "type": "video",
        "title": "镜头 1 · 已完成视频",
        "position": {"x": 1080, "y": 0},
        "width": 320,
        "height": 236,
        "metadata": {
            "content": f"{ROOT}/{resource['id']}/file",
            "storageKey": "resource:" + resource["id"],
            "workflowKind": "shot",
            "shotIndex": 1,
            "status": "success",
            "prompt": "已有视频的私人提示词",
        },
    }
    references, reference_bytes = [], {}
    if video_input == "keyframe":
        for index, color in enumerate(("red", "green"), 1):
            original = png(color)
            uploaded = upload(
                owner, original, source=document["id"], key="storyboard-keyframe-" + str(index)
            )
            assert uploaded.status_code == 200, uploaded.text
            image = uploaded.json()["resource"]
            identifier = "keyframe-" + str(index)
            reference_bytes[image["id"]] = original
            references.append(
                {
                    "id": identifier,
                    "type": "image",
                    "title": "首帧 " + str(index),
                    "position": {"x": -440, "y": index * 280},
                    "width": 320,
                    "height": 236,
                    "metadata": {
                        "content": f"{ROOT}/{image['id']}/file",
                        "storageKey": "resource:" + image["id"],
                        "status": "success",
                    },
                }
            )
    rows = [
        {
            "id": f"video-shot-{index}",
            "shotNumber": index,
            "durationSeconds": 8 if index == 3 else 4,
            "plotDescription": f"车站画面 {index}",
            "videoMotionPrompt": prompt,
            "dialogue": "",
            "characters": [],
            "assetBindings": [],
            "status": "idle",
            **({"videoNodeId": existing["id"]} if index == 1 else {}),
            **({"imageNodeId": "keyframe-2" if index == 3 else "keyframe-1"} if references else {}),
        }
        for index, prompt in enumerate(PROMPTS, 1)
    ]
    script = {
        "id": "existing-script",
        "type": "script",
        "title": "分镜视频批次",
        "position": {"x": 0, "y": 0},
        "width": 920,
        "height": 420,
        "metadata": {
            "workflowKind": "script",
            "storyboard": {
                "rows": rows,
                "visibleColumns": [
                    "shotNumber",
                    "durationSeconds",
                    "videoMotionPrompt",
                    "dialogue",
                    "assets",
                ],
                "referenceNodeIds": [],
            },
        },
    }
    document["nodes"] = [script, existing, *references]
    document["connections"] = []
    commit(owner, path, document, "storyboard-video-fixture")
    member, member_user = account(runtime.app, "storyboard_video_member")
    outsider, _ = account(runtime.app, "storyboard_video_outsider")
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "storyboard_video_owner",
            "memberUsername": "storyboard_video_member",
            "password": PASSWORD,
            "width": width,
            "mode": "storyboard_video",
            "kind": "video",
            "videoInputMode": video_input,
            "releaseFile": str(runtime.release_path),
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-storyboard-media-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=300)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Storyboard video browser timed out: {errors}\n{output}")
        finally:
            runtime.released.set()
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert process.returncode == 0, (
            f"Storyboard video browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert (
        report["step"] == "complete"
        and report["page_errors"] == report["http_errors"] == database_errors == []
    )
    assert len(report["submissions"]) == 2 and report["resource_posts"] == 3
    assert report["resource_posts_after_completion"] == 3
    assert report["video_input_mode"] == video_input
    assert records(runtime.app, user) == {"tasks": 2, "records": 2, "bindings": 2}
    assert len(runtime.creates) == 2 and {item["id"] for item in runtime.creates} == {
        "storyboard-video-1",
        "storyboard-video-2",
    }
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 4 + len(references)
    assert (
        next(node for node in final["nodes"] if node["id"] == existing["id"])
        == report["original_image"]
    )
    final_script = next(node for node in final["nodes"] if node["id"] == script["id"])
    final_rows = final_script["metadata"]["storyboard"]["rows"]
    assert [row["id"] for row in final_rows] == [row["id"] for row in rows]
    batch = final_script["metadata"]["generationBatches"][-1]
    assert (
        batch["id"] == report["batch_id"]
        and batch["status"] == "completed"
        and batch["mode"] == "storyboard_video"
    )
    assert member.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert (
        "generationBatches"
        not in next(node for node in shared["nodes"] if node["id"] == script["id"])["metadata"]
    )
    assert outsider.get(path + "/my-document").status_code == 404
    with runtime.app[1]() as session:
        for index, item in enumerate(batch["items"], 1):
            assert item["rowId"] == rows[index]["id"] and item["status"] == "succeeded"
            target = next(node for node in final["nodes"] if node["id"] == item["nodeId"])
            assert (
                final_rows[index]["videoNodeId"] == target["id"]
                and target["metadata"]["shotIndex"] == index + 1
            )
            assert target["metadata"]["seconds"] == str(rows[index]["durationSeconds"])
            task_id = item["taskId"]
            task = session.get(AsyncTask, int(task_id))
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == task.id)
            )
            binding = session.scalar(
                select(CanvasTaskBinding).where(CanvasTaskBinding.async_task_id == task.id)
            )
            assert task.status == record.status == "succeeded" and task.message_status == "idle"
            assert binding.node_key == binding.source_node_key == target["id"]
            created = next(
                create for create in runtime.creates if PROMPTS[index] in create["prompt"]
            )
            assert created["seconds"] == str(rows[index]["durationSeconds"])
            assert created["size"] == "16:9"
            expected_prompt = (
                "参考资产：@图片1\n" + PROMPTS[index]
                if video_input == "keyframe"
                else PROMPTS[index]
            )
            assert created["prompt"] == record.request_data["input"]["prompt"] == expected_prompt
            assert target["metadata"]["prompt"] in (PROMPTS[index], expected_prompt)
            assert target["metadata"]["prompt"] == report["running_prompts"][target["id"]]
            original_refs = record.request_data["canvas_request"]["input"]["referenceImages"]
            if video_input == "keyframe":
                assert len(original_refs) == 1
                identifier = original_refs[0]["storageKey"].removeprefix("resource:")
                assert created["input_reference"] == reference_bytes[identifier]
                assert original_refs[0]["id"] == rows[index]["imageNodeId"]
            else:
                assert original_refs == [] and "input_reference" not in created
            media_id = target["metadata"]["storageKey"].removeprefix("resource:")
            stored = session.get(MediaFile, int(media_id))
            expected = runtime.media[rows[index]["durationSeconds"]]
            assert (
                stored.published_at is not None
                and stored.duration_ms == rows[index]["durationSeconds"] * 1000
            )
            location = ObjectLocation.parse(
                stored.storage_locator, {runtime.app[2].minio_video_bucket}
            )
            with runtime.app[0].state.storage.open(
                location.bucket, location.object_name
            ) as response:
                assert response.read() == expected
            result = next(node for node in report["output_nodes"] if node["id"] == target["id"])
            assert result["sha256"] == hashlib.sha256(expected).hexdigest()
            assert any(
                edge["fromNodeId"] == script["id"]
                and edge["toNodeId"] == target["id"]
                and edge.get("fromHandleId") == "row:" + rows[index]["id"]
                for edge in final["connections"]
            )
            public = next(node for node in shared["nodes"] if node["id"] == target["id"])
            for private in ("prompt", "composerContent", "taskId", "seconds", "model"):
                assert private not in public["metadata"]
            assert member.get(f"{TASKS}/{task_id}").status_code == 404
            assert owner.get(f"{ROOT}/{media_id}/file").content == expected
            assert member.get(f"{ROOT}/{media_id}/file").status_code == 404
            assert outsider.get(f"{ROOT}/{media_id}/file").status_code == 404
        for preview in report["video_previews"]:
            preview_id = preview["storageKey"].removeprefix("resource:")
            stored = session.get(MediaFile, int(preview_id))
            assert stored.format_code == "image/jpeg" and stored.published_at is not None
            content = minio_bytes(runtime.app, stored)
            assert hashlib.sha256(content).hexdigest() == preview["sha256"]
            with Image.open(io.BytesIO(content)) as image:
                assert image.size == (160, 90)
                red, green, blue = image.convert("RGB").getpixel((80, 45))
                assert blue > 240 and red < 10 and green < 10
            public = next(node for node in shared["nodes"] if node["id"] == preview["id"])
            assert public["metadata"]["videoPreview"]["storageKey"] == preview["storageKey"]
            assert owner.get(f"{ROOT}/{preview_id}/file").content == content
            assert member.get(f"{ROOT}/{preview_id}/file").status_code == 404
            assert outsider.get(f"{ROOT}/{preview_id}/file").status_code == 404
    assert database_errors == []
