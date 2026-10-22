"""隔离 MySQL 上验证图片工具事务、生成管线与作品/私人执行隔离。"""

from io import BytesIO
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select

from short_drama.ai import GenerationResult
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import (
    AIModelConfig,
    ImageGroup,
    ImageGroupItem,
    MediaFile,
    ShotImage,
    ShotScript,
)
from short_drama.domain.collaboration import User
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.base import utcnow
from short_drama.service.image_operation_service import ImageOperationService
from short_drama.service.shot_script_service import ShotScriptService
from tests.integration.standard.assets.test_asset_image_generation import (
    create_asset,
    drain,
    submit,
    submit_shot,
)
from tests.integration.standard.assets.test_asset_image_generation import (
    flow as flow,
)

pytestmark = pytest.mark.integration


def actor(identifier=1):
    return ActorContext(
        identifier, f"artist{identifier}", "fixture@example.test", True, 1, "", "test"
    )


def prepare(flow, kind, parameters=None, index=0):
    tools = ImageOperationService(flow.session, flow.settings, flow.storage)
    current = tools.current("asset", flow.asset.id)
    item = current["items"][index]
    operation = tools.create_operation(
        {
            "item_id": item["id"],
            "expected_item_version": item["item_version"],
            "operation_type": kind,
            "parameters": parameters or {},
        },
        str(uuid4()),
    )
    return tools, current, operation


def initialize(flow, count=1):
    flow.session.info["actor"] = actor()
    flow.asset = create_asset(flow)
    task = submit(flow, flow.asset, count)
    assert drain(flow, task) == "succeeded"
    flow.session.info["actor"] = actor()


def test_image_group_migration_reentry_rejects_disabled_check(migration_mysql_engine):
    from scripts.image_groups_ddl import apply_ddl

    with migration_mysql_engine.begin() as connection:
        assert apply_ddl(connection) == []
        connection.exec_driver_sql(
            "ALTER TABLE image_groups ALTER CHECK ck_image_groups_count NOT ENFORCED"
        )
        try:
            with pytest.raises(RuntimeError, match="CHECK 定义或启用状态不匹配"):
                apply_ddl(connection)
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE image_groups ALTER CHECK ck_image_groups_count ENFORCED"
            )
        assert apply_ddl(connection) == []


@pytest.mark.parametrize("kind", ["texture", "angle", "lighting", "emotion"])
def test_ai_image_tools_use_common_admission_and_archive_into_original_position(flow, kind):
    initialize(flow, 2)
    parameters = {"instructions": "仅调整当前图片，保留主体"}
    if kind == "emotion":
        parameters.update(x=0.25, y=0.25, width=0.5, height=0.5)
    tools, current, operation = prepare(flow, kind, parameters, index=1)
    prepared = tools.execute_operation(operation["id"])
    assert prepared["status"] == "prepared"
    receipt, created = flow.generations.create(
        "image",
        {
            "config_id": str(flow.config.id),
            "source": {"scene": "image_operation", "operation_id": operation["id"]},
            "input": {"prompt": "", "reference_media_ids": []},
            "parameters": {"count": 1},
        },
        key := str(uuid4()),
    )
    assert created
    assert flow.generations.lookup_by_key("image", key)["generation_id"] == receipt["generation_id"]
    assert drain(flow, receipt["generation_id"]) == "succeeded"
    flow.session.expunge_all()
    updated = tools.current("asset", flow.asset.id)
    assert updated["id"] == current["id"] and updated["primary_index"] == 1
    assert updated["items"][0]["media_id"] == current["items"][0]["media_id"]
    assert updated["items"][1]["media_id"] != current["items"][1]["media_id"]
    assert updated["items"][1]["history"][0]["media_id"] == current["items"][1]["media_id"]
    assert tools.get_operation(operation["id"])["status"] == "completed"
    assert "canvas_request" not in flow.provider.calls[-1]
    assert flow.provider.calls[-1]["source"]["scene"] == "image_operation"
    assert len(flow.provider.calls) == 2


def test_mask_capability_rejects_before_task(flow):
    initialize(flow)
    tools, current, operation = prepare(flow, "mask", {"instructions": "将背景改为雨夜"})
    stream = BytesIO()
    mask = Image.new("RGBA", (4, 4), "white")
    mask.putpixel((1, 1), (255, 255, 255, 0))
    mask.save(stream, "PNG")
    stream.seek(0)
    tools.execute_operation(operation["id"], stream)
    with pytest.raises(WorkflowError) as rejected:
        flow.generations.create(
            "image",
            {
                "config_id": str(flow.config.id),
                "input": {"prompt": ""},
                "source": {"scene": "image_operation", "operation_id": operation["id"]},
            },
            str(uuid4()),
        )
    assert rejected.value.code == "mask_not_supported"
    assert len(flow.provider.calls) == 1
    assert (
        tools.current("asset", flow.asset.id)["items"][0]["media_id"]
        == current["items"][0]["media_id"]
    )


def test_annotation_requires_original_dimensions_and_reuses_operation_after_validation(flow):
    initialize(flow)
    tools, current, operation = prepare(flow, "annotation")
    stream = BytesIO()
    Image.new("RGB", (2, 2), "red").save(stream, "PNG")
    stream.seek(0)
    with pytest.raises(WorkflowError) as rejected:
        tools.execute_operation(operation["id"], stream)
    assert rejected.value.code == "invalid_annotation_size"
    assert tools.get_operation(operation["id"])["status"] == "pending"
    stream = BytesIO()
    Image.new("RGB", (4, 4), "red").save(stream, "PNG")
    stream.seek(0)
    assert tools.execute_operation(operation["id"], stream)["status"] == "completed"
    updated = tools.current("asset", flow.asset.id)
    assert updated["items"][0]["history"][0]["media_id"] == current["items"][0]["media_id"]
    assert len(flow.provider.calls) == 1


def test_invalidated_shot_keeps_completed_generation_without_replacing_work(flow):
    shot = ShotScriptService(flow.session).create(
        {"project_id": flow.project.id, "position": 1, "script": "旅人走入雨夜。"}
    )
    task_id = submit_shot(flow, shot.id, 2)
    with flow.session.begin():
        flow.session.get(ShotScript, int(shot.id)).deleted_at = utcnow()
    assert drain(flow, task_id) == "succeeded"
    with flow.factory() as session:
        group = session.scalar(select(ImageGroup).where(ImageGroup.task_id == int(task_id)))
        items = list(
            session.scalars(select(ImageGroupItem).where(ImageGroupItem.group_id == group.id))
        )
        assert all(item.media_id for item in items)
        assert group.status == "completed" and group.primary_index is None
        assert session.scalar(select(ShotImage).where(ShotImage.shot_id == shot.id)) is None


def test_reverse_prompt_requires_declared_vision_and_retains_original_artwork(flow, monkeypatch):
    initialize(flow)
    model = AIModelConfigService(flow.session, settings=flow.settings).create(
        {
            "service_type": "text",
            "name": "看图测试替身",
            "model_key": "fixture-text",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
        }
    )
    tools, current, operation = prepare(flow, "reverse_prompt")
    tools.execute_operation(operation["id"])
    body = {
        "config_id": str(model.id),
        "source": {"scene": "image_operation", "operation_id": operation["id"]},
    }
    with pytest.raises(WorkflowError) as rejected:
        flow.generations.create("text", body, str(uuid4()))
    assert rejected.value.code == "vision_not_supported"
    with flow.session.begin():
        flow.session.get(AIModelConfig, model.id).capability_cache = {
            "canvas_text_capability": {
                "references": {"maxImages": 1, "maxImageBytes": 1024**2, "promptMaxChars": 4000}
            },
        }
    monkeypatch.setattr(
        flow.provider,
        "submit",
        lambda *_args, **_kw: GenerationResult(
            status="succeeded",
            adapter="openai_chat.v1",
            text="雨夜庭院，电影光线",
        ),
    )
    receipt, _ = flow.generations.create("text", body, str(uuid4()))
    assert drain(flow, receipt["generation_id"]) == "succeeded"
    flow.session.expunge_all()
    assert (
        flow.generations.detail(receipt["generation_id"])["result"]["text"]["content"]
        == "雨夜庭院，电影光线"
    )
    assert tools.get_operation(operation["id"])["status"] == "completed"
    assert (
        tools.current("asset", flow.asset.id)["items"][0]["media_id"]
        == current["items"][0]["media_id"]
    )


def test_group_artwork_is_shared_but_prompts_tasks_and_operation_remain_private(flow):
    initialize(flow, 2)
    tools, current, operation = prepare(flow, "resize", {"output_width": 2, "output_height": 3})
    tools.execute_operation(operation["id"])
    with flow.factory.begin() as session:
        session.add(
            User(
                id=2,
                username="member",
                display_name="成员",
                email="member@example.test",
                password_hash="fixture",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
        session.flush()
    flow.session.expunge_all()
    flow.session.info["actor"] = actor(2)
    with pytest.raises(NotFound):
        tools.current("asset", flow.asset.id)
    with pytest.raises(NotFound):
        tools.get_operation(operation["id"])
    flow.session.info["actor"] = actor(1)
    own = tools.current("asset", flow.asset.id)
    assert own["task_id"] and own["items"][0]["prompt"]


def test_local_crop_restore_and_split_preserve_original_media_and_dimensions(flow):
    initialize(flow)
    tools, current, operation = prepare(flow, "crop", {"width": 0.5})
    tools.execute_operation(operation["id"])
    cropped = tools.current("asset", flow.asset.id)
    assert (cropped["items"][0]["width"], cropped["items"][0]["height"]) == (2, 4)
    original_id = current["items"][0]["media_id"]
    item = cropped["items"][0]
    restore = tools.create_operation(
        {
            "item_id": item["id"],
            "expected_item_version": item["item_version"],
            "operation_type": "replace",
            "restore_media_id": original_id,
        },
        str(uuid4()),
    )
    tools.execute_operation(restore["id"])
    assert tools.current("asset", flow.asset.id)["items"][0]["media_id"] == original_id
    _, _, split = prepare(flow, "split", {"rows": 2, "columns": 2})
    tools.execute_operation(split["id"])
    divided = tools.current("asset", flow.asset.id)
    assert divided["expected_count"] == 4 and divided["primary_index"] == 1
    assert tools.history("asset", flow.asset.id)["total"] == 2
    with flow.session.begin():
        assert flow.session.get(MediaFile, int(original_id)) is not None
        assert (
            flow.session.scalar(
                select(ImageGroupItem).where(ImageGroupItem.media_id == int(original_id))
            )
            is not None
        )
