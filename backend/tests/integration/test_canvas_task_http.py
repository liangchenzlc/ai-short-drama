"""原版操作信封接入真实 FastAPI、账号、CSRF 与 MySQL。"""

from short_drama.schemas.canvas_generation import CanvasTaskBindRead
from tests.integration.test_canvas_task_bindings import complete, prepared
from tests.integration.test_identity_collaboration import account
from tests.integration.test_identity_collaboration import identity_app as identity_app


def test_bind_http_keeps_source_envelope_and_rejects_client_results(identity_app):
    client, user = account(identity_app, "canvas_task_http")
    client.headers["X-Canvas-Actor"] = user["id"]
    project = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "task-http-project"},
        json={"name": "生成回填", "aspect": "16:9", "workspace_mode": "infinite_canvas"},
    ).json()
    with identity_app[1]() as session:
        _, _, task_id, _, op = prepared(
            session,
            project=(int(project["id"]), int(project["primary_canvas_id"])),
            user_id=int(user["id"]),
        )
        complete(session, task_id)
    url = "/api/v1/canvas-runtime/ops/canvas.task.bind"
    payload = op.model_dump(mode="json", by_alias=True)
    rejected = client.post(
        url, json={**payload, "params": {**payload["params"], "content": "客户端伪造"}}
    )
    assert rejected.status_code == 422
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    CanvasTaskBindRead.model_validate(body)
    assert body["op"] == "canvas.task.bind" and body["opId"] == op.op_id
    assert body["result"]["taskId"] == str(task_id)
    assert isinstance(body["result"]["revision"], str)
    assert body["result"]["node"]["metadata"]["content"] == "生成的正文"
    replay = client.post(url, json=payload)
    assert replay.status_code == 200 and replay.json()["replayed"]
    changed = client.post(
        url, json={**payload, "params": {**payload["params"], "nodeId": "different"}}
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "canvas_idempotency_conflict"
    mismatch = client.post(url, headers={"X-Canvas-Actor": "1"}, json=payload)
    assert (
        mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "canvas_actor_changed"
    )
