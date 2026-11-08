from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    MediaAsset,
    MediaFile,
    ShotImage,
    ShotVideo,
)
from short_drama.service.base import utcnow
from short_drama.service.creation.storyboard.images.image import ShotImageService
from short_drama.service.creation.storyboard.management.script import ShotScriptService
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.service.generations.admission.generation import AIGenerationService
from short_drama.service.generations.execution.execution import GenerationExecutionService
from short_drama.service.media.library.assets import MediaAssetService
from tests.support.generation_fixtures import config, generation_session, settings
from tests.support.integration.identity import actor as fixture_actor
from tests.unit.standard.storyboard.test_project_storyboard import setup_storyboard


def setup_video(session):
    model = config(session, "video")
    model.base_url = "https://api.modelhub.cc"
    model.model_key = "seedance-2.0-mini"
    session.commit()
    project, episode = setup_storyboard(session)
    storyboard = ProjectStoryboardService(session)
    shot = storyboard.create(
        episode,
        {
            "storyboard_version": "1",
            "script": "林晚先抬眼，再用右手拿起红伞。镜头缓推至中近景。",
            "duration_ms": 5000,
        },
        "video-shot",
    )["shot"]
    session.add(
        MediaFile(
            id=101,
            project_id=episode,
            format_code="image/png",
            storage_locator="minio://images/frame.png",
            original_name="",
        )
    )
    session.commit()
    ShotImageService(session).create(
        {
            "project_id": episode,
            "shot_id": shot["id"],
            "media_id": "101",
            "layout": "single",
            "aspect": "16:9",
            "resolution": "2K",
            "context_hash": shot["context_hash"],
        }
    )
    return SimpleNamespace(
        session=session,
        project=project,
        episode=episode,
        shot_id=shot["id"],
        storyboard=storyboard,
        generations=AIGenerationService(session, settings),
    )


def current(flow):
    return flow.storyboard.get(flow.project, flow.shot_id)["shot"]


def request(shot):
    return {
        "source": {
            "scene": "shot_video",
            "shot_id": shot["id"],
            "row_version": shot["row_version"],
            "context_hash": shot["video_context_hash"],
            "reference_media_id": shot["image"]["media_id"],
        }
    }


def output(flow, task_id, media_id=201):
    session = flow.session
    record = session.scalar(
        select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
    )
    session.add(
        MediaFile(
            id=media_id,
            project_id=flow.project,
            format_code="video/mp4",
            storage_locator=f"minio://videos/{media_id}.mp4",
            original_name="",
            duration_ms=5000,
        )
    )
    session.add(
        MediaAsset(
            id=media_id + 100,
            record_id=record.id,
            output_index=1,
            media_id=media_id,
            media_type="video",
            name="候选视频",
            row_version=1,
        )
    )
    session.commit()
    return media_id + 100


def adoption(shot):
    return {
        "target": {"type": "shot_video", "id": shot["id"]},
        "expected_media_id": shot["video"]["media_id"] if shot["video"] else None,
        "expected_row_version": shot["row_version"],
        "expected_context_hash": shot["video_context_hash"],
    }


def test_video_prompt_includes_saved_camera_and_sound_details():
    with generation_session() as session:
        flow = setup_video(session)
        shot = current(flow)
        flow.storyboard.update(
            flow.project,
            shot["id"],
            {
                "row_version": shot["row_version"],
                "details": {"camera_movement": "缓慢推进", "sound": "门铃轻响"},
            },
        )
        shot = current(flow)
        task, _ = flow.generations.create("video", request(shot), "video-details")
        detail = flow.generations.detail(task["generation_id"])
        assert detail["source_snapshot"]["shot"]["details"]["camera_movement"] == "缓慢推进"
        assert "缓慢推进" in detail["effective_prompt"]
        assert "门铃轻响" in detail["effective_prompt"]


def test_video_prompt_keeps_saved_shot_content_with_a_custom_prompt():
    with generation_session() as session:
        flow = setup_video(session)
        shot = current(flow)
        shot = flow.storyboard.update(
            flow.project,
            shot["id"],
            {
                "row_version": shot["row_version"],
                "title": "雨中回望",
                "shot_size": "close_up",
                "video_prompt": "镜头微微晃动",
            },
        )["shot"]
        task, _ = flow.generations.create("video", request(shot), "video-saved-shot")
        detail = flow.generations.detail(task["generation_id"])
        assert detail["source_snapshot"]["shot"]["title"] == "雨中回望"
        assert detail["source_snapshot"]["shot"]["shot_size"] == "close_up"
        assert "雨中回望" in detail["effective_prompt"]
        assert "close_up" in detail["effective_prompt"]
        assert "林晚先抬眼" in detail["effective_prompt"]
        assert "镜头微微晃动" in detail["effective_prompt"]


@pytest.mark.parametrize("references", [[], [101, 102]])
def test_assistant_video_freezes_explicit_references_and_allows_no_image(references):
    from short_drama.schemas.assistant.operations.media import AssistantShotVideoRequest
    from short_drama.service.assistant.operations.media import admit_media_locked
    from short_drama.service.assistant.operations.shot_video import prepare_shot_video_locked
    from short_drama.service.creation.storyboard.videos.video_admission import (
        shot_video_target_matches,
    )

    with generation_session() as session:
        flow = setup_video(session)
        if not references:
            session.delete(session.scalar(select(ShotImage)))
        else:
            session.add(
                MediaFile(
                    id=102,
                    project_id=flow.project,
                    format_code="image/png",
                    storage_locator="minio://images/asset.png",
                    original_name="",
                )
            )
        session.commit()
        shot = current(flow)
        values = AssistantShotVideoRequest.model_validate(
            {
                "target_kind": "shot_video",
                "target_id": shot["id"],
                "row_version": shot["row_version"],
                "model_config_id": "1",
                "model_version": "1",
                "prompt": "人物回头",
                "reference_media_ids": references,
            }
        )
        with session.begin():
            payload, target = prepare_shot_video_locked(
                session, 1, str(flow.project), values, int(shot["row_version"])
            )
            assert target["provider_input"]["reference_media_ids"] == references
            assert shot_video_target_matches(session, 1, target)
            assert payload["source"]["reference_media_id"] == ("101" if references else None)
            intent = SimpleNamespace(
                id=41,
                operation_id=42,
                media_kind="video",
                frozen_request=payload,
                frozen_target=target,
            )
            admitted = admit_media_locked(session, 1, intent, settings)
            record = session.scalar(
                select(AIGenerationRecord).where(
                    AIGenerationRecord.task_id == int(admitted["generation_id"])
                )
            )
            assert record.request_data["input"]["reference_media_ids"] == references
            assert record.request_data["source_snapshot"]["reference_media_ids"] == list(
                map(str, references)
            )
            assert record.request_data["source_snapshot"]["input_mode"] == (
                "omni_reference" if references else "text_to_video"
            )


def test_video_can_be_generated_and_adopted_without_a_shot_image():
    with generation_session() as session:
        flow = setup_video(session)
        session.delete(session.scalar(select(ShotImage)))
        session.commit()
        shot = current(flow)
        task, created = flow.generations.create(
            "video",
            {
                "source": {
                    "scene": "shot_video",
                    "shot_id": shot["id"],
                    "row_version": shot["row_version"],
                    "context_hash": shot["video_context_hash"],
                }
            },
            "text-only-video",
        )
        assert created
        detail = flow.generations.detail(task["generation_id"])
        assert detail["input"]["reference_media_ids"] == []
        assert "参考图" not in detail["effective_prompt"]
        assert "分镜图" not in detail["effective_prompt"]
        candidate = output(flow, task["generation_id"])
        MediaAssetService(session, settings, None).apply(candidate, adoption(current(flow)))
        assert current(flow)["video"]["media_id"] == "201"


@pytest.mark.parametrize("layout", ["single", "four", "five", "nine"])
@pytest.mark.parametrize("aspect", [None, "9:16"])
def test_video_saved_snapshot_replay_and_history(layout, aspect):
    with generation_session() as session:
        flow = setup_video(session)
        session.scalar(select(ShotImage)).layout = layout
        session.commit()
        shot = current(flow)
        body = request(shot)
        body["parameters"] = {"aspect": aspect}
        task, created = flow.generations.create("video", body, "video-generation")
        assert created
        replay, created = flow.generations.create("video", body, "video-generation")
        assert not created and replay["generation_id"] == task["generation_id"]
        detail = flow.generations.detail(task["generation_id"])
        assert detail["input"]["reference_media_ids"] == ["101"]
        assert "first_frame_media_id" not in detail["input"]
        assert detail["source_snapshot"]["reference_layout"] == layout
        assert detail["source_snapshot"]["input_mode"] == "omni_reference"
        assert detail["parameters"] == {
            "duration_ms": 5000,
            "resolution": "720p",
            "aspect": aspect,
        }
        assert "林晚先抬眼" in detail["source_snapshot"]["user_prompt"]
        assert "光源方向" in detail["source_snapshot"]["system_prompt"]
        assert "minio://" not in detail["effective_prompt"]
        assert current(flow)["video"] is None
        asset_id = output(flow, task["generation_id"])
        filters = {"source_scene": "shot_video", "source_id": shot["id"]}
        assert flow.generations.list(filters=filters)["total"] == 1
        media = MediaAssetService(session, settings, None)
        assert media.list(filters=filters)["total"] == 1
        apply_body = adoption(current(flow))
        applied = media.apply(asset_id, apply_body)
        assert media.apply(asset_id, apply_body) == applied
        adopted = current(flow)
        assert adopted["video"]["media_id"] == "201"
        assert not adopted["video"]["is_stale"]
        assert not adopted["image"]["is_stale"]
        updated = flow.storyboard.update(
            flow.project,
            shot["id"],
            {
                "row_version": adopted["row_version"],
                "video_prompt": "右手握住伞柄，固定机位。",
            },
        )["shot"]
        assert updated["video_context_hash"] != adopted["video_context_hash"]
        assert updated["video"]["media_id"] == "201"
        assert not updated["video"]["is_stale"] and not updated["image"]["is_stale"]
        assert (
            flow.generations.detail(task["generation_id"])["source_snapshot"]
            == detail["source_snapshot"]
        )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("missing", "video_reference_required"),
        ("frame", "shot_version_conflict"),
        ("version", "shot_version_conflict"),
        ("settings", "generation_settings_changed"),
    ],
)
def test_video_rejects_invalid_context_before_task_creation(change, code):
    with generation_session() as session:
        flow = setup_video(session)
        body = request(current(flow))
        image = session.scalar(select(ShotImage))
        if change == "missing":
            session.delete(image)
        if change == "frame":
            body["source"]["reference_media_id"] = "999"
        if change == "version":
            body["source"]["row_version"] = "999"
        if change == "settings":
            body["parameters"] = {"duration_ms": 6000}
        session.commit()
        with pytest.raises(WorkflowError) as caught:
            flow.generations.create("video", body, "bad-context")
        assert caught.value.code == code
        assert flow.generations.list(filters={"service_type": "video"})["total"] == 0


def test_legacy_image_context_does_not_invalidate_the_current_video_reference():
    with generation_session() as session:
        flow = setup_video(session)
        image = session.scalar(select(ShotImage))
        image.context_hash = "a" * 64
        session.commit()
        shot = current(flow)
        assert not shot["image"]["is_stale"]
        task, created = flow.generations.create("video", request(shot), "legacy-image-context")
        assert created
        detail = flow.generations.detail(task["generation_id"])
        assert detail["input"]["reference_media_ids"] == ["101"]
        assert detail["source_snapshot"]["reference_media_id"] == "101"


def test_video_duration_override_is_saved_without_invalidating_adopted_works():
    with generation_session() as session:
        flow = setup_video(session)
        shot = current(flow)
        task, _ = flow.generations.create("video", request(shot), "original-duration")
        asset_id = output(flow, task["generation_id"])
        MediaAssetService(session, settings, None).apply(asset_id, adoption(current(flow)))
        adopted = current(flow)
        updated = flow.storyboard.update(
            flow.project,
            flow.shot_id,
            {
                "row_version": adopted["row_version"],
                "video_settings": {"resolution": "720p", "duration_ms": 15000},
            },
        )["shot"]
        assert updated["duration_ms"] == 5000
        assert updated["context_hash"] == shot["context_hash"]
        assert updated["video_context_hash"] != adopted["video_context_hash"]
        assert not updated["image"]["is_stale"] and not updated["video"]["is_stale"]
        assert current(flow)["video_settings"]["duration_ms"] == 15000
        body = request(updated)
        body["parameters"] = {"resolution": "720p", "duration_ms": 15000}
        task, _ = flow.generations.create("video", body, "longer-video")
        detail = flow.generations.detail(task["generation_id"])
        assert detail["parameters"]["duration_ms"] == 15000
        assert detail["source_snapshot"]["video_duration_ms"] == 15000
        assert detail["source_snapshot"]["shot"]["duration_ms"] == 5000
        assert "本次视频时长：15 秒" in detail["effective_prompt"]
        body["parameters"]["duration_ms"] = 5000
        with pytest.raises(WorkflowError) as caught:
            flow.generations.create("video", body, "unsaved-duration")
        assert caught.value.code == "generation_settings_changed"


def test_video_adoption_blocks_concurrent_edits_even_when_stale_is_acknowledged():
    with generation_session() as session:
        flow = setup_video(session)
        shot = current(flow)
        task, _ = flow.generations.create("video", request(shot), "adoption")
        asset_id = output(flow, task["generation_id"])
        old = adoption(shot)
        flow.storyboard.update(
            flow.project,
            shot["id"],
            {"row_version": shot["row_version"], "video_prompt": "缓慢抬头"},
        )
        media = MediaAssetService(session, settings, None)
        with pytest.raises(WorkflowError, match="分镜"):
            media.apply(asset_id, {**old, "acknowledge_stale_source": True})
        with pytest.raises(WorkflowError, match="来源"):
            media.apply(asset_id, adoption(current(flow)))
        applied = media.apply(
            asset_id, {**adoption(current(flow)), "acknowledge_stale_source": True}
        )
        assert applied["media_id"] == "201"
        assert (
            media.apply(asset_id, {**adoption(current(flow)), "acknowledge_stale_source": True})
            == applied
        )
        assert not current(flow)["video"]["is_stale"]
        latest = flow.storyboard.get(flow.project, shot["id"])
        flow.storyboard.delete(
            flow.project,
            shot["id"],
            {
                "row_version": latest["shot"]["row_version"],
                "storyboard_version": latest["storyboard_version"],
            },
        )
        with pytest.raises(WorkflowError) as caught:
            media.apply(asset_id, {**adoption(current(flow)), "acknowledge_stale_source": True})
        assert caught.value.code == "shot_archived"
        assert session.scalar(select(ShotVideo)).media_id == 201


def test_video_adoption_detects_a_changed_unlinked_asset_reference():
    from short_drama.domain import Asset
    from short_drama.service.creation.assets.management.assets import AssetService
    from short_drama.service.creation.drafts.drafts import UserCreationDraftService

    with generation_session() as session:
        flow = setup_video(session)
        for identifier in (501, 502):
            session.add(
                MediaFile(
                    id=identifier,
                    project_id=flow.project,
                    format_code="image/png",
                    storage_locator=f"minio://images/{identifier}.png",
                    original_name="",
                )
            )
        session.commit()
        asset = AssetService(session).create(
            {"kind": "prop", "name": "红伞", "media_id": "501"}, project_id=flow.project
        )
        shot = current(flow)
        draft = UserCreationDraftService(session).create_for_scope(
            flow.project,
            {
                "object_id": flow.shot_id,
                "purpose": "storyboard_video",
                "model_config_id": "1",
                "video_references": [{"type": "asset", "asset_id": str(asset.id)}],
            },
        )
        body = request(shot)
        body["source"].update(draft_id=draft["id"], draft_version=draft["draft_version"])
        task, _ = flow.generations.create("video", body, "video-unlinked-asset")
        candidate = output(flow, task["generation_id"])
        saved = session.get(Asset, asset.id)
        saved.media_id = 502
        session.commit()
        with pytest.raises(WorkflowError, match="来源"):
            MediaAssetService(session, settings, None).apply(candidate, adoption(current(flow)))


def generated_video(flow, key, media_id):
    task, _ = flow.generations.create("video", request(current(flow)), key)
    asset_id = output(flow, task["generation_id"], media_id)
    session = flow.session
    saved_task = session.get(AsyncTask, int(task["generation_id"]))
    saved_task.project_id = flow.project
    saved_task.scope_user_id = None
    record = session.scalar(
        select(AIGenerationRecord).where(AIGenerationRecord.task_id == saved_task.id)
    )
    record.status = "succeeded"
    record.response_data = {
        "expected_count": 1,
        "media_manifest": [{"output_index": 1, "asset_id": str(asset_id), "saved": True}],
    }
    media = session.get(MediaFile, media_id)
    media.project_id = saved_task.project_id
    media.scope_user_id = None
    media.created_by = media.updated_by = saved_task.initiated_by
    session.commit()
    return saved_task.id


def confirm_generated_video(flow, task_id):
    with flow.session.begin():
        task = flow.session.get(AsyncTask, task_id)
        record = flow.session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        return MediaAssetService(flow.session, settings, None).confirm_generated_shot_video_locked(
            task, record
        )


def adopt_generated_video(flow, task_id):
    record = flow.session.scalar(
        select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
    )
    candidate = flow.session.scalar(select(MediaAsset).where(MediaAsset.record_id == record.id))
    flow.session.commit()
    return MediaAssetService(flow.session, settings, None).apply(
        candidate.id, adoption(current(flow))
    )


def test_completed_video_is_applied_automatically():
    with generation_session() as session:
        flow = setup_video(session)
        before = current(flow)
        task_id = generated_video(flow, "automatic-video", 201)
        receipt = confirm_generated_video(flow, task_id)
        assert receipt["status"] == "applied", receipt
        confirmed = current(flow)
        assert confirmed["video"]["media_id"] == "201"
        assert int(confirmed["row_version"]) == int(before["row_version"]) + 1
        assert confirm_generated_video(flow, task_id) == receipt
        assert session.get(MediaFile, 201).published_at is not None


def test_regenerated_video_replaces_the_current_video():
    with generation_session() as session:
        flow = setup_video(session)
        original = generated_video(flow, "original-video", 201)
        assert confirm_generated_video(flow, original)["status"] == "applied"
        assert current(flow)["video"]["media_id"] == "201"
        newer = generated_video(flow, "new-video", 202)
        assert confirm_generated_video(flow, newer)["status"] == "applied"
        assert current(flow)["video"]["media_id"] == "202"


def test_assistant_video_completion_applies_automatically():
    from short_drama.schemas.assistant.operations.media import AssistantShotVideoRequest
    from short_drama.service.assistant.operations.media_settlement import _result_refs
    from short_drama.service.assistant.operations.shot_video import (
        prepare_shot_video_locked,
        settle_shot_video_locked,
    )

    with generation_session() as session:
        flow = setup_video(session)
        original = generated_video(flow, "assistant-original", 201)
        adopt_generated_video(flow, original)
        shot = current(flow)
        values = AssistantShotVideoRequest.model_validate(
            {
                "target_kind": "shot_video",
                "target_id": shot["id"],
                "row_version": shot["row_version"],
                "model_config_id": "1",
                "model_version": "1",
                "prompt": "人物回头",
                "reference_media_ids": [101],
            }
        )
        with session.begin():
            _, target = prepare_shot_video_locked(
                session, 1, str(flow.project), values, int(shot["row_version"])
            )
        pending = generated_video(flow, "assistant-pending", 202)
        with session.begin():
            task = session.get(AsyncTask, pending)
            task.status, task.finished_at = "succeeded", utcnow()
            record = session.scalar(
                select(AIGenerationRecord).where(AIGenerationRecord.task_id == pending)
            )
            candidate = session.scalar(select(MediaAsset).where(MediaAsset.record_id == record.id))
            intent = SimpleNamespace(frozen_target=target, operation_id=42)
            result, status, error = settle_shot_video_locked(session, 1, intent, task, [candidate])
            assert status == "succeeded" and error is None
            assert result["adopted"] is True
            intent.result, intent.status = result, status
            intent.media_kind, intent.requested_count, intent.generation_task_id = (
                "video",
                1,
                pending,
            )
            assert _result_refs([intent])[0]["status"] == "adopted"
        assert current(flow)["video"]["media_id"] == "202"


@pytest.mark.parametrize("newest_first", [False, True])
def test_parallel_video_completion_keeps_the_newest_result(newest_first):
    with generation_session() as session:
        flow = setup_video(session)
        older = generated_video(flow, "older-video", 201)
        newer = generated_video(flow, "newer-video", 202)
        first, second = (newer, older) if newest_first else (older, newer)
        confirm_generated_video(flow, first)
        confirm_generated_video(flow, second)
        assert current(flow)["video"]["media_id"] == "202"


@pytest.mark.parametrize(
    "change",
    [
        "script",
        "title",
        "reference",
        "archived",
        "native",
        "quality",
        "revoked",
        "cancelled",
        "scope",
    ],
)
def test_invalidated_video_result_preserves_the_previous_confirmed_work(change):
    from short_drama.domain.identity.accounts.user import User

    with generation_session() as session:
        flow = setup_video(session)
        original = generated_video(flow, "existing-video", 201)
        confirm_generated_video(flow, original)
        adopt_generated_video(flow, original)
        pending = generated_video(flow, "delayed-video", 202)
        shot = current(flow)
        if change in {"script", "title"}:
            flow.storyboard.update(
                flow.project,
                flow.shot_id,
                {"row_version": shot["row_version"], change: "已保存的新内容"},
            )
        elif change == "archived":
            ShotScriptService(session).delete(flow.shot_id)
        else:
            if change == "reference":
                session.add(
                    MediaFile(
                        id=102,
                        format_code="image/png",
                        storage_locator="minio://images/replaced.png",
                        original_name="",
                    )
                )
                session.scalar(select(ShotImage)).media_id = 102
            if change == "native":
                record = session.scalar(
                    select(AIGenerationRecord).where(AIGenerationRecord.task_id == pending)
                )
                record.request_data = {
                    **record.request_data,
                    "source_snapshot": {
                        **record.request_data["source_snapshot"],
                        "native_speech": {"lines": [], "voices": []},
                    },
                }
            if change in {"native", "quality"}:
                session.get(MediaFile, 202).video_metadata = {
                    "native_quality": {"technical_pass": False}
                }
            if change == "revoked":
                session.get(User, 1).status = "disabled"
            if change == "cancelled":
                session.get(AsyncTask, pending).cancel_requested = 1
            if change == "scope":
                actor = session.info.pop("actor")
                session.get(MediaFile, 202).project_id = None
                session.get(MediaFile, 202).scope_user_id = 1
            session.commit()
            if change == "scope":
                session.info["actor"] = actor
        receipt = confirm_generated_video(flow, pending)
        assert receipt["status"] == "skipped"
        assert current(flow)["video"]["media_id"] == "201"
        assert session.get(MediaFile, 202).published_at is None


def test_worker_completion_exposes_a_safe_warning_when_the_source_has_changed():
    with generation_session() as session:
        flow = setup_video(session)
        original = generated_video(flow, "original-video", 201)
        confirm_generated_video(flow, original)
        adopt_generated_video(flow, original)
        pending = generated_video(flow, "delayed-video", 202)
        shot = current(flow)
        flow.storyboard.update(
            flow.project,
            flow.shot_id,
            {"row_version": shot["row_version"], "title": "生成期间已编辑"},
        )
        task = session.get(AsyncTask, pending)
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == pending)
        )
        task.status, task.next_action = "running", "save"
        task.lock_token = "worker-completion-token"
        task.locked_until = utcnow() + timedelta(seconds=60)
        version = task.message_version
        session.commit()
        factory = sessionmaker(
            bind=session.get_bind(),
            expire_on_commit=False,
            autoflush=False,
            info={"actor": fixture_actor()},
        )
        executor = GenerationExecutionService(factory, settings, None, None)
        executor._save(task, record, version, "worker-completion-token")
        session.expire_all()
        detail = flow.generations.detail(pending)
        assert detail["status"] == "succeeded"
        assert detail["error"] == {
            "code": "shot_video_auto_apply_skipped",
            "message": (
                "视频已生成并保存，未替换当前确定视频。"
                "分镜、提示词、参考图或声音已变化，请核对生成记录。"
            ),
        }
        assert not detail["can_retry"] and not detail["can_resume"]
        assert current(flow)["video"]["media_id"] == "201"
        assert session.get(MediaFile, 202).published_at is None


@pytest.mark.parametrize("state", ["failed", "incomplete", "canvas", "agent"])
def test_unsuccessful_or_other_workflows_do_not_replace_the_current_video(state):
    with generation_session() as session:
        flow = setup_video(session)
        original = generated_video(flow, "original-video", 201)
        confirm_generated_video(flow, original)
        adopt_generated_video(flow, original)
        task_id = generated_video(flow, "ignored-video", 202)
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        if state == "failed":
            record.status = "failed"
        elif state == "incomplete":
            record.response_data = {"expected_count": 1, "media_manifest": []}
        elif state == "canvas":
            record.request_data = {**record.request_data, "canvas_request": {}}
        elif state == "agent":
            record.config_snapshot = {**record.config_snapshot, "agent_managed": True}
        session.commit()
        assert confirm_generated_video(flow, task_id) is None
        assert current(flow)["video"]["media_id"] == "201"
        assert session.get(MediaFile, 202).published_at is None
