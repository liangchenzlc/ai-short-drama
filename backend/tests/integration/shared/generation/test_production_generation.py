"""Business generation over MySQL, optionally real isolated RabbitMQ/MinIO.

The provider is always controlled: this suite cannot call a paid model.
"""

import base64
import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.ai import GenerationResult
from short_drama.core.config import Settings
from short_drama.domain import (
    AsyncTask,
    MediaAsset,
    ScriptShotRecord,
    ShotImage,
    ShotScript,
)
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.service.creation.writing.script.documents import ProjectWritingService
from short_drama.service.generations.admission.business import GenerationBusinessService
from short_drama.service.generations.admission.generation import AIGenerationService
from short_drama.service.generations.execution.execution import GenerationExecutionService
from short_drama.service.media.image_groups.groups import ImageGroupService
from short_drama.service.media.library.assets import MediaAssetService
from short_drama.service.projects.management.project import ProjectService
from tests.support.integration.identity import actor as fixture_actor
from tests.support.integration.identity import session_factory

pytestmark = pytest.mark.integration


class ControlledProvider:
    def __init__(self):
        self.calls = []

    def validate(self, *_):
        return {}

    def submit(self, snapshot, request, *_args, **_kwargs):
        scene = request["source"]["scene"]
        self.calls.append(scene)
        if scene == "shot_image":
            output = BytesIO()
            Image.new("RGB", (4, 4), "blue").save(output, "PNG")
            return GenerationResult(
                status="succeeded",
                adapter="openai_images.v1",
                outputs=[{"base64": base64.b64encode(output.getvalue()).decode()}],
            )
        text = (
            "EXT. GARDEN - DAY\nA character enters."
            if scene == "script_rewrite"
            else (
                '{"shots":[{"title":"Garden entrance","shot_size":"wide_shot",'
                '"source_excerpt":"EXT. GARDEN - DAY",'
                '"story_beat":"A character enters the garden.","script":"Wide garden shot",'
                '"duration_ms":3000,"asset_ids":[]}]}'
            )
        )
        return GenerationResult(
            status="succeeded", adapter="openai_chat.v1", text=text, finish_reason="stop"
        )


@pytest.mark.parametrize("infrastructure", [False, True])
def test_script_to_storyboard_and_automatic_image_primary(mysql_engine, db_session, infrastructure):
    if infrastructure and os.environ.get("TEST_PRODUCTION_INFRA") != "1":
        pytest.skip("Opt in to isolated real RabbitMQ and MinIO smoke")
    namespace = "production_test_" + uuid4().hex
    settings = Settings() if infrastructure else Settings(_env_file=None)
    settings = settings.model_copy(update={"generation_queue_namespace": namespace})
    factory = session_factory(mysql_engine)
    provider = ControlledProvider()
    storage = None
    sender = None
    uploaded = []
    if infrastructure:
        from short_drama.storage.minio import MinioStorage
        from short_drama.tasks.runtime.publisher import RabbitSender

        storage = MinioStorage(settings)
        original_put = storage.put

        def tracked_put(bucket, key, *args):
            uploaded.append((bucket, key))
            return original_put(bucket, key, *args)

        storage.put = tracked_put
        sender = RabbitSender(settings)
    executor = GenerationExecutionService(factory, settings, provider, storage)

    def drain(task_id, kind):
        from short_drama.tasks.runtime.publisher import Publisher

        for _ in range(5):
            with factory() as session:
                task = session.get(AsyncTask, int(task_id))
                if task.status in {"succeeded", "failed"}:
                    assert task.status == "succeeded", task.error
                    return
                version = task.message_version
            if sender:
                assert Publisher(factory, settings, sender).tick()
                with sender.app.connection_for_read() as connection:
                    queue = sender.queues[kind](connection)
                    message = queue.get(no_ack=False)
                    assert message is not None
                    assert message.payload[0] == [str(task_id), str(version)]
                    executor.execute(task_id, version)
                    message.ack()
            else:
                executor.execute(task_id, version)
            executor.execute(task_id, version)  # duplicate deliveries are harmless
        pytest.fail("Controlled generation did not finish")

    try:
        configs = {}
        for kind in ("text", "image"):
            configs[kind] = (
                AIModelConfigService(db_session)
                .create(
                    {
                        "service_type": kind,
                        "name": "controlled " + kind,
                        "model_key": "fixture",
                        "provider": "ark",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                    }
                )
                .id
            )
        project = ProjectService(db_session).create_project({"name": namespace})
        p = project.id
        writing = ProjectWritingService(db_session)
        saved = writing.save_script(
            p, {"content_version": "1", "script_id": None, "content": "A garden encounter."}
        )
        script_id = saved["script"]["id"]
        generations = AIGenerationService(db_session, settings)
        task, _ = generations.create(
            "text",
            {
                "config_id": str(configs["text"]),
                "source": {
                    "scene": "script_rewrite",
                    "project_id": str(p),
                    "content_version": "2",
                    "script_id": script_id,
                    "start": 0,
                    "end": 19,
                    "selected_text": "A garden encounter.",
                },
            },
            "novel-smoke",
        )
        drain(task["generation_id"], "text")
        confirmed = writing.get(p)
        assert confirmed["editing_script"]["content"] == "EXT. GARDEN - DAY\nA character enters."
        assert confirmed["content_version"] == "3"
        task, _ = generations.create(
            "text",
            {
                "config_id": str(configs["text"]),
                "source": {
                    "scene": "script_shots",
                    "project_id": str(p),
                    "script_id": script_id,
                    "content_version": confirmed["content_version"],
                },
            },
            "shots-smoke",
        )
        drain(task["generation_id"], "text")
        board = ProjectStoryboardService(db_session)
        assert board.list(p)["total"] == 0
        payload = {
            "mode": "append",
            "content_version": confirmed["content_version"],
            "storyboard_version": "1",
        }
        barrier = Barrier(2)

        def apply_storyboard(_attempt):
            with Session(
                mysql_engine,
                expire_on_commit=False,
                autoflush=False,
                info={"actor": fixture_actor()},
            ) as session:
                barrier.wait(timeout=10)
                return GenerationBusinessService(session).apply_storyboard(
                    p, task["generation_id"], payload
                )

        with ThreadPoolExecutor(max_workers=2) as executor_pool:
            applications = list(executor_pool.map(apply_storyboard, range(2)))
        assert sorted(result["already_applied"] for result in applications) == [False, True]
        assert applications[0]["shot_ids"] == applications[1]["shot_ids"]
        assert {result["storyboard_version"] for result in applications} == {"2"}
        shot_id = applications[0]["shot_ids"][0]
        db_session.expire_all()
        assert board.list(p)["total"] == 1
        adopted = board.get(p, shot_id)["shot"]
        assert adopted["title"] == "Garden entrance" and adopted["shot_size"] == "wide_shot"
        with db_session.begin():
            assert (
                db_session.scalar(
                    select(func.count()).select_from(ShotScript).where(ShotScript.project_id == p)
                )
                == 1
            )
            assert (
                db_session.scalar(
                    select(func.count())
                    .select_from(ScriptShotRecord)
                    .where(ScriptShotRecord.batch_id == int(task["generation_id"]))
                )
                == 1
            )
        if infrastructure:
            context = board.get_shot_context(p, shot_id)
            task, _ = generations.create(
                "image",
                {
                    "config_id": str(configs["image"]),
                    "input": {"prompt": ""},
                    "parameters": {"count": 1, "aspect": "16:9", "resolution": "2K"},
                    "source": {
                        "scene": "shot_image",
                        "shot_id": shot_id,
                        "layout": "single",
                        "context_mode": "saved",
                        "row_version": "1",
                        "context_hash": context["context_hash"],
                    },
                },
                "image-smoke",
            )
            drain(task["generation_id"], "image")
            group = ImageGroupService(db_session, settings, storage).current("shot", shot_id)
            assert group["id"] == task["image_group_id"]
            assert group["status"] == "completed" and group["primary_index"] == 1
            saved = board.get(p, shot_id)["shot"]
            assert saved["image"]["media_id"] == group["items"][0]["media_id"]
            assert not saved["image"]["is_stale"] and saved["row_version"] == "2"
            with db_session.begin():
                assert db_session.scalar(select(ShotImage)).media_id == int(
                    group["items"][0]["media_id"]
                )
                asset_id = db_session.scalar(select(MediaAsset.id))
            # 旧媒体库的并发重放不应重复写主图或推进已自动保存的版本。
            body = {
                "target": {"type": "shot_image", "id": shot_id},
                "expected_media_id": saved["image"]["media_id"],
                "expected_row_version": saved["row_version"],
                "expected_context_hash": context["context_hash"],
            }
            image_barrier = Barrier(2)

            def adopt_image(_attempt):
                with Session(
                    mysql_engine,
                    expire_on_commit=False,
                    autoflush=False,
                    info={"actor": fixture_actor()},
                ) as session:
                    image_barrier.wait(timeout=10)
                    return MediaAssetService(session, settings, storage).apply(asset_id, body)

            with ThreadPoolExecutor(max_workers=2) as executor_pool:
                adoptions = list(executor_pool.map(adopt_image, range(2)))
            assert adoptions[0] == adoptions[1]
            assert adoptions[0]["row_version"] == "2"
            assert adoptions[0]["storyboard_version"] == "3"
            db_session.expire_all()
            with db_session.begin():
                assert db_session.scalar(select(func.count()).select_from(ShotImage)) == 1
            assert board.get(p, shot_id)["shot"]["image"] is not None
            assert uploaded
        assert provider.calls == (
            ["script_rewrite", "script_shots", "shot_image"]
            if infrastructure
            else ["script_rewrite", "script_shots"]
        )
    finally:
        if storage:
            for bucket, key in uploaded:
                storage.remove(bucket, key)
            storage.close()
        if sender:
            from kombu import Queue

            with sender.app.connection_for_write() as connection:
                for queue in sender.queues.values():
                    queue(connection).delete()
                    Queue(queue.name + ".dead")(connection).delete()
                sender.exchange(connection).delete()
                sender.dead_exchange(connection).delete()
            sender.app.close()
