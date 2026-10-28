"""真实 MySQL 验证源节点顺序及保存边界；不调用模型或证明媒体播放。"""

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import ProjectCanvas
from short_drama.domain.canvas.document.canvas import CanvasNode
from short_drama.service.canvas.document.canvas import CanvasService
from tests.integration.canvas.document.test_canvas_workspace import actor, add_user, create, node

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "kinds",
    [("image", "video", "text", "audio"), ("video", "image", "audio", "text")],
    ids=["image_before_video", "video_before_image"],
)
def test_source_order_roundtrips_and_owner_reorder_keeps_private_drafts(db_session, kinds):
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    document = service.read(project_id, canvas_id, private=True)["source_document"]
    document["nodes"] = []
    for kind in kinds:
        item = node(kind)
        item["type"] = kind
        item["metadata"] = {"prompt": f"{kind} 本人草稿", "model": "private-model"}
        document["nodes"].append(item)
    first_request = {"expected_row_version": document["revision"], "source_document": document}
    first_result = service.commit(project_id, canvas_id, first_request, "order-first")
    assert service.commit(project_id, canvas_id, first_request, "order-first") == first_result
    assert (
        service.read(project_id, canvas_id, private=True)["source_document"]["nodes"]
        == (document["nodes"])
    )
    with db_session.begin():
        cached_canvas = db_session.get(ProjectCanvas, canvas_id)
        assert cached_canvas.row_version == int(first_result["row_version"])
        saved = db_session.scalars(
            select(CanvasNode).where(CanvasNode.canvas_id == canvas_id).order_by(CanvasNode.z_index)
        ).all()
        assert [(row.node_key, row.z_index) for row in saved] == list(
            zip(kinds, range(len(kinds)), strict=True)
        )

    add_user(db_session)
    with Session(db_session.bind, expire_on_commit=False, autoflush=False) as other:
        other.info["actor"] = actor(2)
        with pytest.raises(NotFound):
            CanvasService(other).read(project_id, canvas_id, private=True)

    with Session(db_session.bind, expire_on_commit=False, autoflush=False) as other:
        other.info["actor"] = actor(1)
        own = CanvasService(other)
        reopened = own.read(project_id, canvas_id, private=True)["source_document"]
        assert reopened["nodes"] == document["nodes"]
        reopened["nodes"].reverse()
        own.commit(
            project_id,
            canvas_id,
            {"expected_row_version": reopened["revision"], "source_document": reopened},
            "order-owner-reopened",
        )

    with Session(db_session.bind, expire_on_commit=False, autoflush=False) as reopened:
        reopened.info["actor"] = actor(1)
        restored = CanvasService(reopened).read(project_id, canvas_id, private=True)
        assert restored["source_document"]["nodes"] == document["nodes"][::-1]

    mine = service.read(project_id, canvas_id, private=True)["source_document"]
    assert mine["revision"] == str(int(first_result["row_version"]) + 1)
    assert cached_canvas.row_version == int(mine["revision"])
    assert mine["nodes"] == document["nodes"][::-1]
    with db_session.begin():
        saved = db_session.scalars(
            select(CanvasNode).where(CanvasNode.canvas_id == canvas_id).order_by(CanvasNode.z_index)
        ).all()
        assert [(row.node_key, row.z_index) for row in saved] == list(
            zip(kinds[::-1], range(len(kinds)), strict=True)
        )

    stale = deepcopy(first_request)
    with pytest.raises(WorkflowError) as failure:
        service.commit(project_id, canvas_id, stale, "order-stale")
    assert failure.value.code == "canvas_revision_conflict"
    assert (
        service.read(project_id, canvas_id, private=True)["source_document"]["nodes"]
        == (mine["nodes"])
    )
