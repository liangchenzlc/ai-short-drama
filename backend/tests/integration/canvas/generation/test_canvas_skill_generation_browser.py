"""已有 Skill 的原连接点生成、冻结与窗口恢复；供应商是受控本机 HTTP。"""

import json
import os
import shutil
import subprocess
import threading
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    CanvasResult,
    CanvasTaskBinding,
    CanvasTaskTextDelta,
    MediaFile,
)
from short_drama.domain.canvas.document.canvas import CanvasNode, CanvasNodeUserState
from short_drama.service.base import utcnow
from tests.integration.canvas.document.test_canvas_browser import isolated_api, stop_browser
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    FIRST,
    SECOND,
    executor,
    save_channel,
)
from tests.integration.canvas.image.test_canvas_text_image_runtime import (
    text_image_runtime as text_image_runtime,
)
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import PASSWORD, account
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original Skill generation UI with isolated HTTP/MySQL/MinIO",
    ),
]
PROMPT = "写出清晨车站的一段镜头正文。"
SNAPSHOT = {
    "id": "skill-generation-private-template",
    "name": "本人镜头模板",
    "category": "storyboard",
    "outputMode": "text",
    "description": "保持角色和镜头的前后连续性。",
    "template": "逐镜头写出人物行动与环境，保持角色设定一致。",
    "outputContract": "仅输出镜头正文。",
    "version": 3,
    "tags": ["分镜", "私人模板"],
}
SKILL_INPUT = "\n\n".join(
    [
        "【技能：本人镜头模板】",
        "用途：保持角色和镜头的前后连续性。",
        "执行模板：\n逐镜头写出人物行动与环境，保持角色设定一致。",
        "输出约束：\n仅输出镜头正文。",
        "请严格执行该技能，只输出结果，不要输出解释性套话。",
    ]
)


@pytest.mark.parametrize(
    ("width", "variant"), [(1440, "snapshot"), (600, "snapshot"), (600, "body"), (600, "empty")]
)
def test_original_skill_generation_freezes_private_input_and_recovers_after_window_close(
    text_image_runtime, database_errors, tmp_path, width, variant
):
    runtime = text_image_runtime
    node = shutil.which("node")
    assert node, "Node.js is required for this explicit Skill browser integration"
    frontend = Path(__file__).resolve().parents[5] / "frontend" / "canvas"
    owner, owner_user = account(runtime.app, "skill_generation_owner")
    member, member_user = account(runtime.app, "skill_generation_member")
    outsider, _ = account(runtime.app, "skill_generation_outside")
    created = owner.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "skill-generation-project"},
        json={"name": "技能引用生成", "workspace_mode": "infinite_canvas"},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    path = f"/api/v1/projects/{project['id']}/canvases/{project['primary_canvas_id']}"
    initial = owner.get(path + "/my-document")
    assert initial.status_code == 200, initial.text
    document = initial.json()["source_document"]
    private = {} if variant == "empty" else {"content": "作者私人正文回退"}
    if variant == "snapshot":
        private["skillSnapshot"] = deepcopy(SNAPSHOT)
    upstream = SKILL_INPUT if variant == "snapshot" else private.get("content", "")
    expected_prompt = PROMPT + ("\n\n" + upstream if upstream else "")
    updated_private = deepcopy(private)
    if variant == "snapshot":
        updated_private["skillSnapshot"]["template"] = "后续任务的新模板，不能替换已受理提示词。"
        updated_private["skillSnapshot"]["version"] = 4
    else:
        updated_private["content"] = "后续任务的新正文，不能替换已受理提示词。"
    document["nodes"] = [
        {
            "id": "skill-input",
            "type": "skill",
            "title": "技能引用",
            "position": {"x": -160, "y": -150},
            "width": 320,
            "height": 280,
            "metadata": private,
        }
    ]
    saved = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "skill-generation-existing-snapshot"},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert saved.status_code == 200, saved.text
    save_channel(runtime, owner)
    settings = runtime.app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    release_path = tmp_path / "allow-skill-generation-terminal"
    stopped = threading.Event()
    worker_errors = []
    runtime.state["hold"] = True
    generation = executor(runtime)

    def work():
        try:
            while not stopped.wait(0.02):
                with runtime.app[1]() as session:
                    pending = list(
                        session.execute(
                            select(AsyncTask.id, AsyncTask.message_version)
                            .where(
                                AsyncTask.project_id == int(project["id"]),
                                AsyncTask.initiated_by == int(owner_user["id"]),
                                AsyncTask.status.not_in({"succeeded", "failed", "cancelled"}),
                                AsyncTask.message_status == "pending",
                                AsyncTask.next_run_at <= utcnow(),
                            )
                            .order_by(AsyncTask.id)
                        )
                    )
                for identifier, version in pending:
                    if stopped.is_set():
                        break
                    generation.execute(identifier, version)
        except BaseException as error:
            worker_errors.append(error)

    def release():
        while not stopped.wait(0.02):
            if release_path.exists():
                runtime.finish_allowed.set()
                return

    worker = threading.Thread(target=work, name="canvas-skill-generation-worker", daemon=True)
    releaser = threading.Thread(target=release, name="canvas-skill-generation-release", daemon=True)
    with isolated_api(runtime.app[1], settings, runtime.app[0].state.storage) as api_url:
        worker.start()
        releaser.start()
        config = {
            "apiUrl": api_url,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "skill_generation_owner",
            "memberUsername": "skill_generation_member",
            "outsideUsername": "skill_generation_outside",
            "password": PASSWORD,
            "prompt": PROMPT,
            "expectedPrompt": expected_prompt,
            "privateMetadata": private,
            "updatedPrivateMetadata": updated_private,
            "first": FIRST,
            "second": SECOND,
            "releasePath": str(release_path),
            "protocol": runtime.protocol,
            "variant": variant,
            "width": width,
            "outputDirectory": str(tmp_path / "output" / "playwright"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-skill-generation-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=180)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Skill generation browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
            stopped.set()
            runtime.finish_allowed.set()
            worker.join(15)
            releaser.join(5)
            (tmp_path / "database-errors.json").write_text(
                json.dumps(database_errors, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        assert not worker.is_alive() and not releaser.is_alive()
        assert not worker_errors, worker_errors
        assert process.returncode == 0, (
            f"Skill generation browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)

    assert report["step"] == "complete" and report["width"] == width
    assert report["variant"] == variant and report["protocol"] == runtime.protocol
    assert report["page_errors"] == report["failed_routes"] == database_errors == []
    assert report["task_post_count"] == 1 and report["resource_post_count"] == 0
    for field in (
        "original_connection_created_text",
        "skill_prompt_compiled",
        "private_delta_kept_loading",
        "pending_source_saved_before_close",
        "closed_window_before_terminal",
        "private_template_changed_after_admission",
        "same_task_cookie_only_recovery",
        "original_consumer_bound_text",
        "final_cookie_only_recovery",
    ):
        assert report[field], field
    assert runtime.first_sent.is_set() and release_path.exists()
    assert runtime.supplier_errors == [] and len(runtime.posts) == 1
    wire = runtime.posts[0]
    assert wire["authenticated"] and wire["custom_header"]
    assert wire["images"] == wire["videos"] == []
    body = wire["body"]
    assert body["model"] == "gpt-4o-mini" and body["stream"] is True
    assert body["input" if runtime.protocol == "openai-response" else "messages"] == [
        {"role": "user", "content": expected_prompt}
    ]
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == 2 and len(final["connections"]) == 1
    assert final["nodes"][0]["metadata"] == updated_private
    text = next(item for item in final["nodes"] if item["id"] == report["text_node_id"])
    assert text["metadata"]["content"] == FIRST + SECOND
    assert member.get(path + "/my-document").status_code == 404
    shared = owner.get(path).json()["source_document"]
    assert shared["nodes"][0]["metadata"] == {}
    assert next(item for item in shared["nodes"] if item["id"] == text["id"])["metadata"] == {
        "content": FIRST + SECOND,
        "fontSize": 14,
        "generatedFromNodeId": text["id"],
    }
    with runtime.app[1]() as session:
        tasks = list(session.scalars(select(AsyncTask)))
        assert len(tasks) == 1 and tasks[0].status == "succeeded"
        task_id = tasks[0].id
        assert str(task_id) == report["task_id"] and tasks[0].initiated_by == int(owner_user["id"])
        binding = session.scalar(select(CanvasTaskBinding))
        assert binding.node_key == text["id"] and binding.initiated_by == int(owner_user["id"])
        record = session.scalar(select(AIGenerationRecord))
        assert record.text_content == FIRST + SECOND
        assert record.adapter == (
            "openai_responses.v1" if runtime.protocol == "openai-response" else "openai_chat.v1"
        )
        frozen = record.request_data
        assert frozen["input"]["messages"] == [{"role": "user", "content": expected_prompt}]
        assert frozen["canvas_request"]["prompt"] == expected_prompt
        assert frozen["canvas_request"]["input"]["prompt"] == expected_prompt
        assert frozen["source_snapshot"]["source_node"]["id"] == text["id"]
        result = session.scalar(select(CanvasResult))
        assert result.attachment_status == "attached" and result.attachment_receipt_id is not None
        assert result.content_json == {"content": FIRST + SECOND}
        deltas = list(
            session.scalars(select(CanvasTaskTextDelta).order_by(CanvasTaskTextDelta.sequence))
        )
        assert [(delta.sequence, delta.content) for delta in deltas] == [(1, FIRST), (2, SECOND)]
        skill = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "skill-input"))
        assert skill.content_json["metadata"] == {}
        states = list(
            session.scalars(
                select(CanvasNodeUserState).where(
                    CanvasNodeUserState.canvas_id == skill.canvas_id,
                    CanvasNodeUserState.node_key == skill.node_key,
                )
            )
        )
        assert len(states) == 1 and states[0].user_id == int(owner_user["id"])
        assert states[0].draft_json == updated_private
        assert session.scalar(select(MediaFile.id)) is None
    for client in (member, outsider):
        assert client.get(f"/api/v1/canvas-runtime/tasks/{task_id}").status_code == 404
        assert client.get(f"/api/v1/canvas-runtime/tasks/{task_id}/text-deltas").status_code == 404
    assert outsider.get(path).status_code == 404
    assert outsider.get(path + "/my-document").status_code == 404
    assert database_errors == []
