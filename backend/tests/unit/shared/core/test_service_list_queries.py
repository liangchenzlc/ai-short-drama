"""Authenticated list behavior and bounded queries; MySQL evidence is separate."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session

import short_drama.db.access  # noqa: F401
from short_drama.domain import (
    AIGenerationRecord,
    AsyncTask,
    MediaFile,
    Project,
    ProjectScript,
    ShotAsset,
    ShotScript,
)
from short_drama.domain.assistant.artifacts.models import AgentArtifact
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun, AgentToolCall
from short_drama.domain.creation.sound.voices.voices import (
    CharacterVoice,
    ProjectSoundMode,
    ShotDialogue,
)
from short_drama.service.assistant.execution.run import AgentRunService
from short_drama.service.assistant.operations.artifact import AgentArtifactService
from short_drama.service.assistant.tasks.task import AssistantTaskService
from short_drama.service.base import utcnow
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.creation.sound.voices.voices import (
    NativeVoiceService,
    native_context,
    native_contexts,
)
from short_drama.service.creation.storyboard.management.storyboard import ProjectStoryboardService
from short_drama.service.creation.storyboard.videos.video_context import video_context_hash
from short_drama.service.generations.admission.generation import AIGenerationService
from short_drama.service.generations.results.presentation import generation_display_context
from short_drama.service.projects.management.project import ProjectService
from tests.support.generation_fixtures import config, generation_session, settings


@contextmanager
def select_queries(session):
    statements = []

    def count(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", count)


def workspace(session):
    project = ProjectService(session).create_project({"name": "P"})
    return int(project.id)


@pytest.mark.parametrize("size", [3, 20])
def test_sound_reads_and_snapshots_batch_media_and_preserve_actor_scope(size):
    from short_drama.core.config import Settings
    from short_drama.core.exceptions import NotFound
    from short_drama.domain import ProjectAssembly
    from short_drama.domain.creation.sound.tracks.sound import ProjectSound, SoundMediaReference
    from short_drama.schemas.creation.sound.tracks.sound import SoundDocument
    from short_drama.service.creation.sound.tracks.sound import ProjectSoundService

    cfg = Settings(_env_file=None, native_video_enabled=False)
    with generation_session() as session:
        project_id = workspace(session)
        session.info.pop("actor")
        session.add(
            ProjectAssembly(
                id=500,
                project_id=project_id,
                aspect="auto",
                resolution="720p",
                row_version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        for index in range(size):
            session.add_all(
                [
                    MediaFile(
                        id=1000 + index,
                        project_id=project_id,
                        format_code="audio/wav",
                        duration_ms=1000,
                        storage_locator=f"minio://{cfg.minio_audio_bucket}/{index}",
                        original_name=str(index),
                        created_by=1,
                        published_at=utcnow(),
                    ),
                    MediaFile(
                        id=2000 + index,
                        project_id=project_id,
                        format_code="audio/mp4",
                        duration_ms=1000,
                        storage_locator=f"minio://{cfg.minio_audio_bucket}/proxy-{index}",
                        original_name=str(index),
                        created_by=1,
                        published_at=utcnow(),
                    ),
                    SoundMediaReference(
                        assembly_id=500, media_id=1000 + index, proxy_media_id=2000 + index
                    ),
                ]
            )
        session.add(
            MediaFile(
                id=3000,
                scope_user_id=2,
                format_code="audio/wav",
                duration_ms=1000,
                storage_locator=f"minio://{cfg.minio_audio_bucket}/private",
                original_name="private",
            )
        )
        session.commit()
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1, request_id="sound-batch")
        service = ProjectSoundService(
            session, cfg, SimpleNamespace(presigned_get=lambda *_: "https://fixture/audio")
        )
        document = SoundDocument(
            schema_version=2,
            audio_clips=[
                {
                    "id": f"clip-{index}",
                    "media_id": str(1000 + index),
                    "trim_out_ms": 1000,
                    "duration_ms": 1000,
                }
                for index in range(size)
            ],
        ).model_dump(mode="json")
        sound = ProjectSound(
            assembly_id=500,
            row_version=1,
            document=document,
            reviewed_timeline_hash="0" * 64,
            updated_at=utcnow(),
        )
        creation, assembly = SimpleNamespace(id=project_id), SimpleNamespace(id=500)
        with session.begin(), select_queries(session) as statements:
            result = service._read(creation, assembly, sound, {"clips": []}, for_update=False)
        assert len(result["media"]) == len(result["uploads"]) == size
        assert len(statements) <= 7
        session.add(sound)
        session.commit()
        with session.begin(), select_queries(session) as statements:
            snapshot = service.snapshot(
                SimpleNamespace(id=500, project_id=project_id), {"clips": []}, for_update=False
            )
        assert len(snapshot["media"]) == size
        assert len(statements) <= 4
        sound = SimpleNamespace(
            row_version=1,
            reviewed_timeline_hash="0" * 64,
            document={
                **document,
                "audio_clips": [{**document["audio_clips"][0], "media_id": "3000"}],
            },
        )
        with session.begin(), pytest.raises(NotFound):
            service._read(creation, assembly, sound, {"clips": []}, for_update=False)


def test_native_prefetch_preserves_disabled_settings_without_database_reads():
    shot = SimpleNamespace(id=1)
    assert native_context(None, shot, settings=SimpleNamespace()) is None
    assert native_contexts(None, None, [shot], settings=SimpleNamespace()) == {1: None}


def test_native_voice_reads_are_unlocked_and_dialogue_save_keeps_scope_locks():
    cfg = SimpleNamespace(native_video_enabled=True, audio_production_enabled=True)
    with generation_session() as session:
        project_id = workspace(session)
        character = AssetService(session).create(
            {"kind": "character", "name": "Actor"}, project_id=project_id
        )
        now = utcnow()
        session.add(
            ShotScript(
                id=100,
                project_id=project_id,
                position=1,
                script="S",
                duration_ms=5000,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        session.info["actor"] = SimpleNamespace(user_id=1, request_id="native-read-locks")
        session.info["request_project"] = project_id
        locked_tables = []

        def capture(state):
            if getattr(state.statement, "_for_update_arg", None) is not None:
                locked_tables.extend(mapper.local_table.name for mapper in state.all_mappers)

        event.listen(session, "do_orm_execute", capture)
        try:
            service = NativeVoiceService(session, cfg)
            assert service.mode(project_id)["mode"] == "legacy"
            assert service.voices(project_id, character.id)["candidates"] == []
            assert service.dialogue(project_id, 100)["row_version"] == 0
            assert locked_tables == []
            service.save_dialogue(
                project_id,
                100,
                {
                    "row_version": 0,
                    "request_id": "save",
                    "document": {"lines": [], "reviewed": True},
                },
            )
            assert {"projects", "shot_scripts", "shot_dialogues"} <= set(locked_tables)
        finally:
            event.remove(session, "do_orm_execute", capture)


def test_unlocked_storyboard_read_refreshes_existing_episode_version():
    with generation_session() as session:
        project_id = workspace(session)
        with session.begin():
            held_episode = session.get(Project, project_id)
        with Session(session.get_bind()) as writer, writer.begin():
            writer.execute(
                update(Project).where(Project.id == project_id).values(storyboard_version=2)
            )
        assert held_episode.storyboard_version == 1
        cfg = SimpleNamespace(native_video_enabled=False)
        result = ProjectStoryboardService(session, cfg).list(project_id)
        assert result["storyboard_version"] == "2"


@pytest.mark.parametrize("size", [3, 20])
def test_authenticated_project_list_reuses_opened_time(size):
    with generation_session() as session:
        projects = [ProjectService(session).create_project({"name": f"P{i}"}) for i in range(size)]
        opened = ProjectService(session).open(projects[0].id).last_opened_at
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1)
        with select_queries(session) as statements:
            page = ProjectService(session).list_projects(limit=20)
        assert len(page.items) == size
        assert page.items[0].id == projects[0].id
        assert page.items[0].last_opened_at == opened
        assert all(row.last_opened_at <= opened for row in page.items[1:])
        assert page.items[0].capabilities == {"edit": True}
        assert len(statements) <= 2


@pytest.mark.parametrize("size", [3, 20])
def test_authenticated_task_list_batches_records_owners_and_legacy_origins(size):
    with generation_session() as session:
        project_id = workspace(session)
        model = config(session)
        now = utcnow()
        session.add(
            ShotScript(
                id=50,
                project_id=project_id,
                position=1,
                script="S",
                duration_ms=5000,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        for index in range(size):
            session.add(
                AsyncTask(
                    id=100 + index,
                    project_id=project_id,
                    initiated_by=1,
                    service_type="image",
                    status="queued",
                    idempotency_key=f"task-{index}",
                    request_hash="0" * 64,
                    next_action="submit",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
        requests = {}
        for index in range(size):
            if index % 4 < 2:
                request = {
                    "source": {"scene": "shot_image", "shot_id": "50" if index % 2 else "999"}
                }
            else:
                request = {
                    "source": {
                        "scene": "asset_image",
                        "asset_id": "999",
                        "project_id": str(project_id) if index % 2 else "999",
                    }
                }
                if index % 2:
                    request["source_snapshot"] = {
                        "asset": {"kind": "character", "name": "Frozen Actor"}
                    }
            if index == 0:
                request["display_context"] = {"subject": "Frozen origin"}
            requests[str(100 + index)] = request
            for call in (1, 2):
                session.add(
                    AIGenerationRecord(
                        id=200 + 2 * index + call,
                        task_id=100 + index,
                        call_no=call,
                        config_id=model.id,
                        config_snapshot={"name": f"call-{call}"},
                        request_data=request,
                        status="prepared",
                        created_at=now,
                        updated_at=now,
                    )
                )
        session.commit()
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1)
        with session.begin():
            expected = {
                key: value.get("display_context") or generation_display_context(session, value)
                for key, value in requests.items()
            }
        with select_queries(session) as statements:
            page = AIGenerationService(session, settings).list(limit=20)
        assert len(page["items"]) == size
        for row in page["items"]:
            assert row["config"]["name"] == "call-1"
            assert row["display_context"] == expected[row["generation_id"]]
            assert row["can_cancel"] and not row["can_retry"] and not row["can_resume"]
        assert len(statements) <= 8


@pytest.mark.parametrize("size", [3, 20])
def test_authenticated_native_storyboard_batches_missing_dialogue(monkeypatch, size):
    cfg = SimpleNamespace(native_video_enabled=True, audio_production_enabled=True)
    monkeypatch.setattr("short_drama.service.creation.sound.voices.context.Settings", lambda: cfg)
    with generation_session() as session:
        project_id = workspace(session)
        now = utcnow()
        session.add(ProjectSoundMode(project_id=project_id, mode="native", row_version=1))
        for index in range(size):
            session.add(
                ShotScript(
                    id=100 + index,
                    project_id=project_id,
                    position=index + 1,
                    script="S",
                    duration_ms=5000,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.add(
            ShotDialogue(shot_id=100, row_version=3, document={"reviewed": True, "lines": []})
        )
        session.commit()
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1)
        with select_queries(session) as statements:
            page = ProjectStoryboardService(session, cfg).list(project_id)
        assert len(page["items"]) == size
        assert len(statements) <= 8
        with session.begin():
            for row in page["items"]:
                shot = session.get(ShotScript, int(row["id"]))
                assert row["native_speech"] == native_context(session, shot, settings=cfg)
                assert row["video_context_hash"] == video_context_hash(
                    row["context_hash"],
                    None,
                    row["video_prompt"],
                    row["video_settings"],
                    session=session,
                    shot=shot,
                )
        assert page["items"][0]["native_speech"]["dialogue_version"] == 3
        assert page["items"][1]["native_speech"]["dialogue_version"] == 0


def agent_rows(session, size):
    project_id = workspace(session)
    session.info.pop("actor")
    now = utcnow()
    connection = session.connection().connection.driver_connection
    connection.create_function("OCTET_LENGTH", 1, lambda value: len(value.encode()))
    connection.create_function("JSON_OBJECT", 0, lambda: "{}")
    for index in range(size):
        session.add(
            AgentConversation(
                id=100 + index,
                owner_user_id=1,
                project_id=project_id,
                title=f"C{index}",
                scope_version=3,
                fixed_requirements={},
                created_at=now,
                updated_at=now,
            )
        )
        if index:
            for call, status in ((1, "failed"), (2, "succeeded")):
                session.add(
                    AgentRun(
                        id=200 + index * 2 + call,
                        conversation_id=100 + index,
                        trigger_message_id=1000 + index * 2 + call,
                        initiated_by=1,
                        model_config_id=1,
                        status=status,
                        checkpoint={},
                        config_snapshot={},
                        budget={},
                        usage={},
                        created_at=now,
                        updated_at=now,
                        finished_at=now,
                    )
                )
        session.add(
            ProjectScript(
                id=400 + index,
                project_id=project_id,
                position=index + 1,
                content=f"Script {index}",
                created_by=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            AgentArtifact(
                id=500 + index,
                project_id=project_id,
                tool_call_id=1000 + index,
                result_index=1,
                kind="script_candidate",
                script_id=400 + index,
                source_content=f"Original Script {index}",
                created_by=1,
                source_snapshot={
                    "project_id": str(project_id),
                    "content_version": 1,
                    "storyboard_version": 1,
                    "project_row_version": 1,
                },
                metadata_json={},
                created_at=now,
                updated_at=now,
            )
        )
    session.commit()
    session.expunge_all()
    session.info["actor"] = SimpleNamespace(user_id=1)
    return project_id


@pytest.mark.parametrize("size", [3, 20])
def test_agent_conversation_and_shared_artifact_lists_batch_details(size):
    with generation_session() as session:
        project_id = agent_rows(session, size)
        cfg = SimpleNamespace(agent_enabled=True)
        with select_queries(session) as statements:
            page = AssistantTaskService(session, cfg).list_global_conversations(
                project_id=project_id
            )
        assert len(page.items) == size
        assert page.items[-1].last_run_status is None
        assert all(row.last_run_status == "succeeded" for row in page.items[:-1])
        assert len(statements) <= 5
        with select_queries(session) as statements:
            page = AgentArtifactService(session).list(project_id)
        assert [row["preview"] for row in page["items"]] == [
            f"Original Script {index}" for index in reversed(range(size))
        ]
        assert len(statements) <= 6


@pytest.mark.parametrize("size", [3, 20])
def test_agent_runtime_batches_active_run_metadata(size):
    with generation_session() as session:
        agent_rows(session, 1)
        session.info.pop("actor")
        now = utcnow()
        for index in range(size):
            session.add(
                AgentRun(
                    id=200 + index,
                    conversation_id=100,
                    trigger_message_id=1000 + index,
                    initiated_by=1,
                    model_config_id=1,
                    status="running",
                    checkpoint={},
                    config_snapshot={},
                    budget={},
                    usage={},
                    created_at=now,
                    updated_at=now,
                    finished_at=None,
                )
            )
            if index:
                for call in (1, 2):
                    session.add(
                        AgentToolCall(
                            id=300 + 2 * index + call,
                            run_id=200 + index,
                            turn_id=1000 + index,
                            call_index=call,
                            provider_call_id=f"call-{call}",
                            tool_name="plan",
                            arguments={},
                            arguments_hash="0" * 64,
                            idempotency_key=f"{index:032}{call:032}",
                            status="waiting_review",
                            review_payload={"title": f"review-{call}", "summary": "S", "steps": []},
                            review_hash="0" * 64,
                            created_at=now,
                            updated_at=now,
                        )
                    )
        session.commit()
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1)
        with select_queries(session) as statements:
            state = AgentRunService(session, SimpleNamespace(agent_enabled=True)).runtime_state(100)
        assert state.active_run.id == 200
        assert state.queued_runs == []
        assert len(statements) <= 6


def test_native_storyboard_reuses_shared_voice_sample_and_preserves_strict_validation(monkeypatch):
    from short_drama.core.exceptions import WorkflowError

    cfg = SimpleNamespace(native_video_enabled=True, audio_production_enabled=True)
    monkeypatch.setattr("short_drama.service.creation.sound.voices.context.Settings", lambda: cfg)
    with generation_session() as session:
        project_id = workspace(session)
        character = AssetService(session).create(
            {"kind": "character", "name": "Actor"}, project_id=project_id
        )
        session.add(
            MediaFile(
                id=80,
                project_id=project_id,
                format_code="audio/wav",
                storage_locator="minio://voice/sample.wav",
                published_at=utcnow(),
                duration_ms=3100,
                byte_size=100,
                checksum_sha256="1" * 64,
            )
        )
        session.add(ProjectSoundMode(project_id=project_id, mode="native", row_version=1))
        session.add(
            CharacterVoice(
                project_id=project_id,
                asset_id=character.id,
                row_version=2,
                record_id=90,
                media_id=80,
            )
        )
        now = utcnow()
        for index in range(20):
            session.add(
                ShotScript(
                    id=100 + index,
                    project_id=project_id,
                    position=index + 1,
                    script="S",
                    duration_ms=5000,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                ShotAsset(
                    id=200 + index,
                    project_id=project_id,
                    shot_id=100 + index,
                    asset_id=character.id,
                )
            )
            session.add(
                ShotDialogue(
                    shot_id=100 + index,
                    row_version=1,
                    document={
                        "reviewed": True,
                        "lines": [{"character_id": character.id, "text": "Hello"}],
                    },
                )
            )
        session.commit()
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1)
        with select_queries(session) as statements:
            page = ProjectStoryboardService(session, cfg).list(project_id)
        assert len(statements) <= 12
        with session.begin():
            for row in page["items"]:
                shot = session.get(ShotScript, int(row["id"]))
                from short_drama.service.creation.writing.script.publication import (
                    public_voice_context,
                )

                assert row["native_speech"] == public_voice_context(
                    native_context(session, shot, strict=True, settings=cfg)
                )
                assert row["native_speech"]["voices"][0]["version"] == 2
                assert row["video_context_hash"] == video_context_hash(
                    row["context_hash"],
                    None,
                    "",
                    row["video_settings"],
                    session=session,
                    shot=shot,
                )
        session.info.pop("actor")
        with session.begin():
            session.get(MediaFile, 80).duration_ms = 1000
        session.info["actor"] = SimpleNamespace(user_id=1)
        with session.begin(), pytest.raises(WorkflowError) as error:
            native_context(session, session.get(ShotScript, 100), strict=True, settings=cfg)
        assert error.value.code == "native_voice_required"


def test_task_list_uses_latest_call_for_resume_and_retains_owner_permissions():
    with generation_session() as session:
        project_id = workspace(session)
        model = config(session)
        now = utcnow()
        for index in range(20):
            status = "queued" if index == 0 else "failed"
            session.add(
                AsyncTask(
                    id=100 + index,
                    project_id=project_id,
                    initiated_by=2 if index == 0 else 1,
                    service_type="image",
                    status=status,
                    idempotency_key=f"task-{index}",
                    request_hash="0" * 64,
                    next_action="submit",
                    created_at=now,
                    updated_at=now,
                    finished_at=now if status == "failed" else None,
                    error={"code": "business_save_failed"} if index % 2 else {"code": "timeout"},
                )
            )
            for call in (1, 2):
                session.add(
                    AIGenerationRecord(
                        id=200 + 2 * index + call,
                        task_id=100 + index,
                        call_no=call,
                        config_id=model.id,
                        config_snapshot={"name": f"call-{call}"},
                        request_data={"display_context": {"subject": "Frozen"}},
                        status="prepared" if call == 1 else "succeeded" if index % 2 else "unknown",
                        text_content="Saved output" if call == 2 and index % 2 else None,
                        created_at=now,
                        updated_at=now,
                    )
                )
        session.commit()
        session.expunge_all()
        session.info["actor"] = SimpleNamespace(user_id=1)
        with select_queries(session) as statements:
            page = AIGenerationService(session, settings).list(limit=20)
        assert len(statements) <= 5
        assert len(page["items"]) == 19
        for row in page["items"]:
            index = int(row["generation_id"]) - 100
            assert row["config"]["name"] == "call-1"
            assert row["can_cancel"] is False
            assert row["can_resume"] is bool(index % 2)
            assert row["can_retry"] is False
