"""图片组与逐图处理的业务测试；供应商和对象存储使用本地替身。"""

from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select

from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import AssetImageCandidate, ImageGroup
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.image_group_service import ImageGroupService
from short_drama.service.image_operation_service import ImageOperationService
from short_drama.service.image_processing import (
    composite_emotion,
    decode,
    emotion_artifacts,
    process,
)
from short_drama.service.project_service import ProjectService
from short_drama.storage.models import ObjectLocation, StoredObject
from tests.support.generation_fixtures import generation_session


def pixels(size=(12, 8), color=(30, 70, 120, 255)):
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, "PNG")
    return output.getvalue()


class Storage:
    def __init__(self):
        self.objects = {}

    def put(self, bucket, key, stream, length, mime):
        data = stream.read()
        assert len(data) == length
        self.objects[bucket, key] = data, mime
        return self.stat(bucket, key)

    def stat(self, bucket, key):
        if (bucket, key) not in self.objects:
            raise NotFound()
        data, mime = self.objects[bucket, key]
        return StoredObject(
            bucket=bucket,
            object_name=key,
            storage_locator=ObjectLocation(bucket, key).locator,
            size=len(data),
            content_type=mime,
        )

    @contextmanager
    def open(self, bucket, key):
        data, _ = self.objects[bucket, key]
        yield SimpleNamespace(
            stream=lambda size: (data[i : i + size] for i in range(0, len(data), size))
        )

    def remove(self, bucket, key, **_):
        self.objects.pop((bucket, key), None)

    def presigned_get(self, bucket, key, expiry):
        return f"https://media.invalid/{bucket}/{key}"


@pytest.fixture
def flow():
    session = generation_session()
    session.info["actor"] = ActorContext(1, "fixture", "test@example.test", True, 1, "", "test")
    settings = Settings(_env_file=None, auth_enabled=False, agent_enabled=False)
    storage = Storage()
    project = ProjectService(session).create({"name": "图片组"})
    library = AssetLibraryService(session, settings, storage)
    asset, _ = library.create(
        "project",
        project.id,
        project.id,
        {"name": "庭院", "kind": "scene", "prompt": "雨夜"},
        str(uuid4()),
    )
    config = AIModelConfigService(session, settings=settings).create(
        {
            "service_type": "image",
            "name": "图片测试替身",
            "model_key": "fixture",
            "provider": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        }
    )
    value = SimpleNamespace(
        session=session,
        storage=storage,
        settings=settings,
        asset=asset,
        project=project,
        library=library,
        config=config,
        groups=ImageGroupService(session, settings, storage),
        tools=ImageOperationService(session, settings, storage),
        generations=AIGenerationService(session, settings),
    )
    try:
        yield value
    finally:
        session.close()
        session.get_bind().dispose()


def group(flow, count=1):
    asset = flow.library.get(flow.asset.id)
    receipt, _ = flow.generations.create(
        "image",
        {
            "config_id": str(flow.config.id),
            "input": {"prompt": "仅本人可见"},
            "parameters": {"count": count},
            "source": {
                "scene": "asset_image",
                "project_id": str(flow.project.id),
                "asset_id": str(asset.id),
                "row_version": str(asset.row_version),
            },
        },
        str(uuid4()),
    )
    current = flow.groups.current("asset", asset.id)
    assert current["task_id"] == receipt["generation_id"]
    return current


def fill(flow, current, index, data=None):
    # 模拟 Worker 在已归档事务中回填固定 output_index；不声称真实供应商验收。
    with flow.tools._transaction():
        row = flow.session.get(ImageGroup, int(current["id"]))
        item = flow.groups.groups.items(row.id, lock=True)[index - 1]
        values = flow.tools._upload(data or pixels(color=(index * 30, 80, 140, 255)))
        media = flow.tools._media(values, (None, flow.project.id), 1)
        item.media_id = media.id
        item.item_version += 1
        from short_drama.service.publication import publish

        publish(media)
        if index == 1:
            flow.groups.write_primary(row, item)
    return flow.groups.current("asset", flow.asset.id)


def operation(flow, current, kind, parameters=None, index=1, **extra):
    item = current["items"][index - 1]
    body = {
        "item_id": item["id"],
        "expected_item_version": item["item_version"],
        "operation_type": kind,
        "parameters": parameters or {},
        **extra,
    }
    return flow.tools.create_operation(body, str(uuid4())), body


@pytest.mark.parametrize("count", [1, 2, 4])
def test_fixed_positions_and_first_result_is_primary(flow, count):
    current = group(flow, count)
    assert len(current["items"]) == count
    assert all(item["status"] == "generating" for item in current["items"])
    current = fill(flow, current, 1)
    asset = flow.library.get(flow.asset.id)
    assert str(asset.media_id) == current["items"][0]["media_id"]
    assert current["primary_index"] == 1
    with flow.tools._transaction(read_only=True):
        assert flow.session.scalar(select(func.count()).select_from(AssetImageCandidate)) == 0


def test_manual_primary_survives_late_first_result_and_version_conflict(flow):
    current = fill(flow, group(flow, 4), 2)
    payload = {
        "item_id": current["items"][1]["id"],
        "expected_item_version": current["items"][1]["item_version"],
        "expected_primary_version": current["primary_version"],
        "expected_target_version": current["target_version"],
    }
    selected = flow.groups.primary(current["id"], payload)
    current = fill(flow, selected, 1)
    assert current["primary_index"] == 2
    assert str(flow.library.get(flow.asset.id).media_id) == current["items"][1]["media_id"]
    with pytest.raises(WorkflowError, match="主图或图片已变化"):
        flow.groups.primary(current["id"], payload)


def test_crop_and_nonprimary_edit_preserve_positions_and_originals(flow):
    current = fill(flow, fill(flow, group(flow, 2), 1), 2)
    original = current["items"][1]["media_id"]
    created, body = operation(flow, current, "crop", {"width": 0.5}, index=2)
    assert flow.tools.create_operation(body, created_key := str(uuid4()))["id"]
    duplicate = flow.tools.create_operation(body, created_key)
    assert duplicate["status"] == "pending"
    result = flow.tools.execute_operation(created["id"])
    assert flow.tools.execute_operation(created["id"]) == result
    updated = flow.groups.current("asset", flow.asset.id)
    assert updated["primary_index"] == 1
    assert (updated["items"][1]["width"], updated["items"][1]["height"]) == (6, 8)
    assert updated["items"][1]["history"][0]["media_id"] == original
    assert updated["items"][0]["media_id"] == current["items"][0]["media_id"]
    with pytest.raises(Conflict):
        flow.tools.create_operation({**body, "parameters": {"width": 0.25}}, created_key)


def test_split_creates_new_group_and_first_primary_with_old_group_history(flow):
    current = fill(flow, group(flow), 1, pixels((11, 7)))
    created, _ = operation(flow, current, "split", {"rows": 2, "columns": 2})
    result = flow.tools.execute_operation(created["id"])
    new = flow.groups.current("asset", flow.asset.id)
    assert result["result_group_id"] == new["id"] != current["id"]
    assert new["primary_index"] == 1 and new["expected_count"] == 4
    assert [(item["width"], item["height"]) for item in new["items"]] == [
        (5, 3),
        (6, 3),
        (5, 4),
        (6, 4),
    ]
    assert flow.groups.history("asset", flow.asset.id)["total"] == 2


def test_stale_operation_cannot_overwrite_and_upload_checks_dimensions(flow):
    current = fill(flow, group(flow), 1)
    created, _ = operation(flow, current, "annotation")
    with pytest.raises(WorkflowError, match="原图尺寸一致"):
        flow.tools.execute_operation(created["id"], BytesIO(pixels((3, 3))))
    assert (
        flow.groups.current("asset", flow.asset.id)["items"][0]["media_id"]
        == current["items"][0]["media_id"]
    )
    fill(flow, group(flow), 1)
    with pytest.raises(WorkflowError, match="图片或当前图片组已变化"):
        flow.tools.execute_operation(created["id"], BytesIO(pixels()))


def test_delayed_ai_edit_is_retained_in_history_without_overwriting(flow):
    current = fill(flow, group(flow), 1)
    created, _ = operation(flow, current, "texture", {"instructions": "皮肤纹理"})
    flow.tools.execute_operation(created["id"])
    newer = fill(flow, group(flow), 1)
    with flow.tools._transaction():
        op, row, scope, old, item = flow.tools._operation(created["id"])
        media = flow.tools._media(flow.tools._upload(pixels(color=(255, 0, 0, 255))), scope, 1)
        flow.tools.replace_locked(op, row, scope, old, item, media)
    assert flow.tools.get_operation(created["id"])["status"] == "conflict"
    assert flow.groups.current("asset", flow.asset.id)["id"] == newer["id"]
    assert flow.groups.history("asset", flow.asset.id)["total"] == 3


def test_pixel_processing_bounds_and_emotion_preserves_outside_region():
    source = pixels((100, 80))
    output = process(
        source, "resize", {"output_width": 3, "output_height": 7, "interpolation": "high"}
    )
    assert decode(output[0]).size == (3, 7)
    artifacts, geometry = emotion_artifacts(
        source, {"x": 0.4, "y": 0.3, "width": 0.2, "height": 0.3}
    )
    assert decode(artifacts[0]).size == decode(artifacts[2]).size
    result = decode(composite_emotion(source, pixels((100, 100), (255, 0, 0, 255)), geometry))
    assert result.size == (100, 80)
    assert result.getpixel((0, 0)) == decode(source).getpixel((0, 0))
    assert result.getpixel((50, 36)) != decode(source).getpixel((50, 36))
