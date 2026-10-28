"""永久媒体引用的真实HTTP/MySQL/MinIO差异保存回归。"""

import json
import os
import re
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update

from short_drama.domain.canvas.document.canvas import (
    CanvasMediaReference,
    CanvasRevision,
    CanvasUserMediaReference,
)
from short_drama.service.base import utcnow
from tests.integration.canvas.resources.test_canvas_library import (
    database_errors as database_errors,
)
from tests.integration.canvas.resources.test_canvas_resources import (
    ROOT,
    account,
    canvas,
    png,
    upload,
)
from tests.integration.canvas.resources.test_canvas_resources import resource_app as resource_app
from tests.integration.standard.identity.test_identity_collaboration import (
    identity_app as identity_app,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="永久媒体引用回归需显式启用隔离MinIO",
    ),
]


def saved_document(
    client: TestClient, path: str, document: dict[str, Any], key: str
) -> dict[str, Any]:
    response = client.post(
        path + "/commits",
        headers={"Idempotency-Key": key},
        json={"expected_row_version": document["revision"], "source_document": document},
    )
    assert response.status_code == 200, response.text
    response = client.get(path + "/my-document")
    assert response.status_code == 200, response.text
    return response.json()["source_document"]


def reference_rows(resource_app: Any, canvas_id: int) -> dict[tuple[str, int], dict[str, Any]]:
    with resource_app[1]() as session:
        return {
            (model.__tablename__, row.id): {
                column.key: deepcopy(getattr(row, column.key)) for column in model.__table__.columns
            }
            for model in (CanvasMediaReference, CanvasUserMediaReference)
            for row in session.scalars(
                select(model).where(model.canvas_id == canvas_id, model.owner_kind != "revision")
            )
        }


def reference_scene(resource_app: Any, count: int, label: str):
    owner, _ = account(resource_app, label)
    project, path, document = canvas(owner, label + "-project")
    resources = []
    for color in ("red", "blue"):
        response = upload(owner, png(color), key=label + "-" + color, source=document["id"])
        assert response.status_code == 200, response.text
        resources.append(response.json()["resource"])
    shared, private = resources
    document["nodes"] = [
        {
            "id": "prose",
            "type": "text",
            "title": "文字",
            "position": {"x": 0, "y": 0},
            "width": 320,
            "height": 220,
            "metadata": {"content": "首次保存的文字"},
        },
        *[
            {
                "id": f"image-{index}",
                "type": "image",
                "title": f"图片 {index}",
                "position": {"x": 400 + index * 350, "y": 0},
                "width": 320,
                "height": 220,
                "metadata": {
                    "storageKey": f"resource:{shared['id']}",
                    "content": f"{ROOT}/{shared['id']}/file",
                    "prompt": f"本人参数 {index}",
                    "referenceImages": [f"resource:{private['id']}"],
                },
            }
            for index in range(count)
        ],
    ]
    return (
        owner,
        project,
        path,
        saved_document(owner, path, document, label + "-seed"),
        shared,
        private,
    )


@pytest.mark.parametrize("media_nodes", [2, 100])
def test_text_edit_does_not_rewrite_unchanged_public_and_private_media_references(
    resource_app: Any, database_errors: list, media_nodes: int, tmp_path: Path
) -> None:
    owner, project, path, document, shared, private = reference_scene(
        resource_app, media_nodes, f"refs_owner_{media_nodes}"
    )
    outsider, _ = account(resource_app, f"refs_other_{media_nodes}")
    engine = resource_app[1].kw["bind"]
    assert (engine.url.database or "").endswith("_test")
    before = reference_rows(resource_app, int(project["primary_canvas_id"]))
    before_counts = Counter(key[0] for key in before)
    assert before_counts == {
        "canvas_media_references": media_nodes * 2,
        "canvas_user_media_references": media_nodes,
    }
    document["nodes"][0]["metadata"]["content"] = "只修改文字，永久媒体及本人参考保持"
    updates: Counter[str] = Counter()
    statements: Counter[str] = Counter()

    def collect(_connection, _cursor, statement, parameters, _context, many):
        statements[statement.lstrip().split(None, 1)[0].upper()] += 1
        match = re.match(r"\s*UPDATE `?(canvas_(?:user_)?media_references)`?\s", statement, re.I)
        if match:
            updates[match[1].lower()] += len(parameters) if many else 1

    event.listen(engine, "before_cursor_execute", collect)
    started = perf_counter()
    try:
        response = owner.post(
            path + "/commits",
            headers={"Idempotency-Key": f"refs-text-edit-{media_nodes}"},
            json={"expected_row_version": document["revision"], "source_document": document},
        )
    finally:
        duration_ms = (perf_counter() - started) * 1000
        event.remove(engine, "before_cursor_execute", collect)
    output = Path(os.environ.get("CANVAS_REFERENCE_SQL_OUTPUT", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    (output / f"media-references-{media_nodes}.json").write_text(
        json.dumps(
            {
                "scope": "真实HTTP/MySQL/MinIO，两个PNG永久资源；单次回归数据，不是p95或供应商验收",
                "source_sha256": sha256(
                    (
                        Path(__file__).resolve().parents[4]
                        / "src/short_drama/service/canvas_service.py"
                    ).read_bytes()
                ).hexdigest(),
                "media_nodes": media_nodes,
                "node_count": media_nodes + 1,
                "before_reference_counts": before_counts,
                "response_status": response.status_code,
                "duration_ms": duration_ms,
                "sql_statements": sum(statements.values()),
                "sql_by_verb": statements,
                "updated_reference_bind_rows": updates,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assert response.status_code == 200, response.text
    assert int(response.json()["row_version"]) == int(document["revision"]) + 1
    assert sum(updates.values()) == 0, "文字编辑不得重写未变的共享作品或本人参考引用"
    assert reference_rows(resource_app, int(project["primary_canvas_id"])) == before
    current = owner.get(path + "/my-document").json()["source_document"]
    assert current["nodes"] == document["nodes"]
    public = owner.get(path).json()["source_document"]
    assert all(
        "referenceImages" not in node["metadata"] and "prompt" not in node["metadata"]
        for node in public["nodes"]
    )
    assert owner.get(f"{ROOT}/{shared['id']}/file").content == png("red")
    assert owner.get(f"{ROOT}/{private['id']}/file").content == png("blue")
    assert outsider.get(f"{ROOT}/{private['id']}/file").status_code == 404
    assert outsider.get(path).status_code == 404
    assert outsider.get(f"{ROOT}/{shared['id']}/file").status_code == 404
    assert database_errors == []


def test_media_replacement_removal_history_and_foreign_project_guard(resource_app: Any) -> None:
    owner, project, path, document, shared, private = reference_scene(
        resource_app, 2, "refs_boundary_owner"
    )
    outsider, _ = account(resource_app, "refs_boundary_outsider")
    canvas_id = int(project["primary_canvas_id"])
    before = reference_rows(resource_app, canvas_id)
    previous_document = deepcopy(document)
    replacements = []
    for color in ("green", "yellow"):
        response = upload(owner, png(color), key="refs-replace-" + color, source=document["id"])
        assert response.status_code == 200, response.text
        replacements.append(response.json()["resource"])
    public_replacement, private_replacement = replacements
    engine = resource_app[1].kw["bind"]
    assert (engine.url.database or "").endswith("_test")
    with engine.begin() as connection:
        connection.execute(
            update(CanvasRevision.__table__)
            .where(CanvasRevision.canvas_id == canvas_id)
            .values(created_at=utcnow() - timedelta(minutes=6))
        )
    document["nodes"][1]["metadata"].update(
        storageKey=f"resource:{public_replacement['id']}",
        content=f"{ROOT}/{public_replacement['id']}/file",
        referenceImages=[f"resource:{private_replacement['id']}"],
    )
    document = saved_document(owner, path, document, "refs-replace-both")
    after = reference_rows(resource_app, canvas_id)
    assert after.keys() == before.keys(), "同槽替换应保留引用身份"
    changes = 0
    for key, row in after.items():
        if row["media_id"] != before[key]["media_id"]:
            changes += 1
            assert row["updated_at"] > before[key]["updated_at"]
        else:
            assert row == before[key], "另一节点的共享/私人引用不得被替换操作重写"
    assert changes == 3
    history = owner.get(path + "/revisions").json()["items"][0]
    frozen = owner.get(path + f"/revisions/{history['id']}").json()["source_document"]
    assert frozen["nodes"] == previous_document["nodes"]
    assert owner.get(f"{ROOT}/{public_replacement['id']}/file").content == png("green")
    assert owner.get(f"{ROOT}/{private_replacement['id']}/file").content == png("yellow")
    assert outsider.get(f"{ROOT}/{private['id']}/file").status_code == 404
    assert owner.get(f"{ROOT}/{shared['id']}/file").content == png("red")
    for field in ("storageKey", "content", "referenceImages"):
        document["nodes"][1]["metadata"].pop(field)
    document = saved_document(owner, path, document, "refs-remove-both")
    replaced_ids = {int(value["id"]) for value in replacements}
    assert reference_rows(resource_app, canvas_id) == {
        key: row for key, row in after.items() if row["media_id"] not in replaced_ids
    }
    foreign_project, _, foreign_document = canvas(owner, "refs-foreign-project")
    assert foreign_project["id"] != project["id"]
    response = upload(owner, png("purple"), key="refs-foreign", source=foreign_document["id"])
    assert response.status_code == 200, response.text
    foreign_resource = response.json()["resource"]
    invalid = deepcopy(document)
    invalid["nodes"][2]["metadata"].update(
        storageKey=f"resource:{foreign_resource['id']}",
        content=f"{ROOT}/{foreign_resource['id']}/file",
    )
    before_invalid = reference_rows(resource_app, canvas_id)
    response = owner.post(
        path + "/commits",
        headers={"Idempotency-Key": "refs-invalid-project"},
        json={"expected_row_version": document["revision"], "source_document": invalid},
    )
    assert response.status_code == 404, response.text
    assert owner.get(path + "/my-document").json()["source_document"] == document
    assert reference_rows(resource_app, canvas_id) == before_invalid
    assert (
        owner.get(path + f"/revisions/{history['id']}").json()["source_document"]["nodes"]
        == previous_document["nodes"]
    )
