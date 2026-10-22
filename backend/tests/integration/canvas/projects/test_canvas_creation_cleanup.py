"""创建暂存跨页回收：真实HTTP/MySQL/MinIO及规范资源保护，故障注入不代表断网。"""

import os
from copy import deepcopy
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasCreationAttempt,
    CanvasCreationResource,
    MediaFile,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas_creation_cleanup import cleanup_canvas_creations
from short_drama.service.canvas_service import CanvasService
from short_drama.storage.models import ObjectLocation
from tests.integration.canvas.projects.test_canvas_creation import creation_request
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resource_copy import register
from tests.integration.canvas.resources.test_canvas_resources import ROOT, account, png, upload
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="创建暂存跨页回收需显式启用隔离MinIO",
    ),
]


def pending_creations(resource_app: Any, monkeypatch: pytest.MonkeyPatch):
    owner, _ = account(resource_app, "creation_cleanup_pages_owner")
    other, other_user = account(resource_app, "creation_cleanup_pages_other")
    uploaded = upload(owner, png())
    assert uploaded.status_code == 200, uploaded.text
    original = uploaded.json()["resource"]
    register(owner, "creation-original", original)
    staged = []

    def reject_graph(*args, **kwargs):
        raise WorkflowError("injected_graph_failure", "测试跨页创建暂存回收", 503)

    # 每条准备态都经过真实HTTP复制；只注入最终图事务失败，过期时间由测试推进。
    with monkeypatch.context() as fault:
        fault.setattr(CanvasService, "_replace_graph", reject_graph)
        for index in range(103):
            key = f"creation-cleanup-page-{index}"
            request = creation_request(original, key)
            response = owner.post(
                "/api/v1/canvas-workspace", headers={"Idempotency-Key": key}, json=request
            )
            assert response.status_code == 503, response.text
            with resource_app[1].begin() as session:
                attempt = session.scalar(
                    select(CanvasCreationAttempt).where(
                        CanvasCreationAttempt.idempotency_key == key
                    )
                )
                assert attempt is not None and attempt.status == "pending"
                attempt.expires_at = (
                    utcnow() + timedelta(minutes=90)
                    if index == 102
                    else (utcnow() - timedelta(seconds=1))
                )
                resource = session.scalar(
                    select(CanvasCreationResource).where(
                        CanvasCreationResource.attempt_id == attempt.id
                    )
                )
                assert resource is not None and resource.status == "copied"
                staged.append(
                    {
                        "attempt_id": attempt.id,
                        "target_id": resource.target_resource_id,
                        "locator": resource.storage_locator,
                        "key": key,
                        "request": request,
                        "request_hash": attempt.request_hash,
                        "request_json": deepcopy(attempt.request_json),
                    }
                )

    # 模拟页首100条仍被规范记录保留的准备态，真实保护检查必须继续失败。
    # 后50条由另一个账号的二进制记录保留，清理不能受当前账号的读取范围影响。
    with resource_app[1].begin() as session:
        source = session.get(MediaFile, int(original["id"]))
        for index, item in enumerate(staged[:100]):
            if index < 50:
                values = {
                    column.key: deepcopy(getattr(source, column.key))
                    for column in MediaFile.__table__.columns
                }
                values.update(id=item["target_id"], storage_locator=item["locator"])
                session.add(MediaFile(**values))
            else:
                session.add(
                    CanvasBinaryResource(
                        id=item["target_id"],
                        scope_user_id=int(other_user["id"]),
                        project_id=None,
                        published_at=None,
                        resource_kind="file",
                        mime_type="image/png",
                        storage_locator=item["locator"],
                        original_name="retained.png",
                        byte_size=source.byte_size,
                        checksum_sha256=source.checksum_sha256,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                        created_by=int(other_user["id"]),
                        updated_by=int(other_user["id"]),
                    )
                )
    return owner, other, original, staged


def test_blocked_first_page_does_not_starve_later_independent_files_and_same_key_recovery(
    resource_app: Any, monkeypatch: pytest.MonkeyPatch, database_errors: list
):
    owner, other, original, staged = pending_creations(resource_app, monkeypatch)
    storage, settings = resource_app[0].state.storage, resource_app[2]
    bucket = settings.minio_image_bucket
    assert len({item["locator"] for item in staged}) == 103
    for item in staged:
        location = ObjectLocation.parse(item["locator"], {bucket})
        assert storage.stat(bucket, location.object_name).size == len(png())

    dry_run = cleanup_canvas_creations(resource_app[1], storage, settings)
    remove = storage.remove
    lost_target = f"canvas/resources/{staged[101]['target_id']}"
    lost_acknowledgements = 0

    def lose_one_ack(bucket_name, object_name):
        nonlocal lost_acknowledgements
        remove(bucket_name, object_name)
        if object_name == lost_target and lost_acknowledgements == 0:
            lost_acknowledgements += 1
            raise StorageUnavailable("测试删除已完成但响应丢失")

    with monkeypatch.context() as fault:
        fault.setattr(storage, "remove", lose_one_ack)
        first = cleanup_canvas_creations(resource_app[1], storage, settings, apply=True)
    second = cleanup_canvas_creations(resource_app[1], storage, settings, apply=True)

    assert first == {"examined": 2, "remove_requests": 1, "failed": 101, "applied": True}, (
        "页首规范记录必须保留，第二页两个独立对象仍应被尝试回收",
        first,
    )
    assert dry_run == {"examined": 2, "remove_requests": 0, "failed": 100, "applied": False}
    assert second == {"examined": 2, "remove_requests": 2, "failed": 100, "applied": True}
    assert lost_acknowledgements == 1
    for item in staged[100:102]:
        with pytest.raises(NotFound):
            storage.stat(bucket, f"canvas/resources/{item['target_id']}")
    for item in [*staged[:100], staged[102]]:
        with storage.open(bucket, f"canvas/resources/{item['target_id']}") as response:
            assert response.read() == png()

    with resource_app[1]() as session:
        for index, item in enumerate(staged):
            attempt = session.get(CanvasCreationAttempt, item["attempt_id"])
            assert attempt.status == "pending"
            assert attempt.request_hash == item["request_hash"]
            assert attempt.request_json == item["request_json"]
            resource = session.scalar(
                select(CanvasCreationResource).where(
                    CanvasCreationResource.attempt_id == attempt.id
                )
            )
            assert resource.target_resource_id == item["target_id"]
            assert resource.storage_locator == item["locator"]
            assert resource.status == ("pending" if index in (100, 101) else "copied")
            if index in (100, 101):
                assert resource.copied_at is None
                assert resource.source_media_id is resource.source_binary_id is None
                assert resource.released_at is not None
        assert session.get(MediaFile, staged[0]["target_id"]) is not None
        assert session.get(CanvasBinaryResource, staged[99]["target_id"]) is not None

    item = staged[101]
    headers = {"Idempotency-Key": item["key"]}
    resumed = owner.post("/api/v1/canvas-workspace", headers=headers, json=item["request"])
    assert resumed.status_code == 201, resumed.text
    result = resumed.json()
    assert result["resource_map"] == {original["id"]: str(item["target_id"])}
    assert (
        owner.post("/api/v1/canvas-workspace", headers=headers, json=item["request"]).json()
        == result
    )
    assert owner.get(f"{ROOT}/{item['target_id']}/file").content == png()
    assert owner.get(f"{ROOT}/{original['id']}/file").content == png()
    assert other.get(f"{ROOT}/{item['target_id']}/file").status_code == 404
    assert other.get(f"{ROOT}/{staged[0]['target_id']}/file").status_code == 404
    assert owner.get(f"{ROOT}/{staged[99]['target_id']}/file").status_code == 404
    assert database_errors == []
    owner.close()
    other.close()
