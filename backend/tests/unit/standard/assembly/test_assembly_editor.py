"""Atomic editor contracts and immutable recognition inputs, without paid providers."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import MediaFile, ProjectRenderJob
from short_drama.schemas.creation.sound.tracks.sound import SoundDocument
from short_drama.service.creation.assembly.editing.editor import (
    AssemblyEditorService,
    voice_context,
)
from short_drama.service.creation.assembly.materials.media import AssemblyMediaService
from short_drama.service.creation.writing.script.publication import publish
from tests.support.generation_fixtures import config, generation_session
from tests.unit.standard.assembly.test_project_assembly import flow


def setup(session):
    f, old = flow(session)
    old.initialize(f.project)
    media = session.get(MediaFile, 201)
    media.project_id = int(f.project)
    media.scope_user_id = None
    publish(media)
    settings = Settings(auth_enabled=False)
    for item in session.scalars(select(MediaFile)):
        bucket = (
            settings.minio_video_bucket
            if item.format_code.startswith("video/")
            else settings.minio_image_bucket
        )
        item.storage_locator = f"minio://{bucket}/fixture-{item.id}"
    session.commit()
    storage = SimpleNamespace(
        presigned_get=lambda bucket, name, expiry: f"http://fixture/{bucket}/{name}"
    )
    svc = AssemblyEditorService(session, settings, storage)
    return f, svc, svc.get_editor(f.project)


def payload(state):
    return {
        "row_version": state["assembly"]["row_version"],
        "sound_row_version": state["sound"]["row_version"],
        "resolution": state["assembly"]["resolution"],
        "aspect": state["assembly"]["aspect"],
        "request_id": str(uuid4()),
        "clips": [
            {
                k: c[k]
                for k in (
                    "id",
                    "media_id",
                    "source_type",
                    "included",
                    "muted",
                    "trim_in_ms",
                    "trim_out_ms",
                    "display_duration_ms",
                )
            }
            for c in state["clips"]
        ],
        "sound": SoundDocument(schema_version=2).model_dump(mode="json"),
    }


def test_atomic_editor_persists_manual_output_choice_across_sync():
    with generation_session() as session:
        f, svc, initial = setup(session)
        body = payload(initial)
        body["aspect"] = "16:9"
        saved = svc.save_editor(f.project, body)
        synced = svc.sync(
            f.project,
            {
                "row_version": saved["assembly"]["row_version"],
                "source_hash": saved["source_hash"],
            },
        )
        assert synced["assembly"]["aspect"] == "16:9"
        assert synced["assembly"]["output_width"] == 1280


def test_editor_saves_both_versions_atomically_and_replays_exact_request():
    with generation_session() as session:
        f, svc, initial = setup(session)
        body = payload(initial)
        body["clips"][0]["trim_in_ms"] = 200
        body["sound"]["subtitles"] = [
            {"id": "caption", "start_ms": 0, "end_ms": 1000, "text": "你好"}
        ]
        saved = svc.save_editor(f.project, body)
        assert saved["sound"]["row_version"] == "1"
        assert saved["clips"][0]["trim_in_ms"] == 200
        assert svc.save_editor(f.project, body)["assembly"] == saved["assembly"]
        reused = deepcopy(body)
        reused["resolution"] = "1080p"
        with pytest.raises(Conflict):
            svc.save_editor(f.project, reused)
        stale = payload(saved)
        stale["sound_row_version"] = "0"
        stale["clips"][0]["trim_in_ms"] = 500
        with pytest.raises(Conflict):
            svc.save_editor(f.project, stale)
        assert svc.get_editor(f.project)["clips"][0]["trim_in_ms"] == 200


def test_image_insertion_transform_and_invalid_source_roll_back():
    with generation_session() as session:
        f, svc, state = setup(session)
        image = MediaFile(
            id=202,
            project_id=int(f.project),
            format_code="image/png",
            original_name="构图.png",
            width=300,
            height=500,
            storage_locator=f"minio://{svc.settings.minio_image_bucket}/test.png",
        )
        publish(image)
        session.add(image)
        session.commit()
        state = svc.get_editor(f.project)
        assert any(m["media_id"] == "202" for m in state["materials"])
        body = payload(state)
        body["clips"].append(
            {
                "id": str(uuid4()),
                "media_id": "202",
                "source_type": "image",
                "display_duration_ms": 3000,
                "included": True,
                "muted": True,
                "trim_in_ms": 0,
                "trim_out_ms": None,
                "transform": {"fit": "cover", "scale": 1.5, "x": 0.3, "y": 0.7},
            }
        )
        saved = svc.save_editor(f.project, body)
        assert saved["clips"][-1]["shot_id"] is None
        assert saved["clips"][-1]["duration_ms"] == 3000
        assert saved["clips"][-1]["transform"]["scale"] == 1.5
        invalid = payload(saved)
        invalid["clips"][-1]["source_type"] = "video"
        invalid["clips"][-1]["display_duration_ms"] = None
        with pytest.raises(WorkflowError, match="展示时长"):
            svc.save_editor(f.project, invalid)
        assert len(svc.get_editor(f.project)["clips"]) == 2


def test_recognition_freezes_source_and_does_not_silently_choose_cloud():
    with generation_session() as session:
        f, svc, state = setup(session)
        state = svc.save_editor(f.project, payload(state))
        service = AssemblyMediaService(
            session, Settings(auth_enabled=False, canvas_whisper_base_url=""), svc.storage
        )
        request = {"row_version": state["assembly"]["row_version"], "language": "zh"}
        with pytest.raises(WorkflowError, match="Whisper"):
            service.create_media_job(f.project, request, "missing", "transcription")
        service.settings = service.settings.model_copy(
            update={"canvas_whisper_base_url": "http://127.0.0.1:8765"}
        )
        job = service.create_media_job(f.project, request, "recognition-key", "transcription")
        row = session.get(ProjectRenderJob, int(job["id"]))
        frozen = deepcopy(row.snapshot)
        assert frozen["transcription"]["voice_context_hash"] == state["voice_context_hash"]
        session.commit()
        update = payload(state)
        update["clips"][0]["trim_in_ms"] = 1000
        latest = svc.save_editor(f.project, update)
        assert latest["voice_context_hash"] != state["voice_context_hash"]
        assert (
            service.create_media_job(f.project, request, "recognition-key", "transcription")["id"]
            == job["id"]
        )
        assert session.get(ProjectRenderJob, int(job["id"])).snapshot == frozen
        session.commit()
        unrelated = deepcopy(frozen)
        unrelated["sound"]["document"]["subtitles"] = [{"text": "校对"}]
        assert voice_context(unrelated) == voice_context(frozen)


def test_editor_rolls_back_picture_when_sound_media_is_invalid():
    with generation_session() as session:
        f, svc, initial = setup(session)
        body = payload(initial)
        body["clips"][0]["trim_in_ms"] = 500
        body["sound"]["audio_clips"] = [
            {
                "id": "missing",
                "media_id": "999999",
                "name": "缺失素材",
                "kind": "music",
                "start_ms": 0,
                "duration_ms": 1000,
                "trim_out_ms": 1000,
            }
        ]
        with pytest.raises(NotFound):
            svc.save_editor(f.project, body)
        after = svc.get_editor(f.project)
        assert after["assembly"] == initial["assembly"]
        assert after["sound"]["row_version"] == initial["sound"]["row_version"]
        assert after["clips"][0]["trim_in_ms"] == 0


@pytest.mark.parametrize("ids", [["0"], ["18446744073709551616"], ["bad"], ["1"] * 901])
def test_recovery_material_identifiers_reject_invalid_ranges_before_query(ids):
    with generation_session() as session:
        f, svc, _ = setup(session)
        with pytest.raises(WorkflowError, match="素材标识"):
            svc.get_editor(f.project, material_ids=ids)


def test_recognition_context_ignores_names_and_track_lock_but_tracks_loop_phase():
    source = {
        "clips": [{"clip_id": "picture", "media_id": "1", "trim_out_ms": 3000}],
        "sound": {
            "document": {
                "schema_version": 2,
                "tracks": [{"id": "dialogue", "muted": False, "locked": False}],
                "audio_clips": [{"kind": "dialogue", "name": "对白", "loop_offset_ms": 0}],
            }
        },
    }
    edited = deepcopy(source)
    edited["sound"]["document"]["tracks"][0]["locked"] = True
    edited["sound"]["document"]["audio_clips"][0]["name"] = "重命名"
    assert voice_context(edited) == voice_context(source)
    edited["sound"]["document"]["audio_clips"][0]["loop_offset_ms"] = 200
    assert voice_context(edited) != voice_context(source)


def test_cloud_submission_requires_reconciliation_and_analysis_cache_is_private():
    with generation_session() as session:
        f, svc, state = setup(session)
        state = svc.save_editor(f.project, payload(state))
        model = config(session, "audio", 77)
        model.runtime_profile = {
            "protocol": "openai-audio",
            "api_format": "openai",
            "capability_config": {"audio": {"transcriptionSupported": True}},
        }
        session.commit()
        service = AssemblyMediaService(session, svc.settings, svc.storage)
        request = {"row_version": state["assembly"]["row_version"], "model_config_id": "77"}
        job = service.create_media_job(f.project, request, "cloud-original", "transcription")
        row = session.get(ProjectRenderJob, int(job["id"]))
        row.status, row.manifest = "failed", {"submitted": True}
        session.commit()
        with pytest.raises(WorkflowError, match="待核实"):
            service.create_media_job(f.project, request, "cloud-new-key", "transcription")
        assert not service.media_job(f.project, job["id"])["can_retry"]
        acknowledged = service.media_job(f.project, job["id"], "acknowledge")
        assert acknowledged["result"]["submitted"] and acknowledged["result"]["reconciled"]
        assert (
            service.create_media_job(f.project, request, "cloud-after-check", "transcription")["id"]
            != job["id"]
        )
        analysis = {"media_id": "201"}
        first = service.create_media_job(f.project, analysis, "analysis-1", "audio_analysis")
        row = session.get(ProjectRenderJob, int(first["id"]))
        row.status, row.manifest = "succeeded", {"waveform": [0.1]}
        session.commit()
        assert (
            service.create_media_job(f.project, analysis, "analysis-2", "audio_analysis")["id"]
            == first["id"]
        )
        row = session.get(ProjectRenderJob, int(first["id"]))
        actor = session.info.pop("actor")
        row.initiated_by = 99999
        session.commit()
        session.info["actor"] = actor
        assert (
            service.create_media_job(f.project, analysis, "analysis-3", "audio_analysis")["id"]
            != first["id"]
        )


def test_sync_keeps_independent_mute_and_fixed_music_and_adds_probed_original():
    from short_drama.service.creation.assembly.editing.associations import sync_associations

    first = {
        "clip_id": "a",
        "media_id": "201",
        "trim_in_ms": 0,
        "trim_out_ms": 2000,
        "duration_ms": 2000,
        "has_audio": True,
        "muted": False,
    }
    before = {"clips": [first]}
    doc = SoundDocument(
        schema_version=2,
        audio_clips=[
            {
                "id": "original",
                "media_id": "201",
                "kind": "original",
                "start_ms": 0,
                "duration_ms": 2000,
                "trim_out_ms": 2000,
                "anchor_clip_id": "a",
                "muted": True,
            },
            {
                "id": "music",
                "media_id": "201",
                "kind": "music",
                "start_ms": 250,
                "duration_ms": 1000,
                "trim_out_ms": 1000,
            },
        ],
    ).model_dump(mode="json")
    added = {**first, "clip_id": "b", "media_id": "202"}
    changed = sync_associations(doc, before, {"clips": [added, first]})
    assert next(c for c in changed["audio_clips"] if c["id"] == "original")["start_ms"] == 2000
    assert next(c for c in changed["audio_clips"] if c["id"] == "original")["muted"]
    assert next(c for c in changed["audio_clips"] if c["id"] == "music")["start_ms"] == 250
    assert len(changed["audio_clips"]) == 3
    pending = {"clips": [first, {**added, "trim_out_ms": None}]}
    loaded = sync_associations(doc, pending, {"clips": [first, added]})
    assert any(c["media_id"] == "202" for c in loaded["audio_clips"])


def test_recovery_material_hydration_is_limited_to_published_current_project():
    with generation_session() as session:
        f, svc, _ = setup(session)
        for identifier in range(1000, 1302):
            media = MediaFile(
                id=identifier,
                project_id=int(f.project),
                format_code="image/png",
                original_name="恢复素材",
                storage_locator=f"minio://{svc.settings.minio_image_bucket}/{identifier}.png",
            )
            publish(media)
            session.add(media)
        session.commit()
        assert not any(m["media_id"] == "1000" for m in svc.get_editor(f.project)["materials"])
        hydrated = svc.get_editor(f.project, material_ids=["1000", "999999"])
        assert any(m["media_id"] == "1000" for m in hydrated["materials"])
        assert not any(m["media_id"] == "999999" for m in hydrated["materials"])


@pytest.mark.parametrize("kind", ["material", "music"])
@pytest.mark.parametrize("outcome", ["committed", "rolled_back", "verification_unavailable"])
def test_upload_compensation_checks_durable_references_and_exact_versions(
    monkeypatch, kind, outcome
):
    from io import BytesIO

    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    import short_drama.service.creation.assembly.editing.editor as assembly_editor_service
    import short_drama.service.creation.sound.tracks.adoption as project_sound_service
    from short_drama.core.exceptions import DatabaseUnavailable
    from short_drama.service.creation.sound.tracks.sound import ProjectSoundService
    from short_drama.storage.models import StoredObject

    with generation_session() as session:
        f, editor, _ = setup(session)
        editor.settings = editor.settings.model_copy(update={"audio_production_enabled": True})
        written, deleted, verification_sessions = [], [], []
        state = {"staged": False, "failed": False}

        def upload(*_args, **_kwargs):
            stored = StoredObject(
                bucket=editor.settings.minio_audio_bucket,
                object_name=f"uploaded-{len(written)}",
                storage_locator=f"minio://{editor.settings.minio_audio_bucket}/{len(written)}",
                size=5,
                content_type="audio/wav",
                version_id=f"version-{len(written)}",
            )
            written.append(stored)
            return stored

        def finish(*_args):
            state["staged"] = True
            if outcome != "committed":
                state["failed"] = True
                raise RuntimeError("serialization failed before commit")
            return "https://fixture/upload"

        storage = SimpleNamespace(
            upload=upload,
            download_url=finish,
            delete=lambda locator, *, version_id=None: deleted.append((locator, version_id)),
        )
        monkeypatch.setattr(assembly_editor_service, "StorageService", lambda *_: storage)
        monkeypatch.setattr(project_sound_service, "StorageService", lambda *_: storage)
        monkeypatch.setattr(
            assembly_editor_service,
            "inspect_resource",
            lambda *_: SimpleNamespace(
                duration_ms=None,
                mime_type="image/png",
                width=1,
                height=1,
                checksum="0" * 64,
                video_metadata=None,
            ),
        )
        monkeypatch.setattr(editor, "_material_read", finish)
        monkeypatch.setattr(
            project_sound_service,
            "inspect_audio",
            lambda *_: {"mime": "audio/wav", "duration_ms": 1000},
        )

        def proxy(_path, directory, _settings):
            result = directory / "proxy.m4a"
            result.write_bytes(b"proxy")
            return result

        monkeypatch.setattr(project_sound_service, "audio_proxy", proxy)

        def lose_acknowledgement(_session):
            if state["staged"]:
                raise OperationalError("COMMIT", {}, OSError("commit response lost"))

        def fail_verification(_connection, _cursor, statement, *_args):
            if outcome == "verification_unavailable" and state["failed"]:
                raise OperationalError(statement, {}, OSError("database unavailable"))

        def capture_verification(orm_state):
            if state["staged"] and orm_state.is_select and orm_state.session is not session:
                verification_sessions.append(
                    (
                        orm_state.session is session,
                        getattr(orm_state.statement, "_for_update_arg", None) is not None,
                        "actor" in orm_state.session.info,
                    )
                )

        engine = session.get_bind()
        event.listen(session, "after_commit", lose_acknowledgement)
        event.listen(engine, "before_cursor_execute", fail_verification)
        event.listen(Session, "do_orm_execute", capture_verification)
        try:
            error = DatabaseUnavailable if outcome == "committed" else RuntimeError
            with pytest.raises(error):
                if kind == "material":
                    editor.upload_material(f.project, BytesIO(b"image"), "test.png", "image/png")
                else:
                    service = ProjectSoundService(session, editor.settings, editor.storage)
                    service.upload(f.project, BytesIO(b"audio"), "test.wav")
        finally:
            event.remove(session, "after_commit", lose_acknowledgement)
            event.remove(engine, "before_cursor_execute", fail_verification)
            event.remove(Session, "do_orm_execute", capture_verification)
        assert len(written) == (1 if kind == "material" else 2)
        assert verification_sessions == [(False, True, False)]
        assert deleted == (
            [(row.storage_locator, row.version_id) for row in written]
            if outcome == "rolled_back"
            else []
        )
        with Session(engine) as independent:
            durable = set(
                independent.scalars(
                    select(MediaFile.storage_locator).where(
                        MediaFile.storage_locator.in_([row.storage_locator for row in written])
                    )
                )
            )
        assert durable == (
            {row.storage_locator for row in written} if outcome == "committed" else set()
        )
