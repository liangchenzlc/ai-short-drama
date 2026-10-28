"""Disposable MySQL, real FFmpeg and durable generation workers; synthetic speech provider."""

import base64
import copy
import subprocess
from io import BytesIO
from uuid import uuid4

import pytest

from short_drama.ai.types import GenerationResult
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.domain import MediaFile, ShotVideo
from short_drama.service.ai_config.management.models import AIModelConfigService
from short_drama.service.creation.assembly.document.assembly import ProjectAssemblyService
from short_drama.service.creation.assembly.rendering.renderer import executable
from short_drama.service.creation.sound.tracks.sound import ProjectSoundService
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.tasks.rendering.render import RenderExecutor
from tests.integration.standard.assets.test_asset_image_generation import drain
from tests.integration.standard.assets.test_asset_image_generation import flow as flow

pytestmark = pytest.mark.integration


def setup(flow, tmp_path):
    flow.settings.audio_production_enabled = True
    flow.settings.render_scratch_root = str(tmp_path / "jobs")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=30:d=2",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    data = source.read_bytes()
    flow.storage.put(
        flow.settings.minio_video_bucket, "sound-test.mp4", BytesIO(data), len(data), "video/mp4"
    )
    shot = ProjectStoryboardService(flow.session).create(
        flow.project.id,
        {"storyboard_version": "1", "script": "甲：你好，世界。", "duration_ms": 2000},
        "sound-shot",
    )["shot"]
    flow.session.add(
        MediaFile(
            id=301,
            project_id=flow.project.id,
            format_code="video/mp4",
            storage_locator=f"minio://{flow.settings.minio_video_bucket}/sound-test.mp4",
            duration_ms=2000,
            video_metadata={
                "duration_ms": 2000,
                "width": 320,
                "height": 180,
                "has_audio": False,
                "preview_locator": f"minio://{flow.settings.minio_video_bucket}/sound-test.mp4",
                "preview_version": 2,
            },
        )
    )
    flow.session.flush()
    flow.session.add(
        ShotVideo(
            id=401,
            project_id=flow.project.id,
            shot_id=int(shot["id"]),
            media_id=301,
            duration=2000,
            resolution="720p",
            context_hash=shot["video_context_hash"],
        )
    )
    flow.session.commit()
    assembly = ProjectAssemblyService(flow.session, flow.settings, flow.storage)
    assembly.initialize(flow.project.id)
    audio_config = AIModelConfigService(flow.session).create(
        {
            "service_type": "audio",
            "name": "speech-fixture",
            "model_key": "speech",
            "provider": "fixture",
            "base_url": "https://speech.example/v1",
        }
    )
    speech = tmp_path / "speech.wav"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=600:sample_rate=48000:duration=0.5",
            str(speech),
        ],
        check=True,
        timeout=30,
    )

    def submit(snapshot, request, *args, **kwargs):
        flow.provider.calls.append(copy.deepcopy(request))
        return GenerationResult(
            status="succeeded",
            adapter="openai_speech.v1",
            outputs=[{"base64": base64.b64encode(speech.read_bytes()).decode()}],
        )

    flow.provider.submit = submit
    sound = ProjectSoundService(flow.session, flow.settings, flow.storage)
    return assembly, sound, audio_config, speech


def save(flow, sound, state, document, reviewed=False):
    body = {
        "row_version": state["row_version"],
        "timeline_hash": state["timeline_hash"],
        "document": document,
        "reviewed": reviewed,
        "request_id": str(uuid4()),
    }
    result = sound.save(flow.project.id, body)
    assert sound.save(flow.project.id, body) == result  # receipt lost after commit
    return result


def test_adopted_speech_enters_multitrack_dialogue_with_a_stable_link(flow, tmp_path):
    _, sound, config, _ = setup(flow, tmp_path)
    state = sound.get(flow.project.id)
    doc = copy.deepcopy(state["document"])
    doc["schema_version"] = 2
    doc["dialogue"] = [
        {
            "id": "line1",
            "character": "甲",
            "text": "你好世界",
            "voice": "voice1",
            "config_id": str(config.id),
            "start_ms": 250,
        }
    ]
    state = save(flow, sound, state, doc)
    body = {
        "config_id": str(config.id),
        "source": {
            "scene": "dialogue_audio",
            "project_id": str(flow.project.id),
            "line_id": "line1",
            "row_version": state["row_version"],
        },
    }
    task, _ = flow.generations.create("audio", body, "multitrack-speech-once")
    assert drain(flow, task["generation_id"]) == "succeeded"
    candidate = sound.candidates(flow.project.id, "line1")[0]["outputs"][0]
    assert sound.get(flow.project.id)["document"]["audio_clips"] == []
    adoption = {
        "row_version": state["row_version"],
        "line_id": "line1",
        "media_id": candidate["media_id"],
    }
    adopted = sound.adopt(flow.project.id, adoption)
    audio = adopted["document"]["audio_clips"]
    assert len(audio) == 1 and audio[0]["kind"] == "dialogue"
    assert audio[0]["dialogue_id"] == "line1" and audio[0]["anchor_clip_id"]
    assert audio[0]["start_ms"] == 250 and audio[0]["duration_ms"] == 500
    assert sound.adopt(flow.project.id, adoption)["document"]["audio_clips"] == audio


def test_speech_candidates_staleness_music_export_and_video_review(flow, tmp_path):
    assembly, sound, config, speech = setup(flow, tmp_path)
    state = sound.get(flow.project.id)
    doc = state["document"]
    doc["dialogue"] = [
        {
            "id": "line1",
            "character": "甲",
            "text": "你好，世界",
            "voice": "voice1",
            "config_id": str(config.id),
            "start_ms": 250,
            "media_id": None,
            "adopted_hash": None,
        }
    ]
    state = save(flow, sound, state, doc)
    body = {
        "config_id": str(config.id),
        "source": {
            "scene": "dialogue_audio",
            "project_id": str(flow.project.id),
            "row_version": str(state["row_version"]),
            "line_id": "line1",
        },
    }
    task, _ = flow.generations.create("audio", body, "speech-once")
    with pytest.raises(WorkflowError) as error:
        flow.generations.create("audio", body, "speech-duplicate")
    assert error.value.code == "audio_task_active"
    assert drain(flow, task["generation_id"]) == "succeeded"
    assert len(flow.provider.calls) == 1
    again, created = flow.generations.create("audio", body, "speech-once")
    assert not created and again["generation_id"] == task["generation_id"]
    candidates = sound.candidates(flow.project.id, "line1")
    assert len(candidates) == 1 and len(candidates[0]["outputs"]) == 1
    assert sound.get(flow.project.id)["document"]["dialogue"][0]["media_id"] is None
    mid = candidates[0]["outputs"][0]["media_id"]
    adoption = {"row_version": state["row_version"], "line_id": "line1", "media_id": mid}
    state = sound.adopt(
        flow.project.id,
        adoption,
    )
    assert sound.adopt(flow.project.id, adoption) == state
    body["source"]["row_version"] = str(state["row_version"])
    cancelled, _ = flow.generations.create("audio", body, "speech-cancelled")
    cancelled_state = flow.generations.cancel(cancelled["generation_id"])
    assert cancelled_state["status"] == "cancelled" and not cancelled_state["can_retry"]
    with pytest.raises(WorkflowError) as retry_error:
        flow.generations.retry(cancelled["generation_id"], {}, "unsafe-speech-retry")
    assert retry_error.value.code == "audio_retry_required"
    modified = copy.deepcopy(state["document"])
    modified["dialogue"][0]["text"] = "内容已修改"
    stale = save(flow, sound, state, modified)
    assert stale["stale_lines"] == ["line1"]
    with pytest.raises(Conflict):
        sound.adopt(
            flow.project.id,
            {"row_version": stale["row_version"], "line_id": "line1", "media_id": mid},
        )
    restored = save(flow, sound, stale, state["document"])
    uploaded = sound.upload(flow.project.id, BytesIO(speech.read_bytes()), "配乐.wav")
    assert uploaded["media_id"] != uploaded["proxy_media_id"]
    doc = copy.deepcopy(restored["document"])
    doc["music"] = {
        "media_id": uploaded["media_id"],
        "trim_out_ms": 500,
        "loop": True,
        "volume": 0.1,
    }
    doc["subtitles"] = [{"start_ms": 250, "end_ms": 750, "text": "你好，世界"}]
    reviewed = save(flow, sound, restored, doc, True)
    video = assembly.get(flow.project.id)
    request = {
        "row_version": video["assembly"]["row_version"],
        "source_hash": video["source_hash"],
        "acknowledge_stale_source": True,
    }
    job = assembly.export(flow.project.id, request, "sound-export")
    RenderExecutor(flow.factory, flow.settings, flow.storage).execute(job["id"], 1)
    result = assembly.job_action(flow.project.id, job["id"], "get")
    assert result["status"] == "succeeded", result
    assert abs(result["duration_ms"] - 2000) <= 34
    # A new probe can change source timing without a sound edit.
    source = flow.session.get(MediaFile, 301)
    source.video_metadata = {**source.video_metadata, "duration_ms": 1000}
    source.duration_ms = 1000
    flow.session.commit()
    assert sound.get(flow.project.id)["needs_review"]
    assert reviewed["document"]["dialogue"][0]["start_ms"] == 250
    video = assembly.get(flow.project.id)
    with pytest.raises(WorkflowError) as error:
        assembly.export(
            flow.project.id,
            {
                "row_version": video["assembly"]["row_version"],
                "source_hash": video["source_hash"],
                "acknowledge_stale_source": True,
            },
            "review-required",
        )
    assert error.value.code == "sound_review_required"


def test_audio_migration_can_be_repeated(migration_mysql_engine):
    from sqlalchemy import inspect

    from scripts.sound_ddl import apply

    apply(migration_mysql_engine)
    apply(migration_mysql_engine)
    assert {"project_sounds", "project_voice_defaults", "sound_media_references"} <= set(
        inspect(migration_mysql_engine).get_table_names()
    )


def test_extracted_dialogue_remains_a_review_candidate(flow, tmp_path):
    import json

    _, sound, _, _ = setup(flow, tmp_path)
    config = AIModelConfigService(flow.session).create(
        {
            "service_type": "text",
            "name": "text-fixture",
            "provider": "fixture",
            "model_key": "text",
            "base_url": "https://text.example/v1",
        }
    )
    raw = json.dumps(
        [{"character": "甲", "text": "你好，世界。", "start_ms": 500}], ensure_ascii=False
    )
    flow.provider.submit = lambda *a, **k: GenerationResult(
        status="succeeded", adapter="openai_chat.v1", text=raw
    )
    task, _ = flow.generations.create(
        "text",
        {
            "config_id": str(config.id),
            "source": {
                "scene": "dialogue_extract",
                "project_id": str(flow.project.id),
            },
            "parameters": {},
        },
        "extract-dialogue",
    )
    assert drain(flow, task["generation_id"]) == "succeeded"
    candidate = sound.extraction(flow.project.id, task["generation_id"])
    assert candidate["raw_text"] == raw
    assert candidate["dialogue"][0]["text"] == "你好，世界。"
    assert candidate["dialogue"][0]["media_id"] is None
    assert sound.get(flow.project.id)["document"]["dialogue"] == []


def test_sound_http_scope_schema_conflict_and_extraction_review(flow, tmp_path):
    import asyncio

    import httpx

    from short_drama.api.dependencies.session import get_session
    from short_drama.main import create_app

    _, _, _, _ = setup(flow, tmp_path)
    app = create_app(flow.settings)
    app.state.settings, app.state.storage = flow.settings, flow.storage
    app.dependency_overrides[get_session] = lambda: flow.session
    root = f"/api/v1/projects/{flow.project.id}/sound"

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get(root + "/capabilities")).json() == {"enabled": True}
            state = (await client.get(root)).json()
            body = {
                "row_version": 0,
                "timeline_hash": state["timeline_hash"],
                "request_id": "http-save",
                "document": state["document"],
                "reviewed": True,
            }
            saved = await client.put(root, json=body)
            assert saved.status_code == 200, saved.text
            assert (await client.put(root, json=body)).json() == saved.json()
            assert (
                await client.put(root, json={**body, "request_id": "old-version"})
            ).status_code == 409
            invalid = {
                **body,
                "document": {
                    **body["document"],
                    "subtitles": [{"start_ms": 500, "end_ms": 100, "text": "bad"}],
                },
            }
            assert (await client.put(root, json=invalid)).status_code == 422
            imported = await client.post(
                root + "/subtitles/import",
                json={"text": "1\n00:00:00,000 --> 00:00:01,000\n你好\n"},
            )
            assert imported.status_code == 200 and imported.json()["subtitles"][0]["text"] == "你好"
            assert (
                await client.post(
                    root + "/music", files={"file": ("fake.mp3", b"not audio", "audio/mpeg")}
                )
            ).status_code == 422
            assert (
                await client.get(root.replace(str(flow.project.id), "99999", 1))
            ).status_code == 404

    asyncio.run(scenario())
