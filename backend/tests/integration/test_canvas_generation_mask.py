"""真实资源与独立 Worker 的源蒙版准入、multipart、隔离和删除保护。"""

import json
import os
from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.domain import AIGenerationRecord, AsyncTask, MediaFile
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_generation_broker import broker_app as broker_app
from tests.integration.test_canvas_generation_broker import eventually, poll_task
from tests.integration.test_canvas_generation_runtime import TASKS, admit, bind, records, seed
from tests.integration.test_canvas_library import ASSETS
from tests.integration.test_canvas_library_deletion import commit, register
from tests.integration.test_canvas_resources import ROOT, canvas, upload
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_BROKER_INTEGRATION") != "1"
        or os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="Enable real isolated source mask AMQP/HTTP/MySQL/MinIO verification",
    ),
]


def mask_bytes(*, size=(37, 19), alpha=True, selected=True):
    image = Image.new("RGBA" if alpha else "RGB", size, "white")
    if alpha and selected:
        image.putpixel((1, 1), (255, 255, 255, 0))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def saved_resource(client, content, source):
    response = upload(client, content, source=source, key="mask-resource-" + uuid4().hex)
    assert response.status_code == 200, response.text
    return response.json()["resource"]


def configured_request(runtime, *, username="canvas_mask_author"):
    client, user, project, path, request = seed(runtime.app, username, kind="image")
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    original = client.get(model_path)
    assert original.status_code == 200, original.text
    patched = client.patch(
        model_path,
        json={
            "row_version": original.json()["row_version"],
            "base_url": runtime.url,
            "apikey": "canvas-mask-placeholder-not-a-real-key",
        },
    )
    assert patched.status_code == 200, patched.text
    source = saved_resource(client, runtime.image, request["projectId"])
    request["input"]["referenceImages"] = [
        {"id": "source", "storageKey": "resource:" + source["id"], "type": "image/png"}
    ]
    return client, user, project, path, request, source


def attach_mask(request, mask):
    request["input"]["mask"] = {
        "id": "mask",
        "name": "mask.png",
        "type": "image/png",
        "storageKey": "resource:" + mask["id"],
    }


def test_saved_mask_crosses_real_worker_multipart_and_remains_private_and_pinned(broker_app):
    runtime = broker_app
    client, user, project, path, request, source = configured_request(runtime)
    content = mask_bytes()
    mask = saved_resource(client, content, request["projectId"])
    attach_mask(request, mask)
    register(client, "mask-input", mask, status="archived")
    member, other = account(runtime.app, "canvas_mask_reader")
    join(runtime.app, client, member, project["id"], other["id"])
    task = admit(client, request)

    def current():
        return poll_task(runtime, client, task["id"])

    ready = eventually(
        current, lambda row: row is not None and row["status"] in {"succeeded", "failed"}
    )
    assert ready["status"] == "succeeded" and ready["resultState"] == "READY", ready
    assert len(runtime.posts) == 1
    target, parts = runtime.posts[0]
    assert target == "/v1/images/edits"
    assert [(name, mime, data) for name, filename, mime, data in parts if filename] == [
        ("image", "image/png", runtime.image),
        ("mask", "image/png", content),
    ]
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert record.request_data["input"]["reference_media_ids"] == [source["id"]]
        assert record.request_data["canvas_parameters"]["mask_media_id"] == mask["id"]
        assert "mask" not in (record.request_data.get("resolved_parameters") or {})
    assert member.get(f"{ROOT}/{mask['id']}/file").status_code == 404
    # 冻结的 JSON 任务输入须保留已使用的 mask；仅有任务状态字段的图也不能回收它。
    removed = client.delete(ASSETS + "/mask-input", params={"expectedStatus": "archived"})
    assert removed.status_code == 409, removed.text
    assert removed.json()["error"]["code"] == "canvas_asset_in_use"
    assert client.get(f"{ROOT}/{mask['id']}/file").content == content
    document = client.get(path + "/my-document").json()["source_document"]
    document["nodes"][0]["metadata"]["mask"] = deepcopy(request["input"]["mask"])
    commit(client, path, document, "mask-private-source")
    shared = member.get(path).json()["source_document"]["nodes"][0]
    assert "mask" not in shared["metadata"]
    bound = bind(client, request, ready)
    output = json.loads(ready["resultJson"])["images"][0]
    assert bound["result"]["node"]["metadata"]["storageKey"] == output["storageKey"]
    assert member.get(output["url"]).content == runtime.image
    assert member.get(f"{ROOT}/{mask['id']}/file").status_code == 404


@pytest.mark.parametrize(
    "case,code",
    [
        ("dimensions", "mask_dimensions_mismatch"),
        ("rgb", "invalid_mask_image"),
        ("opaque", "invalid_mask_image"),
        ("broken", "invalid_mask_image"),
    ],
)
def test_invalid_stored_mask_fails_before_any_real_http_post(broker_app, case, code):
    runtime = broker_app
    client, _, _, _, request, _ = configured_request(runtime)
    content = mask_bytes(
        size=(36, 19) if case == "dimensions" else (37, 19),
        alpha=case != "rgb",
        selected=case != "opaque",
    )
    mask = saved_resource(client, content, request["projectId"])
    if case == "broken":
        with runtime.app[1]() as session:
            media = session.get(MediaFile, int(mask["id"]))
            location = ObjectLocation.parse(
                media.storage_locator, {runtime.app[2].minio_image_bucket}
            )
        invalid = b"broken mask bytes"
        runtime.app[0].state.storage.put(
            location.bucket, location.object_name, BytesIO(invalid), len(invalid), "image/png"
        )
    attach_mask(request, mask)
    task = admit(client, request)

    def current():
        return poll_task(runtime, client, task["id"])

    failed = eventually(
        current, lambda row: row is not None and row["status"] in {"succeeded", "failed"}
    )
    assert failed["status"] == "failed" and failed["errorCode"] == code, failed
    assert failed["canRetry"] and not runtime.posts
    with runtime.app[1]() as session:
        record = session.scalar(select(AIGenerationRecord))
        assert record.status == "failed"
        assert session.get(AsyncTask, int(task["id"])).status == "failed"


def test_foreign_project_mask_is_rejected_atomically(broker_app):
    runtime = broker_app
    client, user, _, _, request, _ = configured_request(runtime)
    _, _, document = canvas(client, "foreign-mask-project")
    mask = saved_resource(client, mask_bytes(), document["id"])
    attach_mask(request, mask)
    response = client.post(TASKS, json=request)
    assert response.status_code == 404, response.text
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert not runtime.posts


def test_same_project_members_cannot_submit_another_authors_unpublished_mask(broker_app):
    runtime = broker_app
    owner, _, project, _, _, _ = configured_request(runtime)
    member, user = account(runtime.app, "canvas_mask_other_author")
    join(runtime.app, owner, member, project["id"], user["id"])
    member, _, _, _, request = seed(
        runtime.app, "unused", client=member, user=user, project=project, kind="image"
    )
    own_source = saved_resource(member, runtime.image, request["projectId"])
    private_mask = saved_resource(owner, mask_bytes(), request["projectId"])
    with runtime.app[1]() as session:
        stored_mask = session.get(MediaFile, int(private_mask["id"]))
        assert stored_mask.published_at is None
    register(owner, "private-mask", private_mask)
    assert owner.get(ASSETS + "/private-mask").status_code == 200
    assert member.get(ASSETS + "/private-mask").status_code == 404
    assert all(item["id"] != "private-mask" for item in member.get(ASSETS).json()["assets"])
    request["input"]["referenceImages"] = [
        {"id": "source", "storageKey": "resource:" + own_source["id"], "type": "image/png"}
    ]
    attach_mask(request, private_mask)
    assert member.get(f"{ROOT}/{private_mask['id']}/file").status_code == 404
    response = member.post(TASKS, json=request)
    assert response.status_code == 404, response.text
    assert records(runtime.app, user) == {"tasks": 0, "records": 0, "bindings": 0}
    assert not runtime.posts
