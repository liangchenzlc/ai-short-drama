"""Read SQL must avoid write mutexes; mutations retain their lock/version contract.

SQLite executes the business paths; MySQL compilation checks requested lock SQL.
Physical MySQL concurrency is exercised separately by integration tests.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql

import short_drama.db.access as _access  # noqa: F401
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict
from short_drama.domain import MediaFile, ProjectScript
from short_drama.domain.creation.sound.tracks.sound import ProjectSound, SoundMediaReference
from short_drama.schemas.creation.sound.tracks.sound import SoundDocument
from short_drama.service.base import utcnow
from short_drama.service.creation.sound.tracks.sound import ProjectSoundService
from short_drama.service.projects.management.project import ProjectService
from tests.support.generation_fixtures import generation_session
from tests.unit.standard.assembly.test_project_assembly import body, export_body, flow
from tests.unit.standard.assets.test_asset_extraction import extraction
from tests.unit.standard.writing.test_project_writing import setup


@contextmanager
def capture_sql(session):
    statements = []

    def observe(state):
        statements.append(str(state.statement.compile(dialect=mysql.dialect())))

    event.listen(session, "do_orm_execute", observe)
    try:
        yield statements
    finally:
        event.remove(session, "do_orm_execute", observe)


def actor_scope(session, project_id):
    session.info["actor"] = SimpleNamespace(user_id=1, request_id="read-lock-regression")
    session.info["request_project"] = int(project_id)


def test_writing_and_project_reads_avoid_write_locks_but_save_keeps_version_mutex():
    with generation_session() as session:
        e, writing = setup(session)
        saved = writing.save_script(
            e, {"script_id": None, "content": "script", "content_version": "1"}
        )
        confirmed = writing.get(e)
        with session.begin():
            session.get(ProjectScript, int(saved["script"]["id"])).created_by = 1
        actor_scope(session, e)
        with capture_sql(session) as reads:
            assert writing.get(e) == confirmed
            assert confirmed["confirmed_script_id"] == saved["script"]["id"]
            assert ProjectService(session).get(e).id == e
            assert ProjectService(session).list_projects().total == 1
        assert reads and all("FOR UPDATE" not in sql for sql in reads)
        with capture_sql(session) as writes:
            writing.save_script(
                e,
                {
                    "script_id": saved["script"]["id"],
                    "content": "new",
                    "content_version": confirmed["content_version"],
                },
            )
        locks = [sql for sql in writes if "FOR UPDATE" in sql]
        assert any("FROM projects" in sql for sql in locks)
        with pytest.raises(Conflict):
            writing.save_script(
                e,
                {
                    "script_id": saved["script"]["id"],
                    "content": "stale",
                    "content_version": confirmed["content_version"],
                },
            )
        assert writing.get(e)["editing_script"]["content"] == "new"


@pytest.mark.parametrize("with_audio", [False, True])
def test_assembly_sound_and_job_reads_avoid_locks_but_edit_keeps_assembly_mutex(with_audio):
    with generation_session() as session:
        f, assembly = flow(session)
        state = assembly.initialize(f.project)
        job = assembly.export(f.project, export_body(state), "lock-test-export")
        settings = Settings(
            _env_file=None, audio_production_enabled=True, minio_image_bucket="images"
        )
        with session.begin():
            for media in session.scalars(select(MediaFile)):
                if media.published_at is None:
                    media.published_at = utcnow()
            if with_audio:
                session.add(
                    MediaFile(
                        id=900,
                        project_id=int(f.project),
                        format_code="audio/wav",
                        storage_locator=f"minio://{settings.minio_audio_bucket}/music.wav",
                        duration_ms=1000,
                        original_name="music.wav",
                        created_by=1,
                        published_at=utcnow(),
                    )
                )
                session.add(
                    ProjectSound(
                        assembly_id=int(state["assembly"]["id"]),
                        row_version=1,
                        document=SoundDocument(
                            music={"media_id": "900", "trim_out_ms": 1000}
                        ).model_dump(mode="json"),
                        updated_at=utcnow(),
                    )
                )
                session.add(
                    SoundMediaReference(assembly_id=int(state["assembly"]["id"]), media_id=900)
                )
        assembly.settings = settings
        actor_scope(session, f.project)
        storage = SimpleNamespace(presigned_get=lambda *_: "https://media.example.test/music.wav")
        sound = ProjectSoundService(session, settings, storage)
        with capture_sql(session) as reads:
            assert assembly.get(f.project)["clips"] == state["clips"]
            assert assembly.jobs(f.project)["items"][0]["id"] == job["id"]
            assert assembly.job_action(f.project, job["id"], "get")["id"] == job["id"]
            assert sound.get(f.project)["row_version"] == str(int(with_audio))
        assert reads and all("FOR UPDATE" not in sql for sql in reads)
        from short_drama.service.creation.assembly.editing.editor import AssemblyEditorService

        editor = AssemblyEditorService(session, settings, storage)
        payload = body(editor.get_editor(f.project))
        payload["clips"][0]["muted"] = True
        with capture_sql(session) as writes:
            editor.save_editor(f.project, payload)
        locks = [sql for sql in writes if "FOR UPDATE" in sql]
        assert any("FROM project_assemblies" in sql for sql in locks)


def test_extraction_read_is_unlocked_and_does_not_mutate_candidate_versions():
    with extraction() as (session, _p, e, _writing, _sid, tid, service):
        actor_scope(session, e)
        with capture_sql(session) as reads:
            first = service.get(e, tid)
            second = service.get(e, tid)
        assert first == second and not first["stale"]
        assert reads and all("FOR UPDATE" not in sql for sql in reads)
