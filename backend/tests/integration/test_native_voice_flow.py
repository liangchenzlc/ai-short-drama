"""Real disposable MySQL and decoded local media; no live model or object store calls."""

import base64
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_asset_image_generation import create_asset, drain
from test_asset_image_generation import flow as flow
from test_episode_sound import setup as setup_sound

from short_drama.ai.types import GenerationResult
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import AIGenerationRecord, MediaFile
from short_drama.domain.native_voice import CharacterVoice
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.generation_batch_service import GenerationBatchService, dispatch_batches
from short_drama.service.native_voice_service import NativeVoiceService
from short_drama.service.shot_asset_service import ShotAssetService
from short_drama.service.shot_image_service import ShotImageService

pytestmark = pytest.mark.integration


def setup(flow, tmp_path, monkeypatch):
    import subprocess

    from short_drama.service.video_render import executable

    monkeypatch.setenv("NATIVE_VIDEO_ENABLED", "true")
    flow.settings.native_video_enabled = flow.settings.audio_production_enabled = True
    flow.settings.encryption_key = SecretStr(base64.b64encode(b"n" * 32).decode())
    service = NativeVoiceService(flow.session, flow.settings, flow.storage)
    service.set_mode(flow.project.id, {"row_version": 0, "mode": "native"})
    config = AIModelConfigService(flow.session).create(
        {
            "service_type": "audio",
            "name": "voice design fixture",
            "model_key": "cosyvoice-v3.5-flash",
            "provider": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/api/v1",
        }
    )
    audio = tmp_path / "reference.wav"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=500:sample_rate=24000:duration=3.2",
            str(audio),
        ],
        check=True,
        timeout=30,
    )

    def submit(snapshot, request, *args, **kwargs):
        flow.provider.calls.append(deepcopy(request))
        return GenerationResult(
            status="succeeded",
            adapter="dashscope_voice_design.v1",
            provider_task_id="fixture",
            voice={"voice_id": "voice-fixture", "target_model": "cosyvoice-v3.5-flash"},
            outputs=[
                {"base64": base64.b64encode(audio.read_bytes()).decode(), "media_type": "audio"}
            ],
        )

    flow.provider.submit = submit
    return service, config


def design(flow, service, config, asset, key=None):
    body = {
        "config_id": str(config.id),
        "source": {
            "scene": "character_voice_design",
            "project_id": str(flow.project.id),
            "asset_id": str(asset.id),
            "voice_prompt": "沉稳自然的男声",
            "preview_text": "清晨的风吹过窗前，今天又是新的开始。",
        },
    }
    task, _ = flow.generations.create("audio", body, key or str(uuid4()))
    return task, body


def test_voice_design_save_failure_recovers_same_sample_without_another_post(
    flow, tmp_path, monkeypatch
):
    svc, config = setup(flow, tmp_path, monkeypatch)
    asset = create_asset(flow, "character")
    task, body = design(flow, svc, config, asset, "voice-once")
    real_put = flow.storage.put

    def fail(*args, **kwargs):
        raise RuntimeError("injected object-store outage")

    flow.storage.put = fail
    from datetime import timedelta

    from short_drama.domain import AsyncTask
    from short_drama.service.base import utcnow

    flow.executor.execute(task["generation_id"], 1)
    flow.executor.execute(task["generation_id"], 2)
    with flow.factory.begin() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(
                AIGenerationRecord.task_id == int(task["generation_id"])
            )
        )
        record.response_data = {
            **record.response_data,
            "archive_started_at": (utcnow() - timedelta(days=2)).isoformat(),
        }
        session.get(AsyncTask, int(task["generation_id"])).next_run_at = utcnow()
    assert drain(flow, task["generation_id"]) == "failed"
    candidates = svc.voices(flow.project.id, asset.id)
    assert candidates["candidates"][0]["can_resume"]
    with flow.factory() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(
                AIGenerationRecord.task_id == int(task["generation_id"])
            )
        )
        assert record.response_data["media_manifest"][0]["inline_cipher"]
    again, created = flow.generations.create("audio", body, "voice-once")
    assert not created and again["generation_id"] == task["generation_id"]
    with pytest.raises(WorkflowError):
        design(flow, svc, config, asset)
    flow.storage.put = real_put
    flow.generations.resume(task["generation_id"])
    assert drain(flow, task["generation_id"]) == "succeeded"
    assert len(flow.provider.calls) == 1
    state = svc.voices(flow.project.id, asset.id)
    candidate = state["candidates"][0]
    assert candidate["adoptable"] and state["record_id"] is None
    adoption = {"row_version": 0, "record_id": candidate["record_id"]}
    adopted = svc.adopt(flow.project.id, asset.id, adoption)
    assert svc.adopt(flow.project.id, asset.id, adoption) == adopted
    with pytest.raises(NotFound):
        svc.voices(99999, asset.id)


def test_two_speakers_frozen_video_inputs_voice_change_pauses_waiting_batch(
    flow, tmp_path, monkeypatch
):
    svc, config = setup(flow, tmp_path, monkeypatch)
    assets = [create_asset(flow, "character") for _ in range(2)]
    for asset in assets:
        task, _ = design(flow, svc, config, asset)
        assert drain(flow, task["generation_id"]) == "succeeded"
        record = svc.voices(flow.project.id, asset.id)["candidates"][0]["record_id"]
        svc.adopt(flow.project.id, asset.id, {"row_version": 0, "record_id": record})
    storyboard = EpisodeStoryboardService(flow.session)
    shot = storyboard.create(
        flow.project.id,
        flow.episode.id,
        {"storyboard_version": "1", "script": "两人轮流交谈", "duration_ms": 4000},
        "native-shot",
    )["shot"]
    from short_drama.service.episode_asset_service import EpisodeAssetService

    for index, asset in enumerate(assets, 1):
        EpisodeAssetService(flow.session).create(
            {"episode_id": flow.episode.id, "asset_id": asset.id, "position": index}
        )
        ShotAssetService(flow.session).create(
            {"episode_id": flow.episode.id, "shot_id": shot["id"], "asset_id": asset.id}
        )
    doc = {
        "reviewed": True,
        "lines": [
            {"character_id": str(a.id), "text": f"第{i + 1}句", "speech": "onscreen"}
            for i, a in enumerate(assets)
        ],
    }
    body = {"row_version": 0, "request_id": "dialogue-once", "document": doc}
    saved = svc.save_dialogue(flow.project.id, flow.episode.id, shot["id"], body)
    assert svc.save_dialogue(flow.project.id, flow.episode.id, shot["id"], body) == saved
    with pytest.raises(Conflict):
        svc.save_dialogue(
            flow.project.id, flow.episode.id, shot["id"], {**body, "request_id": "stale"}
        )
    shot = storyboard.get(flow.project.id, flow.episode.id, shot["id"])["shot"]
    flow.session.add(
        MediaFile(
            id=303,
            format_code="image/png",
            storage_locator=f"minio://{flow.settings.minio_image_bucket}/reference.png",
        )
    )
    flow.session.commit()
    ShotImageService(flow.session).create(
        {
            "episode_id": flow.episode.id,
            "shot_id": shot["id"],
            "media_id": "303",
            "layout": "single",
            "aspect": "16:9",
            "resolution": "2K",
            "context_hash": shot["context_hash"],
        }
    )
    video_config = AIModelConfigService(flow.session).create(
        {
            "service_type": "video",
            "name": "native video fixture",
            "model_key": "seedance-2.0-mini",
            "provider": "modelhub",
            "base_url": "https://api.modelhub.cc",
        }
    )
    flow.settings.generation_batches_enabled = True
    batches = GenerationBatchService(flow.session, flow.settings)
    request = {
        "scene": "shot_video",
        "config_id": str(video_config.id),
        "scope": {
            "library": "episode",
            "project_id": str(flow.project.id),
            "episode_id": str(flow.episode.id),
        },
        "source_ids": [shot["id"]],
    }
    preview = batches.preflight(request)
    assert preview["task_count"] == 1, preview
    batch, _ = batches.create(
        {**request, "preflight_hash": preview["preflight_hash"], "accepted_ids": [shot["id"]]},
        "native-batch",
    )
    item = batches.detail(batch["id"])["items"][0]
    with flow.factory() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(item["task_id"]))
        )
        frozen = record.request_data
        assert frozen["parameters"]["generate_audio"] is True
        assert len(frozen["input"]["audio_reference_media_ids"]) == 2
        assert [
            v["character_id"] for v in frozen["source_snapshot"]["native_speech"]["voices"]
        ] == [str(a.id) for a in assets]
    with flow.factory.begin() as session:
        session.get(CharacterVoice, (flow.project.id, assets[0].id)).row_version += 1
    assert dispatch_batches(flow.factory, flow.settings) == 0
    assert batches.detail(batch["id"])["status"] == "paused"
    assert len(flow.provider.calls) == 2  # character samples only; no per-shot TTS
    # Both URL and inline video results retain the same audio gate through archival.
    import subprocess

    from short_drama.domain import MediaAsset
    from short_drama.service.video_render import executable

    for audible in (False, True):
        path = tmp_path / f"native-{audible}.mp4"
        audio = "sine=frequency=700:duration=4" if audible else "anullsrc=r=48000:cl=stereo"
        subprocess.run(
            [
                executable("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=s=160x90:r=30:d=4",
                "-f",
                "lavfi",
                "-i",
                audio,
                "-t",
                "4",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(path),
            ],
            check=True,
            timeout=30,
        )
        data = path.read_bytes()

        def video_submit(snapshot, request, *args, data=data, audible=audible, **kwargs):
            flow.provider.calls.append(deepcopy(request))
            output = (
                {"base64": base64.b64encode(data).decode()}
                if audible
                else {"url": "https://media.invalid/video.mp4"}
            )
            return GenerationResult(
                status="succeeded", adapter="modelhub_video.v1", outputs=[output]
            )

        flow.provider.submit = video_submit
        flow.provider.download_media = lambda *args, data=data: (data, "video/mp4")
        flow.session.expire_all()
        current = storyboard.get(flow.project.id, flow.episode.id, shot["id"])["shot"]
        task, _ = flow.generations.create(
            "video",
            {
                "config_id": str(video_config.id),
                "source": {
                    "scene": "shot_video",
                    "shot_id": shot["id"],
                    "row_version": current["row_version"],
                    "context_hash": current["video_context_hash"],
                    "reference_media_id": "303",
                },
            },
            str(uuid4()),
        )
        assert drain(flow, task["generation_id"]) == "succeeded"
        with flow.factory() as session:
            candidate = session.scalar(
                select(MediaAsset)
                .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
                .where(AIGenerationRecord.task_id == int(task["generation_id"]))
            )
            media = session.get(MediaFile, candidate.media_id)
            assert media.video_metadata["native_quality"]["technical_pass"] is audible
            asset_id = candidate.id
        body = {
            "target": {"type": "shot_video", "id": shot["id"]},
            "expected_media_id": None,
            "expected_row_version": current["row_version"],
            "expected_context_hash": current["video_context_hash"],
        }
        if audible:
            assert flow.media.apply(asset_id, body)["media_id"] == str(candidate.media_id)
        else:
            with pytest.raises(WorkflowError) as invalid:
                flow.media.apply(asset_id, body)
            assert invalid.value.code == "native_audio_invalid"

    from short_drama.service.episode_assembly_service import EpisodeAssemblyService
    from short_drama.service.episode_sound_service import EpisodeSoundService

    assembly = EpisodeAssemblyService(flow.session, flow.settings, flow.storage)
    assembly.initialize(flow.project.id, flow.episode.id)
    sound = EpisodeSoundService(flow.session, flow.settings, flow.storage)
    svc.save_dialogue(
        flow.project.id,
        flow.episode.id,
        shot["id"],
        {
            "row_version": 1,
            "request_id": "changed-after-adoption",
            "document": {
                "reviewed": False,
                "lines": [{"character_id": str(assets[0].id), "text": "后来改写的台词"}],
            },
        },
    )
    draft = sound.native_subtitles(flow.project.id, flow.episode.id)
    assert [s["text"] for s in draft["subtitles"]] == ["第1句", "第2句"]
    assert draft["subtitles"][-1]["end_ms"] == 4000 and draft["reviewed"] is False
    original_scope = sound._scope

    def split_scope(*args, for_update=True):
        episode, assembly, document, video = original_scope(*args, for_update=for_update)
        clip = {**video["clips"][0], "trim_in_ms": 0, "trim_out_ms": 33}
        return episode, assembly, document, {**video, "clips": [clip] * 3}

    monkeypatch.setattr(sound, "_scope", split_scope)
    assert (
        sound.native_subtitles(flow.project.id, flow.episode.id)["subtitles"][-1]["end_ms"] == 100
    )


def test_mode_switch_preserves_legacy_dialogue_and_blocks_post_dubbing(flow, tmp_path, monkeypatch):
    _, sound, _, _ = setup_sound(flow, tmp_path)
    monkeypatch.setenv("NATIVE_VIDEO_ENABLED", "true")
    flow.settings.native_video_enabled = True
    svc = NativeVoiceService(flow.session, flow.settings, flow.storage)
    state = sound.get(flow.project.id, flow.episode.id)
    doc = state["document"]
    doc["dialogue"] = [
        {"id": "legacy", "text": "原配音", "voice": "v", "character": "甲", "start_ms": 0}
    ]
    sound.save(
        flow.project.id,
        flow.episode.id,
        {
            "row_version": state["row_version"],
            "timeline_hash": state["timeline_hash"],
            "document": doc,
            "reviewed": False,
            "request_id": "legacy",
        },
    )
    svc.set_mode(flow.project.id, {"row_version": 0, "mode": "native"})
    native = sound.get(flow.project.id, flow.episode.id)
    assert native["mode"] == "native" and native["document"]["dialogue"] == []
    with pytest.raises(Conflict):
        sound.adopt(
            flow.project.id,
            flow.episode.id,
            {"row_version": native["row_version"], "line_id": "legacy", "media_id": "301"},
        )
    sound.save(
        flow.project.id,
        flow.episode.id,
        {
            "row_version": native["row_version"],
            "timeline_hash": native["timeline_hash"],
            "document": native["document"],
            "reviewed": True,
            "request_id": "native",
        },
    )
    svc.set_mode(flow.project.id, {"row_version": 1, "mode": "legacy"})
    assert (
        sound.get(flow.project.id, flow.episode.id)["document"]["dialogue"][0]["text"] == "原配音"
    )


def test_native_voice_migration_repeatable(migration_mysql_engine):
    from sqlalchemy import inspect

    from scripts.native_voice_ddl import apply

    apply(migration_mysql_engine)
    apply(migration_mysql_engine)
    assert {"character_voices", "project_sound_modes", "shot_dialogues"} <= set(
        inspect(migration_mysql_engine).get_table_names()
    )


def test_native_http_capability_scope_schema_and_creation_replay(flow, tmp_path, monkeypatch):
    import asyncio

    import httpx

    from short_drama.api.dependencies import get_session
    from short_drama.main import create_app

    _, config = setup(flow, tmp_path, monkeypatch)
    asset = create_asset(flow, "character")
    app = create_app(flow.settings)
    app.state.settings, app.state.storage = flow.settings, flow.storage
    app.dependency_overrides[get_session] = lambda: flow.session
    root = f"/api/v1/projects/{flow.project.id}"
    body = {
        "config_id": str(config.id),
        "source": {
            "scene": "character_voice_design",
            "project_id": str(flow.project.id),
            "asset_id": str(asset.id),
            "voice_prompt": "自然青年男声",
            "preview_text": "清晨的风吹过窗前，今天又是新的开始。",
        },
    }

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            caps = await client.get("/api/v1/native-voice/capabilities")
            assert caps.json()["enabled"] and caps.json()["max_speakers"] == 2
            assert (await client.get(root + "/sound-mode")).json()["mode"] == "native"
            assert (await client.get(root + f"/characters/{asset.id}/voice")).status_code == 200
            assert (await client.get(root + "/characters/99999/voice")).status_code == 404
            headers = {"Idempotency-Key": "http-design-once"}
            first = await client.post("/api/v1/ai/generations/audio", json=body, headers=headers)
            assert first.status_code in (200, 202), first.text
            repeat = await client.post("/api/v1/ai/generations/audio", json=body, headers=headers)
            assert first.json()["generation_id"] == repeat.json()["generation_id"]
            invalid = {**body, "source": {**body["source"], "audio_url": "https://sample.invalid"}}
            assert (
                await client.post("/api/v1/ai/generations/audio", json=invalid, headers=headers)
            ).status_code == 422
            flow.settings.native_video_enabled = False
            assert not (await client.get("/api/v1/native-voice/capabilities")).json()["enabled"]
            assert (await client.get(root + "/sound-mode")).status_code == 404

    asyncio.run(scenario())
    assert flow.provider.calls == []
